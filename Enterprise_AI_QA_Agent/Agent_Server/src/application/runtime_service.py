from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import uuid4

from src.application.model_runtime_service import ModelRuntimeService
from src.application.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.domain.models import SessionRecord
from src.registry.tools import ToolRegistry
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
        max_iterations: int = 8,
    ) -> None:
        self._graph = graph
        self._model_runtime_service = model_runtime_service
        self._tool_runtime_service = tool_runtime_service
        self._tool_registry = tool_registry
        self._max_iterations = max_iterations

    async def execute_turn(
        self,
        session: SessionRecord,
        request: ExecutionRequest,
    ) -> RuntimeTurnResult:
        initial_state = {
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
            "active_mcp_servers": [],
            "mcp_prompt_blocks": [],
            "available_tool_keys": [],
            "allowed_tool_keys": [],
            "approval_required_tool_keys": [],
            "pending_approvals": [],
            "plan_steps": [],
            "system_prompt": "",
            "runtime_messages": [],
            "model_request_payload": {},
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
            "loop_iteration": 0,
            "max_iterations": self._max_iterations,
            "continue_loop": False,
            "termination_reason": "",
        }
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

        result = await self._run_until_settled(initial_state)

        append_graph_event(
            result,
            "runtime.turn_completed",
            "runtime",
            "Runtime execution finished for the current turn.",
            final_response_preview=truncate_text(result["final_response"], 160),
            final_response_length=len(result["final_response"]),
            pending_approval_count=len(result["pending_approvals"]),
            tool_result_count=len(result["tool_results"]),
        )

        events = [
            ExecutionEvent(
                type=entry["type"],
                session_id=session.id,
                timestamp=datetime.utcnow(),
                payload=entry["payload"],
            )
            for entry in result["event_log"]
        ]

        snapshot = SessionSnapshot(
            id=str(uuid4()),
            session_id=session.id,
            version=session.snapshot_count + 1,
            stage="waiting_approval" if result["pending_approvals"] else "completed",
            created_at=datetime.utcnow(),
            graph_state={
                "turn_id": request.turn_id,
                "trace_id": result["trace_id"],
                "selected_agent_key": result["selected_agent_key"],
                "selected_agent_name": result["selected_agent_name"],
                "selected_model_key": result["selected_model_key"],
                "selected_model_name": result["selected_model_name"],
                "selected_model_provider": result["selected_model_provider"],
                "resolved_skill_keys": result["resolved_skill_keys"],
                "skill_prompt_blocks": result["skill_prompt_blocks"],
                "memory_hits": result["memory_hits"],
                "memory_prompt_blocks": result["memory_prompt_blocks"],
                "active_mcp_servers": result["active_mcp_servers"],
                "mcp_prompt_blocks": result["mcp_prompt_blocks"],
                "available_tool_keys": result["available_tool_keys"],
                "allowed_tool_keys": result["allowed_tool_keys"],
                "approval_required_tool_keys": result["approval_required_tool_keys"],
                "plan_steps": result["plan_steps"],
                "system_prompt": result["system_prompt"],
                "runtime_messages": result["runtime_messages"],
                "model_request_payload": result["model_request_payload"],
                "model_response_text": result["model_response_text"],
                "assistant_tool_call_message": result["assistant_tool_call_message"],
                "model_tool_calls": result["model_tool_calls"],
                "tool_results": result["tool_results"],
                "tool_messages": result["tool_messages"],
                "worker_dispatches": result["worker_dispatches"],
                "context_bundle": result["context_bundle"],
                "event_log": result["event_log"],
                "pending_turn": result["pending_turn"],
                "loop_iteration": result["loop_iteration"],
                "max_iterations": result["max_iterations"],
                "termination_reason": result["termination_reason"],
            },
        )

        return RuntimeTurnResult(
            output_text=result["final_response"],
            events=events,
            snapshot=snapshot,
            approvals=result["pending_approvals"],
            state=result,
            tool_messages=self._to_chat_messages(request.turn_id, result["tool_results"]),
            pending_turn=result["pending_turn"],
        )

    async def resume_after_approval(
        self,
        session: SessionRecord,
            approval: dict,
    ) -> RuntimeTurnResult | None:
        pending_turn = dict(session.metadata.get("pending_turn") or {})
        if not pending_turn:
            return None

        tool_call = ModelToolCall(
            id=str(approval["metadata"].get("call_id", approval["id"])),
            name=approval["tool_key"],
            arguments=approval["metadata"].get("arguments", {}),
        )
        events_payload: list[dict[str, object]] = []
        tool_results = list(pending_turn.get("tool_results", []))
        tool_messages = list(pending_turn.get("tool_messages", []))
        resume_tool_messages = list(pending_turn.get("resume_tool_messages", []))
        pending_ids = list(pending_turn.get("pending_approval_ids", []))
        selected_agent_key = str(pending_turn.get("selected_agent_key", session.selected_agent or ""))
        selected_agent_name = str(pending_turn.get("selected_agent_name", ""))
        selected_model_key = str(pending_turn.get("selected_model_key", session.preferred_model or ""))
        selected_model_name = str(pending_turn.get("selected_model_name", ""))
        selected_model_provider = str(pending_turn.get("selected_model_provider", ""))
        context = ToolExecutionContext(
            session_id=session.id,
            turn_id=str(pending_turn.get("turn_id", "")),
            trace_id=str(pending_turn.get("trace_id", "")),
            user_message=str(pending_turn.get("user_message", "")),
            normalized_input=str(pending_turn.get("normalized_input", "")),
            context_bundle=dict(pending_turn.get("context_bundle", {})),
            selected_agent_key=selected_agent_key,
            selected_model_key=selected_model_key,
        )
        turn_id = str(pending_turn.get("turn_id", ""))

        if approval["status"] == "approved":
            events_payload.append(
                {
                    "type": "tool.execution_started",
                    "payload": {
                        "phase": "approval_resume",
                        "message": f"Approved tool '{approval['tool_key']}' is now executing.",
                        "tool_key": approval["tool_key"],
                        "approval_id": approval["id"],
                    },
                }
            )
            execution_record = await self._tool_runtime_service.execute(
                tool=self._tool_registry.get(approval["tool_key"]),
                call=tool_call,
                context=context,
            )
            events_payload.append(
                {
                    "type": "tool.execution_completed"
                    if execution_record.status == "completed"
                    else "tool.execution_failed",
                    "payload": {
                        "phase": "approval_resume",
                        "message": execution_record.summary,
                        "tool_key": execution_record.tool_key,
                        "approval_id": approval["id"],
                        "status": execution_record.status,
                    },
                }
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
            events_payload.append(
                {
                    "type": "tool.execution_denied",
                    "payload": {
                        "phase": "approval_resume",
                        "message": execution_record.summary,
                        "tool_key": execution_record.tool_key,
                        "approval_id": approval["id"],
                    },
                }
            )

        tool_results = [
            item for item in tool_results if item.get("approval_id") != approval["id"]
        ]
        tool_results.append(execution_record.model_dump(mode="python"))
        approved_tool_message = self._build_tool_message(execution_record)
        tool_messages.append(approved_tool_message)
        resume_tool_messages.append(approved_tool_message)
        pending_ids = [item for item in pending_ids if item != approval["id"]]
        pending_turn["tool_results"] = tool_results
        pending_turn["tool_messages"] = tool_messages
        pending_turn["resume_tool_messages"] = resume_tool_messages
        pending_turn["pending_approval_ids"] = pending_ids

        output_text = ""
        emitted_tool_results = [execution_record.model_dump(mode="python")]
        if not pending_ids:
            existing_tool_result_count = len(tool_results)
            state = {
                "session_id": session.id,
                "turn_id": turn_id,
                "trace_id": str(pending_turn.get("trace_id", "")) or str(uuid4()),
                "user_message": str(pending_turn.get("user_message", "")),
                "normalized_input": str(pending_turn.get("normalized_input", "")),
                "session_mode": session.session_mode.value,
                "runtime_mode": session.runtime_mode.value,
                "message_count": len(session.messages),
                "preferred_model": selected_model_key,
                "selected_agent_key": selected_agent_key,
                "selected_agent_name": selected_agent_name,
                "selected_model_key": selected_model_key,
                "selected_model_name": selected_model_name,
                "selected_model_provider": selected_model_provider,
                "requested_skill_keys": list(pending_turn.get("requested_skill_keys", [])),
                "resolved_skill_keys": list(pending_turn.get("resolved_skill_keys", [])),
                "skill_prompt_blocks": list(pending_turn.get("skill_prompt_blocks", [])),
                "memory_hits": list(pending_turn.get("memory_hits", [])),
                "memory_prompt_blocks": list(pending_turn.get("memory_prompt_blocks", [])),
                "active_mcp_servers": list(pending_turn.get("active_mcp_servers", [])),
                "mcp_prompt_blocks": list(pending_turn.get("mcp_prompt_blocks", [])),
                "available_tool_keys": list(pending_turn.get("available_tool_keys", [])),
                "allowed_tool_keys": list(pending_turn.get("allowed_tool_keys", [])),
                "approval_required_tool_keys": list(pending_turn.get("approval_required_tool_keys", [])),
                "pending_approvals": [],
                "plan_steps": [],
                "system_prompt": str(pending_turn.get("system_prompt", "")),
                "runtime_messages": [
                    *list(pending_turn.get("conversation_messages", [])),
                    *resume_tool_messages,
                ],
                "model_request_payload": {},
                "model_response_text": "",
                "assistant_tool_call_message": {},
                "model_tool_calls": [],
                "tool_results": tool_results,
                "tool_messages": tool_messages,
                "worker_dispatches": list(pending_turn.get("worker_dispatches", [])),
                "context_bundle": dict(pending_turn.get("context_bundle", {})),
                "event_log": events_payload,
                "final_response": "",
                "pending_turn": {},
                "loop_iteration": int(pending_turn.get("loop_iteration", 0)),
                "max_iterations": int(pending_turn.get("max_iterations", self._max_iterations)),
                "continue_loop": False,
                "termination_reason": "",
            }
            final_state = await self._run_until_settled(state)
            output_text = final_state["final_response"]
            events_payload = final_state["event_log"]
            pending_turn = final_state["pending_turn"]
            tool_results = final_state["tool_results"]
            tool_messages = final_state["tool_messages"]
            emitted_tool_results = [
                execution_record.model_dump(mode="python"),
                *tool_results[existing_tool_result_count:],
            ]
            state = final_state
        else:
            state = {
                "turn_id": turn_id,
                "trace_id": str(pending_turn.get("trace_id", "")) or str(uuid4()),
                "selected_agent_key": selected_agent_key,
                "selected_agent_name": selected_agent_name,
                "selected_model_key": selected_model_key,
                "selected_model_name": selected_model_name,
                "selected_model_provider": selected_model_provider,
                "resolved_skill_keys": list(pending_turn.get("resolved_skill_keys", [])),
                "skill_prompt_blocks": list(pending_turn.get("skill_prompt_blocks", [])),
                "memory_hits": list(pending_turn.get("memory_hits", [])),
                "memory_prompt_blocks": list(pending_turn.get("memory_prompt_blocks", [])),
                "active_mcp_servers": list(pending_turn.get("active_mcp_servers", [])),
                "mcp_prompt_blocks": list(pending_turn.get("mcp_prompt_blocks", [])),
                "available_tool_keys": list(pending_turn.get("available_tool_keys", [])),
                "allowed_tool_keys": list(pending_turn.get("allowed_tool_keys", [])),
                "approval_required_tool_keys": list(pending_turn.get("approval_required_tool_keys", [])),
                "plan_steps": [],
                "model_tool_calls": [],
                "tool_results": tool_results,
                "tool_messages": tool_messages,
                "worker_dispatches": list(pending_turn.get("worker_dispatches", [])),
                "context_bundle": dict(pending_turn.get("context_bundle", {})),
                "event_log": events_payload,
                "pending_turn": pending_turn,
                "pending_approvals": [],
                "final_response": output_text,
                "loop_iteration": int(pending_turn.get("loop_iteration", 0)),
                "max_iterations": int(pending_turn.get("max_iterations", self._max_iterations)),
                "termination_reason": "waiting_approval",
            }

        snapshot = SessionSnapshot(
            id=str(uuid4()),
            session_id=session.id,
            version=session.snapshot_count + 1,
            stage="waiting_approval" if pending_ids else "completed",
            created_at=datetime.utcnow(),
            graph_state={
                "turn_id": state["turn_id"],
                "trace_id": state["trace_id"],
                "selected_agent_key": state["selected_agent_key"],
                "selected_agent_name": state["selected_agent_name"],
                "selected_model_key": state["selected_model_key"],
                "selected_model_name": state["selected_model_name"],
                "resolved_skill_keys": state.get("resolved_skill_keys", []),
                "tool_results": tool_results,
                "tool_messages": tool_messages,
                "worker_dispatches": state.get("worker_dispatches", []),
                "event_log": events_payload,
                "pending_turn": pending_turn,
            },
        )
        events = [
            ExecutionEvent(
                type=entry["type"],
                session_id=session.id,
                timestamp=datetime.utcnow(),
                payload=entry["payload"],
            )
            for entry in events_payload
        ]
        return RuntimeTurnResult(
            output_text=output_text,
            events=events,
            snapshot=snapshot,
            approvals=[],
            state=state,
            tool_messages=self._to_chat_messages(turn_id, emitted_tool_results),
            pending_turn=pending_turn,
        )

    async def _run_until_settled(self, state: dict[str, Any]) -> dict[str, Any]:
        current_state = state
        while True:
            result = await self._graph.ainvoke(current_state)
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
                        "status": item.get("status", ""),
                        "trace_id": item.get("trace_id", ""),
                        "ordinal": index,
                    },
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
