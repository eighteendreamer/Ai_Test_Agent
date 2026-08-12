from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.graph.nodes.tool_executor import _run_skill_loader
from src.modes.ui_automation_mode.manifest import MODE_MANIFEST
from src.modes.ui_automation_mode.skills import UI_AUTOMATION_SKILL_KEYS
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.agent import ModelDescriptor
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


class _FakeModelRegistry:
    def resolve_for_agent(self, requested_key, supported_model_keys):
        return ModelDescriptor(
            key="fake-model",
            name="fake-model",
            provider="fake",
            summary="Fake model for UI Skill tests.",
        )


class _FakeMcpRuntimeService:
    def list_active_servers(self):
        return []

    def build_prompt_blocks(self, active_servers):
        return []


def _skill_state() -> dict:
    tools = ToolRegistry()
    return {
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "selected_agent_key": "ui-automation-agent",
        "context_bundle": {},
        "available_tool_keys": ["skill"],
        "deferred_tool_keys": [item.key for item in tools.list() if item.key != "skill"],
        "model_visible_tool_keys": ["skill"],
        "allowed_tool_keys": ["skill"],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "permission_decisions": [],
        "resolved_skill_keys": [],
        "requested_skill_keys": [],
        "skill_prompt_blocks": [],
    }


def _router_state(selected_mode: dict) -> dict:
    return {
        "session_id": "ui-session",
        "turn_id": "ui-turn",
        "trace_id": "ui-trace",
        "user_message": "探索登录页面",
        "normalized_input": "探索登录页面",
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "mode_key": "ui_automation",
        "message_count": 1,
        "preferred_model": "",
        "selected_agent_key": "ui-automation-agent",
        "selected_agent_name": "",
        "selected_model_key": "",
        "selected_model_name": "",
        "selected_model_provider": "",
        "requested_skill_keys": list(UI_AUTOMATION_SKILL_KEYS),
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


def test_ui_mode_manifest_agent_and_registry_use_new_skills():
    registry = SkillRegistry()
    agent = AgentRegistry().get("ui-automation-agent")

    assert MODE_MANIFEST["default_skill_keys"] == UI_AUTOMATION_SKILL_KEYS
    assert UI_AUTOMATION_SKILL_KEYS == ["playwright-e2e-testing", "playwright-cli"]
    assert [skill.key for skill in registry.get_many(UI_AUTOMATION_SKILL_KEYS)] == UI_AUTOMATION_SKILL_KEYS
    assert agent.supported_skills == UI_AUTOMATION_SKILL_KEYS


def test_ui_mode_request_contains_only_new_default_skill_keys():
    orchestrator = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="ui-session",
        title="ui",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="ui_automation",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    request = orchestrator.orchestrate(
        session,
        SendMessageRequest(content="探索登录页面", mode_key="ui_automation"),
    )
    assert request.mode_key == "ui_automation"
    assert request.skill_keys == UI_AUTOMATION_SKILL_KEYS


@pytest.mark.asyncio
async def test_ui_router_injects_selected_bodies_and_browser_tools():
    registry = SkillRegistry()
    router = build_router_node(
        AgentRegistry(),
        ToolRegistry(),
        _FakeModelRegistry(),
        registry,
        SkillRuntimeService(registry),
        _FakeMcpRuntimeService(),
        None,
    )
    routed = await router(_router_state(ModeRegistry().get("ui_automation").model_dump(mode="python")))
    combined = "\n".join(routed["skill_prompt_blocks"])

    assert routed["resolved_skill_keys"] == UI_AUTOMATION_SKILL_KEYS
    assert "# Playwright E2E 测试" in combined
    assert "playwright-cli" in combined
    assert "ui-automation-runner" in routed["available_tool_keys"]
    assert "ui-page-explorer" in routed["available_tool_keys"]
    assert "browser-automation" in routed["available_tool_keys"]
    assert "file-artifact-manager" in routed["available_tool_keys"]


def test_dynamic_ui_skill_load_exposes_existing_browser_runtime():
    registry = SkillRegistry()
    state = _skill_state()
    result = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="load-ui-e2e",
            name="skill",
            arguments={"action": "load", "skill_keys": ["playwright-e2e-testing"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=SkillRuntimeService(registry),
    )

    assert result.status == "completed"
    assert "# Playwright E2E 测试" in result.output["instructions"][0]
    assert {"ui-page-explorer", "browser-automation", "browser-control"}.issubset(
        result.output["loaded_tools"]
    )


def test_playwright_e2e_skill_states_current_runtime_boundary():
    skill_file = SkillRegistry().skills_root / "playwright-e2e-testing" / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")

    assert len(content.splitlines()) < 500
    assert "测试执行员工未实现" in content
    assert "不要假设候选文档中的 Node Playwright Test runner 已安装" in content
