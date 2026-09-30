from __future__ import annotations

from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.core.world_state import (
    DynamicSection,
    SectionSnapshot,
    StaticSection,
    WorldState,
)
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.registry.agents import AgentRegistry


def test_changed_static_section_emits_a_delta() -> None:
    assert SectionSnapshot("identity", "Agent Alpha").content_hash
    world = WorldState()
    world.register_section(StaticSection("identity", "Agent Alpha"))
    assert world.render()[0].content == "Agent Alpha"

    world.register_section(StaticSection("identity", "Agent Beta"))
    fragments = world.render()

    assert len(fragments) == 1
    assert fragments[0].kind == "delta"
    assert "Agent Beta" in fragments[0].content


def test_changed_dynamic_section_emits_a_delta() -> None:
    content = ["Memory Alpha"]
    world = WorldState()
    world.register_section(DynamicSection("memory", _content_provider=lambda: content[0]))
    assert world.render()[0].content == "Memory Alpha"

    content[0] = "Memory Beta"
    fragments = world.render()

    assert len(fragments) == 1
    assert fragments[0].kind == "delta"
    assert "Memory Beta" in fragments[0].content
    assert world.render() == []


def test_prompt_assembler_uses_current_session_without_shared_world_state_metric() -> None:
    assemble = build_prompt_assembler_node(PromptAssemblyService(), AgentRegistry())

    def state(session_id: str, agent_name: str) -> dict:
        return {
            "session_id": session_id,
            "turn_id": "turn-1",
            "trace_id": f"trace-{session_id}",
            "selected_agent_name": agent_name,
            "selected_agent_key": "coordinator",
            "selected_model_key": "model",
            "session_mode": "normal",
            "runtime_mode": "interactive",
            "mode_key": "default",
            "loop_iteration": 0,
            "user_message": "Hello",
            "normalized_input": "Hello",
            "runtime_messages": [],
            "context_bundle": {},
        }

    first = assemble(state("session-alpha", "Agent Alpha"))
    second = assemble(state("session-beta", "Agent Beta"))

    assert "Agent Alpha" in first["system_prompt"]
    assert "Agent Beta" in second["system_prompt"]
    assert "Agent Alpha" not in second["system_prompt"]
    assert "world_state_changed_fragments" not in second["event_log"][-1]["payload"]
