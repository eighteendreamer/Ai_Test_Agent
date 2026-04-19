from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.schemas.prompting import PromptAssemblyResult, PromptSection


class PromptAssemblyService:
    def build_for_turn(
        self,
        state: Mapping[str, Any],
        available_agent_keys: Sequence[str],
    ) -> PromptAssemblyResult:
        system_sections = self._build_system_sections(
            state=state,
            available_agent_keys=available_agent_keys,
        )
        runtime_sections: list[PromptSection] = []
        runtime_messages = list(state.get("runtime_messages") or [])
        if not runtime_messages:
            runtime_sections = self._build_runtime_sections(state)
            runtime_messages = self._render_runtime_messages(runtime_sections)

        return PromptAssemblyResult(
            system_sections=system_sections,
            runtime_message_sections=runtime_sections,
            system_prompt=self._render_system_prompt(system_sections),
            runtime_messages=runtime_messages,
        )

    def _build_system_sections(
        self,
        state: Mapping[str, Any],
        available_agent_keys: Sequence[str],
    ) -> list[PromptSection]:
        selected_agent_name = str(state.get("selected_agent_name") or state.get("selected_agent_key") or "agent")
        selected_agent_key = str(state.get("selected_agent_key") or "agent")
        selected_model_key = str(state.get("selected_model_key") or "auto")
        session_mode = str(state.get("session_mode") or "normal")
        runtime_mode = str(state.get("runtime_mode") or "interactive")
        resolved_skills = list(state.get("resolved_skill_keys") or [])
        sections = [
            PromptSection(
                key="identity",
                title="Identity",
                source="prompt_assembly.base",
                cache_scope="static",
                priority=10,
                content=(
                    f"You are the '{selected_agent_name}' runtime inside Enterprise AI QA Agent.\n"
                    f"Primary role key: {selected_agent_key}.\n"
                    "Operate as a Claude Code style agent runtime rather than a generic chatbot."
                ),
            ),
            PromptSection(
                key="execution_contract",
                title="Execution Contract",
                source="prompt_assembly.base",
                cache_scope="static",
                priority=20,
                content=(
                    "Follow a Claude Code style execution discipline:\n"
                    "- Stay tool-aware and prefer executing registered tools over merely describing them.\n"
                    "- Keep answers grounded in runtime evidence, tool output, and persisted session context.\n"
                    "- Respect harness constraints: permission gate, checkpoint-ready execution, replayability, and verification.\n"
                    "- Never invent tools, agent keys, MCP servers, or capabilities that are not registered."
                ),
            ),
            PromptSection(
                key="mode_context",
                title="Session Mode",
                source="prompt_assembly.runtime",
                cache_scope="dynamic",
                priority=30,
                content=(
                    f"Current session mode: {session_mode}.\n"
                    f"Current runtime mode: {runtime_mode}.\n"
                    f"Selected model key: {selected_model_key}."
                ),
            ),
            PromptSection(
                key="history_and_tool_preference",
                title="Tool Preference",
                source="prompt_assembly.base",
                cache_scope="static",
                priority=40,
                content=(
                    "When a registered tool can improve accuracy or collect evidence, call the tool instead of only describing what it would do.\n"
                    "If the user asks about prior questions, conversation history, session counts, or wants a session report, prefer the session history tools over reconstructing history from memory alone."
                ),
            ),
        ]

        if selected_agent_key == "coordinator":
            sections.append(
                PromptSection(
                    key="coordinator_contract",
                    title="Coordinator Contract",
                    source="prompt_assembly.coordinator",
                    cache_scope="dynamic",
                    priority=50,
                    content=(
                        "You are the coordinator, not the worker.\n"
                        "- Use 'subagent-dispatch' to launch workers for research, implementation, verification, or reporting.\n"
                        "- Worker results return later as user-role XML messages beginning with <task-notification>.\n"
                        "- Do not thank workers or talk to them directly. Synthesize their result for the user and decide the next step.\n"
                        "- Keep the coordinator focused on orchestration, task decomposition, verification routing, and result integration.\n"
                        f"- Valid agent keys for subagent dispatch are limited to: {', '.join(available_agent_keys) or 'none'}.\n"
                        "- Never retry fabricated agent keys after an 'Unknown agent' failure.\n"
                        "- If the user greets you or asks who you are, introduce yourself in Chinese as the enterprise QA coordinator nicknamed '小天', then continue with your capabilities."
                    ),
                    metadata={"available_agent_keys": list(available_agent_keys)},
                )
            )

        skill_blocks = list(state.get("skill_prompt_blocks") or [])
        if skill_blocks:
            sections.append(
                PromptSection(
                    key="skills",
                    title="Active Skill Directives",
                    source="skills",
                    cache_scope="dynamic",
                    priority=60,
                    content="\n".join(skill_blocks),
                    metadata={"resolved_skill_keys": resolved_skills},
                )
            )

        observation_blocks = list(state.get("observation_prompt_blocks") or [])
        if observation_blocks:
            sections.append(
                PromptSection(
                    key="observations",
                    title="Historical Testing Observations",
                    source="observations",
                    cache_scope="dynamic",
                    priority=70,
                    content="\n".join(observation_blocks),
                    metadata={"observation_hit_count": len(state.get("observation_hits") or [])},
                )
            )

        memory_blocks = list(state.get("memory_prompt_blocks") or [])
        if memory_blocks:
            sections.append(
                PromptSection(
                    key="memory",
                    title="Relevant Persistent Memory",
                    source="memory",
                    cache_scope="dynamic",
                    priority=80,
                    content="\n".join(memory_blocks),
                    metadata={"memory_hit_count": len(state.get("memory_hits") or [])},
                )
            )

        mcp_blocks = list(state.get("mcp_prompt_blocks") or [])
        if mcp_blocks:
            sections.append(
                PromptSection(
                    key="mcp",
                    title="Available MCP Runtimes",
                    source="mcp",
                    cache_scope="dynamic",
                    priority=90,
                    content="\n".join(mcp_blocks),
                    metadata={"active_mcp_count": len(state.get("active_mcp_servers") or [])},
                )
            )

        return sorted(sections, key=lambda item: item.priority)

    def _build_runtime_sections(self, state: Mapping[str, Any]) -> list[PromptSection]:
        resolved_skills = list(state.get("resolved_skill_keys") or [])
        model_visible_tools = list(state.get("model_visible_tool_keys") or [])
        allowed_tools = list(state.get("allowed_tool_keys") or [])
        approval_tools = list(state.get("approval_required_tool_keys") or [])
        denied_tools = list(state.get("denied_tool_keys") or [])
        plan_steps = list(state.get("plan_steps") or [])
        context_bundle = dict(state.get("context_bundle") or {})
        attachment_count = int(len(context_bundle.get("attachments") or []))
        input_envelope = dict(context_bundle.get("input_envelope") or {})
        input_routing = dict(context_bundle.get("input_routing") or {})
        sections = [
            PromptSection(
                key="user_request",
                title="User Request",
                source="prompt_assembly.runtime",
                channel="runtime_message",
                cache_scope="ephemeral",
                priority=10,
                content=str(state.get("user_message") or "").strip() or "(empty request)",
            ),
            PromptSection(
                key="normalized_input",
                title="Normalized Input",
                source="prompt_assembly.runtime",
                channel="runtime_message",
                cache_scope="ephemeral",
                priority=20,
                content=str(state.get("normalized_input") or "").strip() or "(same as user request)",
            ),
            PromptSection(
                key="execution_plan",
                title="Execution Plan",
                source="planner",
                channel="runtime_message",
                cache_scope="dynamic",
                priority=30,
                content="\n".join(f"- {step}" for step in plan_steps) or "- No explicit plan steps were generated.",
            ),
            PromptSection(
                key="tool_access",
                title="Tool Access",
                source="permission_gate",
                channel="runtime_message",
                cache_scope="dynamic",
                priority=40,
                content=(
                    f"Model-visible tools: {self._format_csv(model_visible_tools)}\n"
                    f"Allowed safe tools: {self._format_csv(allowed_tools)}\n"
                    f"Approval-gated tools: {self._format_csv(approval_tools)}\n"
                    f"Denied tools: {self._format_csv(denied_tools)}"
                ),
            ),
            PromptSection(
                key="context_summary",
                title="Context Summary",
                source="context_builder",
                channel="runtime_message",
                cache_scope="dynamic",
                priority=50,
                content=(
                    f"Resolved skills: {self._format_csv(resolved_skills)}\n"
                    f"Input message kind: {input_envelope.get('message_kind', 'user_input')}\n"
                    f"Submit mode: {input_envelope.get('submit_mode', 'standard')}\n"
                    f"Command name: {input_envelope.get('command_name') or 'none'}\n"
                    f"Execution lane: {input_routing.get('execution_lane', 'conversation_turn')}\n"
                    f"Queue behavior: {input_routing.get('queue_behavior', 'reject_when_busy')}\n"
                    f"Interrupt policy: {input_routing.get('interrupt_policy', 'wait_for_active_turn')}\n"
                    f"Attachment count: {attachment_count}\n"
                    f"Harness flags: {self._format_csv(context_bundle.get('harness_flags') or [])}\n"
                    f"Observation categories: {self._format_csv(context_bundle.get('observation_categories') or [])}\n"
                    f"Context keys: {self._format_csv(sorted(context_bundle.keys()))}"
                ),
                metadata={"context_keys": sorted(context_bundle.keys())},
            ),
        ]
        return sections

    def _render_system_prompt(self, sections: Sequence[PromptSection]) -> str:
        rendered = [section.render() for section in sections if section.render()]
        return "\n\n".join(rendered).strip()

    def _render_runtime_messages(
        self,
        sections: Sequence[PromptSection],
    ) -> list[dict[str, Any]]:
        rendered = [section.render() for section in sections if section.render()]
        if not rendered:
            return []
        return [{"role": "user", "content": "\n\n".join(rendered).strip()}]

    def _format_csv(self, values: Sequence[Any]) -> str:
        normalized = [str(item).strip() for item in values if str(item).strip()]
        return ", ".join(normalized) if normalized else "none"
