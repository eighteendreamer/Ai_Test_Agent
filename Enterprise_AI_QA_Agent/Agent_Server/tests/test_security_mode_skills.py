from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.modes.security_testing_mode.manifest import MODE_MANIFEST
from src.modes.security_testing_mode.skills import SECURITY_TESTING_AGENT_SKILLS, SECURITY_TESTING_SKILL_KEYS
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


def _state(selected_mode: dict, agent_key: str = "security-testing-agent") -> dict:
    return {
        "session_id": "security-session", "turn_id": "security-turn", "trace_id": "security-trace",
        "user_message": "对已授权测试目标执行安全评估", "normalized_input": "对已授权测试目标执行安全评估",
        "session_mode": "normal", "runtime_mode": "interactive", "mode_key": "security_testing", "message_count": 1,
        "preferred_model": "", "selected_agent_key": agent_key, "selected_agent_name": "", "selected_model_key": "",
        "selected_model_name": "", "selected_model_provider": "",
        "requested_skill_keys": list(SECURITY_TESTING_AGENT_SKILLS[agent_key]), "resolved_skill_keys": [],
        "skill_prompt_blocks": [], "memory_hits": [], "memory_prompt_blocks": [], "observation_hits": [],
        "observation_prompt_blocks": [], "active_mcp_servers": [], "mcp_prompt_blocks": [], "available_tool_keys": [],
        "deferred_tool_keys": [], "model_visible_tool_keys": [], "allowed_tool_keys": [], "approval_required_tool_keys": [],
        "denied_tool_keys": [], "permission_decisions": [], "pending_approvals": [], "plan_steps": [],
        "system_prompt_sections": [], "runtime_message_sections": [], "system_prompt": "", "runtime_messages": [],
        "model_request_payload": {}, "model_response_summary": {}, "model_response_text": "", "turn_token_usage": {},
        "model_context_window": 0, "assistant_tool_call_message": {}, "model_tool_calls": [], "tool_results": [],
        "tool_messages": [], "worker_dispatches": [], "context_bundle": {"selected_mode": selected_mode}, "event_log": [],
        "final_response": "", "pending_turn": {}, "control_state": "", "interrupt_requested": False,
        "interrupt_reason": "", "loop_iteration": 0, "max_iterations": 8, "continue_loop": False,
        "termination_reason": "", "_event_queue": None,
    }


def test_security_manifest_registry_and_agents_use_real_skills():
    skills = SkillRegistry()
    agents = AgentRegistry()
    assert MODE_MANIFEST["default_skill_keys"] == SECURITY_TESTING_SKILL_KEYS
    assert MODE_MANIFEST["activation_policy"] == "explicit_only"
    assert MODE_MANIFEST["minimum_authorization"] == "verified_target_authorization"
    assert [skill.key for skill in skills.get_many(SECURITY_TESTING_SKILL_KEYS)] == SECURITY_TESTING_SKILL_KEYS
    for agent_key, expected in SECURITY_TESTING_AGENT_SKILLS.items():
        assert agents.get(agent_key).supported_skills == expected
    assert agents.get("security-recon-worker").supported_skills == []


def test_security_request_contains_only_new_default_skills():
    service = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="security-session", title="security", status=SessionStatus.idle,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key="security_testing",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    request = service.orchestrate(
        session,
        SendMessageRequest(content="对已授权测试目标执行安全评估", mode_key="security_testing"),
    )
    assert request.mode_key == "security_testing"
    assert request.skill_keys == SECURITY_TESTING_SKILL_KEYS


@pytest.mark.asyncio
async def test_security_coordinator_injects_skills_but_only_public_runner():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(), ToolRegistry(), _FakeModelRegistry(), skills,
        SkillRuntimeService(skills), _FakeMcpRuntimeService(), None,
    )
    selected_mode = ModeRegistry().get("security_testing").model_dump(mode="python")
    routed = await router(_state(selected_mode))
    content = "\n".join(routed["skill_prompt_blocks"])
    assert "# OWASP 安全审查" in content
    assert "# 认证与授权安全测试" in content
    assert "# 输入校验安全测试" in content
    assert "security-scan-runner" in routed["available_tool_keys"]
    assert "credential-attack-runner" not in routed["available_tool_keys"]
    assert "exploit-workbench-runner" not in routed["available_tool_keys"]


@pytest.mark.asyncio
async def test_security_auth_worker_gets_auth_skill_and_runner():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(), ToolRegistry(), _FakeModelRegistry(), skills,
        SkillRuntimeService(skills), _FakeMcpRuntimeService(), None,
    )
    selected_mode = ModeRegistry().get("security_testing").model_dump(mode="python")
    routed = await router(_state(selected_mode, "security-auth-worker"))
    content = "\n".join(routed["skill_prompt_blocks"])
    assert "# 认证与授权安全测试" in content
    assert "# OWASP 安全审查" not in content
    assert "credential-attack-runner" in routed["available_tool_keys"]


def test_security_skills_are_compact_and_do_not_bypass_authorization():
    root = SkillRegistry().skills_root
    for key in SECURITY_TESTING_SKILL_KEYS:
        content = (root / key / "SKILL.md").read_text(encoding="utf-8")
        assert len(content.splitlines()) < 500
        assert "授权" in content
    assert not (root / "vulnerability-analysis").exists()
    assert not (root / "network-reconnaissance").exists()
