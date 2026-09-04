from __future__ import annotations

import json
import re
from typing import Any

from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
from src.application.security.approval_scope_service import ApprovalScopeService
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.application.security.prompt_injection_policy import PromptInjectionPolicy
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.core.safety_gate import SafetyGate, SecurityContext, SafetyDecision
from src.graph.state import AgentGraphState
from src.infrastructure.storage_utils import make_json_safe
from src.registry.tools import ToolRegistry
from src.registry.skills import SkillRegistry
from src.runtime.execution_logging import append_graph_event
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord
from src.schemas.session import MessageKind, RuntimeMode, SessionMode


def build_tool_executor_node(
    tool_registry: ToolRegistry,
    permission_service: PermissionService,
    tool_runtime_service: ToolRuntimeService,
    tool_job_service: ToolJobService | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_runtime_service: SkillRuntimeService | None = None,
    tool_message_max_chars: int = 24000,
    safety_gate: SafetyGate | None = None,
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

        if safety_gate is not None:
            security_ctx = SecurityContext(
                session_id=state["session_id"],
                agent_key=state.get("selected_agent_key", ""),
                mode_key=state.get("mode_key", ""),
                transcript_preview=state.get("user_message", "")[:500],
                user_message=state.get("user_message", ""),
            )
            for tool_call in state.get("model_tool_calls", []):
                verdict = await safety_gate.evaluate(
                    tool_call={"tool_name": tool_call.get("name", ""), "arguments": tool_call.get("arguments", {})},
                    context=security_ctx,
                )
                if verdict.decision == SafetyDecision.DENY:
                    append_graph_event(
                        state,
                        "safety.tool_denied",
                        "safety_gate",
                        f"Tool call '{tool_call.get('name')}' denied by SafetyGate.",
                        tool_name=tool_call.get("name", ""),
                        reason=verdict.reason,
                        risk_level=verdict.risk_level,
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
                skill_registry=skill_registry,
                skill_runtime_service=skill_runtime_service,
            )
            _apply_tool_output_restrictions(
                state=state,
                execution_record=execution_record,
                tool_registry=tool_registry,
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
                "deferred_tool_keys": state["deferred_tool_keys"],
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
            message_budget = resolve_tool_message_budget(state, tool_message_max_chars)
            state["runtime_messages"] = [
                *state["runtime_messages"],
                state["assistant_tool_call_message"],
                *[apply_tool_message_budget(item, message_budget) for item in new_tool_messages],
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
    skill_registry: SkillRegistry | None = None,
    skill_runtime_service: SkillRuntimeService | None = None,
) -> dict[str, Any]:
    state.setdefault("event_log", [])
    permission_decision = _find_permission_decision(state, tool_call.name)
    try:
        tool = tool_registry.get(tool_call.name)
    except KeyError:
        unknown_output = _build_unknown_tool_output(state, tool_call.name)
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool_call.name,
            tool_name=tool_call.name,
            status="failed",
            summary=str(unknown_output.get("summary") or f"Model requested unknown tool '{tool_call.name}'."),
            input=tool_call.arguments,
            output=unknown_output,
        )
        append_graph_event(
            state,
            "tool.execution_failed",
            "tool_executor",
            result.summary,
            tool_key=tool_call.name,
            call_id=tool_call.id,
            status="failed",
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if tool.key not in state.get("available_tool_keys", []):
        reason = f"Tool '{tool.name}' was not exposed to the selected agent for this turn."
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="denied",
            summary=reason,
            input=tool_call.arguments,
            output={
                "error": "tool_not_exposed",
                "permission_reason": reason,
                "permission_reason_code": "tool_not_exposed_to_agent",
            },
        )
        append_graph_event(
            state,
            "tool.execution_denied",
            "tool_executor",
            reason,
            tool_key=tool.key,
            call_id=tool_call.id,
            permission_reason_code="tool_not_exposed_to_agent",
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
            call_id=tool_call.id,
            permission_source=(permission_decision or {}).get("source", "static_policy"),
            permission_reason=denial_reason,
            permission_reason_code=(permission_decision or {}).get("reason_code", "restricted_default_deny"),
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    execution_safety = ExecutionSafetyPolicy().evaluate_tool_call(
        tool=tool,
        arguments=tool_call.arguments,
        active_mode_key=str(state.get("mode_key") or "default"),
        context=state.get("context_bundle") or {},
    )
    if execution_safety.behavior == "deny":
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="denied",
            summary=execution_safety.reason,
            input=tool_call.arguments,
            output={
                "error": "execution_safety_denied",
                "permission_reason": execution_safety.reason,
                "permission_reason_code": execution_safety.reason_code,
            },
        )
        append_graph_event(
            state,
            "tool.execution_denied",
            "tool_executor",
            execution_safety.reason,
            tool_key=tool.key,
            call_id=tool_call.id,
            permission_reason_code=execution_safety.reason_code,
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if tool.key == "skill":
        append_graph_event(
            state,
            "tool.execution_started",
            "tool_executor",
            f"Tool '{tool.key}' execution started.",
            tool_key=tool.key,
            tool_name=tool.name,
            call_id=tool_call.id,
            arguments=tool_call.arguments,
        )
        result = _run_skill_loader(
            state=state,
            tool_call=tool_call,
            tool_registry=tool_registry,
            permission_service=permission_service,
            skill_registry=skill_registry,
            skill_runtime_service=skill_runtime_service,
        )
        append_graph_event(
            state,
            "tool.execution_completed" if result.status == "completed" else "tool.execution_failed",
            "tool_executor",
            f"Tool '{tool.key}' finished with status '{result.status}'.",
            tool_key=tool.key,
            tool_name=tool.name,
            call_id=tool_call.id,
            status=result.status,
            summary=result.summary,
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    validation_errors = _validate_tool_input(tool.input_schema, tool_call.arguments)
    if validation_errors:
        result = ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="failed",
            summary=(
                f"Tool '{tool.key}' arguments are invalid. Ask the user for missing or invalid values "
                "instead of retrying with guessed data."
            ),
            input=tool_call.arguments,
            output={"error": "invalid_tool_arguments", "validation_errors": validation_errors},
        )
        append_graph_event(
            state,
            "tool.execution_failed",
            "tool_executor",
            f"Tool '{tool.key}' arguments failed validation.",
            tool_key=tool.key,
            tool_name=tool.name,
            call_id=tool_call.id,
            status="failed",
            summary=result.summary,
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
            tool_name=tool.name,
            call_id=tool_call.id,
            status="failed",
        )
        return {
            "tool_result": result.model_dump(mode="python"),
            "tool_message": build_tool_message(result),
            "approval": None,
        }

    if tool.key in state["approval_required_tool_keys"] or execution_safety.behavior == "ask":
        reason = str(
            execution_safety.reason
            or (permission_decision or {}).get("reason")
            or (
                f"Tool '{tool.name}' requires explicit approval before execution "
                f"in {state['session_mode']} mode."
            )
        )
        execution_policy_approval = execution_safety.behavior == "ask"
        permission_source = (
            "execution_safety_policy"
            if execution_policy_approval
            else str((permission_decision or {}).get("source") or "static_policy")
        )
        permission_reason_code = (
            execution_safety.reason_code
            if execution_policy_approval
            else str((permission_decision or {}).get("reason_code") or "approval_required_default")
        )
        permission_policy_key = (
            "execution_safety_policy.tool_call"
            if execution_policy_approval
            else str((permission_decision or {}).get("policy_key") or "permission_level.ask")
        )
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
                    "permission_reason_code": permission_reason_code,
                    "permission_policy_key": permission_policy_key,
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
                "permission_reason_code": permission_reason_code,
                "permission_policy_key": permission_policy_key,
                "approval_mode_key": (
                    "security_tool_bootstrap"
                    if tool.key == "security-tool-bootstrap"
                    else str(state.get("mode_key") or "default")
                ),
                "approval_scope_hash": ApprovalScopeService().build_hash(
                    mode_key=(
                        "security_tool_bootstrap"
                        if tool.key == "security-tool-bootstrap"
                        else str(state.get("mode_key") or "default")
                    ),
                    tool_key=tool.key,
                    arguments=tool_call.arguments,
                    context=state.get("context_bundle") or {},
                ),
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
            call_id=tool_call.id,
            tool_job_id=approval_job_id,
            approval_id=approval.id,
            arguments=tool_call.arguments,
            permission_source=permission_source,
            permission_reason_code=permission_reason_code,
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
        tool_name=tool.name,
        call_id=tool_call.id,
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
        tool_name=tool.name,
        call_id=tool_call.id,
        tool_job_id=result.job_id,
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
        "content_provenance": "tool_output",
        "instruction_authority": "none",
    }
    return {
        "role": "tool",
        "tool_call_id": record.call_id,
        "name": record.tool_key,
        "content": json.dumps(make_json_safe(payload), ensure_ascii=False),
    }


TOOL_MESSAGE_MIN_BUDGET_CHARS = 4000


def resolve_tool_message_budget(state: AgentGraphState, base_max_chars: int) -> int:
    """Tighten the tool message budget as the model context fills up.

    The latest provider-reported prompt_tokens is compared against the
    configured model context window; the closer we are to the window, the
    smaller the budget for tool result messages fed back into the loop.
    """
    usage = state.get("turn_token_usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    context_window = int(state.get("model_context_window") or 0)
    if prompt_tokens <= 0 or context_window <= 0:
        return base_max_chars
    ratio = prompt_tokens / context_window
    if ratio <= 0.6:
        return base_max_chars
    if ratio <= 0.75:
        return max(TOOL_MESSAGE_MIN_BUDGET_CHARS, base_max_chars // 2)
    return max(TOOL_MESSAGE_MIN_BUDGET_CHARS, base_max_chars // 4)


def apply_tool_message_budget(message: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Downgrade an oversized tool message to a structured digest.

    The full output is untouched in tool_results / tool jobs storage; only
    the model-visible copy inside runtime_messages is reduced.
    """
    content = str(message.get("content") or "")
    if len(content) <= max_chars:
        return message
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        digest_payload = {
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "output_digest": _digest_value(payload.get("output"), max_string_chars=max(200, max_chars // 20)),
            "truncated": True,
            "detail_hint": (
                "Full tool output exceeded the context budget and was replaced by this digest. "
                "The complete result is persisted in the session tool results."
            ),
        }
        content = json.dumps(make_json_safe(digest_payload), ensure_ascii=False)
        if len(content) <= max_chars:
            return {**message, "content": content}
    return {**message, "content": content[: max(0, max_chars - 16)] + "...(truncated)"}


def _digest_value(value: Any, max_string_chars: int, depth: int = 0) -> Any:
    if depth >= 6:
        return "...(depth truncated)"
    if isinstance(value, str):
        if len(value) > max_string_chars:
            return value[:max_string_chars] + f"...(+{len(value) - max_string_chars} chars)"
        return value
    if isinstance(value, dict):
        return {
            str(key): _digest_value(item, max_string_chars, depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, list):
        digest = [_digest_value(item, max_string_chars, depth + 1) for item in value[:20]]
        if len(value) > 20:
            digest.append(f"...(+{len(value) - 20} items)")
        return digest
    return value


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


def _run_skill_loader(
    *,
    state: AgentGraphState,
    tool_call: ModelToolCall,
    tool_registry: ToolRegistry,
    permission_service: PermissionService,
    skill_registry: SkillRegistry | None,
    skill_runtime_service: SkillRuntimeService | None,
) -> ToolExecutionRecord:
    if skill_registry is None or skill_runtime_service is None:
        return ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key="skill",
            tool_name="Skill Loader",
            status="failed",
            summary="Skill runtime is not configured.",
            input=tool_call.arguments,
            output={"error": "skill_runtime_unavailable"},
        )

    arguments = dict(tool_call.arguments or {})
    action = str(arguments.get("action") or "load").strip().lower()
    requested_keys = [str(item).strip() for item in arguments.get("skill_keys", []) if str(item).strip()]
    query = str(arguments.get("query") or "").strip()
    safety_assessment = dict(state.get("context_bundle", {}).get("safety_assessment") or {})
    if action != "search" and "do_not_expand_tool_access" in safety_assessment.get("restrictions", []):
        return ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key="skill",
            tool_name="Skill Loader",
            status="denied",
            summary="Skill loading is frozen because untrusted content contained control-like instructions.",
            input=arguments,
            output={
                "error": "tool_expansion_frozen",
                "reason_code": "indirect_prompt_injection_restriction",
            },
        )
    catalog = skill_registry.list()

    if action == "read_reference":
        skill_key = requested_keys[0] if requested_keys else ""
        reference_path = str(arguments.get("reference_path") or "").strip()
        if skill_key not in state.get("resolved_skill_keys", []):
            return ToolExecutionRecord(
                call_id=tool_call.id,
                tool_key="skill",
                tool_name="Skill Loader",
                status="failed",
                summary="Load the Skill before reading one of its references.",
                input=arguments,
                output={"error": "skill_not_loaded", "skill_key": skill_key},
            )
        try:
            reference_content = skill_runtime_service.read_reference(skill_key, reference_path)
        except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
            return ToolExecutionRecord(
                call_id=tool_call.id,
                tool_key="skill",
                tool_name="Skill Loader",
                status="failed",
                summary=f"Unable to read Skill reference: {exc}",
                input=arguments,
                output={"error": "invalid_skill_reference", "detail": str(exc)},
            )
        return ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key="skill",
            tool_name="Skill Loader",
            status="completed",
            summary=f"Loaded Skill reference '{reference_path}'.",
            input=arguments,
            output={
                "skill_key": skill_key,
                "reference_path": reference_path,
                "reference_content": reference_content,
            },
        )

    matched = skill_registry.get_many(requested_keys) or _match_skills(catalog, query)
    matched_payload = [
        {
            "key": item.key,
            "name": item.name,
            "description": item.description,
            "tags": item.tags,
            "tool_keys": item.tool_keys,
        }
        for item in matched
    ]

    if action == "search":
        return ToolExecutionRecord(
            call_id=tool_call.id,
            tool_key="skill",
            tool_name="Skill Loader",
            status="completed",
            summary=f"Found {len(matched_payload)} matching Skill(s).",
            input=arguments,
            output={"matched_skills": matched_payload, "loaded_skills": [], "loaded_tools": []},
        )

    tool_keys = [tool_key for item in matched for tool_key in item.tool_keys]
    if query:
        tool_keys.extend(_match_deferred_tools(tool_registry, state.get("deferred_tool_keys", []), query))
    tool_keys = list(dict.fromkeys(tool_keys))
    valid_tool_keys = [key for key in tool_keys if key in state.get("deferred_tool_keys", [])]
    evaluation = permission_service.evaluate(
        policy_context=_permission_context_from_state(state),
        tools=tool_registry.get_many(valid_tool_keys),
    )

    state["available_tool_keys"] = list(dict.fromkeys([*state.get("available_tool_keys", []), *valid_tool_keys]))
    state["deferred_tool_keys"] = [key for key in state.get("deferred_tool_keys", []) if key not in valid_tool_keys]
    state["allowed_tool_keys"] = list(dict.fromkeys([*state.get("allowed_tool_keys", []), *evaluation.allowed_tool_keys]))
    state["approval_required_tool_keys"] = list(
        dict.fromkeys([*state.get("approval_required_tool_keys", []), *evaluation.approval_required_tool_keys])
    )
    state["denied_tool_keys"] = list(dict.fromkeys([*state.get("denied_tool_keys", []), *evaluation.denied_tool_keys]))
    state["model_visible_tool_keys"] = list(
        dict.fromkeys([*state.get("model_visible_tool_keys", []), *evaluation.model_visible_tool_keys])
    )
    state["permission_decisions"] = [
        *state.get("permission_decisions", []),
        *[item.to_payload() for item in evaluation.decisions],
    ]
    loaded_skill_keys = [item.key for item in matched]
    state["requested_skill_keys"] = list(dict.fromkeys([*state.get("requested_skill_keys", []), *loaded_skill_keys]))
    state["resolved_skill_keys"] = list(dict.fromkeys([*state.get("resolved_skill_keys", []), *loaded_skill_keys]))
    instructions = skill_runtime_service.build_prompt_blocks(
        loaded_skill_keys,
        include_content=True,
    )
    state["skill_prompt_blocks"] = list(dict.fromkeys([*state.get("skill_prompt_blocks", []), *instructions]))

    return ToolExecutionRecord(
        call_id=tool_call.id,
        tool_key="skill",
        tool_name="Skill Loader",
        status="completed",
        summary=(
            f"Loaded {len(loaded_skill_keys)} Skill(s) and exposed "
            f"{len(evaluation.model_visible_tool_keys)} permitted tool(s) for the next turn."
        ),
        input=arguments,
        output={
            "matched_skills": matched_payload,
            "loaded_skills": loaded_skill_keys,
            "loaded_tools": evaluation.model_visible_tool_keys,
            "denied_tools": evaluation.denied_tool_keys,
            "instructions": instructions,
        },
    )


def _apply_tool_output_restrictions(
    *,
    state: AgentGraphState,
    execution_record: dict[str, Any],
    tool_registry: ToolRegistry,
) -> None:
    tool_result = execution_record.get("tool_result")
    if not isinstance(tool_result, dict):
        return
    output = tool_result.get("output")
    security = output.get("_security") if isinstance(output, dict) else None
    signals = security.get("indirect_injection_signals") if isinstance(security, dict) else None
    if not isinstance(signals, list) or not signals:
        return

    context_bundle = dict(state.get("context_bundle") or {})
    safety = dict(context_bundle.get("safety_assessment") or {})
    assessment = PromptInjectionPolicy().assess(output, "tool_output")
    safety = PromptInjectionPolicy().merge_into_safety(safety, [assessment])
    context_bundle["safety_assessment"] = safety
    state["context_bundle"] = context_bundle

    required_capabilities = set(context_bundle.get("required_capabilities") or [])
    state["deferred_tool_keys"] = [
        key
        for key in state.get("deferred_tool_keys", [])
        if required_capabilities.intersection(tool_registry.get(key).capability_keys)
    ]
    append_graph_event(
        state,
        "security.indirect_prompt_injection_detected",
        "tool_executor",
        "Tool output contained control-like instructions and dynamic tool expansion was frozen.",
        tool_key=tool_result.get("tool_key"),
        signal_keys=list(dict.fromkeys(str(item) for item in signals)),
        remaining_deferred_tool_count=len(state["deferred_tool_keys"]),
    )


def _match_skills(catalog: list[Any], query: str) -> list[Any]:
    terms = _search_terms(query)
    if not terms:
        return []
    scored: list[tuple[int, Any]] = []
    for skill in catalog:
        haystack = " ".join([skill.key, skill.name, skill.summary, skill.description, *skill.tags]).lower()
        score = sum(3 if term in skill.key.lower() else 1 for term in terms if term in haystack)
        if score:
            scored.append((score, skill))
    return [item for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].key))[:3]]


def _match_deferred_tools(tool_registry: ToolRegistry, deferred_keys: list[str], query: str) -> list[str]:
    terms = _search_terms(query)
    scored: list[tuple[int, str]] = []
    for tool in tool_registry.get_many(deferred_keys):
        haystack = " ".join([tool.key, tool.name, tool.description, tool.category, *tool.tags]).lower()
        score = sum(3 if term in tool.key.lower() else 1 for term in terms if term in haystack)
        if score:
            scored.append((score, tool.key))
    return [key for _, key in sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:12]]


def _search_terms(query: str) -> list[str]:
    return [item for item in re.split(r"[^\w\u4e00-\u9fff]+", query.lower()) if len(item) > 1]


def _permission_context_from_state(state: AgentGraphState) -> PermissionPolicyContext:
    input_envelope = dict(state.get("context_bundle", {}).get("input_envelope") or {})
    input_routing = dict(state.get("context_bundle", {}).get("input_routing") or {})
    safety_assessment = dict(state.get("context_bundle", {}).get("safety_assessment") or {})
    return PermissionPolicyContext(
        session_mode=SessionMode(state["session_mode"]),
        runtime_mode=RuntimeMode(state["runtime_mode"]),
        selected_agent_key=state["selected_agent_key"],
        message_kind=MessageKind(input_envelope.get("message_kind", MessageKind.user_input.value)),
        submit_mode=str(input_envelope.get("submit_mode") or "immediate"),
        execution_lane=str(input_routing.get("execution_lane") or "conversation_turn"),
        source=str(input_envelope.get("source") or "session.send_message"),
        active_mode_key=str(state.get("mode_key") or "default"),
        workflow_mode_key=str(state.get("mode_key") or "default"),
        safety_decision=str(safety_assessment.get("decision") or "allow"),
        safety_risk_level=str(safety_assessment.get("risk_level") or "low"),
        authorization_status=str(safety_assessment.get("authorization_status") or "not_required"),
        environment=str(safety_assessment.get("environment") or "unknown"),
    )


def _validate_tool_input(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    properties = dict(schema.get("properties") or {})
    for key in schema.get("required") or []:
        value = arguments.get(key)
        if key not in arguments or value is None or value == "":
            errors.append(f"Missing required field: {key}")
    for key, value in arguments.items():
        rule = properties.get(key)
        if not isinstance(rule, dict) or value is None:
            continue
        expected = rule.get("type")
        if expected == "array" and not isinstance(value, list):
            errors.append(f"Field '{key}' must be an array.")
            continue
        if expected == "string" and not isinstance(value, str):
            errors.append(f"Field '{key}' must be a string.")
            continue
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"Field '{key}' must be an integer.")
            continue
        if expected == "boolean" and not isinstance(value, bool):
            errors.append(f"Field '{key}' must be a boolean.")
            continue
        if expected == "array" and len(value) < int(rule.get("minItems") or 0):
            errors.append(f"Field '{key}' must contain at least {rule.get('minItems')} item(s).")
        item_rule = rule.get("items") if expected == "array" else None
        if isinstance(item_rule, dict) and item_rule.get("format") == "email":
            invalid = [item for item in value if not _is_email_address(item)]
            if invalid:
                errors.append(f"Field '{key}' contains invalid email address values.")
    return errors


def _is_email_address(value: Any) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(value or "").strip()))


def _build_unknown_tool_output(state: AgentGraphState, requested_name: str) -> dict[str, Any]:
    available_skills = [
        item
        for item in dict(state.get("context_bundle") or {}).get("available_skills", [])
        if isinstance(item, dict)
    ]
    skill_catalog = {
        str(item.get("key") or "").strip(): item
        for item in available_skills
        if str(item.get("key") or "").strip()
    }
    model_visible_tools = [str(item).strip() for item in state.get("model_visible_tool_keys", []) if str(item).strip()]

    suggested_tools = _suggest_tool_names(requested_name, model_visible_tools)
    skill_match = skill_catalog.get(requested_name)

    if skill_match is not None:
        return {
            "error": "unknown_tool",
            "error_kind": "skill_invoked_as_tool",
            "skill_key": requested_name,
            "skill_name": str(skill_match.get("name") or requested_name),
            "summary": (
                f"'{requested_name}' is a skill, not a callable tool. "
                "Call the registered 'skill' tool with this key to load it."
            ),
            "suggested_tools": suggested_tools,
            "model_visible_tools": model_visible_tools,
        }

    return {
        "error": "unknown_tool",
        "error_kind": "unregistered_tool_name",
        "requested_tool": requested_name,
        "summary": f"Model requested unknown tool '{requested_name}'.",
        "suggested_tools": suggested_tools,
        "model_visible_tools": model_visible_tools,
    }


def _suggest_tool_names(requested_name: str, model_visible_tools: list[str]) -> list[str]:
    del requested_name
    return list(dict.fromkeys(["skill", *model_visible_tools]))[:8]
