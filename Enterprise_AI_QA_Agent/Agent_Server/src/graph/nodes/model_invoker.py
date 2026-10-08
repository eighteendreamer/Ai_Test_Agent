from __future__ import annotations

from src.application.context.token_usage import TokenUsage, normalize_usage
from src.application.models.model_runtime_service import ModelRuntimeService
from src.graph.state import AgentGraphState
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event, summarize_messages, truncate_text
from src.schemas.model_config import ModelInvocationRequest, is_model_failure_summary
from src.schemas.prompting import PromptSection
from src.application.runtime.case_action_binding import action_input_schema


def build_model_invoker_node(
    model_runtime_service: ModelRuntimeService,
    tool_registry: ToolRegistry,
):
    async def model_invoker(state: AgentGraphState) -> AgentGraphState:
        # The graph re-enters after approval without incrementing loop_iteration.
        # Count logical model admissions at the send boundary; SDK attempts remain
        # a distinct transport metric. Old checkpoints have no cumulative counter.
        count = state.get("model_call_count")
        usage = state.get("turn_token_usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        context_window = int(state.get("model_context_window") or 0)
        context_exhausted = prompt_tokens > 0 and context_window > 0 and prompt_tokens >= context_window * 0.9
        if count is not None and (count >= state["max_iterations"] or context_exhausted):
            reason = "context_budget_exhausted" if context_exhausted else "max_iterations"
            state["model_tool_calls"] = []
            state["assistant_tool_call_message"] = {}
            state["model_response_summary"] = {"mode": "budget_exhausted"}
            state["model_response_text"] = (
                "The turn reached its model or context limit across tool loops and approvals. "
                "Existing tool evidence is preserved; no additional model request was sent."
            )
            state["final_response"] = state["model_response_text"]
            state["pending_turn"] = {}
            state["continue_loop"] = False
            state["termination_reason"] = reason
            append_graph_event(state, "graph." + ("context_budget_exhausted" if context_exhausted else "max_iterations_reached"), "model_invoker",
                "Runtime stopped before another model admission exceeded the original turn limit.",
                model_call_count=count, max_iterations=state["max_iterations"],
                prompt_tokens=prompt_tokens, context_window=context_window)
            return state
        model_visible_tool_keys = list(state.get("model_visible_tool_keys", state["available_tool_keys"]))
        append_graph_event(
            state,
            "graph.execution_started",
            "model_invoker",
            "Runtime is preparing the Claude Code style model call stage.",
            selected_agent=state["selected_agent_key"],
            selected_model=state["selected_model_key"],
            model_visible_tool_count=len(model_visible_tool_keys),
            allowed_tool_count=len(state["allowed_tool_keys"]),
            approval_required_count=len(state["approval_required_tool_keys"]),
            loop_iteration=state["loop_iteration"],
        )

        system_sections = [
            PromptSection.model_validate(item)
            for item in list(state.get("system_prompt_sections") or [])
        ]
        runtime_message_sections = [
            PromptSection.model_validate(item)
            for item in list(state.get("runtime_message_sections") or [])
        ]

        if not state["system_prompt"]:
            if not system_sections:
                system_sections = _build_fallback_system_sections(state)
                state["system_prompt_sections"] = [
                    item.model_dump(mode="python") for item in system_sections
                ]
            state["system_prompt"] = "\n\n".join(
                section.render() for section in system_sections if section.render()
            ).strip()

        if not state["runtime_messages"]:
            if not runtime_message_sections:
                runtime_message_sections = _build_fallback_runtime_sections(state)
                state["runtime_message_sections"] = [
                    item.model_dump(mode="python") for item in runtime_message_sections
                ]
            rendered_runtime_message = "\n\n".join(
                section.render() for section in runtime_message_sections if section.render()
            ).strip()
            state["runtime_messages"] = (
                [{"role": "user", "content": rendered_runtime_message}]
                if rendered_runtime_message
                else []
            )

        request_payload = ModelInvocationRequest(
            system_prompt=state["system_prompt"],
            messages=state["runtime_messages"],
            tools=[{**tool, "input_schema": action_input_schema(tool["input_schema"], state.get("context_bundle") or {})}
                for tool in tool_registry.build_model_tools(model_visible_tool_keys)],
            system_prompt_sections=system_sections,
            runtime_message_sections=runtime_message_sections,
            trace_context={
                "session_id": state["session_id"],
                "turn_id": state["turn_id"],
                "trace_id": state["trace_id"],
                "mode_key": state["mode_key"],
                "agent_key": state["selected_agent_key"],
                "project_id": str((state.get("context_bundle") or {}).get("project_id") or ""),
                "test_run_id": str(
                    (state.get("context_bundle") or {}).get("test_run_id") or ""
                ),
                "run_item_id": str(
                    (state.get("context_bundle") or {}).get("run_item_id")
                    or (state.get("context_bundle") or {}).get("test_run_item_id")
                    or ""
                ),
                "attempt_id": str(
                    (state.get("context_bundle") or {}).get("attempt_id") or ""
                ),
                "thread_id": str(
                    (state.get("context_bundle") or {}).get("thread_id")
                    or state["session_id"]
                ),
            },
        )
        state["model_request_payload"] = request_payload.model_dump(mode="python")
        state["model_response_summary"] = {}
        state["model_tool_calls"] = []
        state["assistant_tool_call_message"] = {}
        state["model_response_text"] = ""
        state["continue_loop"] = False
        state["termination_reason"] = ""

        append_graph_event(
            state,
            "model.request_prepared",
            "model_invoker",
            "Model request payload has been prepared.",
            model_key=state["selected_model_key"],
            model_name=state["selected_model_name"],
            model_provider=state["selected_model_provider"] or "unknown",
            system_prompt_preview=truncate_text(state["system_prompt"], 180),
            system_prompt_section_count=len(request_payload.system_prompt_sections),
            system_prompt_section_keys=",".join(
                section.key for section in request_payload.system_prompt_sections
            )
            or "none",
            runtime_message_section_count=len(request_payload.runtime_message_sections),
            runtime_message_section_keys=",".join(
                section.key for section in request_payload.runtime_message_sections
            )
            or "none",
            messages=summarize_messages(request_payload.messages),
            tool_candidates=",".join(model_visible_tool_keys) or "none",
            loop_iteration=state["loop_iteration"],
            model_call_count=count + 1 if count is not None else None,
            max_iterations=state["max_iterations"],
        )

        if count is not None:
            state["model_call_count"] = count + 1
        invocation_result = await model_runtime_service.invoke(
            state["selected_model_key"],
            request_payload,
        )
        state["model_response_summary"] = invocation_result.response_summary
        state["model_response_text"] = invocation_result.text
        state["model_tool_calls"] = [item.model_dump(mode="python") for item in invocation_result.tool_calls]
        state["assistant_tool_call_message"] = {
            "role": "assistant",
            "content": invocation_result.text,
            "tool_calls": state["model_tool_calls"],
        }

        latest_usage = normalize_usage(invocation_result.response_summary.get("usage"))
        accumulated_usage = TokenUsage.model_validate(
            state.get("turn_token_usage") or {}
        ).merge_latest(latest_usage)
        state["turn_token_usage"] = accumulated_usage.model_dump(mode="python")

        if is_model_failure_summary(invocation_result.response_summary):
            append_graph_event(
                state,
                "model.invocation_failed",
                "model_invoker",
                "Model invocation failed before an assistant response was generated.",
                model_key=state["selected_model_key"],
                response_mode=invocation_result.response_summary.get("mode", ""),
                error_type=invocation_result.response_summary.get("error_type", ""),
                status_code=invocation_result.response_summary.get("status_code"),
                loop_iteration=state["loop_iteration"],
            )
            return state

        append_graph_event(
            state,
            "model.response_received",
            "model_invoker",
            "Model response has been received by the runtime.",
            model_key=state["selected_model_key"],
            model_name=state["selected_model_name"],
            model_provider=state["selected_model_provider"] or "unknown",
            response_length=len(invocation_result.text),
            response_preview=truncate_text(invocation_result.text, 180),
            finish_reason=invocation_result.response_summary.get("finish_reason", ""),
            tool_call_count=len(invocation_result.tool_calls),
            loop_iteration=state["loop_iteration"],
            prompt_tokens=latest_usage.prompt_tokens,
            completion_tokens=latest_usage.completion_tokens,
            turn_total_tokens=accumulated_usage.total_tokens,
            model_call_count=state.get("model_call_count"),
        )

        return state

    return model_invoker


def route_after_model_invoker(state: AgentGraphState) -> str:
    if state["model_tool_calls"]:
        return "tool_executor"
    return "finalizer"


def _build_fallback_system_sections(state: AgentGraphState) -> list[PromptSection]:
    selected_agent_name = state["selected_agent_name"] or state["selected_agent_key"] or "agent"
    selected_agent_key = state["selected_agent_key"] or "agent"
    session_mode = state["session_mode"] or "normal"
    runtime_mode = state["runtime_mode"] or "interactive"
    return [
        PromptSection(
            key="identity",
            title="Identity",
            source="model_invoker.fallback",
            cache_scope="dynamic",
            priority=10,
            content=(
                f"You are the '{selected_agent_name}' runtime inside Enterprise AI QA Agent.\n"
                f"Primary role key: {selected_agent_key}.\n"
                f"Current session mode: {session_mode}.\n"
                f"Current runtime mode: {runtime_mode}."
            ),
        ),
        PromptSection(
            key="execution_contract",
            title="Execution Contract",
            source="model_invoker.fallback",
            cache_scope="dynamic",
            priority=20,
            content=(
                "Follow Claude Code style execution discipline.\n"
                "- Prefer registered tools when they improve correctness or evidence quality.\n"
                "- Respect permission gates and resumable execution boundaries.\n"
                "- Do not invent capabilities that are not registered in the runtime."
            ),
        ),
    ]


def _build_fallback_runtime_sections(state: AgentGraphState) -> list[PromptSection]:
    return [
        PromptSection(
            key="user_request",
            title="User Request",
            source="model_invoker.fallback",
            channel="runtime_message",
            cache_scope="ephemeral",
            priority=10,
            content=str(state["user_message"]).strip() or "(empty request)",
        ),
        PromptSection(
            key="tool_access",
            title="Tool Access",
            source="model_invoker.fallback",
            channel="runtime_message",
            cache_scope="dynamic",
            priority=20,
            content=(
                f"Allowed safe tools: {', '.join(state['allowed_tool_keys']) or 'none'}\n"
                f"Approval-gated tools: {', '.join(state['approval_required_tool_keys']) or 'none'}"
            ),
        ),
    ]
