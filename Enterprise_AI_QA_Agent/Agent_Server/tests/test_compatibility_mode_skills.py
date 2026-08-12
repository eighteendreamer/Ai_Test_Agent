from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.graph.nodes.tool_executor import _run_skill_loader
from src.modes.compatibility_testing_mode.manifest import MODE_MANIFEST
from src.modes.compatibility_testing_mode.skills import (
    COMPATIBILITY_TESTING_AGENT_SKILLS,
    COMPATIBILITY_TESTING_SKILL_KEYS,
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


def _router_state(selected_mode: dict) -> dict:
    return {
        "session_id": "compatibility-session",
        "turn_id": "compatibility-turn",
        "trace_id": "compatibility-trace",
        "user_message": "规划 Web 多浏览器兼容性测试",
        "normalized_input": "规划 Web 多浏览器兼容性测试",
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "mode_key": "compatibility_testing",
        "message_count": 1,
        "preferred_model": "",
        "selected_agent_key": "compatibility-testing-agent",
        "selected_agent_name": "",
        "selected_model_key": "",
        "selected_model_name": "",
        "selected_model_provider": "",
        "requested_skill_keys": list(COMPATIBILITY_TESTING_SKILL_KEYS),
        "resolved_skill_keys": [],
        "skill_prompt_blocks": [],
        "memory_hits": [],
        "memory_prompt_blocks": [],
        "observation_hits": [],
        "observation_prompt_blocks": [],
        "active_mcp_servers": [],
        "mcp_prompt_blocks": [],
        "available_tool_keys": [],
        "deferred_tool_keys": [],
        "model_visible_tool_keys": [],
        "allowed_tool_keys": [],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "permission_decisions": [],
        "pending_approvals": [],
        "plan_steps": [],
        "system_prompt_sections": [],
        "runtime_message_sections": [],
        "system_prompt": "",
        "runtime_messages": [],
        "model_request_payload": {},
        "model_response_summary": {},
        "model_response_text": "",
        "turn_token_usage": {},
        "model_context_window": 0,
        "assistant_tool_call_message": {},
        "model_tool_calls": [],
        "tool_results": [],
        "tool_messages": [],
        "worker_dispatches": [],
        "context_bundle": {"selected_mode": selected_mode},
        "event_log": [],
        "final_response": "",
        "pending_turn": {},
        "control_state": "",
        "interrupt_requested": False,
        "interrupt_reason": "",
        "loop_iteration": 0,
        "max_iterations": 8,
        "continue_loop": False,
        "termination_reason": "",
        "_event_queue": None,
    }


def _skill_state() -> dict:
    tools = ToolRegistry()
    return {
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "selected_agent_key": "compatibility-testing-agent",
        "context_bundle": {},
        "available_tool_keys": ["skill"],
        "deferred_tool_keys": [tool.key for tool in tools.list() if tool.key != "skill"],
        "model_visible_tool_keys": ["skill"],
        "allowed_tool_keys": ["skill"],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "permission_decisions": [],
        "resolved_skill_keys": [],
        "requested_skill_keys": [],
        "skill_prompt_blocks": [],
    }


def test_compatibility_manifest_registry_and_agent_reuse_professional_skills():
    skills = SkillRegistry()
    agents = AgentRegistry()

    assert MODE_MANIFEST["default_skill_keys"] == COMPATIBILITY_TESTING_SKILL_KEYS
    assert [skill.key for skill in skills.get_many(COMPATIBILITY_TESTING_SKILL_KEYS)] == (
        COMPATIBILITY_TESTING_SKILL_KEYS
    )
    for agent_key, expected in COMPATIBILITY_TESTING_AGENT_SKILLS.items():
        assert agents.get(agent_key).supported_skills == expected


def test_compatibility_request_contains_only_new_default_skills():
    service = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="compatibility-session",
        title="compatibility",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="compatibility_testing",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    request = service.orchestrate(
        session,
        SendMessageRequest(content="规划 Web 多浏览器兼容性测试", mode_key="compatibility_testing"),
    )
    assert request.mode_key == "compatibility_testing"
    assert request.skill_keys == COMPATIBILITY_TESTING_SKILL_KEYS


@pytest.mark.asyncio
async def test_compatibility_router_injects_reused_skills_and_existing_tools():
    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(),
        ToolRegistry(),
        _FakeModelRegistry(),
        skills,
        SkillRuntimeService(skills),
        _FakeMcpRuntimeService(),
        None,
    )
    selected_mode = ModeRegistry().get("compatibility_testing").model_dump(mode="python")
    routed = await router(_router_state(selected_mode))
    content = "\n".join(routed["skill_prompt_blocks"])

    assert routed["resolved_skill_keys"] == COMPATIBILITY_TESTING_SKILL_KEYS
    assert "# Playwright E2E 测试" in content
    assert "# API 契约测试" in content
    assert "# 冒烟测试规划" in content
    assert "compatibility-test-runner" in routed["available_tool_keys"]
    assert "ui-automation-runner" not in routed["available_tool_keys"]
    assert "api-test-runner" not in routed["available_tool_keys"]
    assert "smoke-suite-runner" not in routed["available_tool_keys"]
    registered_tools = {tool.key for tool in ToolRegistry().list()}
    assert {"ui-automation-runner", "api-test-runner", "smoke-suite-runner"}.issubset(
        registered_tools
    )


def test_compatibility_dynamic_skill_load_reuses_one_global_instance():
    skills = SkillRegistry()
    result = _run_skill_loader(
        state=_skill_state(),
        tool_call=ModelToolCall(
            id="load-compatibility-ui",
            name="skill",
            arguments={"action": "load", "skill_keys": ["playwright-e2e-testing"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=skills,
        skill_runtime_service=SkillRuntimeService(skills),
    )

    assert result.status == "completed"
    assert result.output["loaded_skills"] == ["playwright-e2e-testing"]
    assert "# Playwright E2E 测试" in result.output["instructions"][0]
    assert "ui-automation-runner" in result.output["loaded_tools"]
    assert (skills.skills_root / "playwright-e2e-testing" / "SKILL.md").is_file()
    assert not (skills.skills_root / "compatibility-playwright-e2e-testing").exists()


def test_compatibility_skills_are_compact_and_describe_runtime_boundaries():
    root = SkillRegistry().skills_root
    for key in COMPATIBILITY_TESTING_SKILL_KEYS:
        content = (root / key / "SKILL.md").read_text(encoding="utf-8")
        assert len(content.splitlines()) < 500
    assert "当前执行边界" in (root / "playwright-e2e-testing" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "已注册的 `api-tester` 或 `api-test-runner`" in (
        root / "api-contract-testing" / "SKILL.md"
    ).read_text(encoding="utf-8")
