from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.tool_executor import _run_skill_loader
from src.graph.nodes.router import build_router_node
from src.modes.code_review_mode.manifest import MODE_MANIFEST
from src.modes.code_review_mode.orchestrator import build_code_review_campaign
from src.modes.code_review_mode.skills import CODE_REVIEW_REVIEWER_SKILLS, CODE_REVIEW_SKILL_KEYS
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


class _FakeModelRegistry:
    def resolve_for_agent(self, requested_key, supported_model_keys):
        from src.schemas.agent import ModelDescriptor

        return ModelDescriptor(
            key="fake-model",
            name="fake-model",
            provider="fake",
            summary="Fake model for code review Skill tests.",
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
        "selected_agent_key": "code-review-agent",
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
        "session_id": "test-session",
        "turn_id": "test-turn",
        "trace_id": "test-trace",
        "user_message": "审查当前项目",
        "normalized_input": "审查当前项目",
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "mode_key": "code_review",
        "message_count": 1,
        "preferred_model": "",
        "selected_agent_key": "code-review-agent",
        "selected_agent_name": "",
        "selected_model_key": "",
        "selected_model_name": "",
        "selected_model_provider": "",
        "requested_skill_keys": list(CODE_REVIEW_SKILL_KEYS),
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


def test_code_review_mode_uses_only_registered_professional_skills():
    registry = SkillRegistry()

    assert MODE_MANIFEST["default_skill_keys"] == CODE_REVIEW_SKILL_KEYS
    assert [skill.key for skill in registry.get_many(CODE_REVIEW_SKILL_KEYS)] == CODE_REVIEW_SKILL_KEYS
    for key in CODE_REVIEW_SKILL_KEYS:
        skill_file = registry.skills_root / key / "SKILL.md"
        assert skill_file.is_file()
        assert len(skill_file.read_text(encoding="utf-8").splitlines()) < 500


def test_code_review_orchestrator_assigns_skills_by_reviewer_role():
    context = ToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        trace_id="trace-1",
        user_message="审查这个项目",
        normalized_input="审查这个项目",
        context_bundle={},
    )
    campaign = build_code_review_campaign(
        {
            "project_name": "demo",
            "project_path": ".",
            "cross_review_rounds": 1,
            "launch_workers": False,
        },
        context,
    )

    workers = campaign["dispatch_payload"]["workers"]
    by_role = {worker["context"]["debate_role"]: worker["skill_keys"] for worker in workers}
    assert by_role == CODE_REVIEW_REVIEWER_SKILLS
    assert campaign["summary_agent"]["skill_keys"] == []

    agents = AgentRegistry()
    for worker in workers:
        assert worker["skill_keys"] == agents.get(worker["agent_key"]).supported_skills


def test_skill_catalog_discloses_metadata_without_skill_body():
    registry = SkillRegistry()
    runtime = SkillRuntimeService(registry)
    blocks = runtime.build_prompt_blocks(CODE_REVIEW_SKILL_KEYS)
    combined = "\n".join(blocks)

    assert "Use the registered `skill` loader" in combined
    assert "# CI 流水线审查" not in combined
    assert "# OWASP 安全审查" not in combined


def test_selected_code_review_skills_disclose_only_their_bodies():
    registry = SkillRegistry()
    runtime = SkillRuntimeService(registry)
    combined = "\n".join(
        runtime.build_prompt_blocks(CODE_REVIEW_SKILL_KEYS, include_content=True)
    )

    assert "# CI 流水线审查" in combined
    assert "# OWASP 安全审查" in combined
    assert "# API Validation" not in combined


@pytest.mark.asyncio
async def test_code_review_router_injects_selected_skill_bodies():
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
    selected_mode = ModeRegistry().get("code_review").model_dump(mode="python")

    routed = await router(_router_state(selected_mode))
    combined = "\n".join(routed["skill_prompt_blocks"])

    assert routed["resolved_skill_keys"] == CODE_REVIEW_SKILL_KEYS
    assert "# CI 流水线审查" in combined
    assert "# OWASP 安全审查" in combined
    assert len(routed["context_bundle"]["available_skills"]) > len(CODE_REVIEW_SKILL_KEYS)


def test_skill_loader_discloses_full_code_review_skill_on_demand():
    registry = SkillRegistry()
    state = _skill_state()
    result = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="call-code-review-skill",
            name="skill",
            arguments={"action": "load", "skill_keys": ["ci-pipeline-review"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=SkillRuntimeService(registry),
    )

    assert result.status == "completed"
    assert result.output["loaded_skills"] == ["ci-pipeline-review"]
    assert "# CI 流水线审查" in result.output["instructions"][0]
    assert "ci-pipeline-review" in state["resolved_skill_keys"]


def test_uploaded_skill_uses_same_registry_and_progressive_disclosure(tmp_path: Path):
    skill_dir = tmp_path / "user-api-review"
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: user-api-review\n"
        "description: 用户上传的 API 审查流程。\n"
        "---\n\n"
        "# 用户 API 审查正文\n",
        encoding="utf-8",
    )
    (references_dir / "schema.md").write_text("# Schema reference\n", encoding="utf-8")

    registry = SkillRegistry(skills_root=tmp_path)
    runtime = SkillRuntimeService(registry)
    assert registry.get("user-api-review").description == "用户上传的 API 审查流程。"
    assert "用户 API 审查正文" not in runtime.build_prompt_blocks(["user-api-review"])[0]

    state = _skill_state()
    loaded = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="load-user-skill",
            name="skill",
            arguments={"action": "load", "skill_keys": ["user-api-review"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=runtime,
    )
    assert "用户 API 审查正文" in loaded.output["instructions"][0]
    assert "references/schema.md" in loaded.output["instructions"][0]

    reference = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="read-user-reference",
            name="skill",
            arguments={
                "action": "read_reference",
                "skill_keys": ["user-api-review"],
                "reference_path": "references/schema.md",
            },
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=runtime,
    )
    assert reference.status == "completed"
    assert reference.output["reference_content"] == "# Schema reference\n"

    traversal = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="reject-reference-traversal",
            name="skill",
            arguments={
                "action": "read_reference",
                "skill_keys": ["user-api-review"],
                "reference_path": "../SKILL.md",
            },
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=registry,
        skill_runtime_service=runtime,
    )
    assert traversal.status == "failed"
    assert traversal.output["error"] == "invalid_skill_reference"


def test_code_review_mode_defaults_are_available_to_execution_request():
    orchestrator = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="session-code-review",
        title="code review",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="code_review",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    request = orchestrator.orchestrate(
        session,
        SendMessageRequest(content="审查当前项目", mode_key="code_review"),
    )
    assert request.mode_key == "code_review"
    assert request.skill_keys == CODE_REVIEW_SKILL_KEYS
