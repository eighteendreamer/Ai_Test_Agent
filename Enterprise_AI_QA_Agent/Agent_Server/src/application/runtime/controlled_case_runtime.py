"""Formal input/governed execution and an explicit sole Session finalizer port."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Any

from src.application.models.framework_model_call_policy import model_config_fingerprint
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphRequest
from src.application.runtime.framework_graph_invocation import FrameworkGraphOutcome
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.controlled_case_preparation import prepare_controlled_case_input
from src.runtime.execution_context import current_framework_owner, graph_event_scope
from src.runtime.execution_logging import (
    append_graph_event_durable, append_graph_event_intent_durable, prepare_graph_event_log,
    complete_graph_event_log,
)
from src.schemas.controlled_case_task_input import (
    ControlledCasePreparedTaskInputAsset, controlled_case_native_input, controlled_case_schema_tools,
    ControlledCaseResultPreparation, ControlledCaseResultTaskInputAsset, ControlledCaseFinalizationTaskInputAsset,
    parse_controlled_case_task_input,
)
from src.application.runtime.turn_projection import RuntimeTurnResult
from src.schemas.framework_graph_input import FrameworkProjectionIdentity, FrameworkRuntimePreparation
from src.schemas.framework_transcript import CONTROLLED_CASE_CONTEXT_VERSION_KEY
from src.schemas.session import ExecutionEvent, ConversationResponse
from src.schemas.framework_runtime_result import ControlledCaseRuntimeResultAsset
from src.schemas.controlled_case_assessment import ControlledCaseAssessmentAsset


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlledCaseRuntimeOutcome:
    """Optional original assessment preparation; never a completed RunItem."""
    graph_outcome: FrameworkGraphOutcome
    state: dict[str, Any]
    tool_records: list[dict[str, Any]]
    runtime_result: RuntimeTurnResult | None = None
    result_asset: ControlledCaseRuntimeResultAsset | None = None
    conversation_response: ConversationResponse | None = None
    assessment_asset: ControlledCaseAssessmentAsset | None = None


class ControlledCaseRuntime:
    """Reuse shared assembly/tools and the original one-shot executor.

    No upstream counterpart (checked: TurnContextAssembly, original case
    Session execution, controlled executor, v4 §9.2/§12.1): formal input has
    no business user row/message-stage. Do not fake one or run a second loop.
    Neither port is installed in main/Worker or grants recovery authority.
    """

    def __init__(self, *, runtime_service, session_service=None, assessment_service=None):
        if assessment_service is not None and session_service is None:
            raise ValueError("Controlled assessment requires explicit Session finalization assembly")
        self._runtime = runtime_service
        self._sessions = session_service
        self._assessments = assessment_service

    async def prepare_input(self, session, request, *, detail, item, version, attempt_id, projection_identity):
        # Capture all mutable inputs before retrieval/configuration awaits.
        session, request, detail, item, version = deepcopy((session, request, detail, item, version))
        scope = {"session_id": session.id, "turn_id": request.turn_id,
            "run_id": detail.run.id, "run_item_id": item.id, "execution_id": attempt_id}
        phase = "formal_input"
        try:
            identity = FrameworkProjectionIdentity.model_validate(projection_identity.model_dump(mode="json"))
            binding = request.runtime_binding
            metadata = session.metadata or {}
            if (current_framework_owner() is not None or binding is None or binding.runtime_kind != "langgraph"
                or binding.runtime_version != "v2.0.0" or identity.binding != binding
                or (request.session_id, request.turn_id) != (session.id, binding.turn_id)
                or binding.session_id != session.id or session.project_id != detail.run.project_id
                or request.user_message_id is not None or session.messages or session.snapshot_count != 0
                or identity.snapshot_version != 1
                or session.mode_key != detail.run.mode_key or request.mode_key != detail.run.mode_key
                or metadata.get("source") != "test_run_agent_execution"
                or metadata.get("run_id") != detail.run.id or metadata.get("run_item_id") != item.id):
                raise ValueError("Controlled preparation requires its dedicated original formal Session and binding")
            context = build_agent_case_context(detail, item, version)
            if request.user_message != context.objective:
                raise ValueError("Controlled preparation input differs from its formal objective")
            native = controlled_case_native_input(context, attempt_id, binding.turn_id)
            # Validate the original claim/plan/attempt BEFORE retrieval. This
            # pure seed is not persisted or used as application preparation.
            seed = ControlledCaseGraphRequest(session_id=session.id, turn_id=binding.turn_id,
                trace_id=request.trace_id, model_key=request.model_key, checkpoint_namespace=binding.checkpoint_namespace,
                system_prompt="", framework_messages=native)
            prepare_controlled_case_input(detail=detail, item=item, version=version,
                attempt_id=attempt_id, binding=binding, graph_request=seed)
            phase = "model_configuration"
            config = self._runtime._model_runtime_service.get_model_config(request.model_key)
            if (config is None or model_config_fingerprint(config) != binding.model_config_fingerprint
                or (config.provider, config.transport) != (binding.model_provider, binding.model_transport)):
                raise ValueError("Controlled preparation differs from its bound model configuration")
            phase = "context_assembly"
            if self._runtime._turn_context_assembly is None:
                raise RuntimeError("Controlled context assembly is unavailable in this Worker")
            # Preparation owns reserved framework flags. Request context cannot
            # turn formal data into Deep Agents state or transcript authority.
            request.context = {key: value for key, value in request.context.items()
                if not key.startswith(("framework_", "deep_agent_")) and key != CONTROLLED_CASE_CONTEXT_VERSION_KEY}
            request.context.update(context.runtime_context(), attempt_id=attempt_id,
                case_execution_managed=True, case_assessment_requested=True,
                trusted_security_runtime_direct_execution=False)
            if request.mode_key == "ui_automation":
                # Same managed RunItem rule as SessionService.execute_case_turn.
                request.skill_keys = [key for key in request.skill_keys if key != "playwright-e2e-testing"]
            state = self._runtime._build_initial_state(session, request, include_framework_transcript=False)
            state["runtime_messages"] = [{"role": "user", "content": context.objective}]
            state["framework_messages"] = native
            state["context_bundle"].update({CONTROLLED_CASE_CONTEXT_VERSION_KEY: 1,
                "framework_tool_projection_version": 3,
                "framework_model_protocol": {"provider": config.provider, "transport": config.transport}})
            # These observed event intents live in the same frozen input. The
            # running owner acknowledges their original IDs before graph work.
            # Never leak them into another Turn's active sink during preparation.
            with graph_event_scope(None):
                state = await self._runtime._turn_context_assembly.prepare(state)
            from src.application.testing.verification_service import VerificationService
            from src.schemas.framework_verification_projection import FrameworkVerificationPreparation, VERIFICATION_PREPARATION_KEY

            verification = VerificationService.prepare_projection(identity, trace_id=state["trace_id"])
            state["context_bundle"][VERIFICATION_PREPARATION_KEY] = verification
            if (state["selected_model_provider"], state["selected_model_transport"]) != (
                    binding.model_provider, binding.model_transport):
                raise ValueError("Controlled context assembly cannot replace the bound model protocol")
            graph = ControlledCaseGraphRequest(session_id=session.id, turn_id=binding.turn_id,
                trace_id=state["trace_id"], model_key=state["selected_model_key"],
                checkpoint_namespace=binding.checkpoint_namespace, system_prompt=state["system_prompt"],
                framework_messages=state["framework_messages"], business_tools=controlled_case_schema_tools(state),
                interrupt_on={key: {"allowed_decisions": ["approve", "reject"]}
                    for key in state["approval_required_tool_keys"]})
            phase = "frozen_preparation"
            asset = prepare_controlled_case_input(detail=detail, item=item, version=version,
                attempt_id=attempt_id, binding=binding, graph_request=graph,
                runtime_preparation=FrameworkRuntimePreparation(identity=identity, initial_state=state),
                result_preparation=ControlledCaseResultPreparation(schema_version=1,
                    timestamp_source="native_checkpoint.ts", output_contract="current_final_ai_text"),
                verification_preparation=FrameworkVerificationPreparation.model_validate(verification))
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_runtime_preparation_failed", extra={**scope,
                "recovery_reason": phase, "error_type": type(exc).__name__})
            raise
        logger.info("controlled_case_runtime_input_prepared", extra={**scope, "binding_id": binding.binding_id,
            "artifact_id": asset.asset_sha256, "visible_tool_count": len(state["model_visible_tool_keys"])})
        return asset

    async def execute(self, task, *, executor, resource_id, fencing_token):
        # This application port cannot silently upgrade historical schema 1.
        try:
            original = parse_controlled_case_task_input(task.input.get("case_execution_input"))
            if not isinstance(original, ControlledCasePreparedTaskInputAsset):
                raise ValueError("Controlled runtime requires its frozen full application preparation")
        except (TypeError, ValueError) as exc:
            logger.warning("controlled_case_runtime_execution_rejected", extra={"task_id": task.id,
                "session_id": task.session_id, "turn_id": task.turn_id,
                "recovery_reason": "frozen_preparation_unavailable", "error_type": type(exc).__name__})
            raise
        state = None
        records = []
        runtime_result = None
        result_asset = None
        conversation_response = None
        assessment_asset = None

        async def build(prepared):
            nonlocal state
            if not isinstance(prepared, ControlledCasePreparedTaskInputAsset) or prepared.asset_sha256 != original.asset_sha256:
                raise ValueError("Controlled runtime preparation changed before execution")
            state = deepcopy(prepared.runtime_preparation.initial_state)
            intents, state["event_log"] = state["event_log"], []
            for event in intents:
                await append_graph_event_intent_durable(state, ExecutionEvent(session_id=state["session_id"], **event))
            prepare_graph_event_log(state)
            await append_graph_event_durable(state, "runtime.turn_started", "runtime",
                "Controlled formal case execution started for the current turn.", harness="langgraph",
                mode_key=state["mode_key"])
            if state["model_visible_tool_keys"] and (not self._runtime._deep_agent_governed_tools_enabled
                    or self._runtime._tool_governance_service is None):
                raise RuntimeError("Controlled assembled business tool governance is unavailable")
            tools, _ = await self._runtime._build_deep_agent_business_tools(state=state, tool_records=records)
            return tools

        async def project(prepared, outcome):
            nonlocal state, runtime_result, result_asset, conversation_response, assessment_asset
            if not isinstance(prepared, ControlledCaseResultTaskInputAsset) or outcome.native_interrupts:
                # Historical preparation is not silently upgraded. Native wait
                # still needs the original business approval/admission contract.
                return
            from src.runtime.controlled_case_result_reader import ControlledCaseResultReader

            owner = current_framework_owner()
            projected = await ControlledCaseResultReader(executor._tasks).prepare(
                owner, prepared, outcome, state["event_log"])
            await append_graph_event_intent_durable(state, projected.events[-1])
            await complete_graph_event_log(state, outcome.native_terminal.proof.model_dump(mode="json"))
            if isinstance(prepared, ControlledCaseFinalizationTaskInputAsset):
                from src.runtime.framework_runtime_results import FrameworkRuntimeResults

                result_asset = await FrameworkRuntimeResults(executor._tasks).freeze_controlled_case(
                    owner, runtime_result=projected, native=outcome.native_terminal)
                if self._sessions is not None:
                    conversation_response = await self._sessions.finalize_controlled_case_result(
                        owner, native=outcome.native_terminal)
                    if self._assessments is not None:
                        assessment_asset = await self._assessments.freeze(owner, native=outcome.native_terminal)
            runtime_result = projected
            state = deepcopy(projected.state)

        outcome = await executor.execute(task, build_business_tools=build,
            resource_id=resource_id, fencing_token=fencing_token, project_outcome=project)
        return ControlledCaseRuntimeOutcome(graph_outcome=outcome, state=state, tool_records=records,
            runtime_result=runtime_result, result_asset=result_asset, conversation_response=conversation_response,
            assessment_asset=assessment_asset)
