from __future__ import annotations

import json
from typing import Any

from src.application.permission_service import PermissionService
from src.application.tool_job_service import ToolJobService
from src.application.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.graph.state import AgentGraphState
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


def build_tool_executor_node(
    tool_registry: ToolRegistry,
    permission_service: PermissionService,
    tool_runtime_service: ToolRuntimeService,
    tool_job_service: ToolJobService | None = None,
):
    async def tool_executor(state: AgentGraphState) -> AgentGraphState:
        append_graph_event(
            state,
            "model.tool_calls_received",
            "tool_executor",
            "Model requested one or more tool calls.",
            tool_call_names=",".join(item["name"] for item in state["model_tool_calls"]),
            tool_call_count=len(state["model_tool_calls"]),
        )

        prior_tool_messages = list(state["tool_messages"])
        prior_tool_results = list(state["tool_results"])
        new_tool_messages: list[dict[str, Any]] = []
        tool_results = list(prior_tool_results)
        pending_approvals: list[dict[str, Any]] = []
        tool_context = ToolExecutionContext(
            session_id=state["session_id"],
            turn_id=state["turn_id"],
            trace_id=state["trace_id"],
            user_message=state["user_message"],
            normalized_input=state["normalized_input"],
            context_bundle=state["context_bundle"],
            selected_agent_key=state["selected_agent_key"],
            selected_model_key=state["selected_model_key"],
        )

        for raw_tool_call in state["model_tool_calls"]:
            if state.get("interrupt_requested"):
                append_graph_event(
                    state,
                    "tool.execution_skipped",
                    "tool_executor",
                    "Interrupt was requested before the next tool call could start.",
                    tool_key=str(raw_tool_call.get("name", "")),
                    interrupt_reason=state.get("interrupt_reason", ""),
                )
                break
            tool_call = ModelToolCall.model_validate(raw_tool_call)
            execution_record = await _resolve_tool_call(
                state=state,
                tool_call=tool_call,
                tool_registry=tool_registry,
                permission_service=permission_service,
                tool_runtime_service=tool_runtime_service,
                tool_job_service=tool_job_service,
                tool_context=tool_context,
            )
            tool_results.append(execution_record["tool_result"])
            if execution_record["tool_message"]:
                new_tool_messages.append(execution_record["tool_message"])
            if execution_record["approval"]:
                pending_approvals.append(execution_record["approval"])

        state["tool_results"] = tool_results
        state["tool_messages"] = [*prior_tool_messages, *new_tool_messages]
        state["worker_dispatches"] = _collect_worker_dispatches(tool_results)
        state["pending_approvals"] = pending_approvals

        if pending_approvals:
            state["pending_turn"] = {
                "turn_id": state["turn_id"],
                "trace_id": state["trace_id"],
                "user_message": state["user_message"],
                "normalized_input": state["normalized_input"],
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
                "model_visible_tool_keys": state["model_visible_tool_keys"],
                "allowed_tool_keys": state["allowed_tool_keys"],
                "approval_required_tool_keys": state["approval_required_tool_keys"],
                "denied_tool_keys": state["denied_tool_keys"],
                "permission_decisions": state["permission_decisions"],
                "loop_iteration": state["loop_iteration"],
                "max_iterations": state["max_iterations"],
                "context_bundle": state["context_bundle"],
                "system_prompt": state["system_prompt"],
                "conversation_messages": [
                    *state["runtime_messages"],
                    state["assistant_tool_call_message"],
                ],
                "resume_tool_messages": new_tool_messages,
                "tool_messages": state["tool_messages"],
                "tool_results": tool_results,
                "worker_dispatches": state["worker_dispatches"],
                "pending_approval_ids": [item["id"] for item in pending_approvals],
            }
            append_graph_event(
                state,
                "graph.waiting_for_approval",
                "tool_executor",
                "Execution is paused until approval-gated tools are resolved.",
                approval_count=len(pending_approvals),
            )
        else:
            state["runtime_messages"] = [
                *state["runtime_messages"],
                state["assistant_tool_call_message"],
                *new_tool_messages,
            ]
            state["continue_loop"] = True
            append_graph_event(
                state,
                "graph.loop_prepared",
                "tool_executor",
                "Tool results were appended and the runtime will re-enter the model loop.",
                loop_iteration=state["loop_iteration"],
                tool_result_count=len(tool_results),
            )

        return state

    return tool_executor


