from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.modes.smoke_testing_mode.manifest import MODE_MANIFEST
from src.modes.smoke_testing_mode.skills import SMOKE_TESTING_AGENT_SKILLS, SMOKE_TESTING_SKILL_KEYS
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.agent import ModelDescriptor
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus


class _FakeModelRegistry:
    def resolve_for_agent(self, requested_key, supported_model_keys):
        return ModelDescriptor(key="fake", name="fake", provider="fake", summary="fake")


class _FakeMcpRuntimeService:
    def list_active_servers(self):
        return []

    def build_prompt_blocks(self, active_servers):
        return []


def _state(selected_mode: dict, agent_key: str = "smoke-testing-agent") -> dict:
    requested = SMOKE_TESTING_AGENT_SKILLS[agent_key]
    return {
        "session_id": "smoke-session", "turn_id": "smoke-turn", "trace_id": "smoke-trace",
        "user_message": "生成登录核心链路冒烟方案", "normalized_input": "生成登录核心链路冒烟方案",
        "session_mode": "normal", "runtime_mode": "interactive", "mode_key": "smoke_testing", "message_count": 1,
        "preferred_model": "", "selected_agent_key": agent_key, "selected_agent_name": "", "selected_model_key": "",
        "selected_model_name": "", "selected_model_provider": "", "requested_skill_keys": requested,
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


def test_smoke_manifest_registry_and_agents_use_planning_and_shared_skills():
    skills = SkillRegistry()
    agents = AgentRegistry()
    assert MODE_MANIFEST["default_skill_keys"] == SMOKE_TESTING_SKILL_KEYS
    assert [skill.key for skill in skills.get_many(SMOKE_TESTING_SKILL_KEYS)] == SMOKE_TESTING_SKILL_KEYS
    for agent_key, expected in SMOKE_TESTING_AGENT_SKILLS.items():
        assert agents.get(agent_key).supported_skills == expected
    assert "api-contract-testing" in agents.get("smoke-testing-agent").supported_skills
    assert "playwright-e2e-testing" in agents.get("smoke-testing-agent").supported_skills


def test_smoke_request_contains_default_planning_skill():
    service = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="smoke-session", title="smoke", status=SessionStatus.idle, session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive, mode_key="smoke_testing",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    request = service.orchestrate(
        session,
        SendMessageRequest(content="生成登录核心链路冒烟方案", mode_key="smoke_testing"),
    )
    assert request.mode_key == "smoke_testing"
    assert request.skill_keys == SMOKE_TESTING_SKILL_KEYS


@pytest.mark.asyncio
async def test_smoke_router_injects_planning_skill_and_only_smoke_entry_tool():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(), ToolRegistry(), _FakeModelRegistry(), skills,
        SkillRuntimeService(skills), _FakeMcpRuntimeService(), None,
    )
    selected_mode = ModeRegistry().get("smoke_testing").model_dump(mode="python")
    routed = await router(_state(selected_mode))
    content = "\n".join(routed["skill_prompt_blocks"])
    assert "# 冒烟测试规划" in content
    assert "smoke-suite-runner" in routed["available_tool_keys"]
    assert "api-test-runner" not in routed["available_tool_keys"]


def test_smoke_skill_preserves_user_confirmation_protocol():
    content = (SkillRegistry().skills_root / "smoke-test-planning" / "SKILL.md").read_text(encoding="utf-8")
    assert len(content.splitlines()) < 500
    assert "等待用户确认" in content
    assert "只传递已选 case id/序号" in content
    assert "没有执行证据时不得声称通过" in content
