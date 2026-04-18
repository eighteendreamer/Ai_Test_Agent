from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.application.model_runtime_service import ModelRuntimeService
from src.application.transcript_hygiene_service import TranscriptHygieneService
from src.application.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.domain.models import SessionRecord
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_logging import append_graph_event, truncate_text
from src.schemas.session import ChatMessage, ExecutionEvent, ExecutionRequest, MessageRole, SessionSnapshot
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


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
    ) -> None:
        self._graph = graph
        self._model_runtime_service = model_runtime_service
        self._tool_runtime_service = tool_runtime_service
        self._tool_registry = tool_registry
        self._runtime_control = runtime_control
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._max_iterations = max_iterations

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
    ) -> RuntimeTurnResult:
        self.clear_interrupt(session.id)
        initial_state = self._build_initial_state(session, request)
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
        return await self._execute_state(session, initial_state, on_model_chunk=on_model_chunk)

    async def resume_after_approval(
        self,
        session: SessionRecord,
        approval: dict,
        on_model_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> RuntimeTurnResult | None:
        pending_turn = dict(session.metadata.get("pending_turn") or {})
        if not pending_turn:
            return None

        state = self._state_from_pending_turn(session, pending_turn)
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

        if approval["status"] == "approved":
            append_graph_event(
                state,
                "tool.execution_started",
                "approval_resume",
                f"Approved tool '{approval['tool_key']}' is now executing.",
                tool_key=approval["tool_key"],
                approval_id=approval["id"],
            )
            execution_record = await self._tool_runtime_service.execute(
                tool=self._tool_registry.get(approval["tool_key"]),
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
            execution_record = ToolExecutionRecord(
                call_id=tool_call.id,
                tool_key=approval["tool_key"],
                tool_name=approval["tool_name"],
                status="denied",
                summary=f"Approval denied for tool '{approval['tool_name']}'.",
                input=tool_call.arguments,
                output={"decision_note": approval.get("decision_note")},
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

        state["tool_results"] = tool_results
        state["tool_messages"] = tool_messages
        state["pending_approvals"] = []
        state["pending_turn"] = {}
        state["control_state"] = "resuming"

        if pending_ids:
            pending_turn["tool_results"] = tool_results
            pending_turn["tool_messages"] = tool_messages
            pending_turn["pending_approval_ids"] = pending_ids
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

        state["runtime_messages"] = [
            *list(pending_turn.get("conversation_messages", [])),
            approved_tool_message,
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
    ) -> RuntimeTurnResult:
        self.clear_interrupt(session.id)
        state = self._state_from_snapshot(session, snapshot)
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

    def _build_initial_state(self, session: SessionRecord, request: ExecutionRequest) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "turn_id": request.turn_id,
            "trace_id": str(uuid4()),
            "user_message": request.user_message,
            "normalized_input": request.normalized_input,
            "session_mode": session.session_mode.value,
            "runtime_mode": session.runtime_mode.value,
            "message_count": len(session.messages),
            "preferred_model": request.model_key or session.preferred_model or "",
            "selected_agent_key": request.agent_key or session.selected_agent or "",
            "selected_agent_name": "",
            "selected_model_key": request.model_key or session.preferred_model or "",
            "selected_model_name": "",
            "selected_model_provider": "",
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
            "allowed_tool_keys": [],
            "approval_required_tool_keys": [],
            "denied_tool_keys": [],
            "permission_decisions": [],
            "pending_approvals": [],
            "plan_steps": [],
            "system_prompt": "",
            "runtime_messages": self._build_conversation_messages(session),
            "model_request_payload": {},
            "model_response_summary": {},
            "model_response_text": "",
            "assistant_tool_call_message": {},
            "model_tool_calls": [],
            "tool_results": [],
            "tool_messages": [],
            "worker_dispatches": [],
            "context_bundle": request.context,
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

    def _build_conversation_messages(self, session: SessionRecord) -> list[dict[str, Any]]:
        return self._transcript_hygiene_service.build_runtime_messages(session.messages, limit=24)

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
            "allowed_tool_keys": list(graph_state.get("allowed_tool_keys") or fallback_pending_turn.get("allowed_tool_keys") or []),
            "approval_required_tool_keys": list(graph_state.get("approval_required_tool_keys") or fallback_pending_turn.get("approval_required_tool_keys") or []),
            "denied_tool_keys": list(graph_state.get("denied_tool_keys") or fallback_pending_turn.get("denied_tool_keys") or []),
            "permission_decisions": list(graph_state.get("permission_decisions") or fallback_pending_turn.get("permission_decisions") or []),
            "pending_approvals": list(graph_state.get("pending_approvals") or fallback_pending_turn.get("pending_approvals") or []),
            "plan_steps": list(graph_state.get("plan_steps") or fallback_pending_turn.get("plan_steps") or []),
            "system_prompt": str(graph_state.get("system_prompt") or fallback_pending_turn.get("system_prompt") or ""),
            "runtime_messages": list(graph_state.get("runtime_messages") or fallback_pending_turn.get("runtime_messages") or fallback_pending_turn.get("conversation_messages") or []),
            "model_request_payload": dict(graph_state.get("model_request_payload") or {}),
            "model_response_summary": dict(graph_state.get("model_response_summary") or {}),
            "model_response_text": str(graph_state.get("model_response_text") or ""),
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
        current_state = state
        while True:
            self._apply_interrupt_state(current_state)
            if current_state["interrupt_requested"]:
                return self._interrupt_result(current_state)

            result = await self._graph.ainvoke(current_state)
            self._apply_interrupt_state(result)
            if result["interrupt_requested"]:
                return self._interrupt_result(result)
            if not result["continue_loop"]:
                return result

            append_graph_event(
                result,
                "runtime.loop_reenter",
                "runtime",
                "Runtime is re-entering the recursive model loop for the same turn.",
                next_iteration=result["loop_iteration"] + 1,
                max_iterations=result["max_iterations"],
            )
            result["loop_iteration"] += 1
            current_state = result

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
            "allowed_tool_keys": state["allowed_tool_keys"],
            "approval_required_tool_keys": state["approval_required_tool_keys"],
            "denied_tool_keys": state["denied_tool_keys"],
            "permission_decisions": state["permission_decisions"],
            "pending_approvals": state["pending_approvals"],
            "plan_steps": state["plan_steps"],
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
            "allowed_tool_keys": state["allowed_tool_keys"],
            "approval_required_tool_keys": state["approval_required_tool_keys"],
            "denied_tool_keys": state["denied_tool_keys"],
            "permission_decisions": state["permission_decisions"],
            "user_message": state["user_message"],
            "normalized_input": state["normalized_input"],
            "loop_iteration": state["loop_iteration"],
            "max_iterations": state["max_iterations"],
            "context_bundle": state["context_bundle"],
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
                "allowed_tool_keys": state["allowed_tool_keys"],
                "approval_required_tool_keys": state["approval_required_tool_keys"],
                "denied_tool_keys": state["denied_tool_keys"],
                "permission_decisions": state["permission_decisions"],
                "pending_approvals": state["pending_approvals"],
                "plan_steps": state["plan_steps"],
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
                f"{json.dumps(item.get('output', {}), ensure_ascii=False, indent=2)}"
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
                {
                    "status": record.status,
                    "summary": record.summary,
                    "output": record.output,
                },
                ensure_ascii=False,
            ),
        }
