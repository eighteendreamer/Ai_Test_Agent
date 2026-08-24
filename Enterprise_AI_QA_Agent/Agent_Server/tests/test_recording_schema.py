"""P0-1 录制域数据契约纯单测（不连库）。

验收：schema 校验、批内 (recording_id, seq) 幂等语义、敏感输入脱敏。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.recording import (
    RECORDING_KNOWN_EVENT_TYPES,
    RecordingControlAction,
    RecordingControlRequest,
    RecordingCreateRequest,
    RecordingDriverKind,
    RecordingEventBatchRequest,
    RecordingPublic,
    RecordingSession,
    RecordingStatus,
    RecorderEvent,
    dedupe_event_batch,
    event_identity,
    mask_sensitive_input,
)


def _event(seq: int, event_type: str = "click") -> RecorderEvent:
    return RecorderEvent(
        seq=seq,
        type=event_type,
        page={"url": "https://example.com/login", "title": "Login"},
        target={
            "locators": {"id": "login-submit", "testid": None, "css": "form > button"},
            "tag": "BUTTON",
            "role": "button",
        },
        pixel={
            "viewport_point": {"x": 712, "y": 503},
            "bbox": {"x": 640, "y": 488, "w": 144, "h": 30},
            "rel_offset": {"rx": 0.5, "ry": 0.5},
        },
    )


def test_recording_session_defaults_and_status_machine_values() -> None:
    session = RecordingSession(project_id="proj-1", entry_url="https://example.com")
    assert session.id
    assert session.status is RecordingStatus.launching
    assert session.driver_kind is RecordingDriverKind.embedded
    assert session.step_count == 0
    # 状态机取值必须覆盖方案 4.1 全部状态
    assert {s.value for s in RecordingStatus} == {
        "launching",
        "ready",
        "active",
        "paused",
        "finalizing",
        "completed",
        "discarded",
        "failed",
    }


def test_control_actions_are_the_four_buttons() -> None:
    assert {a.value for a in RecordingControlAction} == {
        "start",
        "pause",
        "resume",
        "stop",
        "destroy",
    }
    request = RecordingControlRequest(action="stop")
    assert request.action is RecordingControlAction.stop


def test_create_request_rejects_blank_project_and_url() -> None:
    with pytest.raises(ValidationError):
        RecordingCreateRequest(project_id="  ", entry_url="https://example.com")
    with pytest.raises(ValidationError):
        RecordingCreateRequest(project_id="proj-1", entry_url="")


def test_event_identity_is_recording_id_and_seq() -> None:
    # 幂等键 = (recording_id, seq)，与 PG 联合唯一约束语义一致
    assert event_identity("rec-1", 3) == ("rec-1", 3)
    assert _event(3).identity("rec-1") == ("rec-1", 3)
    assert event_identity("rec-1", 3) == event_identity("rec-1", 3)
    assert event_identity("rec-1", 3) != event_identity("rec-1", 4)
    assert event_identity("rec-1", 3) != event_identity("rec-2", 3)


def test_dedupe_event_batch_collapses_retries() -> None:
    # 网络重试/重复批次：同 seq 只保留首次出现
    batch = [_event(0), _event(1), _event(2), _event(1), _event(0, "click"), _event(3)]
    deduped = dedupe_event_batch(batch)
    assert [event.seq for event in deduped] == [0, 1, 2, 3]
    # 保留的是首次出现的那条
    assert dedupe_event_batch([_event(5, "click"), _event(5, "fill")])[0].type == "click"


def test_dedupe_event_batch_preserves_order_and_unknown_types() -> None:
    # 未知事件类型前向兼容：原样保留
    batch = [_event(0), _event(1, "custom-future-type"), _event(2, "scroll")]
    deduped = dedupe_event_batch(batch)
    assert [event.seq for event in deduped] == [0, 1, 2]
    assert deduped[1].type == "custom-future-type"


def test_event_batch_request_carries_events() -> None:
    request = RecordingEventBatchRequest(events=[_event(0), _event(1)], client_batch_id="batch-7")
    assert len(request.events) == 2
    assert request.client_batch_id == "batch-7"


def test_recorder_event_rejects_blank_type_and_negative_seq() -> None:
    with pytest.raises(ValidationError):
        RecorderEvent(seq=0, type="   ")
    with pytest.raises(ValidationError):
        RecorderEvent(seq=-1, type="click")


def test_known_event_types_cover_plan_6_1() -> None:
    assert RECORDING_KNOWN_EVENT_TYPES == {
        "click",
        "dblclick",
        "fill",
        "key",
        "submit",
        "scroll",
        "navigate",
        "file_change",
    }


def test_mask_sensitive_input_keeps_length_only() -> None:
    # 安全红线：敏感字段只记长度不记明文
    masked = mask_sensitive_input("s3cret-password!")
    assert masked == {"length": len("s3cret-password!")}
    assert "s3cret" not in str(masked)
    assert mask_sensitive_input("") == {"length": 0}


def test_public_projection_from_session() -> None:
    session = RecordingSession(
        project_id="proj-1",
        entry_url="https://example.com/pay",
        name="支付流程",
        driver_kind=RecordingDriverKind.cdp_attach,
    )
    public = RecordingPublic.from_session(session)
    assert public.id == session.id
    assert public.driver_kind is RecordingDriverKind.cdp_attach
    assert public.status is RecordingStatus.launching
    assert public.step_count == 0
