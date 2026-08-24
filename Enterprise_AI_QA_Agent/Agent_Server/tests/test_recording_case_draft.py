"""录制转用例草稿测试（P2-2）。

- 纯函数 build_recording_draft_payload：步骤生成（人话 action + 定位链 data）、
  脱敏 fill 文案、基线断言三级退化（navigated_to > title > manual_review）、
  source_refs 可追溯、case_key 模式合法、order 连续；
- RecordingCaseDraftService：completed 前置校验 / 项目归属校验 / 空事件流
  拒绝 / 正常路径委托 create_draft（Fake 记录调用）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from src.application.recorder.recording_case_draft_service import (
    RecordingCaseDraftService,
    build_recording_draft_payload,
)
from src.schemas.recording import RecorderEvent, RecordingSession, RecordingStatus


def _session(**overrides: Any) -> RecordingSession:
    defaults: dict[str, Any] = {
        "id": str(uuid4()),
        "project_id": "proj-1",
        "name": "登录流程",
        "entry_url": "https://app.example.com/login",
        "status": RecordingStatus.completed,
    }
    defaults.update(overrides)
    return RecordingSession(**defaults)


def _event(seq: int, etype: str, **overrides: Any) -> RecorderEvent:
    payload: dict[str, Any] = {
        "seq": seq,
        "type": etype,
        "page": {"url": "https://app.example.com/login", "title": "登录"},
        "target": None,
        "value": None,
        "page_effect": {},
    }
    payload.update(overrides)
    return RecorderEvent(**payload)


def _target(name: str) -> dict[str, Any]:
    return {
        "tag": "BUTTON",
        "locators": {"id": "submit", "text": name, "role_name": {"role": "button", "name": name}},
    }


def _login_events() -> list[RecorderEvent]:
    return [
        _event(0, "navigate"),
        _event(1, "page_scan", page_effect={"dom_hash": "abc"}),
        _event(
            2,
            "fill",
            target={"tag": "INPUT", "locators": {"css": "#username", "text": "用户名"}},
            value="alice",
        ),
        _event(3, "fill", value={"length": 8}),
        _event(4, "click", target=_target("登 录")),
        _event(5, "navigate", page={"url": "https://app.example.com/home", "title": "工作台"},
               page_effect={"navigated_to": "https://app.example.com/home"}),
    ]


# ---------------------------------------------------------------- 纯函数


def test_payload_steps_use_human_text_and_carry_locator_data() -> None:
    payload = build_recording_draft_payload(_session(), _login_events())

    assert payload.steps[0].action == "打开 https://app.example.com/login"
    assert payload.steps[1].action == "在「用户名」输入 alice"
    assert "已脱敏" in payload.steps[2].action
    assert payload.steps[3].action == "点击「登 录」"
    # 定位链摘要在 data（回放可执行、评审可查证据）
    assert payload.steps[3].data["locators"]["id"] == "submit"
    assert payload.steps[3].data["seq"] == 4
    # page_scan 不进步骤
    assert all("page_scan" != s.data.get("recorder_type") for s in payload.steps)
    # order 连续（schema 校验隐含，显式断言可读性）
    assert [s.order for s in payload.steps] == list(range(1, len(payload.steps) + 1))


def test_baseline_assertion_prefers_navigated_to() -> None:
    payload = build_recording_draft_payload(_session(), _login_events())
    assert payload.assertions[0].kind == "ui_url"
    assert payload.assertions[0].expected == "https://app.example.com/home"


def test_baseline_assertion_falls_back_to_title_then_manual_review() -> None:
    events = [_event(0, "click", target=_target("登 录"))]  # 无导航无 page_effect
    payload = build_recording_draft_payload(_session(), events)
    assert payload.assertions[0].kind == "ui_title"
    assert payload.assertions[0].operator == "contains"

    bare = [_event(0, "click", target=_target("x"), page={})]
    payload = build_recording_draft_payload(_session(), bare)
    assert payload.assertions[0].kind == "manual_review"


def test_source_refs_and_traceability_metadata() -> None:
    session = _session()
    payload = build_recording_draft_payload(session, _login_events())
    ref = payload.source_refs[0]
    assert ref.source_type == "ui_recording"
    assert ref.source_id == session.id
    # 生成来源证据三件套（规则引擎语义等价记录）
    assert payload.model_key == "rule-based-recorder-conversion"
    assert payload.prompt_version
    assert "recorder" in payload.skill_versions
    # case_key 符合 schema 模式 ^[a-zA-Z0-9][a-zA-Z0-9_-]*$
    assert payload.case_key.startswith("ui_rec_")


# ---------------------------------------------------------------- 服务编排


@dataclass
class _Bundle:
    case: Any
    version: Any


class _FakeTestCaseService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_draft(self, *, project_id: str, payload, created_by=None) -> _Bundle:
        self.calls.append({"project_id": project_id, "payload": payload, "created_by": created_by})
        case = type("Case", (), {"id": "case-1"})()
        version = type("Version", (), {"id": "ver-1"})()
        return _Bundle(case=case, version=version)


class _FakeRecorderService:
    def __init__(self, session: RecordingSession | None, events: list[RecorderEvent]) -> None:
        self._session = session
        self._events = events

    async def get_session(self, recording_id: str) -> RecordingSession | None:
        return self._session

    async def get_events(self, recording_id: str) -> list[RecorderEvent]:
        return list(self._events)


def _service(session: RecordingSession | None, events: list[RecorderEvent]):
    return RecordingCaseDraftService(
        recorder_service=_FakeRecorderService(session, events),
        test_case_service=_FakeTestCaseService(),
    )


def test_create_draft_delegates_to_existing_chain() -> None:
    async def scenario() -> None:
        session = _session()
        service = _service(session, _login_events())
        result = await service.create_draft_from_recording(session.id, created_by="user-1")
        assert result["case"].id == "case-1"
        assert result["step_count"] == 5  # navigate+fill+fill(脱敏)+click+navigate
        call = service._test_case_service.calls[0]
        assert call["project_id"] == "proj-1"
        assert call["created_by"] == "user-1"
        assert call["payload"].source_refs[0].source_id == session.id

    asyncio.run(scenario())


def test_unfinalized_recording_rejected() -> None:
    async def scenario() -> None:
        service = _service(_session(status=RecordingStatus.active), _login_events())
        with pytest.raises(ValueError, match="not finalized"):
            await service.create_draft_from_recording("rec-x")

    asyncio.run(scenario())


def test_unknown_recording_rejected() -> None:
    async def scenario() -> None:
        service = _service(None, [])
        with pytest.raises(ValueError, match="not found"):
            await service.create_draft_from_recording("rec-x")

    asyncio.run(scenario())


def test_project_mismatch_rejected() -> None:
    async def scenario() -> None:
        session = _session()
        service = _service(session, _login_events())
        with pytest.raises(ValueError, match="does not belong"):
            await service.create_draft_from_recording(session.id, project_id="other")

    asyncio.run(scenario())


def test_recording_without_replayable_actions_rejected() -> None:
    async def scenario() -> None:
        session = _session()
        service = _service(session, [_event(0, "page_scan")])
        with pytest.raises(ValueError, match="no replayable"):
            await service.create_draft_from_recording(session.id)

    asyncio.run(scenario())
