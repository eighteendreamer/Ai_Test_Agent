from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.nodes.tool_executor import _resolve_tool_call, _run_skill_loader
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


MAIL_TOOL_KEYS = [
    "mail-status",
    "mail-send",
    "mail-confirm",
    "mail-list",
    "mail-read",
    "mail-search",
    "mail-reply",
    "mail-forward",
    "mail-trash",
    "mail-download-attachment",
]


def _state() -> dict:
    registry = ToolRegistry()
    return {
        "session_mode": "normal",
        "runtime_mode": "interactive",
        "selected_agent_key": "coordinator",
        "context_bundle": {},
        "available_tool_keys": ["skill"],
        "deferred_tool_keys": [item.key for item in registry.list() if item.key != "skill"],
        "model_visible_tool_keys": ["skill"],
        "allowed_tool_keys": ["skill"],
        "approval_required_tool_keys": [],
        "denied_tool_keys": [],
        "permission_decisions": [],
        "resolved_skill_keys": [],
        "requested_skill_keys": [],
        "skill_prompt_blocks": [],
    }


def test_agent_mail_is_declared_by_skill_instead_of_every_agent():
    skill = SkillRegistry().get("agently-mail")
    assert skill.tool_keys == MAIL_TOOL_KEYS


def test_skill_loader_exposes_mail_tools_only_after_loading_skill():
    state = _state()
    assert not any(key.startswith("mail-") for key in state["model_visible_tool_keys"])

    skill_registry = SkillRegistry()
    result = _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="call_1",
            name="skill",
            arguments={"action": "load", "skill_keys": ["agently-mail"]},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=skill_registry,
        skill_runtime_service=SkillRuntimeService(skill_registry),
    )

    assert result.status == "completed"
    assert set(MAIL_TOOL_KEYS).issubset(state["model_visible_tool_keys"])
    assert "mail-send" in state["allowed_tool_keys"]
    assert "mail-confirm" in state["approval_required_tool_keys"]
    assert "agently-mail" in state["resolved_skill_keys"]
    assert "agently-mail" in state["requested_skill_keys"]
    assert all(item != "mail-" + "capability" for item in state["resolved_skill_keys"])
    assert result.output["instructions"]


def test_mail_send_rejects_missing_recipient_before_cli_or_approval():
    state = _state()
    state["available_tool_keys"].append("mail-send")
    state["model_visible_tool_keys"].append("mail-send")
    state["approval_required_tool_keys"].append("mail-send")
    result = asyncio.run(
        _resolve_tool_call(
            state=state,
            tool_call=ModelToolCall(
                id="call_invalid_send",
                name="mail-send",
                arguments={"to": [], "subject": "test", "content": "test"},
            ),
            tool_registry=ToolRegistry(),
            permission_service=PermissionService(),
            tool_runtime_service=None,
            tool_job_service=None,
            tool_context=None,
        )
    )
    assert result["tool_result"]["status"] == "failed"
    assert result["tool_result"]["output"]["error"] == "invalid_tool_arguments"
    assert result["approval"] is None


def test_skill_query_can_load_deferred_tools_without_mode_specific_code():
    state = _state()
    skill_registry = SkillRegistry()
    _run_skill_loader(
        state=state,
        tool_call=ModelToolCall(
            id="call_perf",
            name="skill",
            arguments={"action": "load", "query": "performance load test runner"},
        ),
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        skill_registry=skill_registry,
        skill_runtime_service=SkillRuntimeService(skill_registry),
    )
    assert any(key.startswith("performance-") or key.startswith("perf-") for key in state["model_visible_tool_keys"])


def test_unknown_skill_call_points_to_unified_skill_tool():
    state = {
        "permission_decisions": [],
        "context_bundle": {
            "available_skills": [{"key": "agently-mail", "name": "Tencent Agent Mail"}]
        },
        "model_visible_tool_keys": ["skill"],
        "denied_tool_keys": [],
        "event_log": [],
        "turn_id": "turn_1",
        "trace_id": "trace_1",
    }
    result = asyncio.run(
        _resolve_tool_call(
            state=state,
            tool_call=ModelToolCall(id="call_1", name="agently-mail", arguments={}),
            tool_registry=ToolRegistry(),
            permission_service=None,
            tool_runtime_service=None,
            tool_job_service=None,
            tool_context=None,
        )
    )
    output = result["tool_result"]["output"]
    assert output["error_kind"] == "skill_invoked_as_tool"
    assert output["suggested_tools"] == ["skill"]


def test_input_orchestrator_does_not_add_mail_specific_intent_layer():
    orchestrator = InputOrchestratorService(ModeRegistry())
    session = SessionRecord(
        id="s1",
        title="mail",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    request = orchestrator.orchestrate(session, SendMessageRequest(content="发一个测试邮件"))
    assert "agently-mail" not in request.skill_keys
    assert all(item != "mail-" + "capability" for item in request.skill_keys)