async def _resolve_tool_call(
    state: AgentGraphState,
    tool_call: ModelToolCall,
    tool_registry: ToolRegistry,
    permission_service: PermissionService,
    tool_runtime_service: ToolRuntimeService,
    tool_job_service: ToolJobService | None,
    tool_context: ToolExecutionContext,
) -> dict[str, Any]:
    permission_decision = _find_permission_decision(state, tool_call.name)
    try:
        tool = tool_registry.get(tool_call.name)
    except KeyError:
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool_call.name,
            tool_name=tool_call.name,
            status="failed",
            summary=f"Model requested unknown tool '{tool_call.name}'.",
            input=tool_call.arguments,
            output={"error": "unknown_tool"},
        )
        append_graph_event(
            state,
            "tool.execution_failed",
            "tool_executor",
            f"Model requested unknown tool '{tool_call.name}'.",
            tool_key=tool_call.name,
            status="failed",
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if tool.key in state.get("denied_tool_keys", []):
        denial_reason = str(
            (permission_decision or {}).get("reason")
            or f"Tool '{tool.name}' is denied by the current permission policy."
        )
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="denied",
            summary=denial_reason,
            input=tool_call.arguments,
            output={
                "error": "permission_denied",
                "permission_behavior": "deny",
                "permission_reason": denial_reason,
                "permission_source": (permission_decision or {}).get("source", "static_policy"),
                "permission_visibility": (permission_decision or {}).get("visibility", "hidden"),
                "permission_reason_code": (permission_decision or {}).get("reason_code", "restricted_default_deny"),
                "permission_policy_key": (permission_decision or {}).get("policy_key", "permission_level.restricted"),
            },
        )
        append_graph_event(
            state,
            "tool.execution_denied",
            "tool_executor",
            denial_reason,
            tool_key=tool.key,
            tool_name=tool.name,
            permission_source=(permission_decision or {}).get("source", "static_policy"),
            permission_reason=denial_reason,
            permission_reason_code=(permission_decision or {}).get("reason_code", "restricted_default_deny"),
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if not tool_registry.has_handler_binding(tool.key) and tool.permission_level == "safe":
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="failed",
            summary=f"Tool '{tool.key}' is registered but has no runtime handler binding yet.",
            input=tool_call.arguments,
            output={"error": "missing_handler_binding"},
        )
        append_graph_event(
            state,
            "tool.execution_failed",
            "tool_executor",
            f"Tool '{tool.key}' has no runtime handler binding.",
            tool_key=tool.key,
            status="failed",
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if tool.key in state["approval_required_tool_keys"]:
        reason = str(
            (permission_decision or {}).get("reason")
            or (
                f"Tool '{tool.name}' requires explicit approval before execution "
                f"in {state['session_mode']} mode."
            )
        )
        permission_source = str((permission_decision or {}).get("source") or "static_policy")
        approval_job_id = None
        if tool_job_service is not None:
            approval_job = await tool_job_service.create_job(
                tool=tool,
                call_id=tool_call.id,
                session_id=state["session_id"],
                turn_id=state["turn_id"],
                trace_id=state["trace_id"],
                input_payload=tool_call.arguments,
                metadata={
                    "phase": "approval_pending",
                    "selected_agent_key": state["selected_agent_key"],
                    "selected_model_key": state["selected_model_key"],
                    "permission_behavior": "ask",
                    "permission_source": permission_source,
                    "permission_reason": reason,
                    "permission_visibility": str((permission_decision or {}).get("visibility") or "visible"),
                    "permission_reason_code": str((permission_decision or {}).get("reason_code") or "approval_required_default"),
                    "permission_policy_key": str((permission_decision or {}).get("policy_key") or "permission_level.ask"),
                },
            )
            approval_job_id = approval_job.id
            await tool_job_service.mark_waiting_approval(approval_job.id, summary=reason)
        approval = permission_service.create_approval_request(
            session_id=state["session_id"],
            tool=tool,
            reason=reason,
            metadata={
                "turn_id": state["turn_id"],
                "call_id": tool_call.id,
                "arguments": tool_call.arguments,
                "selected_agent_key": state["selected_agent_key"],
                "selected_model_key": state["selected_model_key"],
                "tool_job_id": approval_job_id,
                "permission_behavior": "ask",
                "permission_source": permission_source,
                "permission_reason": reason,
                "permission_visibility": str((permission_decision or {}).get("visibility") or "visible"),
                "permission_reason_code": str((permission_decision or {}).get("reason_code") or "approval_required_default"),
                "permission_policy_key": str((permission_decision or {}).get("policy_key") or "permission_level.ask"),
            },
        )
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            job_id=approval_job_id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="waiting_approval",
            summary=reason,
            input=tool_call.arguments,
            output={},
            approval_id=approval.id,
        )
        append_graph_event(
            state,
            "tool.execution_blocked",
            "tool_executor",
            f"Tool '{tool.key}' is waiting for approval.",
            tool_key=tool.key,
            tool_name=tool.name,
            approval_id=approval.id,
            arguments=tool_call.arguments,
            permission_source=permission_source,
            permission_reason_code=str((permission_decision or {}).get("reason_code") or "approval_required_default"),
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": None,
            "approval": approval.model_dump(mode="python"),
        }

    append_graph_event(
        state,
        "tool.execution_started",
        "tool_executor",
        f"Tool '{tool.key}' execution started.",
        tool_key=tool.key,
        arguments=tool_call.arguments,
    )
    result = await tool_runtime_service.execute(tool, tool_call, tool_context)
    event_type = "tool.execution_completed" if result.status == "completed" else "tool.execution_failed"
    append_graph_event(
        state,
        event_type,
        "tool_executor",
        f"Tool '{tool.key}' finished with status '{result.status}'.",
        tool_key=tool.key,
        status=result.status,
        summary=result.summary,
    )
    return {
        "tool_result": result.model_dump(mode="python"),
        "tool_message": build_tool_message(result),
        "approval": None,
    }


def build_tool_message(record: ToolExecutionRecord) -> dict[str, Any]:
    payload = {
        "status": record.status,
        "summary": record.summary,
        "output": record.output,
    }
    return {
        "role": "tool",
        "tool_call_id": record.call_id,
        "name": record.tool_key,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _collect_worker_dispatches(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dispatches: list[dict[str, Any]] = []
    for item in tool_results:
        output = item.get("output", {})
        workers = output.get("workers")
        if not isinstance(workers, list):
            continue
        dispatches.extend(worker for worker in workers if isinstance(worker, dict))
    return dispatches


def _find_permission_decision(state: AgentGraphState, tool_key: str) -> dict[str, Any] | None:
    for item in state.get("permission_decisions", []):
        if item.get("tool_key") == tool_key:
            return item
    return None
