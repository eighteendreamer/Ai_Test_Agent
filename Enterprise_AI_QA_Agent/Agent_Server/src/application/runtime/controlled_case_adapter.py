"""Owned direct-graph invocation; not RunItem admission or a production route."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from src.application.deep_agents.framework_transcript import deserialize_messages
from src.application.runtime.framework_graph_invocation import FrameworkGraphOutcome, invoke_and_audit_framework_graph
from src.graph.controlled_case import build_controlled_case_graph


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

    async def execute(self, request: ControlledCaseGraphRequest) -> FrameworkGraphOutcome:
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
                    or owner.binding.checkpoint_namespace != request.checkpoint_namespace):
                    raise ValueError("Controlled graph request differs from its captured LangGraph owner")
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
                            "run_name": "enterprise_ai_qa_agent.controlled_case_turn", "metadata": {
                                "session_id": request.session_id, "turn_id": request.turn_id,
                                "trace_id": request.trace_id, "harness": "langgraph"}},
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
