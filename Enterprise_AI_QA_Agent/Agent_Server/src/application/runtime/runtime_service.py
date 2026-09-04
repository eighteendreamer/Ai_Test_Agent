from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.context.context_compaction_service import ContextCompactionService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.agent_loop import AgentLoop
from src.application.runtime.error_recovery import ErrorRecoveryCascade
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.security.approval_scope_service import ApprovalScopeService
from src.application.security.authorization import verified_grant_matches_target
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.domain.models import SessionRecord
from src.infrastructure.storage_utils import make_json_safe
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_logging import append_graph_event, truncate_text
from src.schemas.model_config import ModelConfigRecord
from src.schemas.session import ChatMessage, ExecutionEvent, ExecutionRequest, MessageRole, SessionSnapshot
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


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
        self.clear_interrupt(session.id)
        compaction_info = None
        if self._context_compaction_service is not None:
            compaction_info = await self._context_compaction_service.maybe_compact(session)
        initial_state = self._build_initial_state(session, request)
        initial_state["_event_queue"] = event_queue
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
            return await self._execute_security_mode_turn(session, request, initial_state)
        return await self._execute_state(session, initial_state, on_model_chunk=on_model_chunk)

    async def resume_after_approval(
        self,
        session: SessionRecord,
        approval: dict,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult | None:
        pending_turn = dict(session.metadata.get("pending_turn") or {})
        if not pending_turn:
            return None

        state = self._state_from_pending_turn(session, pending_turn)
        state["_event_queue"] = event_queue
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
        )

        tool = self._tool_registry.get(approval["tool_key"])
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
            tool_call.arguments = {
                **tool_call.arguments,
                "_server_approval_granted": True,
            }
            if tool.key == "security-tool-bootstrap":
                tool_call.arguments["_p4_approval_scope_hash"] = stored_scope_hash
            append_graph_event(
                state,
                "tool.execution_started",
                "approval_resume",
                f"Approved tool '{approval['tool_key']}' is now executing.",
                tool_key=approval["tool_key"],
                approval_id=approval["id"],
            )
            execution_record = await self._tool_runtime_service.execute(
                tool=tool,
                call=tool_call,
                context=context,
            )
            append_graph_event(
                state,
                "tool.execution_completed" if execution_record.status == "completed" else "tool.execution_failed",
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
            append_graph_event(
                state,
                "tool.execution_denied",
                "approval_resume",
                execution_record.summary,
                tool_key=execution_record.tool_key,
                approval_id=approval["id"],
            )

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

        if pending_ids:
            pending_turn["tool_results"] = tool_results
            pending_turn["tool_messages"] = tool_messages
            pending_turn["pending_approval_ids"] = pending_ids
            pending_turn["pending_approvals"] = remaining_approvals
            state["pending_turn"] = pending_turn
            state["termination_reason"] = "waiting_approval"
            snapshot = self._build_snapshot(session, state, session.snapshot_count + 1)
            events = self._events_from_log(session.id, state["event_log"])
            return RuntimeTurnResult(
                output_text="",
                events=events,
                snapshot=snapshot,
                approvals=[],
                state=state,
                tool_messages=self._to_chat_messages(turn_id, [execution_record.model_dump(mode="python")]),
                pending_turn=pending_turn,
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
            resume_tool_messages = [
                message
                for message in tool_messages
                if str(message.get("tool_call_id") or "") in latest_tool_call_ids
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
        self.clear_interrupt(session.id)
        return await self._execute_state(session, state, on_model_chunk=on_model_chunk)

    async def resume_turn(
        self,
        session: SessionRecord,
        snapshot: SessionSnapshot,
        resume_reason: str,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> RuntimeTurnResult:
        self.clear_interrupt(session.id)
        state = self._state_from_snapshot(session, snapshot)
        state["_event_queue"] = event_queue
        await self._attach_session_resources(state)
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

    def _build_initial_state(self, session: SessionRecord, request: ExecutionRequest) -> dict[str, Any]:
        requested_model_key = request.model_key or session.preferred_model or ""
        model_config = self._effective_model_config(requested_model_key)
        selected_model_key = model_config.key if model_config is not None else (request.model_key or session.preferred_model or "")
        selected_model_name = model_config.name if model_config is not None else ""
        selected_model_provider = model_config.provider if model_config is not None else ""
        return {
            "session_id": session.id,
            "turn_id": request.turn_id,
            "trace_id": str(uuid4()),
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
            "context_bundle": request.context,
            "event_log": [],
            "_streamed_event_count": 0,
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
        safety = request.context.get("safety_assessment")
        authorization_verified = isinstance(safety, dict) and safety.get("authorization_status") == "verified"
        if not authorization_verified:
            authorization_verified = self._trusted_security_grant_matches_request(request)
        return (
            str(request.mode_key or "").strip() == "security_testing"
            and bool(request.context.get("trusted_security_runtime_direct_execution"))
            and authorization_verified
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
            "model_request_payload": dict(graph_state.get("model_request_payload") or {}),
            "model_response_summary": dict(graph_state.get("model_response_summary") or {}),
            "model_response_text": str(graph_state.get("model_response_text") or ""),
            "turn_token_usage": dict(graph_state.get("turn_token_usage") or fallback_pending_turn.get("turn_token_usage") or {}),
            "model_context_window": int(graph_state.get("model_context_window") or fallback_pending_turn.get("model_context_window") or 128000),
            "assistant_tool_call_message": dict(graph_state.get("assistant_tool_call_message") or {}),
            "model_tool_calls": list(graph_state.get("model_tool_calls") or []),
            "tool_results": list(graph_state.get("tool_results") or fallback_pending_turn.get("tool_results") or []),
            "tool_messages": list(graph_state.get("tool_messages") or fallback_pending_turn.get("tool_messages") or []),
            "worker_dispatches": list(graph_state.get("worker_dispatches") or fallback_pending_turn.get("worker_dispatches") or []),
            "context_bundle": dict(graph_state.get("context_bundle") or fallback_pending_turn.get("context_bundle") or {}),
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
        if str(summary.get("mode") or "") != "http_error":
            return
        state["continue_loop"] = False
        state["termination_reason"] = "interrupted"
        state["control_state"] = "interrupted"
        state["interrupt_reason"] = str(summary.get("error") or summary.get("error_type") or "model_interrupted")
        state["pending_turn"] = self._build_pending_turn(state, stage="interrupted")
        append_graph_event(
            state,
            "model.invocation_interrupted",
            "runtime",
            "Model invocation failed and the turn was preserved for resume.",
            error_type=str(summary.get("error_type") or ""),
            status_code=summary.get("status_code"),
            provider=str(summary.get("provider") or ""),
        )

    def _apply_interrupt_state(self, state: dict[str, Any]) -> None:
        reason = self._runtime_control.get_interrupt_reason(str(state["session_id"]))
        state["interrupt_requested"] = bool(reason)
        state["interrupt_reason"] = reason

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
            "model_request_payload": state["model_request_payload"],
            "model_response_summary": state["model_response_summary"],
            "model_response_text": state["model_response_text"],
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
        return {
            "turn_id": state["turn_id"],
            "trace_id": state["trace_id"],
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
            "user_message": state["user_message"],
            "normalized_input": state["normalized_input"],
            "loop_iteration": state["loop_iteration"],
            "max_iterations": state["max_iterations"],
            "context_bundle": state["context_bundle"],
            "system_prompt_sections": state["system_prompt_sections"],
            "runtime_message_sections": state["runtime_message_sections"],
            "system_prompt": state["system_prompt"],
            "conversation_messages": state["runtime_messages"],
            "runtime_messages": state["runtime_messages"],
            "tool_results": state["tool_results"],
            "tool_messages": state["tool_messages"],
            "worker_dispatches": state["worker_dispatches"],
            "pending_approval_ids": [item.get("id") for item in state["pending_approvals"] if item.get("id")],
            "pending_approvals": state["pending_approvals"],
            "latest_execution_stage": stage,
            "control_state": state["control_state"],
            "graph_state": {
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
                "model_request_payload": state["model_request_payload"],
                "model_response_summary": state["model_response_summary"],
                "model_response_text": state["model_response_text"],
                "assistant_tool_call_message": state["assistant_tool_call_message"],
                "model_tool_calls": state["model_tool_calls"],
                "tool_results": state["tool_results"],
                "tool_messages": state["tool_messages"],
                "worker_dispatches": state["worker_dispatches"],
                "context_bundle": state["context_bundle"],
                "event_log": state["event_log"],
                "control_state": state["control_state"],
                "loop_iteration": state["loop_iteration"],
                "max_iterations": state["max_iterations"],
                "continue_loop": False,
                "termination_reason": state["termination_reason"],
                "final_response": state["final_response"],
            },
        }

    def _events_from_log(self, session_id: str, event_log: list[dict[str, Any]]) -> list[ExecutionEvent]:
        return [
            ExecutionEvent(
                type=entry["type"],
                session_id=session_id,
                timestamp=datetime.utcnow(),
                payload=entry["payload"],
            )
            for entry in event_log
        ]

    def _to_chat_messages(self, turn_id: str, tool_results: list[dict]) -> list[ChatMessage]:
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
                    id=str(uuid4()),
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
        return {
            "role": "tool",
            "tool_call_id": record.call_id,
            "name": record.tool_key,
            "content": json.dumps(
                make_json_safe(
                    {
                        "status": record.status,
                        "summary": record.summary,
                        "output": record.output,
                        "content_provenance": "tool_output",
                        "instruction_authority": "none",
                    }
                ),
                ensure_ascii=False,
            ),
        }

    def _latest_assistant_tool_call_ids(self, messages: list[dict[str, Any]]) -> set[str]:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            tool_calls = message.get("tool_calls") or []
            ids = {str(item.get("id") or "") for item in tool_calls if isinstance(item, dict)}
            return {item for item in ids if item}
        return set()
