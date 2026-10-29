from __future__ import annotations

from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.graph.state import AgentGraphState
from src.registry.agents import AgentRegistry
from src.runtime.execution_logging import append_graph_event, append_graph_event_durable, truncate_text
from src.schemas.framework_transcript import uses_turn_context_assembly


def build_prompt_assembler_node(
    prompt_assembly_service: PromptAssemblyService,
    agent_registry: AgentRegistry,
    *, durable_events: bool = False,
):
    def assemble(state: AgentGraphState, event_writer):
        loop_iteration = int(state.get("loop_iteration") or 0)

        assembly = prompt_assembly_service.build_for_turn(
            state=state,
            available_agent_keys=[agent.key for agent in agent_registry.list()],
        )

        state["system_prompt_sections"] = [
            item.model_dump(mode="python") for item in assembly.system_sections
        ]
        state["runtime_message_sections"] = [
            item.model_dump(mode="python") for item in assembly.runtime_message_sections
        ]
        state["system_prompt"] = assembly.system_prompt
        existing_runtime_messages = list(state.get("runtime_messages") or [])
        # The assembly service returns the complete transcript, including any
        # existing history. Appending it would duplicate every message on each
        # Agent iteration, including assistant/tool protocol pairs.
        state["runtime_messages"] = list(assembly.runtime_messages)
        return event_writer(
            state,
            "graph.prompt_assembled",
            "prompt_assembler",
            "Structured prompt sections have been assembled for this turn.",
            system_section_count=len(state["system_prompt_sections"]),
            runtime_message_section_count=len(state["runtime_message_sections"]),
            **({} if uses_turn_context_assembly(state.get("context_bundle") or {}) else {
                "system_prompt_preview": truncate_text(state["system_prompt"], 180)}),
            runtime_message_count=len(state["runtime_messages"]),
            input_runtime_message_count=len(existing_runtime_messages),
            loop_iteration=loop_iteration,
        )

    def prompt_assembler(state: AgentGraphState) -> AgentGraphState:
        assemble(state, append_graph_event)
        return state

    async def acknowledged_prompt_assembler(state: AgentGraphState) -> AgentGraphState:
        await assemble(state, append_graph_event_durable)
        return state

    return acknowledged_prompt_assembler if durable_events else prompt_assembler
