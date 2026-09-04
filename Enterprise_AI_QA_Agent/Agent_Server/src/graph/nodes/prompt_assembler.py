from __future__ import annotations

from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.core.world_state import StaticSection, WorldState
from src.graph.state import AgentGraphState
from src.registry.agents import AgentRegistry
from src.runtime.execution_logging import append_graph_event, truncate_text


def build_prompt_assembler_node(
    prompt_assembly_service: PromptAssemblyService,
    agent_registry: AgentRegistry,
):
    world_state = WorldState()

    def prompt_assembler(state: AgentGraphState) -> AgentGraphState:
        loop_iteration = int(state.get("loop_iteration") or 0)

        assembly = prompt_assembly_service.build_for_turn(
            state=state,
            available_agent_keys=[agent.key for agent in agent_registry.list()],
        )

        for section in assembly.system_sections:
            world_state.register_section(StaticSection(
                _key=section.key,
                _content=section.content,
                _priority=section.priority,
            ))

        fragments = world_state.render()

        state["system_prompt_sections"] = [
            item.model_dump(mode="python") for item in assembly.system_sections
        ]
        state["runtime_message_sections"] = [
            item.model_dump(mode="python") for item in assembly.runtime_message_sections
        ]
        state["system_prompt"] = assembly.system_prompt
        existing_runtime_messages = list(state.get("runtime_messages") or [])
        if assembly.runtime_messages:
            state["runtime_messages"] = [
                *existing_runtime_messages,
                *assembly.runtime_messages,
            ]
        elif not existing_runtime_messages:
            state["runtime_messages"] = []
        append_graph_event(
            state,
            "graph.prompt_assembled",
            "prompt_assembler",
            "Structured prompt sections have been assembled for this turn.",
            system_section_count=len(state["system_prompt_sections"]),
            runtime_message_section_count=len(state["runtime_message_sections"]),
            system_prompt_preview=truncate_text(state["system_prompt"], 180),
            runtime_message_count=len(state["runtime_messages"]),
            world_state_changed_fragments=len(fragments),
            loop_iteration=loop_iteration,
        )
        return state

    return prompt_assembler
