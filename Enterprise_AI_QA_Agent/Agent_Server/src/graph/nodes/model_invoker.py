from __future__ import annotations

from src.application.model_runtime_service import ModelRuntimeService
from src.graph.state import AgentGraphState
from src.registry.agents import AgentRegistry
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event, summarize_messages, truncate_text
from src.schemas.model_config import ModelInvocationRequest


def build_model_invoker_node(
    model_runtime_service: ModelRuntimeService,
    tool_registry: ToolRegistry,
    agent_registry: AgentRegistry,
):
    async def model_invoker(state: AgentGraphState) -> AgentGraphState:
        append_graph_event(
            state,
            "graph.execution_started",
            "model_invoker",
            "Runtime is preparing the Claude Code style model call stage.",
            selected_agent=state["selected_agent_key"],
            selected_model=state["selected_model_key"],
            allowed_tool_count=len(state["allowed_tool_keys"]),
            approval_required_count=len(state["approval_required_tool_keys"]),
            loop_iteration=state["loop_iteration"],
        )

        if not state["system_prompt"]:
            available_agent_keys = ", ".join(agent.key for agent in agent_registry.list()) or "none"
            prompt_sections = [
                (
                    f"You are the '{state['selected_agent_name']}' runtime inside Enterprise AI QA Agent. "
                    f"Operate in {state['session_mode']} session mode and {state['runtime_mode']} runtime mode. "
                    "Follow a Claude Code style execution discipline: reason clearly, stay tool-aware, and summarize actionable next steps. "
                    "When a registered tool can improve the answer, call the tool instead of only describing what it would do. "
                    "If the user asks about conversation history, prior questions, session counts, or wants a session report, prefer the 'session-history' tool over reconstructing history from memory alone."
                ),
            ]
            if state["selected_agent_key"] == "coordinator":
                prompt_sections.append(
                    "Coordinator mode contract:\n"
                    "- You are the coordinator, not the worker.\n"
                    "- Use 'subagent-dispatch' to launch background workers for research, implementation, verification, or reporting.\n"
                    "- Worker results return later as user-role XML messages starting with <task-notification>.\n"
                    "- Do not thank workers or talk to them directly. Synthesize their result for the user and decide the next dispatch.\n"
                    "- Keep the coordinator focused on orchestration, task breakdown, and result integration.\n"
                    f"- Valid agent keys for subagent-dispatch are strictly limited to: {available_agent_keys}.\n"
                    "- Never invent agent keys that are not registered.\n"
                    "- If dispatch returns 'Unknown agent', treat it as a terminal configuration error for this turn and stop retrying fake alternatives.\n"
                    "- When the user greets you or asks who you are, start your first sentence exactly with: "
                    "'你好！我是御策天检 QA Agent，你可以唤我为 小天，是企业级AI质量保障系统的协调器，负责调度和管理测试任务，我可以帮你：' "
                    "Then keep the rest of the response style and capability explanation consistent with the existing coordinator behavior."
                )
            if state["skill_prompt_blocks"]:
                prompt_sections.append("Active skill directives:\n" + "\n".join(state["skill_prompt_blocks"]))
            if state["memory_prompt_blocks"]:
                prompt_sections.append("Relevant persistent memory:\n" + "\n".join(state["memory_prompt_blocks"]))
            if state["mcp_prompt_blocks"]:
                prompt_sections.append("Available MCP runtimes:\n" + "\n".join(state["mcp_prompt_blocks"]))
            state["system_prompt"] = "\n\n".join(section.strip() for section in prompt_sections if section.strip())

        if not state["runtime_messages"]:
            user_message = (
                f"User request: {state['user_message']}\n\n"
                f"Normalized input: {state['normalized_input']}\n"
                f"Resolved skills: {', '.join(state['resolved_skill_keys']) or 'none'}\n"
                f"Allowed safe tools: {', '.join(state['allowed_tool_keys']) or 'none'}\n"
                f"Approval-gated tools: {', '.join(state['approval_required_tool_keys']) or 'none'}\n\n"
                "If you need evidence or retrieval, call the appropriate tools with explicit arguments."
            )
            state["runtime_messages"] = [{"role": "user", "content": user_message}]

        request_payload = ModelInvocationRequest(
            system_prompt=state["system_prompt"],
            messages=state["runtime_messages"],
            tools=tool_registry.build_model_tools(state["available_tool_keys"]),
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
            messages=summarize_messages(request_payload.messages),
            tool_candidates=",".join(state["available_tool_keys"]) or "none",
            loop_iteration=state["loop_iteration"],
        )

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
        )

        return state

    return model_invoker


def route_after_model_invoker(state: AgentGraphState) -> str:
    if state["model_tool_calls"]:
        return "tool_executor"
    return "finalizer"
