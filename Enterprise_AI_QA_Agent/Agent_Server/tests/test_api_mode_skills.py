from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.router import build_router_node
from src.graph.nodes.tool_executor import _run_skill_loader
from src.modes.api_testing_mode.manifest import MODE_MANIFEST
from src.modes.api_testing_mode.skills import API_TESTING_AGENT_SKILLS, API_TESTING_SKILL_KEYS
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
            summary="Fake model for API Skill tests.",
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
        "selected_agent_key": "api-testing-agent",
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
        "session_id": "api-session",
        "turn_id": "api-turn",
        "trace_id": "api-trace",
        "user_message": "验证用户接口契约",
        "normalized_input": "验证用户接口契约",
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "mode_key": "api_testing",
        "message_count": 1,
        "preferred_model": "",
        "selected_agent_key": "api-testing-agent",
        "selected_agent_name": "",
        "selected_model_key": "",
        "selected_model_name": "",
        "selected_model_provider": "",
        "requested_skill_keys": list(API_TESTING_SKILL_KEYS),
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


def test_api_mode_manifest_and_agents_use_new_skills():
    registry = SkillRegistry()
    agents = AgentRegistry()

    assert MODE_MANIFEST["default_skill_keys"] == API_TESTING_SKILL_KEYS
    assert [skill.key for skill in registry.get_many(API_TESTING_SKILL_KEYS)] == API_TESTING_SKILL_KEYS
    for agent_key, expected in API_TESTING_AGENT_SKILLS.items():
        assert agents.get(agent_key).supported_skills == expected
    assert agents.get("api-testing-agent").supported_skills == API_TESTING_SKILL_KEYS


def test_api_skill_files_are_skill_creator_valid_and_compact():
    registry = SkillRegistry()
    for key in API_TESTING_SKILL_KEYS:
        skill_file = registry.skills_root / key / "SKILL.md"
        assert skill_file.is_file()
        assert len(skill_file.read_text(encoding="utf-8").splitlines()) < 500


def test_api_mode_request_contains_only_new_default_skill_keys():
    orchestrator = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="api-session",
        title="api",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="api_testing",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    request = orchestrator.orchestrate(
        session,
        SendMessageRequest(content="验证用户接口契约", mode_key="api_testing"),
    )
    assert request.mode_key == "api_testing"
    assert request.skill_keys == API_TESTING_SKILL_KEYS


@pytest.mark.asyncio
async def test_api_router_injects_selected_skill_bodies_and_tool_keys():
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
    routed = await router(_router_state(ModeRegistry().get("api_testing").model_dump(mode="python")))
    combined = "\n".join(routed["skill_prompt_blocks"])

    assert routed["resolved_skill_keys"] == API_TESTING_SKILL_KEYS
    assert "# API 契约测试" in combined
    assert "# API 测试场景生成" in combined
    assert "api-docs-library" in routed["available_tool_keys"]
    assert "api-test-runner" in routed["available_tool_keys"]
    assert "api-tester" in routed["available_tool_keys"]


def test_api_skill_loader_loads_full_body_and_exposes_api_tools():
    registry = SkillRegistry()
    state = _skill_state()
    result = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="load-api-contract",
            name="skill",
            arguments={"action": "load", "skill_keys": ["api-contract-testing"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=SkillRuntimeService(registry),
    )

    assert result.status == "completed"
    assert result.output["loaded_skills"] == ["api-contract-testing"]
    assert "# API 契约测试" in result.output["instructions"][0]
    assert {"api-docs-library", "api-tester", "api-test-runner"}.issubset(
        result.output["loaded_tools"]
    )


def test_test_data_strategy_is_one_cross_mode_skill_instance():
    registry = SkillRegistry()
    descriptor = registry.get("test-data-strategy")

    assert "api-testing-agent" in descriptor.recommended_agents
    assert "code-testability-reviewer" in descriptor.recommended_agents
    assert descriptor.key == "test-data-strategy"
    assert (registry.skills_root / "test-data-strategy" / "SKILL.md").is_file()
