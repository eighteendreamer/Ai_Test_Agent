"""Owned direct-graph invocation; not RunItem admission or a production route."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
from typing import Any

from src.application.deep_agents.framework_transcript import deserialize_messages
from src.application.runtime.framework_graph_invocation import FrameworkGraphOutcome, invoke_and_audit_framework_graph
from src.graph.controlled_case import build_controlled_case_graph, build_controlled_case_native_schema


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlledCaseGraphRequest:
    session_id: str
    turn_id: str
    trace_id: str
    model_key: str
    checkpoint_namespace: str
    system_prompt: str
    framework_messages: list[dict[str, Any]]
    business_tools: list[Any] = field(default_factory=list)
    interrupt_on: dict[str, Any] = field(default_factory=dict)
    resume: dict[str, Any] | None = None
    continue_from_checkpoint: bool = False


class ControlledCaseGraphAdapter:
    """Compose the original saver/policy scopes with the controlled kernel.

    Native outcomes preserve messages and original accounting, not an
    Assessment, RuntimeTurnResult, recovery permit or application commit.
    Callers still supply frozen server-owned input and governed tool wrappers.
    No filesystem, autonomous planning or subagent surface is added here.
    """

    def __init__(self, *, model_resolver, checkpointer_factory, model_call_policy):
        self._model_resolver = model_resolver
        self._checkpointer_factory = checkpointer_factory
        self._model_call_policy = model_call_policy

    @asynccontextmanager
    async def open_native_reader(self, request, *, source):
        """Inspect original native state without acquiring any execution right.

        The Python request correlates the original scope only. It is NOT a
        versioned persisted AgentCase preparation, a RunItem lease or an
        application recovery/admission proof. Those contracts remain separate.
        """
        from src.schemas.runtime_binding import RuntimeCheckpointSource

        source = RuntimeCheckpointSource.model_validate(source)
        try:
            prepared = _require_native_request(request, source)
            if self._checkpointer_factory is None:
                raise ValueError("Controlled native reader requires its source-only checkpoint provider")
            async with self._checkpointer_factory(source=source) as saver:
                graph = self.native_schema(prepared if prepared is not None else request, source=source, saver=saver)
                if prepared is not None:
                    prepared.require_checkpoint(await saver.aget_tuple({"configurable": {"thread_id": source.binding.thread_id}}))
                logger.info("controlled_case_native_reader_opened", extra={"binding_id": source.binding.binding_id,
                    "task_id": source.task_id, "owner_epoch": source.owner_epoch})
                yield graph, saver
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_native_reader_failed", extra={"binding_id": source.binding.binding_id,
                "task_id": source.task_id, "error_type": type(exc).__name__})
            raise

    def native_schema(self, request, *, source, saver):
        """Use the same inert schema for pooled reads and atomic rechecks."""
        from src.schemas.runtime_binding import RuntimeCheckpointSource

        source = RuntimeCheckpointSource.model_validate(source)
        _require_native_request(request, source)
        return build_controlled_case_native_schema(source=source, checkpointer=saver)

    async def execute(self, request: ControlledCaseGraphRequest) -> FrameworkGraphOutcome:
        return await self._execute(request)

    async def execute_frozen(self, asset, *, business_tools, resume=None,
            continue_from_checkpoint=False) -> FrameworkGraphOutcome:
        """Use the same graph boundary without granting formal recovery rights.

        The caller still owns RunItem admission, tool governance and decision
        delivery. A frozen input and a native checkpoint grant none of those.
        """
        from src.schemas.controlled_case_graph_input import parse_controlled_graph_input

        try:
            prepared = parse_controlled_graph_input(asset)
            request = prepared.request(business_tools=business_tools, resume=resume,
                continue_from_checkpoint=continue_from_checkpoint)
            logger.info("controlled_case_graph_input_loaded", extra={"binding_id": prepared.binding.binding_id,
                "turn_id": prepared.binding.turn_id, "artifact_id": prepared.asset_sha256})
        except Exception as exc:
            logger.warning("controlled_case_graph_input_rejected", extra={"error_type": type(exc).__name__})
            raise
        return await self._execute(request, frozen_input=prepared)

    async def _execute(self, request, *, frozen_input=None):
        try:
            if (not request.session_id or not request.turn_id or not request.model_key
                or self._checkpointer_factory is None or self._model_call_policy is None):
                raise ValueError("Controlled graph requires its request, captured saver and model policy")
            if request.resume is not None and request.continue_from_checkpoint:
                raise ValueError("A checkpoint continuation cannot also submit a new decision")
            async with self._checkpointer_factory() as saver:
                owner = getattr(saver, "runtime_owner", None)
                if (owner is None or owner.binding.runtime_kind != "langgraph"
                    or owner.binding.session_id != request.session_id or owner.binding.turn_id != request.turn_id
                    or owner.binding.checkpoint_namespace != request.checkpoint_namespace
                    or frozen_input is not None and frozen_input.binding != owner.binding):
                    raise ValueError("Controlled graph request differs from its captured LangGraph owner")
                metadata = {"session_id": request.session_id, "turn_id": request.turn_id,
                    "trace_id": request.trace_id, "harness": "langgraph"}
                if frozen_input is not None:
                    from src.schemas.controlled_case_graph_input import CONTROLLED_GRAPH_INPUT_KEY

                    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": request.turn_id}})
                    continuing = request.resume is not None or request.continue_from_checkpoint
                    if checkpoint is not None or continuing:
                        frozen_input.require_checkpoint(checkpoint)
                    if checkpoint is not None and not continuing:
                        raise ValueError("Controlled frozen input already has a checkpoint; initial replay is not allowed")
                    # Pinned SDK get_checkpoint_metadata copies this scalar
                    # into every checkpoint via the original fenced saver.
                    # This sequential observation is NOT atomic case admission.
                    metadata[CONTROLLED_GRAPH_INPUT_KEY] = frozen_input.asset_sha256
                if request.continue_from_checkpoint:
                    invoke_input = None
                elif request.resume is not None:
                    from langgraph.types import Command

                    invoke_input = Command(resume=request.resume)
                else:
                    messages = deserialize_messages(request.framework_messages)
                    if not messages:
                        raise ValueError("Controlled graph requires its original native input messages")
                    invoke_input = {"messages": messages}
                async with self._model_call_policy.execution_scope(owner) as deadline:
                    model = await self._model_resolver(request.model_key)
                    graph = build_controlled_case_graph(model=model, model_key=request.model_key,
                        system_prompt=request.system_prompt, tools=request.business_tools,
                        interrupt_on=request.interrupt_on, owner=owner, checkpointer=saver,
                        model_call_policy=self._model_call_policy)
                    outcome = await invoke_and_audit_framework_graph(agent=graph, invoke_input=invoke_input,
                        invoke_config={"configurable": {"thread_id": request.turn_id},
                            "run_name": "enterprise_ai_qa_agent.controlled_case_turn", "metadata": metadata},
                        checkpointer=saver, execution_owner=owner, model_call_policy=self._model_call_policy,
                        execution_deadline=deadline)
            logger.info("controlled_case_graph_outcome_captured", extra={"binding_id": owner.binding.binding_id,
                "native_terminal": outcome.native_terminal is not None,
                "native_interrupt_count": len(outcome.native_interrupts)})
            return outcome
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_graph_invocation_failed", extra={
                "turn_id": request.turn_id, "error_type": type(exc).__name__})
            raise


def _require_native_request(request, source):
    from src.schemas.controlled_case_graph_input import ControlledCaseGraphInputAsset, parse_controlled_graph_input

    if isinstance(request, (dict, ControlledCaseGraphInputAsset)):
        prepared = parse_controlled_graph_input(request)
        if prepared.binding != source.binding:
            raise ValueError("Controlled frozen input differs from its complete original source binding")
        return prepared
    if (not isinstance(request, ControlledCaseGraphRequest) or source.binding.runtime_kind != "langgraph"
        or (request.session_id, request.turn_id, request.checkpoint_namespace)
        != (source.binding.session_id, source.binding.turn_id, source.binding.checkpoint_namespace)):
        raise ValueError("Controlled native request differs from its original LangGraph source")
