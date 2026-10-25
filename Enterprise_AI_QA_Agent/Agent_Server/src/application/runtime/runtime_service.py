from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.deep_agents import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest
from src.application.observability import LangSmithObservabilityAdapter, TraceContext
from src.application.context.context_compaction_service import ContextCompactionService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.agent_loop import AgentLoop
from src.application.runtime.error_recovery import ErrorRecoveryCascade
from src.application.runtime.state_persistence import build_pending_turn, persisted_turn_budget
from src.application.runtime.tool_child_wait import park_tool_wait, waiting_tool_checkpoints
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_messages import build_tool_message
from src.application.runtime.tool_governance_service import (
    GovernedToolCallContext,
    GovernedToolCallResult,
    ToolGovernanceService,
)
from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
from src.application.capabilities.capability_resolver import CapabilityResolver
from src.application.capabilities.tool_exposure_policy import ToolExposurePolicy
from src.application.test_runs.execution_replanning import TOOL_KEY as REPLANNING_TOOL_KEY, execution_replanning_enabled
from src.registry.agents import AgentRegistry
from src.registry.skills import SkillRegistry
from src.application.security.approval_scope_service import ApprovalScopeService
from src.application.security.authorization import verified_grant_matches_target
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.domain.models import SessionRecord
from src.infrastructure.storage_utils import make_json_safe
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import graph_event_scope, current_framework_owner
from src.runtime.execution_logging import append_graph_event, truncate_text
from src.schemas.model_config import ModelConfigRecord, is_model_failure_summary
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    ExecutionRequest,
    MessageKind,
    MessageRole,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
)
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord
from src.schemas.framework_approval import FrameworkContinuationPlan
from src.schemas.runtime_binding import RuntimeBinding
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired


logger = logging.getLogger(__name__)


@dataclass
class RuntimeTurnResult:
    output_text: str
    events: list[ExecutionEvent]
    snapshot: SessionSnapshot
    approvals: list[dict]
    state: dict
    tool_messages: list[ChatMessage]
    pending_turn: dict


