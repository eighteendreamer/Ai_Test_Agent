from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.graph.nodes.tool_executor import _run_skill_loader
from src.modes.performance_testing_mode.manifest import MODE_MANIFEST
from src.modes.performance_testing_mode.skills import (
    PERFORMANCE_TESTING_AGENT_SKILLS,
    PERFORMANCE_TESTING_SKILL_KEYS,
)
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.agent import ModelDescriptor
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


class _FakeModelRegistry:
    def resolve_for_agent(self, requested_key, supported_model_keys):
        return ModelDescriptor(key="fake", name="fake", provider="fake", summary="fake")


class _FakeMcpRuntimeService:
    def list_active_servers(self):
        return []

    def build_prompt_blocks(self, active_servers):
        return []


def _state(selected_mode: dict, *, agent_key: str = "performance-testing-agent") -> dict:
    return {
        "session_id": "perf-session", "turn_id": "perf-turn", "trace_id": "perf-trace",
        "user_message": "对测试环境执行 k6 基线测试", "normalized_input": "对测试环境执行 k6 基线测试",
        "session_mode": "normal", "runtime_mode": "interactive", "mode_key": "performance_testing",
        "message_count": 1, "preferred_model": "", "selected_agent_key": agent_key,
        "selected_agent_name": "", "selected_model_key": "", "selected_model_name": "",
        "selected_model_provider": "", "requested_skill_keys": list(PERFORMANCE_TESTING_SKILL_KEYS),
        "resolved_skill_keys": [], "skill_prompt_blocks": [], "memory_hits": [], "memory_prompt_blocks": [],
        "observation_hits": [], "observation_prompt_blocks": [], "active_mcp_servers": [], "mcp_prompt_blocks": [],
        "available_tool_keys": [], "deferred_tool_keys": [], "model_visible_tool_keys": [], "allowed_tool_keys": [],
        "approval_required_tool_keys": [], "denied_tool_keys": [], "permission_decisions": [], "pending_approvals": [],
        "plan_steps": [], "system_prompt_sections": [], "runtime_message_sections": [], "system_prompt": "",
        "runtime_messages": [], "model_request_payload": {}, "model_response_summary": {}, "model_response_text": "",
        "turn_token_usage": {}, "model_context_window": 0, "assistant_tool_call_message": {}, "model_tool_calls": [],
        "tool_results": [], "tool_messages": [], "worker_dispatches": [], "context_bundle": {"selected_mode": selected_mode},
        "event_log": [], "final_response": "", "pending_turn": {}, "control_state": "", "interrupt_requested": False,
        "interrupt_reason": "", "loop_iteration": 0, "max_iterations": 8, "continue_loop": False,
        "termination_reason": "", "_event_queue": None,
    }


def _skill_state() -> dict:
    tools = ToolRegistry()
    return {
        "session_mode": "normal", "runtime_mode": "interactive", "selected_agent_key": "performance-testing-agent",
        "context_bundle": {}, "available_tool_keys": ["skill"],
        "deferred_tool_keys": [tool.key for tool in tools.list() if tool.key != "skill"],
        "model_visible_tool_keys": ["skill"], "allowed_tool_keys": ["skill"],
        "approval_required_tool_keys": [], "denied_tool_keys": [], "permission_decisions": [],
        "resolved_skill_keys": [], "requested_skill_keys": [], "skill_prompt_blocks": [],
    }


def test_performance_manifest_registry_and_agents_use_engine_skills():
    skills = SkillRegistry()
    agents = AgentRegistry()
    assert MODE_MANIFEST["default_skill_keys"] == PERFORMANCE_TESTING_SKILL_KEYS
    assert [skill.key for skill in skills.get_many(PERFORMANCE_TESTING_SKILL_KEYS)] == PERFORMANCE_TESTING_SKILL_KEYS
    for agent_key, expected in PERFORMANCE_TESTING_AGENT_SKILLS.items():
        assert agents.get(agent_key).supported_skills == expected


def test_performance_request_contains_new_default_skills():
    service = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="perf-session", title="perf", status=SessionStatus.idle, session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive, mode_key="performance_testing",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    request = service.orchestrate(
        session,
        SendMessageRequest(content="对测试环境执行 k6 基线测试", mode_key="performance_testing"),
    )
    assert request.mode_key == "performance_testing"
    assert request.skill_keys == PERFORMANCE_TESTING_SKILL_KEYS


@pytest.mark.asyncio
async def test_performance_router_injects_engine_skills_and_existing_tools():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(), ToolRegistry(), _FakeModelRegistry(), skills,
        SkillRuntimeService(skills), _FakeMcpRuntimeService(), None,
    )
    routed = await router(_state(ModeRegistry().get("performance_testing").model_dump(mode="python")))
    content = "\n".join(routed["skill_prompt_blocks"])
    assert "# k6 负载测试" in content
    assert "# JMeter 负载测试" in content
    assert "performance-test-runner" in routed["available_tool_keys"]
    assert "perf-plan-compiler" in routed["available_tool_keys"]
    assert "perf-result-analyzer" not in routed["available_tool_keys"]


@pytest.mark.asyncio
async def test_performance_analyst_receives_internal_result_analyzer():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(), ToolRegistry(), _FakeModelRegistry(), skills,
        SkillRuntimeService(skills), _FakeMcpRuntimeService(), None,
    )
    selected_mode = ModeRegistry().get("performance_testing").model_dump(mode="python")
    routed = await router(_state(selected_mode, agent_key="perf-analyst"))
    assert "perf-result-analyzer" in routed["available_tool_keys"]
    assert "performance-test-runner" not in routed["available_tool_keys"]


def test_dynamic_k6_skill_load_exposes_performance_runtime():
    skills = SkillRegistry()
    state = _skill_state()
    result = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="load-k6", name="skill", arguments={"action": "load", "skill_keys": ["k6-load-testing"]}
        ),
        tool_registry=ToolRegistry(), permission_service=PermissionService(),
        skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
    )
    assert result.status == "completed"
    assert "# k6 负载测试" in result.output["instructions"][0]
    assert result.output["loaded_tools"] == ["performance-test-runner"]


def test_performance_skills_are_compact_and_preserve_safety_gates():
    root = SkillRegistry().skills_root
    for key in PERFORMANCE_TESTING_SKILL_KEYS:
        content = (root / key / "SKILL.md").read_text(encoding="utf-8")
        assert len(content.splitlines()) < 500
        assert "smoke" in content.lower()
    assert "生产环境默认禁止" in (root / "k6-load-testing" / "SKILL.md").read_text(encoding="utf-8")