class RuntimeService:
    def __init__(
        self,
        graph,
        model_runtime_service: ModelRuntimeService,
        tool_runtime_service: ToolRuntimeService,
        tool_registry: ToolRegistry,
        runtime_control: RuntimeControlRegistry,
        transcript_hygiene_service: TranscriptHygieneService | None = None,
        max_iterations: int = 8,
        session_resource_service: SessionResourceService | None = None,
        context_compaction_service: ContextCompactionService | None = None,
        context_max_tail_messages: int = 24,
        observability_service: LangSmithObservabilityAdapter | None = None,
        deep_agent_runtime_adapter: DeepAgentRuntimeAdapter | None = None,
        deep_agent_enabled: bool = False,
        deep_agent_pilot_mode_keys: list[str] | None = None,
        deep_agent_read_only_filesystem_enabled: bool = False,
        deep_agent_read_only_max_file_size_mb: int = 10,
        deep_agent_read_only_max_output_chars: int = 120000,
        deep_agent_cognitive_planning_enabled: bool = False,
        deep_agent_turn_timeout_seconds: float = 600.0,
        deep_agent_cognitive_subagents_enabled: bool = False,
        deep_agent_max_subagent_calls_per_turn: int = 1,
        deep_agent_subagent_model_call_limit: int = 6,
        deep_agent_subagent_tool_call_limit: int = 12,
        permission_service: PermissionService | None = None,
        agent_registry: AgentRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        deep_agent_governed_tools_enabled: bool = False,
        tool_job_service: ToolJobService | None = None,
        framework_approval_projection=None,
        framework_approval_continuation=None,
    ) -> None:
        self._graph = graph
        self._model_runtime_service = model_runtime_service
        self._tool_runtime_service = tool_runtime_service
        self._tool_registry = tool_registry
        self._runtime_control = runtime_control
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._max_iterations = max_iterations
        self._session_resource_service = session_resource_service
        self._context_compaction_service = context_compaction_service
        self._context_max_tail_messages = context_max_tail_messages
        self._observability_service = observability_service
        self._deep_agent_runtime_adapter = deep_agent_runtime_adapter
        self._deep_agent_enabled = bool(deep_agent_enabled)
        self._deep_agent_pilot_mode_keys = frozenset(deep_agent_pilot_mode_keys or ("code_review",))
        self._deep_agent_read_only_filesystem_enabled = bool(deep_agent_read_only_filesystem_enabled)
        self._deep_agent_read_only_max_file_size_mb = max(1, int(deep_agent_read_only_max_file_size_mb))
        self._deep_agent_read_only_max_output_chars = max(1000, int(deep_agent_read_only_max_output_chars))
        self._deep_agent_cognitive_planning_enabled = bool(deep_agent_cognitive_planning_enabled)
        if deep_agent_turn_timeout_seconds <= 0:
            raise ValueError("deep_agent_turn_timeout_seconds must be greater than zero")
        self._deep_agent_turn_timeout_seconds = float(deep_agent_turn_timeout_seconds)
        self._deep_agent_cognitive_subagents_enabled = bool(deep_agent_cognitive_subagents_enabled)
        self._deep_agent_max_subagent_calls_per_turn = max(1, int(deep_agent_max_subagent_calls_per_turn))
        self._deep_agent_subagent_model_call_limit = max(1, int(deep_agent_subagent_model_call_limit))
        self._deep_agent_subagent_tool_call_limit = max(1, int(deep_agent_subagent_tool_call_limit))
        self._permission_service = permission_service
        self._agent_registry = agent_registry
        self._skill_registry = skill_registry
        self._deep_agent_governed_tools_enabled = bool(deep_agent_governed_tools_enabled)
        self._tool_job_service = tool_job_service
        self._framework_approval_projection = framework_approval_projection
        self._framework_approval_continuation = framework_approval_continuation
        self._tool_governance_service = (
            ToolGovernanceService(
                tool_registry=tool_registry,
                permission_service=permission_service,
                tool_runtime_service=tool_runtime_service,
                tool_job_service=tool_job_service,
            )
            if permission_service is not None
            else None
        )
        self._error_recovery = ErrorRecoveryCascade(
            context_compaction_service=context_compaction_service,
        )
        self._agent_loop = AgentLoop(
            graph=graph,
            runtime_control=runtime_control,
            max_iterations=max_iterations,
            error_recovery=self._error_recovery,
        )

    def request_interrupt(self, session_id: str, reason: str = "") -> None:
        self._runtime_control.request_interrupt(session_id, reason)

    def clear_interrupt(self, session_id: str) -> None:
        self._runtime_control.clear_interrupt(session_id)

    def is_interrupt_requested(self, session_id: str) -> bool:
        return self._runtime_control.is_interrupt_requested(session_id)

    async def execute_turn(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        with self._runtime_control.execution_scope(session.id), graph_event_scope(event_queue):
            return await self._execute_turn(
                session, request, on_model_chunk=on_model_chunk, event_queue=event_queue,
            )

    async def _execute_turn(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        if self._should_use_deep_agent(request):
            pilot_stage = self._deep_agent_pilot_stage()
            state = self._build_initial_state(session, request)
            self._prepare_deep_agent_governed_tools(session, request, state)
            append_graph_event(
                state,
                "runtime.turn_started",
                "runtime",
                "Deep Agents pilot execution started for the current turn.",
                harness="deepagents",
                pilot_stage=pilot_stage,
                mode_key=request.mode_key,
            )
            return await self._execute_observed(
                session,
                request,
                state,
                lambda: self._execute_deep_agent_turn(
                    session,
                    request,
                    state=state,
                    pilot_stage=pilot_stage,
                    on_model_chunk=on_model_chunk,
                ),
            )
        compaction_info = None
        if self._context_compaction_service is not None:
            compaction_info = await self._context_compaction_service.maybe_compact(session)
        initial_state = self._build_initial_state(session, request)
        await self._attach_session_resources(initial_state)
        if compaction_info:
            append_graph_event(
                initial_state,
                "runtime.context_compacted",
                "runtime",
                "Older conversation history was compacted into a structured summary.",
                covers_until_index=compaction_info.get("covers_until_index", 0),
                compacted_message_count=compaction_info.get("compacted_message_count", 0),
                summary_length=compaction_info.get("summary_length", 0),
            )
        append_graph_event(
            initial_state,
            "runtime.turn_started",
            "runtime",
            "Runtime execution started for the current turn.",
            session_mode=session.session_mode.value,
            runtime_mode=session.runtime_mode.value,
            requested_agent=request.agent_key or session.selected_agent or "auto",
            requested_model=request.model_key or session.preferred_model or "auto",
            requested_skill_count=len(request.skill_keys),
            context_keys=",".join(sorted(request.context.keys())) or "none",
            user_message_preview=truncate_text(request.user_message, 160),
        )
        if (
            self._security_tool_bootstrap_requested(request)
            or self._should_use_dedicated_security_runtime(request)
        ):
            return await self._execute_observed(
                session,
                request,
                initial_state,
                lambda: self._execute_security_mode_turn(session, request, initial_state),
            )
        initial_state["model_call_count"] = 0
        return await self._execute_observed(
            session,
            request,
            initial_state,
            lambda: self._execute_state(session, initial_state, on_model_chunk=on_model_chunk),
        )

    def _should_use_deep_agent(self, request: ExecutionRequest) -> bool:
        binding = request.runtime_binding
        if binding is not None:
            if binding.session_id != request.session_id or binding.turn_id != request.turn_id:
                raise ValueError("Runtime binding does not match the execution request")
            if binding.runtime_kind != "deepagents":
                raise ValueError("Bound runtime has no implemented v2 execution path")
            if not self._deep_agent_enabled or self._deep_agent_runtime_adapter is None:
                raise RuntimeError("The bound Deep Agents runtime is unavailable in this Worker")
            return True
        return bool(
            self._deep_agent_enabled
            and self._deep_agent_runtime_adapter is not None
            and str(request.mode_key or "default") in self._deep_agent_pilot_mode_keys
        )

    def _deep_agent_pilot_stage(self) -> str:
        if self._deep_agent_governed_tools_enabled:
            return "DA-E4"
        if (
            self._deep_agent_cognitive_planning_enabled
            or self._deep_agent_cognitive_subagents_enabled
        ):
            return "DA-E3"
        if self._deep_agent_read_only_filesystem_enabled:
            return "DA-E2"
        return "DA-E1"

    async def _execute_deep_agent_turn(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        *,
        state: dict[str, Any],
        pilot_stage: str,
        on_model_chunk: Callable[[str], Awaitable[None]] | None,
        continuation_plan: FrameworkContinuationPlan | None = None,
    ) -> RuntimeTurnResult:
        """Run the staged Deep Agents pilot while preserving result contracts."""
        if request.runtime_binding is not None:
            owner = current_framework_owner()
            if owner is None or owner.binding != request.runtime_binding:
                raise RuntimeError("Bound runtime requires its captured execution owner")
        def on_subagent_event(item: dict[str, Any]) -> None:
            event_type = str(item.get("type") or "")
            append_graph_event(
                state,
                (
                    "graph.subagent_started"
                    if event_type == "started"
                    else "graph.subagent_completed"
                ),
                "deepagents.subagent",
                (
                    "Deep Agents cognitive subagent started."
                    if event_type == "started"
                    else "Deep Agents cognitive subagent completed."
                ),
                harness="deepagents",
                pilot_stage=pilot_stage,
                subagent_name=str(item.get("name") or ""),
                subagent_run_id=str(item.get("run_id") or ""),
                parent_run_id=str(item.get("parent_run_id") or ""),
                dispatch_owner="deepagents",
                persistent_worker=False,
            )
        approvals: list[dict[str, Any]] = []
        tool_records: list[dict[str, Any]] = list(state.get("tool_results") or [])
        business_tools, interrupt_on = await self._build_deep_agent_business_tools(
            state=state,
            tool_records=tool_records,
        )
        execution_task = asyncio.create_task(
            self._deep_agent_runtime_adapter.execute(
                DeepAgentRuntimeRequest(
                    session_id=session.id,
                    turn_id=request.turn_id,
                    trace_id=state["trace_id"],
                    model_key=state["selected_model_key"],
                    system_prompt=(
                        "You are the Enterprise AI QA Agent code review pilot. "
                        "Return evidence-grounded review guidance. Use only the "
                        "read-only project and selected skill capabilities exposed "
                        "by the current pilot stage. Business tools visible to you are "
                        "governed by the application's permission and approval controls."
                    ),
                    messages=list(state["runtime_messages"]),
                    framework_messages=list(state.get("framework_messages") or []),
                    checkpoint_namespace=str(
                        state.get("context_bundle", {}).get("checkpoint_namespace") or ""
                    ) or None,
                    context={**dict(request.context), "skill_keys": list(request.skill_keys)},
                    read_only_filesystem_enabled=self._deep_agent_read_only_filesystem_enabled,
                    read_only_max_file_size_mb=self._deep_agent_read_only_max_file_size_mb,
                    read_only_max_output_chars=self._deep_agent_read_only_max_output_chars,
                    cognitive_planning_enabled=self._deep_agent_cognitive_planning_enabled,
                    cognitive_subagents_enabled=self._deep_agent_cognitive_subagents_enabled,
                    max_subagent_calls_per_turn=self._deep_agent_max_subagent_calls_per_turn,
                    subagent_model_call_limit=self._deep_agent_subagent_model_call_limit,
                    subagent_tool_call_limit=self._deep_agent_subagent_tool_call_limit,
                    on_subagent_event=(
                        on_subagent_event
                        if self._deep_agent_cognitive_subagents_enabled
                        else None
                    ),
                    business_tools=business_tools,
                    interrupt_on=interrupt_on,
                    resume=(continuation_plan.resume if continuation_plan is not None
                        else state.pop("_deep_agent_resume", None)),
                    continue_from_checkpoint=(continuation_plan.continue_from_checkpoint
                        if continuation_plan is not None else False),
                )
            ),
            name=f"deep-agent-turn:{session.id}:{request.turn_id}",
        )
        self._runtime_control.register_interruptible_task(session.id, execution_task)
        try:
            async with asyncio.timeout(self._deep_agent_turn_timeout_seconds):
                result = await execution_task
        except TimeoutError:
            return self._build_deep_agent_interrupted_result(
                session,
                state,
                pilot_stage=pilot_stage,
                reason=(
                    "Deep Agents cognitive turn exceeded the configured "
                    f"{self._deep_agent_turn_timeout_seconds:g} second limit."
                ),
                cause="timeout",
            )
        except asyncio.CancelledError:
            if not self._runtime_control.is_interrupt_requested(session.id):
                raise
            return self._build_deep_agent_interrupted_result(
                session,
                state,
                pilot_stage=pilot_stage,
                reason=(
                    self._runtime_control.get_interrupt_reason(session.id)
                    or "Deep Agents cognitive turn was interrupted."
                ),
                cause="interrupt",
            )
        finally:
            self._runtime_control.unregister_interruptible_task(session.id, execution_task)
        if continuation_plan is not None:
            settled = await self._framework_approval_continuation.settle(
                current_framework_owner(), continuation_plan.approval.id, continuation_plan.receipt.decision_id)
            record = await self._tool_runtime_service.record_from_job(settled.job)
            record.approval_id = settled.approval.id
            self._merge_tool_records(tool_records, [record.model_dump(mode="python")])
        state["framework_messages"] = list(result.framework_messages or [])
        if result.interrupts:
            approvals = await self._record_deep_agent_interrupts(state, result)
            self._merge_tool_records(tool_records, state.pop("_deep_agent_waiting_records", []))
        elif request.runtime_binding is not None:
            state["context_bundle"]["deep_agent_approval_batch"] = []
        state["plan_steps"] = [item["content"] for item in result.todos]
        state["tool_results"] = tool_records
        state["tool_messages"] = [self._build_tool_message(ToolExecutionRecord.model_validate(item)) for item in tool_records]
        state["pending_approvals"] = approvals
        if approvals:
            state["context_bundle"] = {
                **dict(state.get("context_bundle") or {}),
                "deep_agent_harness": "deepagents",
            }
            state["control_state"] = "waiting_approval"
            state["termination_reason"] = "waiting_approval"
            state["final_response"] = ""
            state["runtime_messages"] = result.messages
            append_graph_event(
                state,
                "graph.waiting_for_approval",
                "deepagents.tool_governance",
                "Deep Agents business tools are paused for existing application approval.",
                approval_count=len(approvals),
                tool_keys=",".join(str(item.get("tool_key") or "") for item in approvals),
                harness="deepagents",
            )
            state["pending_turn"] = self._build_pending_turn(state, stage="waiting_approval")
            snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
            return RuntimeTurnResult(
                output_text="",
                events=self._events_from_log(session.id, state["event_log"]),
                snapshot=snapshot,
                approvals=approvals,
                state=state,
                tool_messages=self._to_chat_messages(state["turn_id"], tool_records,
                    binding_id=request.runtime_binding.binding_id if request.runtime_binding else None),
                pending_turn=state["pending_turn"],
            )
        state["cognitive_subagent_events"] = list(result.subagent_events)
        if result.todos:
            append_graph_event(
                state,
                "graph.plan_built",
                "deepagents.todo",
                "Deep Agents cognitive plan has been synchronized for this turn.",
                harness="deepagents",
                pilot_stage=pilot_stage,
                plan_step_count=len(result.todos),
                plan_outline=" | ".join(item["content"] for item in result.todos),
                todos=result.todos,
            )
        state["model_response_text"] = result.output_text
        state["model_response_summary"] = {
            "mode": "ok",
            "harness": "deepagents",
            "pilot_stage": pilot_stage,
            **result.metadata,
        }
        state["final_response"] = result.output_text
        state["termination_reason"] = "completed"
        state["control_state"] = "completed"
        if on_model_chunk is not None:
            await on_model_chunk(result.output_text)
        append_graph_event(
            state,
            "runtime.turn_completed",
            "runtime",
            "Deep Agents pilot execution finished for the current turn.",
            harness="deepagents",
            pilot_stage=pilot_stage,
            final_response_length=len(result.output_text),
        )
        snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text=result.output_text,
            events=self._events_from_log(session.id, state["event_log"]),
            snapshot=snapshot,
            approvals=[],
            state=state,
            tool_messages=self._to_chat_messages(state["turn_id"], tool_records,
                binding_id=request.runtime_binding.binding_id if request.runtime_binding else None),
            pending_turn={},
        )

    def _build_deep_agent_interrupted_result(
        self,
        session: SessionRecord,
        state: dict[str, Any],
        *,
        pilot_stage: str,
        reason: str,
        cause: str,
    ) -> RuntimeTurnResult:
        """Map a bounded Deep Agents stop to existing terminal runtime contracts.

        Only approval interrupts currently expose checkpoint resume. A user
        cancellation or timeout remains non-resumable until DA-E5 establishes
        lease and idempotency semantics; never send it through the legacy graph.
        """
        logger.warning(
            "deep_agent_turn_stopped",
            extra={
                "session_id": session.id,
                "turn_id": state.get("turn_id", ""),
                "pilot_stage": pilot_stage,
                "cause": cause,
                "resumable": False,
            },
        )
        state["model_response_summary"] = {
            "mode": "interrupted",
            "harness": "deepagents",
            "pilot_stage": pilot_stage,
            "cause": cause,
        }
        state["final_response"] = ""
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["interrupt_requested"] = cause == "interrupt"
        state["interrupt_reason"] = reason
        state["pending_turn"] = {}
        append_graph_event(
            state,
            "runtime.turn_timed_out" if cause == "timeout" else "runtime.interrupt_applied",
            "deepagents",
            reason,
            harness="deepagents",
            pilot_stage=pilot_stage,
            cause=cause,
            resumable=False,
        )
        snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text="",
            events=self._events_from_log(session.id, state["event_log"]),
            snapshot=snapshot,
            approvals=[],
            state=state,
            tool_messages=[],
            pending_turn={},
        )

    async def resume_framework_approval(
        self,
        session: SessionRecord,
        approval_id: str,
        decision_id: str,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        """Continue the owned native decision without rebuilding checkpoint history."""
        with self._runtime_control.execution_scope(session.id), graph_event_scope(event_queue):
            return await self._resume_framework_approval(
                session, approval_id, decision_id, on_model_chunk=on_model_chunk)

    async def _resume_framework_approval(
        self,
        session: SessionRecord,
        approval_id: str,
        decision_id: str,
        *,
        on_model_chunk: Callable[[str], Awaitable[None]] | None,
    ) -> RuntimeTurnResult:
        owner = current_framework_owner()
        pending = dict(session.metadata.get("pending_turn") or {})
        binding_data = pending.get("context_bundle", {}).get("runtime_binding")
        if (owner is None or self._framework_approval_continuation is None or not binding_data
            or owner.binding.session_id != session.id or pending.get("turn_id") != owner.binding.turn_id):
            raise FrameworkApprovalRecoveryRequired("Framework continuation has no matching pending Turn owner")
        state = self._state_from_pending_turn(session, pending)
        try:
            pending_binding = RuntimeBinding.model_validate(binding_data)
            restored_binding = RuntimeBinding.model_validate(state["context_bundle"].get("runtime_binding"))
        except ValidationError as exc:
            raise FrameworkApprovalRecoveryRequired("Framework continuation has a corrupt runtime binding") from exc
        if (pending_binding != owner.binding or state["turn_id"] != owner.binding.turn_id
            or restored_binding != owner.binding
            or state["context_bundle"].get("checkpoint_namespace") != owner.binding.checkpoint_namespace):
            raise FrameworkApprovalRecoveryRequired("Restored runtime state differs from its captured binding")
        request = ExecutionRequest(session_id=session.id, turn_id=state["turn_id"], trace_id=state["trace_id"],
            user_message=state["user_message"], normalized_input=state["normalized_input"],
            mode_key=state["mode_key"], agent_key=state["selected_agent_key"], model_key=state["selected_model_key"],
            skill_keys=state["requested_skill_keys"], context=dict(state["context_bundle"]), runtime_binding=owner.binding)
        if not self._should_use_deep_agent(request):
            raise FrameworkApprovalRecoveryRequired("Framework approval runtime is unavailable")
        plan = await self._framework_approval_continuation.prepare(owner, approval_id, decision_id)
        persisted_events = len(state["event_log"])
        state.update(pending_turn={}, termination_reason="", control_state="resuming")
        append_graph_event(state, "turn.resumed", "deepagents.hitl", "Owned native approval continuation started.",
            approval_id=approval_id, decision_id=decision_id, continue_from_checkpoint=plan.continue_from_checkpoint)
        result = await self._execute_observed(session, request, state,
            lambda: self._execute_deep_agent_turn(session, request, state=state,
                pilot_stage=self._deep_agent_pilot_stage(), on_model_chunk=on_model_chunk, continuation_plan=plan))
        result.events = result.events[persisted_events:]
        return result

    async def resume_after_approval(
        self,
        session: SessionRecord,
        approval: dict,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult | None:
        with self._runtime_control.execution_scope(session.id), graph_event_scope(event_queue):
            return await self._resume_after_approval(
                session, approval, on_model_chunk=on_model_chunk, event_queue=event_queue,
            )

    async def _resume_after_approval(
        self,
        session: SessionRecord,
        approval: dict,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult | None:
        pending_turn = dict(session.metadata.get("pending_turn") or {})
        if not pending_turn:
            return None

        if (pending_turn.get("context_bundle", {}).get("runtime_binding") is not None
            or pending_turn.get("graph_state", {}).get("context_bundle", {}).get("runtime_binding") is not None):
            raise FrameworkApprovalRecoveryRequired("Bound approvals require the owned framework continuation entry")

        if pending_turn.get("context_bundle", {}).get("deep_agent_harness") == "deepagents":
            return await self._resume_deep_agent_after_approval(
                session, pending_turn, approval,
                on_model_chunk=on_model_chunk, event_queue=event_queue,
            )

        state = self._state_from_pending_turn(session, pending_turn)
        await self._attach_session_resources(state)
        turn_id = str(state["turn_id"])
        tool_call = ModelToolCall(
            id=str(approval["metadata"].get("call_id", approval["id"])),
            name=approval["tool_key"],
            arguments=approval["metadata"].get("arguments", {}),
        )
        tool_results = list(state["tool_results"])
        tool_messages = list(state["tool_messages"])
        pending_ids = list(pending_turn.get("pending_approval_ids", []))
        context = ToolExecutionContext(
            session_id=session.id,
            turn_id=turn_id,
            trace_id=str(state["trace_id"]),
            user_message=str(state["user_message"]),
            normalized_input=str(state["normalized_input"]),
            context_bundle=dict(state["context_bundle"]),
            selected_agent_key=str(state["selected_agent_key"]),
            selected_model_key=str(state["selected_model_key"]),
            tool_job_id=str(approval.get("metadata", {}).get("tool_job_id") or ""),
        )

        tool = self._tool_registry.get(approval["tool_key"])
        context = context.bind_call(tool_call)
        stored_scope_hash = str(approval.get("metadata", {}).get("approval_scope_hash") or "")
        approval_mode_key = str(
            approval.get("metadata", {}).get("approval_mode_key")
            or state.get("mode_key")
            or "default"
        )
        scope_matches = bool(stored_scope_hash) and ApprovalScopeService().matches(
            stored_scope_hash,
            mode_key=approval_mode_key,
            tool_key=tool.key,
            arguments=tool_call.arguments,
            context=state.get("context_bundle") or {},
        )
        execution_safety = ExecutionSafetyPolicy().evaluate_tool_call(
            tool=tool,
            arguments=tool_call.arguments,
            active_mode_key=str(state.get("mode_key") or "default"),
            context=state.get("context_bundle") or {},
        )

        if approval["status"] == "approved" and scope_matches and execution_safety.behavior != "deny":
            context.server_approval_granted = True
            if tool.key == "security-tool-bootstrap":
                context.approval_scope_hash = stored_scope_hash
            append_graph_event(
                state,
                "tool.execution_started",
                "approval_resume",
                f"Approved tool '{approval['tool_key']}' is now executing.",
                tool_key=approval["tool_key"],
                approval_id=approval["id"],
            )
            execution_record = None
            if context.context_bundle.get("case_execution_managed"):
                job = (await self._tool_job_service.get_job(context.tool_job_id)
                    if self._tool_job_service is not None and context.tool_job_id else None)
                if (job is None or job.session_id != session.id or job.turn_id != turn_id
                    or job.call_id != tool_call.id or job.tool_key != tool.key
                    or job.input_payload != tool_call.arguments
                    or (context.case_action_binding is not None and job.metadata.get("case_action_binding")
                        != context.case_action_binding.model_dump(mode="json"))
                    or any(job.metadata.get(key) != context.context_bundle.get(key)
                        for key in ("project_id", "run_id", "run_item_id", "attempt_id"))):
                    raise ValueError("固定用例审批工具任务与实际执行身份或参数不一致")
                if job.status.value in {"completed", "partial", "failed", "denied", "cancelled"}:
                    execution_record = await self._tool_runtime_service.record_from_job(job)
                    logger.info("case_approval_tool_result_replayed", extra={"session_id": session.id,
                        "turn_id": turn_id, "approval_id": approval["id"], "tool_job_id": job.id})
                elif job.status.value != "waiting_approval":
                    raise RuntimeError("固定用例审批工具结果未知，需要核对原任务，不能重复操作")
            if execution_record is None:
                execution_record = await self._tool_runtime_service.execute(
                    tool=tool, call=tool_call, context=context,
                )
            append_graph_event(
                state,
                {"completed": "tool.execution_completed", "waiting_children": "tool.waiting_children"}.get(
                    execution_record.status, "tool.execution_failed"),
                "approval_resume",
                execution_record.summary,
                tool_key=execution_record.tool_key,
                approval_id=approval["id"],
                status=execution_record.status,
            )
        else:
            if approval["status"] == "approved" and not scope_matches:
                denial_summary = f"Approval scope changed before tool '{approval['tool_name']}' could execute."
                denial_output = {"error": "approval_scope_mismatch"}
            elif approval["status"] == "approved" and execution_safety.behavior == "deny":
                denial_summary = execution_safety.reason
                denial_output = {
                    "error": "execution_safety_denied",
                    "reason_code": execution_safety.reason_code,
                }
            else:
                denial_summary = f"Approval denied for tool '{approval['tool_name']}'."
                denial_output = {"decision_note": approval.get("decision_note")}
            execution_record = ToolExecutionRecord(
                call_id=tool_call.id,
                tool_key=approval["tool_key"],
                tool_name=approval["tool_name"],
                status="denied",
                summary=denial_summary,
                input=tool_call.arguments,
                output=denial_output,
                approval_id=approval["id"],
            )
            approval_job_id = str(
                approval.get("metadata", {}).get("tool_job_id") or ""
            )
            if approval_job_id and self._tool_job_service is not None:
                await self._tool_job_service.mark_denied(
                    approval_job_id,
                    summary=denial_summary,
                    output_payload=denial_output,
                )
            append_graph_event(
                state,
                "tool.execution_denied",
                "approval_resume",
                execution_record.summary,
                tool_key=execution_record.tool_key,
                approval_id=approval["id"],
            )

        if execution_record.status == "waiting_children":
            state["context_bundle"] = context.context_bundle
            state["runtime_messages"] = list(pending_turn.get("conversation_messages", []))
            state["pending_approvals"] = [item for item in state["pending_approvals"]
                if str(item.get("id") or "") != approval["id"]]
            state["tool_results"] = [item for item in tool_results if item.get("approval_id") != approval["id"]]
            state["tool_results"].append(execution_record.model_dump(mode="python"))
            return self._park_tool_result(session, state, execution_record)

        tool_results = [item for item in tool_results if item.get("approval_id") != approval["id"]]
        tool_results.append(execution_record.model_dump(mode="python"))
        approved_tool_message = self._build_tool_message(execution_record)
        tool_messages.append(approved_tool_message)
        pending_ids = [item for item in pending_ids if item != approval["id"]]

        remaining_approvals = [
            item
            for item in state["pending_approvals"]
            if str(item.get("id") or "") in pending_ids
        ]
        state["tool_results"] = tool_results
        state["tool_messages"] = tool_messages
        state["pending_approvals"] = remaining_approvals
        state["pending_turn"] = {}
        state["control_state"] = "resuming"

        waiting = [item for item in tool_results if item.get("status") == "waiting_children"]
        if waiting:
            state["context_bundle"] = context.context_bundle
            state["pending_turn"] = pending_turn
            state["runtime_messages"] = list(pending_turn.get("conversation_messages", []))
            return self._park_tool_result(session, state, ToolExecutionRecord.model_validate(waiting[0]))

        if pending_ids:
            state["control_state"] = "waiting_approval"
            state["termination_reason"] = "waiting_approval"
            # Rebuild both the outer pending turn and its nested graph checkpoint
            # from the authoritative state. Updating only the outer fields leaves
            # a stale graph_state that wins during the next approval recovery.
            state["pending_turn"] = self._build_pending_turn(state, stage="waiting_approval")
            logger.info(
                "approval_checkpoint_rebuilt",
                extra={
                    "session_id": session.id,
                    "turn_id": turn_id,
                    "approval_id": approval["id"],
                    "remaining_approval_count": len(pending_ids),
                    "tool_result_count": len(tool_results),
                },
            )
            snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
            events = self._events_from_log(session.id, state["event_log"])
            return RuntimeTurnResult(
                output_text="",
                events=events,
                snapshot=snapshot,
                approvals=[],
                state=state,
                tool_messages=self._to_chat_messages(turn_id, [execution_record.model_dump(mode="python")]),
                pending_turn=state["pending_turn"],
            )

        if tool.key == "security-tool-bootstrap":
            output = execution_record.output if isinstance(execution_record.output, dict) else {}
            manifest = output.get("tool_bootstrap") if isinstance(output.get("tool_bootstrap"), dict) else {}
            bootstrap_status = str(manifest.get("status") or execution_record.status).strip()
            final_response = str(
                output.get("summary")
                or execution_record.summary
                or f"P4 tool bootstrap {bootstrap_status}."
            ).strip()
            campaign_state = None
            campaign_id = str(
                manifest.get("campaign_id")
                or tool_call.arguments.get("campaign_id")
                or ""
            ).strip()
            target_allowlist = [
                str(item).strip()
                for item in (tool_call.arguments.get("target_allowlist") or [])
                if str(item).strip()
            ]
            projected_report_markdown = str(output.get("report_markdown") or "").strip()
            if projected_report_markdown:
                state["context_bundle"] = context.context_bundle
                final_response = projected_report_markdown
            elif campaign_id and manifest:
                try:
                    campaign_state = self._tool_runtime_service.record_security_tool_bootstrap_campaign(
                        campaign_id=campaign_id,
                        target_allowlist=target_allowlist,
                        manifest=manifest,
                        tool_summary=final_response,
                        tool_status=execution_record.status,
                        context=context,
                    )
                except Exception as exc:
                    logger.exception(
                        "security_tool_bootstrap_campaign_projection_failed %s",
                        json.dumps(
                            {
                                "session_id": session.id,
                                "campaign_id": campaign_id,
                                "bootstrap_id": str(manifest.get("bootstrap_id") or ""),
                                "error": str(exc),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                    final_response = (
                        f"{final_response}\n\n"
                        "P4 执行结果已保留，但 Campaign 审计投影失败；"
                        "本次结果不能作为完整安全 Campaign 结算。"
                    )
                    state["termination_reason"] = "failed"
                else:
                    state["context_bundle"] = context.context_bundle
                    if campaign_state.report_markdown:
                        final_response = campaign_state.report_markdown
            state["runtime_messages"] = [approved_tool_message]
            state["final_response"] = final_response
            state["control_state"] = "completed"
            state["continue_loop"] = False
            projected_settlement = output.get("settlement")
            projected_settlement_status = str(
                projected_settlement.get("status")
                if isinstance(projected_settlement, dict)
                else ""
            ).strip().lower()
            campaign_settlement_status = (
                campaign_state.campaign.settlement.status
                if campaign_state is not None
                and campaign_state.campaign is not None
                and campaign_state.campaign.settlement is not None
                else projected_settlement_status
            )
            # The previous state necessarily ended at waiting_approval. Once
            # P4 has resumed, settlement is the authoritative terminal state:
            # a partial report is completed work with an environment limit,
            # not an approval still awaiting action.
            state["termination_reason"] = (
                "failed"
                if campaign_settlement_status == "failed"
                or (not campaign_settlement_status and execution_record.status == "failed")
                else ""
            )
            append_graph_event(
                state,
                "security.tool_bootstrap.completed"
                if execution_record.status == "completed"
                else "security.tool_bootstrap.failed",
                "approval_resume",
                final_response,
                tool_key=tool.key,
                approval_id=approval["id"],
                bootstrap_id=str(manifest.get("bootstrap_id") or ""),
                bootstrap_status=bootstrap_status,
                manifest_path=str(manifest.get("manifest_path") or ""),
                cleanup_complete=bool(manifest.get("cleanup_complete")),
                campaign_id=campaign_id,
                campaign_projection_status=(
                    campaign_state.campaign.settlement.status
                    if campaign_state is not None
                    and campaign_state.campaign is not None
                    and campaign_state.campaign.settlement is not None
                    else "not_projected"
                ),
            )
            snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
            return RuntimeTurnResult(
                output_text=final_response,
                events=self._events_from_log(session.id, state["event_log"]),
                snapshot=snapshot,
                approvals=[],
                state=state,
                tool_messages=self._to_chat_messages(
                    turn_id,
                    [execution_record.model_dump(mode="python")],
                ),
                pending_turn={},
            )

        conversation_messages = list(pending_turn.get("conversation_messages", []))
        latest_tool_call_ids = self._latest_assistant_tool_call_ids(conversation_messages)
        if latest_tool_call_ids:
            latest_by_call_id: dict[str, dict[str, Any]] = {}
            for message in tool_messages:
                call_id = str(message.get("tool_call_id") or "")
                if call_id in latest_tool_call_ids:
                    latest_by_call_id[call_id] = message
            resume_tool_messages = [
                latest_by_call_id[key]
                for key in latest_tool_call_ids
                if key in latest_by_call_id
            ]
        else:
            resume_tool_messages = [approved_tool_message]
        state["runtime_messages"] = [
            *conversation_messages,
            *resume_tool_messages,
        ]
        append_graph_event(
            state,
            "turn.resumed",
            "runtime",
            "Turn resumed after approval resolution.",
            resume_reason="approval_resolved",
            approval_id=approval["id"],
        )
        request = ExecutionRequest(
            session_id=session.id,
            turn_id=str(state["turn_id"]),
            trace_id=str(state["trace_id"]),
            user_message=str(state.get("user_message") or ""),
            normalized_input=str(state.get("normalized_input") or ""),
            mode_key=str(state.get("mode_key") or session.mode_key),
            agent_key=str(state.get("selected_agent_key") or ""),
            model_key=str(state.get("selected_model_key") or ""),
            context=dict(state.get("context_bundle") or {}),
        )
        return await self._execute_observed(
            session,
            request,
            state,
            lambda: self._execute_state(session, state, on_model_chunk=on_model_chunk),
        )

    async def _execute_observed(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        state: dict[str, Any],
        executor: Callable[[], Awaitable[RuntimeTurnResult]],
    ) -> RuntimeTurnResult:
        trace_context = TraceContext.from_graph_state(
            state,
            project_id=str(getattr(session, "project_id", "") or ""),
        )
        trace_inputs = {
            "session_id": trace_context.session_id,
            "turn_id": trace_context.turn_id,
            "mode_key": trace_context.mode_key,
        }
        trace_manager = (
            self._observability_service.trace_turn(trace_context, inputs=trace_inputs)
            if self._observability_service is not None
            else nullcontext()
        )
        with trace_manager as trace_scope:
            if trace_scope is not None:
                trace_reference = trace_scope.reference()
                if trace_reference:
                    context_bundle = dict(state.get("context_bundle") or {})
                    context_bundle["langsmith_trace"] = trace_reference
                    state["context_bundle"] = context_bundle
                    append_graph_event(
                        state,
                        "observability.trace_linked",
                        "observability",
                        "Local execution state linked to the external LangSmith trace.",
                        run_id=trace_reference.get("run_id", ""),
                        external_trace_id=trace_reference.get("trace_id", ""),
                    )
            result = await executor()
            if trace_scope is not None:
                trace_scope.set_outcome(
                    termination_reason=str(result.state.get("termination_reason", "")),
                    control_state=str(result.state.get("control_state", "")),
                )
                trace_scope.set_outputs(
                    {
                        "termination_reason": result.state.get("termination_reason", ""),
                        "control_state": result.state.get("control_state", ""),
                        "tool_result_count": len(result.state.get("tool_results") or []),
                        "pending_approval_count": len(result.state.get("pending_approvals") or []),
                        "response_preview": truncate_text(result.output_text, 240),
                    }
                )
        return result

    async def resume_turn(
        self,
        session: SessionRecord,
        snapshot: SessionSnapshot,
        resume_reason: str,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        with self._runtime_control.execution_scope(session.id), graph_event_scope(event_queue):
            return await self._resume_turn(
                session, snapshot, resume_reason, on_model_chunk=on_model_chunk, event_queue=event_queue,
            )

    async def _resume_turn(
        self,
        session: SessionRecord,
        snapshot: SessionSnapshot,
        resume_reason: str,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        state = self._state_from_snapshot(session, snapshot)
        await self._attach_session_resources(state)
        if state.get("pending_turn", {}).get("waiting_tool"):
            return await self._resume_waiting_tool(session, state, on_model_chunk=on_model_chunk)
        state["control_state"] = "resuming"
        state["interrupt_requested"] = False
        state["interrupt_reason"] = ""
        append_graph_event(
            state,
            "turn.resumed",
            "runtime",
            "Turn resumed from the latest snapshot.",
            resume_reason=resume_reason or "manual_resume",
            snapshot_id=snapshot.id,
            snapshot_stage=snapshot.stage,
        )
        return await self._execute_state(session, state, on_model_chunk=on_model_chunk)

    def _park_tool_result(self, session, state, record):
        park_tool_wait(state, record)
        return RuntimeTurnResult(output_text="", events=self._events_from_log(session.id, state["event_log"]),
            snapshot=self._build_snapshot(session, state, session.snapshot_count + 1), approvals=[], state=state,
            tool_messages=[], pending_turn=state["pending_turn"])

    async def _resume_waiting_tool(self, session, state, *, on_model_chunk=None):
        waits = waiting_tool_checkpoints(state["pending_turn"])
        jobs = []
        terminal = {"completed", "partial", "failed", "denied", "cancelled"}
        for wait in waits:
            job = await self._tool_job_service.get_job(wait["job_id"]) if self._tool_job_service is not None else None
            if (job is None or job.session_id != session.id or job.turn_id != state["turn_id"]
                or job.call_id != wait["call_id"] or job.tool_key != wait["tool_key"]
                or job.input_payload != wait["input"]):
                raise ValueError("Original waiting tool identity or frozen input changed")
            if job.status.value not in terminal | {"waiting_children"}:
                raise RuntimeError("Original waiting tool outcome is unknown; reconcile before resuming")
            jobs.append(job)
        for job in jobs:
            call = ModelToolCall(id=job.call_id, name=job.tool_key, arguments=job.input_payload)
            tool = self._tool_registry.get(job.tool_key)
            trusted = job.metadata.get("child_wait") or {}
            context = ToolExecutionContext(session_id=session.id, turn_id=state["turn_id"], trace_id=state["trace_id"],
                user_message=state["user_message"], normalized_input=state["normalized_input"],
                context_bundle=dict(state["context_bundle"]), selected_agent_key=state["selected_agent_key"],
                selected_model_key=state["selected_model_key"], tool_job_id=job.id,
                server_approval_granted=trusted.get("server_approval_granted") is True,
                approval_scope_hash=str(trusted.get("approval_scope_hash") or "")).bind_call(call)
            if job.status.value in terminal:
                record = await self._tool_runtime_service.record_from_job(job)
            else:
                safety = ExecutionSafetyPolicy().evaluate_tool_call(tool=tool, arguments=call.arguments,
                    active_mode_key=state["mode_key"], context=state["context_bundle"])
                if safety.behavior == "deny":
                    raise ValueError("Original waiting tool is no longer permitted: " + safety.reason_code)
                record = await self._tool_runtime_service.execute(tool=tool, call=call, context=context)
            state["context_bundle"] = context.context_bundle
            state["tool_results"] = [record.model_dump(mode="python") if item.get("call_id") == call.id else item
                for item in state["tool_results"]]
            if record.status == "waiting_children":
                return self._park_tool_result(session, state, record)
            message = self._build_tool_message(record)
            state["tool_messages"] = [item for item in state["tool_messages"] if item.get("tool_call_id") != call.id] + [message]
            append_graph_event(state, "tool.child_wait_resumed", "runtime",
                "Original tool settled; retaining its original batch.", tool_job_id=job.id, status=record.status)
        call_ids = self._latest_assistant_tool_call_ids(state["runtime_messages"])
        if state["pending_approvals"]:
            state["runtime_messages"] = [item for item in state["runtime_messages"]
                if not (item.get("role") == "tool" and item.get("tool_call_id") in call_ids)]
            state["control_state"] = state["termination_reason"] = "waiting_approval"
            state["pending_turn"] = self._build_pending_turn(state, stage="waiting_approval")
            return RuntimeTurnResult(output_text="", events=self._events_from_log(session.id, state["event_log"]),
                snapshot=self._build_snapshot(session, state, session.snapshot_count + 1), approvals=[], state=state,
                tool_messages=[], pending_turn=state["pending_turn"])
        messages = {item["tool_call_id"]: item for item in state["tool_messages"]}
        if not call_ids:  # Historical snapshots without a batch assistant message.
            call_ids = [job.call_id for job in jobs]
        state["runtime_messages"] = [item for item in state["runtime_messages"]
            if not (item.get("role") == "tool" and item.get("tool_call_id") in call_ids)] + [
                messages[identity] for identity in call_ids if identity in messages]
        state["pending_turn"] = {}
        state["termination_reason"] = ""
        state["control_state"] = "resuming"
        state["interrupt_requested"] = False
        state["interrupt_reason"] = ""
        return await self._execute_state(session, state, on_model_chunk=on_model_chunk)

    async def _execute_state(
        self,
        session: SessionRecord,
        state: dict[str, Any],
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> RuntimeTurnResult:
        async with self._model_runtime_service.stream_handler(on_model_chunk):
            result = await self._run_until_settled(state)

        self._convert_model_interruption_to_resumable(result)
        if result["termination_reason"] == "interrupted":
            append_graph_event(
                result,
                "turn.interrupted",
                "runtime",
                "Runtime stopped at a safe boundary after an interrupt request.",
                interrupt_reason=result.get("interrupt_reason", ""),
                loop_iteration=result["loop_iteration"],
            )
        else:
            append_graph_event(
                result,
                "runtime.turn_completed",
                "runtime",
                "Runtime execution finished for the current turn.",
                final_response_preview=truncate_text(result["final_response"], 160),
                final_response_length=len(result["final_response"]),
                pending_approval_count=len(result["pending_approvals"]),
                tool_result_count=len(result["tool_results"]),
                control_state=result["control_state"],
            )

        snapshot = self._build_snapshot(session, result, session.snapshot_count + 1)
        if snapshot.stage in {"waiting_approval", "interrupted", "resumable"}:
            append_graph_event(
                result,
                "runtime.resumable_snapshot_saved",
                "runtime",
                "A resumable snapshot has been prepared for this turn.",
                snapshot_stage=snapshot.stage,
                control_state=result["control_state"],
                loop_iteration=result["loop_iteration"],
            )
            snapshot = self._build_snapshot(session, result, session.snapshot_count + 1)

        events = self._events_from_log(session.id, result["event_log"])
        return RuntimeTurnResult(
            output_text=result["final_response"],
            events=events,
            snapshot=snapshot,
            approvals=result["pending_approvals"],
            state=result,
            tool_messages=self._to_chat_messages(result["turn_id"], result["tool_results"]),
            pending_turn=result["pending_turn"],
        )

    async def _attach_session_resources(self, state: dict[str, Any]) -> None:
        if self._session_resource_service is None:
            return
        session_id = str(state.get("session_id") or "").strip()
        if not session_id:
            return
        context_bundle = dict(state.get("context_bundle") or {})
        context_bundle["session_resources"] = await self._session_resource_service.build_context(session_id)
        state["context_bundle"] = context_bundle

    def _prepare_deep_agent_governed_tools(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        state: dict[str, Any],
    ) -> None:
        """Resolve the pilot tool surface with the same Registry policy inputs."""
        if not self._deep_agent_governed_tools_enabled or self._permission_service is None:
            return
        state["context_bundle"] = {
            key: value for key, value in dict(state.get("context_bundle") or {}).items()
            if key not in {"deep_agent_approval_batch", "deep_agent_harness"}
        }
        selected_mode = dict(request.context.get("selected_mode") or {})
        mode_keys = [
            str(item).strip()
            for item in selected_mode.get("registered_tool_keys", [])
            if str(item).strip()
        ]
        requested = [
            str(item).strip()
            for item in request.context.get("requested_tool_keys", [])
            if str(item).strip()
        ]
        skill_keys = [str(item).strip() for item in request.skill_keys if str(item).strip()]
        # Deep Agents' official SkillsMiddleware owns staged skill loading;
        # the legacy graph-only ``skill`` dispatcher must not be exposed as a
        # business tool with a non-existent ToolRuntime handler.
        keys = [*mode_keys, *requested]
        if execution_replanning_enabled(state.get("context_bundle") or {}):
            keys.append(REPLANNING_TOOL_KEY)
        if self._skill_registry is not None:
            keys.extend(
                tool_key
                for skill in self._skill_registry.get_many(skill_keys)
                for tool_key in skill.tool_keys
            )
        agent = None
        if self._agent_registry is not None:
            agent = self._agent_registry.resolve_for_message(
                request.user_message,
                explicit_key=request.agent_key or None,
            )
            state["selected_agent_key"] = agent.key
            state["selected_agent_name"] = agent.name
            if self._skill_registry is not None:
                keys.extend(
                    tool_key
                    for skill in self._skill_registry.get_many(agent.supported_skills)
                    for tool_key in skill.tool_keys
                )
        descriptors = self._tool_registry.get_many(list(dict.fromkeys(keys)))
        descriptors = CapabilityResolver().eligible_tools(
            tools=descriptors,
            active_mode_key=str(state.get("mode_key") or request.mode_key or "default"),
            required_capabilities=[
                str(item).strip()
                for item in request.context.get("required_capabilities", [])
                if str(item).strip()
            ],
            allowed_capabilities=[
                item
                for item in [
                    *selected_mode.get("core_capability_keys", []),
                    *selected_mode.get("on_demand_capability_keys", []),
                ]
                if item not in set(selected_mode.get("denied_capability_keys", []))
            ],
        )
        if agent is not None:
            descriptors = ToolExposurePolicy().filter_supported(tools=descriptors, agent=agent,
                context_bundle=state.get("context_bundle") or {})
        policy = self._build_permission_policy_context(
            session_mode=session.session_mode,
            runtime_mode=session.runtime_mode,
            selected_agent_key=str(state.get("selected_agent_key") or ""),
            mode_key=str(state.get("mode_key") or request.mode_key or "default"),
            context=request.context,
        )
        evaluation = self._permission_service.evaluate(policy_context=policy, tools=descriptors)
        state["available_tool_keys"] = [item.key for item in descriptors]
        state["model_visible_tool_keys"] = list(evaluation.model_visible_tool_keys)
        state["allowed_tool_keys"] = list(evaluation.allowed_tool_keys)
        state["approval_required_tool_keys"] = list(evaluation.approval_required_tool_keys)
        state["denied_tool_keys"] = list(evaluation.denied_tool_keys)
        state["permission_decisions"] = [item.to_payload() for item in evaluation.decisions]
        state["context_bundle"] = {
            **dict(state.get("context_bundle") or {}),
            "deep_agent_governed_tool_keys": list(state["model_visible_tool_keys"]),
            "deep_agent_governed_tool_stage": "DA-E4",
        }

    async def _build_deep_agent_business_tools(
        self,
        *,
        state: dict[str, Any],
        tool_records: list[dict[str, Any]],
    ) -> tuple[list[Any], dict[str, Any]]:
        if not self._deep_agent_governed_tools_enabled or self._tool_governance_service is None:
            return [], {}
        descriptors = self._tool_registry.get_many(list(state.get("model_visible_tool_keys") or []))
        batch = state.get("context_bundle", {}).get("deep_agent_approval_batch", [])
        by_call = {item["metadata"]["call_id"]: item for item in batch}
        from src.application.deep_agents.native_interrupts import executing_tool_identity
        from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
        from src.schemas.framework_approval import framework_tool_call_id
        from src.schemas.runtime_binding import RuntimeBinding

        bound = state.get("context_bundle", {}).get("runtime_binding")
        binding = RuntimeBinding.model_validate(bound) if bound is not None else None
        owner = current_framework_owner()
        gates = frozenset()
        if binding is not None:
            if owner is None or owner.binding != binding or self._framework_approval_projection is None:
                raise FrameworkApprovalRecoveryRequired("Governed tools require their captured native execution owner")
            gates = await self._framework_approval_projection.gate_call_ids(owner)

        def when(request):
            call_id = request.tool_call["id"]
            if binding is not None:
                identity = executing_tool_identity(binding, request.runtime,
                    request.tool_call["name"], request.tool_call["args"])
                call_id = framework_tool_call_id(binding, identity)
            call = ModelToolCall(
                id=call_id, name=request.tool_call["name"],
                arguments=request.tool_call["args"],
            )
            # A resumed official interrupt must retain its original action order
            # even when policy has changed. Execution still rechecks that policy.
            existing_gate = call.id in gates if binding is not None else call.id in by_call
            return existing_gate or self._tool_governance_service.requires_approval(
                call, self._deep_agent_governance_context(state)
            )

        async def invoke(descriptor, tool_runtime, arguments: dict[str, Any]):
            call_id = tool_runtime.tool_call_id
            approved = by_call.get(call_id)
            if binding is not None:
                if current_framework_owner() != owner:
                    raise FrameworkApprovalRecoveryRequired("Governed tool execution owner changed")
                identity = executing_tool_identity(binding, tool_runtime, descriptor.key, arguments)
                call_id = framework_tool_call_id(binding, identity)
                saved = await self._framework_approval_projection.find_for_call(owner, identity)
                if call_id in gates and saved is None:
                    raise FrameworkApprovalRecoveryRequired("Native tool approval disappeared after gate restoration")
                approved = saved.model_dump(mode="python") if saved is not None else None
            append_graph_event(
                state, "tool.execution_requested", "deepagents.tool_governance",
                "Deep Agents requested a governed business tool.",
                tool_key=descriptor.key, call_id=call_id, harness="deepagents",
            )
            result = await self._tool_governance_service.execute(
                ModelToolCall(id=call_id, name=descriptor.key, arguments=arguments),
                self._deep_agent_governance_context(state),
                approval=approved,
            )
            if approved:
                result.record.approval_id = approved["id"]
                result.record.job_id = approved["metadata"].get("tool_job_id")
                if result.record.status == "denied" and self._tool_job_service is not None:
                    if binding is not None:
                        job, replayed = await self._framework_approval_projection.settle_blocked_tool(owner, identity, result.record)
                        record = await self._tool_runtime_service.record_from_job(job)
                        record.approval_id = approved["id"]
                        result = GovernedToolCallResult(record=record, replayed=replayed)
                    else:
                        await self._tool_job_service.mark_denied(
                            result.record.job_id, summary=result.record.summary,
                            output_payload=result.record.output,
                        )
            payload = result.record.model_dump(mode="python")
            tool_records[:] = [item for item in tool_records if item.get("call_id") != call_id]
            tool_records.append(payload)
            event_type = {
                "completed": "tool.execution_completed",
                "denied": "tool.execution_denied",
            }.get(result.record.status, "tool.execution_failed")
            append_graph_event(
                state, event_type, "deepagents.tool_governance", result.record.summary,
                tool_key=result.record.tool_key, call_id=call_id,
                tool_job_id=result.record.job_id, approval_id=result.record.approval_id,
                status=result.record.status, replayed=result.replayed, harness="deepagents",
            )
            return json.dumps(make_json_safe({
                "status": result.record.status, "summary": result.record.summary,
                "output": result.record.output, "instruction_authority": "none",
            }), ensure_ascii=False)

        return LangChainToolAdapter().to_governed_tools(descriptors, invoke=invoke,
            context_bundle=state.get("context_bundle") or {}), {
            item.key: {"allowed_decisions": ["approve", "reject"], "when": when}
            for item in descriptors
        }

    def _deep_agent_governance_context(self, state: dict[str, Any]) -> GovernedToolCallContext:
        execution = ToolExecutionContext(
            session_id=str(state["session_id"]),
            turn_id=str(state["turn_id"]),
            trace_id=str(state["trace_id"]),
            user_message=str(state["user_message"]),
            normalized_input=str(state["normalized_input"]),
            context_bundle=dict(state.get("context_bundle") or {}),
            selected_agent_key=str(state.get("selected_agent_key") or ""),
            selected_model_key=str(state.get("selected_model_key") or ""),
        )
        policy = self._build_permission_policy_context(
            session_mode=SessionMode(state["session_mode"]),
            runtime_mode=RuntimeMode(state["runtime_mode"]),
            selected_agent_key=str(state.get("selected_agent_key") or ""),
            mode_key=str(state.get("mode_key") or "default"),
            context=dict(state.get("context_bundle") or {}),
        )
        return GovernedToolCallContext(
            execution=execution, policy=policy,
            active_mode_key=str(state.get("mode_key") or "default"),
            available_tool_keys=frozenset(state.get("available_tool_keys") or []),
        )

    async def _record_deep_agent_interrupts(self, state: dict[str, Any], result) -> list[dict]:
        """Project official batched or per-call HITL payloads into approvals."""
        if state.get("context_bundle", {}).get("runtime_binding") is not None:
            return await self._record_framework_interrupts(state, result)
        calls = next(
            (list(message.get("tool_calls") or []) for message in reversed(result.messages)
             if message.get("role") == "assistant"), []
        )
        remaining = list(calls)
        existing_approvals = {
            str(item.get("metadata", {}).get("deep_agent_interrupt_id")): item
            for item in state.get("context_bundle", {}).get("deep_agent_approval_batch", [])
            if item.get("metadata", {}).get("deep_agent_interrupt_id")
        }
        approvals = []
        records = []
        for interruption in result.interrupts:
            value = interruption["value"]
            interrupt_mode = "per_call" if value.get("type") == "tool_approval" else "batched"
            actions = (
                [{
                    "name": value["name"],
                    "args": value["args"],
                    "tool_call_id": value.get("tool_call_id"),
                }]
                if interrupt_mode == "per_call"
                else list(value.get("action_requests") or [])
            )
            if not actions:
                raise RuntimeError("Official HITL interrupt contains no tool action.")
            existing = existing_approvals.get(str(interruption["id"]))
            if existing is not None:
                approvals.append(existing)
                continue
            for position, action in enumerate(actions):
                action_call_id = str(action.get("tool_call_id") or "")
                matched = next(
                    (call for call in remaining
                     if (action_call_id and call.get("id") == action_call_id)
                     or (call["name"] == action["name"] and call["args"] == action["args"])),
                    None,
                )
                if matched is None:
                    raise RuntimeError("Official HITL action does not match its tool-call checkpoint.")
                remaining.remove(matched)
                prepared = await self._tool_governance_service.execute(
                    ModelToolCall(
                        id=matched["id"], name=matched["name"], arguments=matched["args"],
                    ),
                    self._deep_agent_governance_context(state), prepare_approval=True,
                )
                if prepared.approval is None:
                    raise RuntimeError("HITL approval policy changed during preparation.")
                prepared.approval["metadata"].update({
                    "deep_agent_interrupt_id": interruption["id"],
                    "deep_agent_action_index": position,
                    "deep_agent_interrupt_mode": interrupt_mode,
                })
                approvals.append(prepared.approval)
                records.append(prepared.record.model_dump(mode="python"))
                append_graph_event(
                    state, "tool.execution_blocked", "deepagents.hitl",
                    prepared.record.summary, tool_key=matched["name"], call_id=matched["id"],
                    approval_id=prepared.approval["id"], tool_job_id=prepared.record.job_id,
                    status="waiting_approval", harness="deepagents",
                )
        state["context_bundle"]["deep_agent_approval_batch"] = approvals
        state["_deep_agent_waiting_records"] = records
        return approvals

    async def load_framework_approval(self, approval_id):
        owner = current_framework_owner()
        if owner is None or self._framework_approval_projection is None:
            raise RuntimeError("Bound approval requires its execution owner and projection service")
        return await self._framework_approval_projection.load(owner, approval_id)

    async def _record_framework_interrupts(self, state, result):
        from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
        from src.schemas.runtime_binding import RuntimeBinding

        binding = RuntimeBinding.model_validate(state["context_bundle"]["runtime_binding"])
        owner = current_framework_owner()
        if (owner is None or owner.binding != binding or self._framework_approval_projection is None
            or self._tool_governance_service is None):
            raise FrameworkApprovalRecoveryRequired("Native interrupt projection has no matching runtime owner")
        identities = result.native_interrupts
        reported = [item["id"] for item in result.interrupts]
        if len(reported) != len(set(reported)) or sorted(item.interrupt_id for item in identities) != sorted(reported):
            raise FrameworkApprovalRecoveryRequired("Native interrupt projection is missing unique identities")
        approvals, records = [], []
        for identity in identities:
            proposal = self._tool_governance_service.prepare_framework_approval(
                binding, identity, self._deep_agent_governance_context(state))
            projected = await self._framework_approval_projection.project(owner, proposal)
            approval, job = projected.approval, projected.job
            approvals.append(approval.model_dump(mode="python"))
            if job.status.value in {"completed", "partial", "failed", "denied", "cancelled"}:
                record = await self._tool_runtime_service.record_from_job(job)
                record.approval_id = approval.id
            elif job.status.value == "waiting_approval":
                record = ToolExecutionRecord(call_id=job.call_id, job_id=job.id, tool_key=job.tool_key,
                    tool_name=job.tool_name, status="waiting_approval", summary=approval.reason,
                    trace_id=job.trace_id, input=job.input_payload, approval_id=approval.id,
                    output={"status": "waiting_approval", "approval_id": approval.id,
                        "instruction": "Do not retry this tool call while human approval is pending."})
            else:
                raise FrameworkApprovalRecoveryRequired("Native approval ToolJob has an unresolved execution")
            records.append(record.model_dump(mode="python"))
        state["context_bundle"]["deep_agent_approval_batch"] = approvals
        state["_deep_agent_waiting_records"] = records
        return approvals

    async def _resume_deep_agent_after_approval(
        self, session: SessionRecord, pending_turn: dict, approval: dict,
        *, on_model_chunk=None, event_queue=None,
    ) -> RuntimeTurnResult | None:
        pending_ids = list(pending_turn.get("pending_approval_ids") or [])
        if approval["id"] not in pending_ids:
            return None
        state = self._state_from_pending_turn(session, pending_turn)
        persisted_events = len(state["event_log"])
        batch = list(state["context_bundle"].get("deep_agent_approval_batch") or [])
        if not batch:
            raise ValueError("This pilot approval predates official HITL checkpoints; start a new turn.")
        batch = [dict(approval) if item["id"] == approval["id"] else item for item in batch]
        state["context_bundle"]["deep_agent_approval_batch"] = batch
        pending_ids.remove(approval["id"])
        interrupt_mode = str(
            approval.get("metadata", {}).get("deep_agent_interrupt_mode")
            or next(
                (
                    item.get("metadata", {}).get("deep_agent_interrupt_mode")
                    for item in batch
                    if item.get("metadata", {}).get("deep_agent_interrupt_mode")
                ),
                "batched",
            )
        )
        if approval["status"] == "denied":
            job_id = approval["metadata"].get("tool_job_id")
            if job_id and self._tool_job_service is not None:
                await self._tool_job_service.mark_denied(
                    job_id, summary="Human rejected the Deep Agents tool call.",
                    output_payload={"approval_id": approval["id"]},
                )
            for record in state["tool_results"]:
                if record.get("approval_id") == approval["id"]:
                    record["status"] = "denied"
                    record["summary"] = "Human rejected the tool call."
                    record["output"] = {"decision_note": approval.get("decision_note")}
        state["pending_approvals"] = [item for item in batch if item["id"] in pending_ids]
        append_graph_event(
            state, "approval.decision_applied", "deepagents.hitl",
            "Application approval decision recorded for official HITL resume.",
            approval_id=approval["id"], decision=approval["status"],
            remaining_approval_count=len(pending_ids),
        )
        if pending_ids and interrupt_mode != "per_call":
            state["pending_turn"] = self._build_pending_turn(state, stage="waiting_approval")
            return RuntimeTurnResult(
                output_text="", approvals=[], state=state, tool_messages=[],
                events=self._events_from_log(session.id, state["event_log"][persisted_events:]),
                snapshot=self._build_snapshot(session, state, session.snapshot_count + 1),
                pending_turn=state["pending_turn"],
            )
        if interrupt_mode == "per_call":
            item = next(item for item in batch if item["id"] == approval["id"])
            state["pending_turn"] = {}
            state["termination_reason"] = ""
            state["control_state"] = "resuming"
            state["_deep_agent_resume"] = {
                item["metadata"]["deep_agent_interrupt_id"]: (
                    {"type": "approve"}
                    if item["status"] == "approved"
                    else {
                        "type": "reject",
                        "message": str(item.get("decision_note") or "Human denied execution."),
                    }
                )
            }
            request = ExecutionRequest(
                turn_id=state["turn_id"], session_id=session.id,
                trace_id=state["trace_id"],
                user_message=state["user_message"], normalized_input=state["normalized_input"],
                mode_key=state["mode_key"], agent_key=state["selected_agent_key"],
                model_key=state["selected_model_key"], skill_keys=state["requested_skill_keys"],
                context=dict(state["context_bundle"]),
            )
            result = await self._execute_observed(
                session, request, state,
                lambda: self._execute_deep_agent_turn(
                    session, request, state=state, pilot_stage="DA-E4", on_model_chunk=on_model_chunk,
                ),
            )
            result.events = result.events[persisted_events:]
            return result
        resume: dict[str, Any] = {}
        for item in batch:
            interrupt_id = item["metadata"]["deep_agent_interrupt_id"]
            decisions = resume.setdefault(interrupt_id, {"decisions": []})["decisions"]
            decisions.append(
                {"type": "approve"} if item["status"] == "approved" else
                {"type": "reject", "message": str(item.get("decision_note") or "Human denied execution.")}
            )
        state["_deep_agent_resume"] = resume
        state["pending_turn"] = {}
        state["termination_reason"] = ""
        state["control_state"] = "resuming"
        request = ExecutionRequest(
            turn_id=state["turn_id"], session_id=session.id,
            trace_id=state["trace_id"],
            user_message=state["user_message"], normalized_input=state["normalized_input"],
            mode_key=state["mode_key"], agent_key=state["selected_agent_key"],
            model_key=state["selected_model_key"], skill_keys=state["requested_skill_keys"],
            context=dict(state["context_bundle"]),
        )
        result = await self._execute_observed(
            session, request, state,
            lambda: self._execute_deep_agent_turn(
                session, request, state=state, pilot_stage="DA-E4", on_model_chunk=on_model_chunk,
            ),
        )
        result.events = result.events[persisted_events:]
        return result

    @staticmethod
    def _build_permission_policy_context(
        *,
        session_mode: SessionMode,
        runtime_mode: RuntimeMode,
        selected_agent_key: str,
        mode_key: str,
        context: dict[str, Any],
    ) -> PermissionPolicyContext:
        input_envelope = dict(context.get("input_envelope") or {})
        routing = dict(context.get("input_routing") or {})
        safety = dict(context.get("safety_assessment") or {})
        return PermissionPolicyContext(
            session_mode=session_mode,
            runtime_mode=runtime_mode,
            selected_agent_key=selected_agent_key,
            message_kind=MessageKind(
                input_envelope.get("message_kind", MessageKind.user_input.value)
            ),
            submit_mode=str(input_envelope.get("submit_mode") or "immediate"),
            execution_lane=str(routing.get("execution_lane") or "conversation_turn"),
            source=str(input_envelope.get("source") or "session.send_message"),
            active_mode_key=mode_key,
            workflow_mode_key=mode_key,
            safety_decision=str(safety.get("decision") or "allow"),
            safety_risk_level=str(safety.get("risk_level") or "low"),
            authorization_status=str(
                safety.get("authorization_status") or "not_required"
            ),
            environment=str(safety.get("environment") or "unknown"),
        )

    def _build_initial_state(self, session: SessionRecord, request: ExecutionRequest) -> dict[str, Any]:
        requested_model_key = request.model_key or session.preferred_model or ""
        model_config = self._effective_model_config(requested_model_key)
        selected_model_key = model_config.key if model_config is not None else (request.model_key or session.preferred_model or "")
        selected_model_name = model_config.name if model_config is not None else ""
        selected_model_provider = model_config.provider if model_config is not None else ""
        return {
            "session_id": session.id,
            "turn_id": request.turn_id,
            "trace_id": request.trace_id or str(uuid4()),
            "user_message": request.user_message,
            "normalized_input": request.normalized_input,
            "session_mode": session.session_mode.value,
            "runtime_mode": session.runtime_mode.value,
            "mode_key": request.mode_key or session.mode_key,
            "message_count": len(session.messages),
            "preferred_model": selected_model_key,
            "selected_agent_key": request.agent_key or session.selected_agent or "",
            "selected_agent_name": "",
            "selected_model_key": selected_model_key,
            "selected_model_name": selected_model_name,
            "selected_model_provider": selected_model_provider,
            "requested_skill_keys": request.skill_keys,
            "resolved_skill_keys": [],
            "skill_prompt_blocks": [],
            "memory_hits": [],
            "memory_prompt_blocks": [],
            "test_case_hits": [],
            "test_case_prompt_blocks": [],
            "observation_hits": [],
            "observation_prompt_blocks": [],
            "active_mcp_servers": [],
            "mcp_prompt_blocks": [],
            "available_tool_keys": [],
            "deferred_tool_keys": [],
            "model_visible_tool_keys": [],
            "allowed_tool_keys": [],
            "approval_required_tool_keys": [],
            "denied_tool_keys": [],
            "permission_decisions": [],
            "pending_approvals": [],
            "plan_steps": [],
            "system_prompt_sections": [],
            "runtime_message_sections": [],
            "system_prompt": "",
            "runtime_messages": self._build_conversation_messages(session),
            "framework_messages": [],
            "model_request_payload": {},
            "model_response_summary": {},
            "model_response_text": "",
            "turn_token_usage": {},
            "model_context_window": int(model_config.context_window) if model_config is not None else 128000,
            "assistant_tool_call_message": {},
            "model_tool_calls": [],
            "tool_results": [],
            "tool_messages": [],
            "worker_dispatches": [],
            "context_bundle": {
                **request.context,
                "project_id": session.project_id,
                **({
                    "runtime_binding": request.runtime_binding.model_dump(mode="json"),
                    "checkpoint_namespace": request.runtime_binding.checkpoint_namespace,
                } if request.runtime_binding is not None else {}),
            },
            "event_log": [],
            "final_response": "",
            "pending_turn": {},
            "control_state": "active_turn",
            "interrupt_requested": False,
            "interrupt_reason": "",
            "loop_iteration": 0,
            "max_iterations": self._max_iterations,
            "continue_loop": False,
            "termination_reason": "",
        }

    def _should_use_dedicated_security_runtime(self, request: ExecutionRequest) -> bool:
        return (
            str(request.mode_key or "").strip() == "security_testing"
            and bool(request.context.get("trusted_security_runtime_direct_execution"))
            and self._trusted_security_grant_matches_request(request)
        )

    @staticmethod
    def _security_tool_bootstrap_requested(request: ExecutionRequest) -> bool:
        explicit = request.context.get("security_tool_bootstrap_request")
        return (
            str(request.mode_key or "").strip() == "security_testing"
            and isinstance(explicit, dict)
            and bool(explicit.get("requested"))
        )

    def _trusted_security_grant_matches_request(self, request: ExecutionRequest) -> bool:
        grant = request.context.get("trusted_security_authorization")
        explicit_arguments = request.context.get("security_runtime_arguments")
        if isinstance(explicit_arguments, dict):
            task = explicit_arguments.get("task")
            targets = [
                str(value).strip()
                for value in (
                    task.get("target") if isinstance(task, dict) else None,
                    explicit_arguments.get("target_url"),
                    explicit_arguments.get("target"),
                    explicit_arguments.get("target_host"),
                    explicit_arguments.get("target_network"),
                )
                if str(value or "").strip()
            ]
            return bool(targets) and all(
                verified_grant_matches_target(grant, target) for target in targets
            )
        security_request = request.context.get("security_testing_request")
        target_url = str(
            (security_request.get("target_url") if isinstance(security_request, dict) else "")
            or request.context.get("target_url")
            or ""
        ).strip()
        if not target_url:
            match = re.search(r"https?://[^\s,;]+", request.user_message or "", flags=re.IGNORECASE)
            target_url = match.group(0).rstrip(".,;)") if match else ""
        return verified_grant_matches_target(grant, target_url)

    async def _execute_security_mode_turn(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
        state: dict[str, Any],
    ) -> RuntimeTurnResult:
        bootstrap_arguments, bootstrap_error = self._build_security_bootstrap_runtime_arguments(request)
        if bootstrap_arguments is not None:
            return self._build_security_bootstrap_waiting_approval(
                session=session,
                request=request,
                state=state,
                arguments=bootstrap_arguments,
            )
        if bootstrap_error:
            return self._build_security_bootstrap_denied(
                session=session,
                state=state,
                error=bootstrap_error,
            )

        tool = self._tool_registry.get("security-scan-runner")
        call = ModelToolCall(
            id=f"security_runtime_{request.turn_id}",
            name=tool.key,
            arguments=self._build_security_runtime_arguments(request),
        )
        context = ToolExecutionContext(
            session_id=session.id,
            turn_id=str(state["turn_id"]),
            trace_id=str(state["trace_id"]),
            user_message=str(state["user_message"]),
            normalized_input=str(state["normalized_input"]),
            context_bundle=dict(state["context_bundle"]),
            selected_agent_key=str(state["selected_agent_key"]),
            selected_model_key=str(state["selected_model_key"]),
        )
        append_graph_event(
            state,
            "security.mode_runtime_selected",
            "runtime",
            "Security testing mode is using the dedicated security runtime pipeline.",
            tool_key=tool.key,
            selected_model_key=str(state["selected_model_key"]),
        )
        execution_record = await self._tool_runtime_service.execute(
            tool=tool,
            call=call,
            context=context,
        )
        state["context_bundle"] = context.context_bundle
        state["tool_results"] = [execution_record.model_dump(mode="python")]
        state["tool_messages"] = [self._build_tool_message(execution_record)]
        state["selected_agent_name"] = str(state["selected_agent_key"] or "")
        output_payload = execution_record.output if isinstance(execution_record.output, dict) else {}
        security_status = str(output_payload.get("status") or "").strip().lower()
        final_response = str(
            output_payload.get("report_markdown")
            or output_payload.get("summary")
            or execution_record.summary
        ).strip()
        state["final_response"] = final_response
        state["control_state"] = "interrupted" if security_status == "interrupted" else "completed"
        state["continue_loop"] = False
        state["termination_reason"] = (
            "interrupted"
            if security_status == "interrupted"
            else "failed"
            if execution_record.status == "failed"
            else ""
        )
        append_graph_event(
            state,
            "runtime.turn_interrupted"
            if security_status == "interrupted"
            else "runtime.turn_completed"
            if execution_record.status != "failed"
            else "runtime.turn_failed",
            "runtime",
            execution_record.summary,
            tool_key=execution_record.tool_key,
            tool_status=execution_record.status,
        )
        snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text=final_response,
            events=self._events_from_log(session.id, state["event_log"]),
            snapshot=snapshot,
            approvals=[],
            state=state,
            tool_messages=self._to_chat_messages(str(state["turn_id"]), state["tool_results"]),
            pending_turn={},
        )

    def _build_security_runtime_arguments(self, request: ExecutionRequest) -> dict[str, Any]:
        explicit = request.context.get("security_runtime_arguments")
        if isinstance(explicit, dict):
            return dict(explicit)

        security_request = request.context.get("security_testing_request")
        arguments = dict(security_request) if isinstance(security_request, dict) else {}

        for key in (
            "objective",
            "target",
            "target_url",
            "target_host",
            "target_network",
            "target_type",
            "scope_preference",
            "auth_hint",
            "credentials",
            "focus_areas",
            "excluded_areas",
            "risk_tolerance",
            "report_recipients",
        ):
            value = request.context.get(key)
            if value is not None and value != "" and key not in arguments:
                arguments[key] = value
        return arguments

    def _build_security_bootstrap_runtime_arguments(
        self,
        request: ExecutionRequest,
    ) -> tuple[dict[str, Any] | None, str]:
        explicit = request.context.get("security_tool_bootstrap_request")
        if not isinstance(explicit, dict):
            return None, ""
        if not bool(explicit.get("requested")):
            return None, ""
        target = str(
            explicit.get("target")
            or explicit.get("target_url")
            or (request.context.get("security_testing_request") or {}).get("target_url")
            or request.context.get("target_url")
            or ""
        ).strip()
        trusted = request.context.get("trusted_security_authorization")
        allowed_targets = []
        if isinstance(trusted, dict):
            allowed_targets = [str(item).strip() for item in (trusted.get("targets") or []) if str(item).strip()]
        if not target or not allowed_targets or not self._target_matches_any_allowed(target, allowed_targets):
            return None, "target_out_of_scope"
        campaign_id = str(
            explicit.get("campaign_id")
            or request.context.get("campaign_id")
            or f"bootstrap-{request.turn_id}"
        ).strip()
        prepared, error = self._tool_runtime_service.prepare_security_tool_bootstrap_arguments(
            campaign_id=campaign_id,
            target_allowlist=allowed_targets,
            profile_key=str(explicit.get("profile_key") or "").strip(),
            tool_name=str(explicit.get("tool_name") or "").strip(),
            requested_version=str(explicit.get("requested_version") or "").strip(),
            timeout_seconds=explicit.get("timeout_seconds"),
        )
        return prepared, error

    def _build_security_bootstrap_waiting_approval(
        self,
        *,
        session: SessionRecord,
        request: ExecutionRequest,
        state: dict[str, Any],
        arguments: dict[str, Any],
    ) -> RuntimeTurnResult:
        tool = self._tool_registry.get("security-tool-bootstrap")
        approval_mode_key = "security_tool_bootstrap"
        scope_hash = ApprovalScopeService().build_hash(
            mode_key=approval_mode_key,
            tool_key=tool.key,
            arguments=arguments,
            context=state.get("context_bundle") or {},
        )
        approval_id = str(uuid4())
        reason = (
            "P4 temporary security-tool readiness requires a dedicated approval for "
            "the exact campaign, targets, package, image, repository and timeout."
        )
        approval = {
            "id": approval_id,
            "session_id": session.id,
            "tool_key": tool.key,
            "tool_name": tool.name,
            "reason": reason,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "metadata": {
                "turn_id": state["turn_id"],
                "call_id": f"security_bootstrap_{state['turn_id']}",
                "arguments": arguments,
                "approval_scope_hash": scope_hash,
                "approval_mode_key": approval_mode_key,
                "approval_kind": "security_tool_bootstrap",
                "permission_behavior": "ask",
                "permission_source": "security_p4_runtime",
                "permission_reason_code": "security_tool_bootstrap_approval_required",
            },
        }
        record = ToolExecutionRecord(
            call_id=approval["metadata"]["call_id"],
            tool_key=tool.key,
            tool_name=tool.name,
            status="waiting_approval",
            summary=reason,
            input=arguments,
            output={"error": "approval_required", "approval_kind": "security_tool_bootstrap"},
            approval_id=approval_id,
        )
        state["tool_results"] = [record.model_dump(mode="python")]
        state["tool_messages"] = []
        state["pending_approvals"] = [approval]
        state["pending_turn"] = self._build_pending_turn(state, stage="waiting_approval")
        state["control_state"] = "waiting_approval"
        state["termination_reason"] = "waiting_approval"
        state["final_response"] = reason
        append_graph_event(
            state,
            "security.tool_bootstrap.approval_required",
            "runtime",
            reason,
            tool_key=tool.key,
            approval_id=approval_id,
            approval_scope_hash=scope_hash,
            campaign_id=arguments.get("campaign_id", ""),
            profile_key=arguments.get("profile_key", ""),
            tool_name=arguments.get("tool_name", ""),
        )
        snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text=reason,
            events=self._events_from_log(session.id, state["event_log"]),
            snapshot=snapshot,
            approvals=[approval],
            state=state,
            tool_messages=[],
            pending_turn=state["pending_turn"],
        )

    def _build_security_bootstrap_denied(
        self,
        *,
        session: SessionRecord,
        state: dict[str, Any],
        error: str,
    ) -> RuntimeTurnResult:
        messages = {
            "bootstrap_disabled": "P4 temporary tool bootstrap is disabled by server configuration.",
            "package_not_allowlisted": "The requested P4 tool/package/profile is not allowlisted.",
            "target_out_of_scope": "P4 temporary tool bootstrap target is outside the verified authorization scope.",
        }
        summary = messages.get(error, error)
        record = ToolExecutionRecord(
            call_id=f"security_bootstrap_{state['turn_id']}",
            tool_key="security-tool-bootstrap",
            tool_name="Security Tool Bootstrap",
            status="denied",
            summary=summary,
            input={},
            output={"error": error},
        )
        state["tool_results"] = [record.model_dump(mode="python")]
        state["tool_messages"] = [self._build_tool_message(record)]
        state["pending_approvals"] = []
        state["pending_turn"] = {}
        state["control_state"] = "completed"
        state["termination_reason"] = ""
        state["final_response"] = summary
        append_graph_event(
            state,
            "security.tool_bootstrap.denied",
            "runtime",
            summary,
            error=error,
        )
        snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
        return RuntimeTurnResult(
            output_text=summary,
            events=self._events_from_log(session.id, state["event_log"]),
            snapshot=snapshot,
            approvals=[],
            state=state,
            tool_messages=self._to_chat_messages(str(state["turn_id"]), state["tool_results"]),
            pending_turn={},
        )

    @staticmethod
    def _target_matches_any_allowed(target: str, allowed_targets: list[str]) -> bool:
        parsed_target = urlparse(target)
        target_host = (parsed_target.hostname or target.split(":", 1)[0]).strip("[]").lower()
        target_port = parsed_target.port
        for allowed in allowed_targets:
            parsed_allowed = urlparse(allowed)
            allowed_host = (parsed_allowed.hostname or allowed.split(":", 1)[0]).strip("[]").lower()
            allowed_port = parsed_allowed.port
            if target_host == allowed_host and (allowed_port is None or allowed_port == target_port):
                return True
        return False

    def _effective_model_config(self, requested_model_key: str = "") -> ModelConfigRecord | None:
        if requested_model_key:
            requested = self._model_runtime_service.get_model_config(requested_model_key)
            if requested is not None:
                return requested
        return self._model_runtime_service.get_default_model_config()

    def _build_conversation_messages(self, session: SessionRecord) -> list[dict[str, Any]]:
        summary_state = dict(session.metadata.get("context_summary") or {})
        summary = str(summary_state.get("summary") or "").strip()
        return self._transcript_hygiene_service.build_runtime_messages(
            session.messages,
            limit=self._context_max_tail_messages,
            summary=summary or None,
            summary_covers_until=int(summary_state.get("covers_until_index") or 0),
        )

    def _state_from_pending_turn(self, session: SessionRecord, pending_turn: dict[str, Any]) -> dict[str, Any]:
        graph_state = dict(pending_turn.get("graph_state", {}))
        conversation_messages = pending_turn.get("conversation_messages")
        if isinstance(conversation_messages, list):
            graph_messages = graph_state.get("runtime_messages")
            if graph_messages != conversation_messages:
                logger.warning(
                    "approval_checkpoint_transcript_reconciled",
                    extra={
                        "session_id": session.id,
                        "turn_id": str(pending_turn.get("turn_id") or graph_state.get("turn_id") or ""),
                        "graph_message_count": len(graph_messages) if isinstance(graph_messages, list) else 0,
                        "conversation_message_count": len(conversation_messages),
                    },
                )
                graph_state["runtime_messages"] = conversation_messages
        return self._state_from_graph_data(
            session=session,
            graph_state=graph_state,
            fallback_pending_turn=pending_turn,
        )

    def _state_from_snapshot(self, session: SessionRecord, snapshot: SessionSnapshot) -> dict[str, Any]:
        state = self._state_from_graph_data(
            session=session,
            graph_state=dict(snapshot.graph_state),
            fallback_pending_turn=dict(snapshot.graph_state.get("pending_turn") or {}),
        )
        state["termination_reason"] = ""
        state["continue_loop"] = False
        return state

    def _state_from_graph_data(
        self,
        session: SessionRecord,
        graph_state: dict[str, Any],
        fallback_pending_turn: dict[str, Any],
    ) -> dict[str, Any]:
        selected_model_key = str(graph_state.get("selected_model_key") or session.preferred_model or "")
        return {
            "session_id": session.id,
            "turn_id": str(graph_state.get("turn_id") or fallback_pending_turn.get("turn_id") or ""),
            "trace_id": str(graph_state.get("trace_id") or fallback_pending_turn.get("trace_id") or str(uuid4())),
            "user_message": str(graph_state.get("user_message") or fallback_pending_turn.get("user_message") or ""),
            "normalized_input": str(graph_state.get("normalized_input") or fallback_pending_turn.get("normalized_input") or ""),
            "session_mode": str(graph_state.get("session_mode") or fallback_pending_turn.get("session_mode") or session.session_mode.value),
            "runtime_mode": str(graph_state.get("runtime_mode") or fallback_pending_turn.get("runtime_mode") or session.runtime_mode.value),
            "mode_key": str(graph_state.get("mode_key") or fallback_pending_turn.get("mode_key") or session.mode_key or "default"),
            "message_count": len(session.messages),
            "preferred_model": selected_model_key,
            "selected_agent_key": str(graph_state.get("selected_agent_key") or fallback_pending_turn.get("selected_agent_key") or session.selected_agent or ""),
            "selected_agent_name": str(graph_state.get("selected_agent_name") or fallback_pending_turn.get("selected_agent_name") or ""),
            "selected_model_key": selected_model_key,
            "selected_model_name": str(graph_state.get("selected_model_name") or fallback_pending_turn.get("selected_model_name") or ""),
            "selected_model_provider": str(graph_state.get("selected_model_provider") or fallback_pending_turn.get("selected_model_provider") or ""),
            "requested_skill_keys": list(graph_state.get("requested_skill_keys") or fallback_pending_turn.get("requested_skill_keys") or []),
            "resolved_skill_keys": list(graph_state.get("resolved_skill_keys") or fallback_pending_turn.get("resolved_skill_keys") or []),
            "skill_prompt_blocks": list(graph_state.get("skill_prompt_blocks") or fallback_pending_turn.get("skill_prompt_blocks") or []),
            "memory_hits": list(graph_state.get("memory_hits") or fallback_pending_turn.get("memory_hits") or []),
            "memory_prompt_blocks": list(graph_state.get("memory_prompt_blocks") or fallback_pending_turn.get("memory_prompt_blocks") or []),
            "test_case_hits": list(graph_state.get("test_case_hits") or fallback_pending_turn.get("test_case_hits") or []),
            "test_case_prompt_blocks": list(graph_state.get("test_case_prompt_blocks") or fallback_pending_turn.get("test_case_prompt_blocks") or []),
            "observation_hits": list(graph_state.get("observation_hits") or fallback_pending_turn.get("observation_hits") or []),
            "observation_prompt_blocks": list(graph_state.get("observation_prompt_blocks") or fallback_pending_turn.get("observation_prompt_blocks") or []),
            "active_mcp_servers": list(graph_state.get("active_mcp_servers") or fallback_pending_turn.get("active_mcp_servers") or []),
            "mcp_prompt_blocks": list(graph_state.get("mcp_prompt_blocks") or fallback_pending_turn.get("mcp_prompt_blocks") or []),
            "available_tool_keys": list(graph_state.get("available_tool_keys") or fallback_pending_turn.get("available_tool_keys") or []),
            "deferred_tool_keys": list(graph_state.get("deferred_tool_keys") or fallback_pending_turn.get("deferred_tool_keys") or []),
            "model_visible_tool_keys": list(
                graph_state.get("model_visible_tool_keys")
                or fallback_pending_turn.get("model_visible_tool_keys")
                or [
                    *list(graph_state.get("allowed_tool_keys") or fallback_pending_turn.get("allowed_tool_keys") or []),
                    *list(graph_state.get("approval_required_tool_keys") or fallback_pending_turn.get("approval_required_tool_keys") or []),
                ]
            ),
            "allowed_tool_keys": list(graph_state.get("allowed_tool_keys") or fallback_pending_turn.get("allowed_tool_keys") or []),
            "approval_required_tool_keys": list(graph_state.get("approval_required_tool_keys") or fallback_pending_turn.get("approval_required_tool_keys") or []),
            "denied_tool_keys": list(graph_state.get("denied_tool_keys") or fallback_pending_turn.get("denied_tool_keys") or []),
            "permission_decisions": list(graph_state.get("permission_decisions") or fallback_pending_turn.get("permission_decisions") or []),
            "pending_approvals": list(graph_state.get("pending_approvals") or fallback_pending_turn.get("pending_approvals") or []),
            "plan_steps": list(graph_state.get("plan_steps") or fallback_pending_turn.get("plan_steps") or []),
            "system_prompt_sections": list(graph_state.get("system_prompt_sections") or fallback_pending_turn.get("system_prompt_sections") or []),
            "runtime_message_sections": list(graph_state.get("runtime_message_sections") or fallback_pending_turn.get("runtime_message_sections") or []),
            "system_prompt": str(graph_state.get("system_prompt") or fallback_pending_turn.get("system_prompt") or ""),
            "runtime_messages": list(graph_state.get("runtime_messages") or fallback_pending_turn.get("runtime_messages") or fallback_pending_turn.get("conversation_messages") or []),
            "framework_messages": list(graph_state.get("framework_messages") or fallback_pending_turn.get("framework_messages") or []),
            "model_request_payload": dict(graph_state.get("model_request_payload") or {}),
            "model_response_summary": dict(graph_state.get("model_response_summary") or {}),
            "model_response_text": str(graph_state.get("model_response_text") or ""),
            **persisted_turn_budget({**fallback_pending_turn, **graph_state}),
            "turn_token_usage": dict(graph_state.get("turn_token_usage") or fallback_pending_turn.get("turn_token_usage") or {}),
            "model_context_window": int(graph_state.get("model_context_window") or fallback_pending_turn.get("model_context_window") or 128000),
            "assistant_tool_call_message": dict(graph_state.get("assistant_tool_call_message") or {}),
            "model_tool_calls": list(graph_state.get("model_tool_calls") or []),
            "tool_results": list(graph_state.get("tool_results") or fallback_pending_turn.get("tool_results") or []),
            "tool_messages": list(graph_state.get("tool_messages") or fallback_pending_turn.get("tool_messages") or []),
            "worker_dispatches": list(graph_state.get("worker_dispatches") or fallback_pending_turn.get("worker_dispatches") or []),
            "context_bundle": {
                **dict(graph_state.get("context_bundle") or fallback_pending_turn.get("context_bundle") or {}),
                "project_id": session.project_id,
            },
            "event_log": list(graph_state.get("event_log") or []),
            "final_response": str(graph_state.get("final_response") or ""),
            "pending_turn": dict(graph_state.get("pending_turn") or fallback_pending_turn or {}),
            "control_state": str(graph_state.get("control_state") or fallback_pending_turn.get("control_state") or "resumable"),
            "interrupt_requested": False,
            "interrupt_reason": "",
            "loop_iteration": int(graph_state.get("loop_iteration") or fallback_pending_turn.get("loop_iteration") or 0),
            "max_iterations": int(graph_state.get("max_iterations") or fallback_pending_turn.get("max_iterations") or self._max_iterations),
            "continue_loop": bool(graph_state.get("continue_loop") or False),
            "termination_reason": str(graph_state.get("termination_reason") or fallback_pending_turn.get("latest_execution_stage") or ""),
        }

    async def _run_until_settled(self, state: dict[str, Any]) -> dict[str, Any]:
        """Delegate to AgentLoop for the main execution loop.

        Phase 1 of D2 upgrade: extracted loop logic into AgentLoop class.
        """
        return await self._agent_loop.run_turn(state)

    def _convert_model_interruption_to_resumable(self, state: dict[str, Any]) -> None:
        summary = dict(state.get("model_response_summary") or {})
        if not is_model_failure_summary(summary):
            return
        state["continue_loop"] = False
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["interrupt_reason"] = str(summary["mode"])
        state["pending_turn"] = self._build_pending_turn(state, stage="interrupted")
        append_graph_event(
            state,
            "model.invocation_interrupted",
            "runtime",
            "Model invocation failed and the turn was preserved for resume.",
            response_mode=str(summary["mode"]),
            error_type=str(summary.get("error_type") or ""),
            status_code=summary.get("status_code"),
            provider=str(summary.get("provider") or ""),
        )

    def _apply_interrupt_state(self, state: dict[str, Any]) -> None:
        session_id = str(state["session_id"])
        state["interrupt_requested"] = self._runtime_control.is_interrupt_requested(session_id)
        state["interrupt_reason"] = self._runtime_control.get_interrupt_reason(session_id)

    def _interrupt_result(self, state: dict[str, Any]) -> dict[str, Any]:
        if state["termination_reason"] != "interrupted":
            append_graph_event(
                state,
                "runtime.interrupt_requested",
                "runtime",
                "Interrupt was requested and will stop execution at this safe boundary.",
                interrupt_reason=state.get("interrupt_reason", ""),
                loop_iteration=state["loop_iteration"],
            )
        state["continue_loop"] = False
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["pending_turn"] = self._build_pending_turn(state, stage="interrupted")
        return state

    def _build_snapshot(self, session: SessionRecord, state: dict[str, Any], version: int) -> SessionSnapshot:
        graph_state = {
            "turn_id": state["turn_id"],
            "trace_id": state["trace_id"],
            "user_message": state["user_message"],
            "normalized_input": state["normalized_input"],
            "session_mode": state["session_mode"],
            "runtime_mode": state["runtime_mode"],
            "mode_key": state["mode_key"],
            "selected_agent_key": state["selected_agent_key"],
            "selected_agent_name": state["selected_agent_name"],
            "selected_model_key": state["selected_model_key"],
            "selected_model_name": state["selected_model_name"],
            "selected_model_provider": state["selected_model_provider"],
            "requested_skill_keys": state["requested_skill_keys"],
            "resolved_skill_keys": state["resolved_skill_keys"],
            "skill_prompt_blocks": state["skill_prompt_blocks"],
            "memory_hits": state["memory_hits"],
            "memory_prompt_blocks": state["memory_prompt_blocks"],
            "test_case_hits": state.get("test_case_hits", []),
            "test_case_prompt_blocks": state.get("test_case_prompt_blocks", []),
            "observation_hits": state["observation_hits"],
            "observation_prompt_blocks": state["observation_prompt_blocks"],
            "active_mcp_servers": state["active_mcp_servers"],
            "mcp_prompt_blocks": state["mcp_prompt_blocks"],
            "available_tool_keys": state["available_tool_keys"],
            "deferred_tool_keys": state["deferred_tool_keys"],
            "model_visible_tool_keys": state["model_visible_tool_keys"],
            "allowed_tool_keys": state["allowed_tool_keys"],
            "approval_required_tool_keys": state["approval_required_tool_keys"],
            "denied_tool_keys": state["denied_tool_keys"],
            "permission_decisions": state["permission_decisions"],
            "pending_approvals": state["pending_approvals"],
            "plan_steps": state["plan_steps"],
            "system_prompt_sections": state["system_prompt_sections"],
            "runtime_message_sections": state["runtime_message_sections"],
            "system_prompt": state["system_prompt"],
            "runtime_messages": state["runtime_messages"],
            "framework_messages": list(state.get("framework_messages") or []),
            "model_request_payload": state["model_request_payload"],
            "model_response_summary": state["model_response_summary"],
            "model_response_text": state["model_response_text"],
            **persisted_turn_budget(state),
            "assistant_tool_call_message": state["assistant_tool_call_message"],
            "model_tool_calls": state["model_tool_calls"],
            "tool_results": state["tool_results"],
            "tool_messages": state["tool_messages"],
            "worker_dispatches": state["worker_dispatches"],
            "context_bundle": state["context_bundle"],
            "event_log": state["event_log"],
            "pending_turn": state["pending_turn"],
            "control_state": state["control_state"],
            "loop_iteration": state["loop_iteration"],
            "max_iterations": state["max_iterations"],
            "continue_loop": state["continue_loop"],
            "termination_reason": state["termination_reason"],
            "final_response": state["final_response"],
        }
        return SessionSnapshot(
            id=str(uuid4()),
            session_id=session.id,
            version=version,
            stage=self._snapshot_stage_for_state(state),
            created_at=datetime.utcnow(),
            graph_state=graph_state,
        )

    def _snapshot_stage_for_state(self, state: dict[str, Any]) -> str:
        if state["termination_reason"] == "interrupted":
            return "interrupted"
        if state["pending_approvals"]:
            return "waiting_approval"
        if state["pending_turn"]:
            return "resumable"
        if state["termination_reason"] == "failed":
            return "failed"
        return "completed"

    def _build_pending_turn(self, state: dict[str, Any], stage: str) -> dict[str, Any]:
        return build_pending_turn(state, stage)

    def _events_from_log(self, session_id: str, event_log: list[dict[str, Any]]) -> list[ExecutionEvent]:
        return [
            ExecutionEvent(
                id=entry.get("id") or str(uuid4()),
                type=entry["type"],
                session_id=session_id,
                timestamp=entry.get("timestamp") or datetime.now(timezone.utc),
                payload=entry["payload"],
            )
            for entry in event_log
        ]

    @staticmethod
    def _merge_tool_records(records: list[dict], updates: list[dict]) -> None:
        by_call = {item["call_id"]: item for item in records}
        by_call.update({item["call_id"]: item for item in updates})
        records[:] = list(by_call.values())

    def _to_chat_messages(self, turn_id: str, tool_results: list[dict], *, binding_id: str | None = None) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for index, item in enumerate(tool_results, start=1):
            if item.get("status") == "waiting_approval":
                continue
            content = (
                f"{item.get('tool_name', item.get('tool_key', 'tool'))}\n"
                f"status: {item.get('status', 'unknown')}\n"
                f"summary: {item.get('summary', '')}\n\n"
                f"{json.dumps(make_json_safe(item.get('output', {})), ensure_ascii=False, indent=2)}"
            ).strip()
            messages.append(
                ChatMessage(
                    id=(str(uuid5(NAMESPACE_URL, f"framework-tool:{binding_id}:{item['call_id']}"))
                        if binding_id is not None else str(uuid4())),
                    role=MessageRole.tool,
                    content=content,
                    created_at=datetime.utcnow(),
                    metadata={
                        "turn_id": turn_id,
                        "tool_key": item.get("tool_key", ""),
                        "tool_name": item.get("tool_name", ""),
                        "tool_call_id": item.get("call_id", ""),
                        "status": item.get("status", ""),
                        "trace_id": item.get("trace_id", ""),
                        "ordinal": index,
                    }
                    | self._transcript_hygiene_service.tool_message_metadata(),
                )
            )
        return messages

    def _build_tool_message(self, record: ToolExecutionRecord) -> dict[str, Any]:
        return build_tool_message(record)

    @staticmethod
    def _latest_assistant_tool_call_ids(messages: list[dict[str, Any]]) -> list[str]:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            tool_calls = message.get("tool_calls") or []
            ids = [
                str(item.get("id") or "")
                for item in tool_calls
                if isinstance(item, dict)
            ]
            return list(dict.fromkeys(item for item in ids if item))
        return []
