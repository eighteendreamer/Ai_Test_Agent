"""RecordingApprovalService 审批编排单测（方案 4.2 环节④/⑤，P0-8）。

Fake store / recorder service，验证：审批创建结构（approval_type / 录制载荷 /
审计快照）、approved → launch + 事件、denied → 降级事件、非 ui_recording 审批
拒绝委托。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.application.recorder.recording_approval_service import RecordingApprovalService
from src.schemas.recording import RecordingSession, RecordingStatus
from src.schemas.session import ToolApprovalRequest, ToolApprovalStatus


class FakeStore:
    def __init__(self) -> None:
        self.approvals: dict[str, ToolApprovalRequest] = {}
        self.events: list[tuple[str, str]] = []  # (session_id, event_type)

    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None:
        self.approvals[approval.id] = approval

    async def append_event(self, session_id: str, event) -> None:
        self.events.append((session_id, event.type))


class FakeRecorderService:
    def __init__(self) -> None:
        self.launch_calls: list = []

    async def launch(self, payload) -> RecordingSession:
        self.launch_calls.append(payload)
        return RecordingSession(
            project_id=payload.project_id,
            entry_url=payload.entry_url,
            session_id=payload.session_id,
            approval_id=payload.approval_id,
            status=RecordingStatus.launching,
        )


class _Request:
    """UIAutomationRequestState 替身（字段兼容即可）。"""

    project_id = "proj-1"
    target_url = "https://example.com"


KNOWLEDGE_GATE = {
    "decision": "need_recording",
    "reason": "insufficient:graph(...)",
    "sources": {"graph": {}, "cases": {}, "memory": {}},
}


def _make_approval(approval_type: str = "ui_recording") -> ToolApprovalRequest:
    metadata = {
        "approval_type": approval_type,
        "recording_request": {
            "project_id": "proj-1",
            "entry_url": "https://example.com",
            "session_id": "session-1",
            "driver": {"kind": "embedded"},
        },
    }
    return ToolApprovalRequest(
        id="appr-1",
        session_id="session-1",
        tool_key="ui-automation-runner",
        tool_name="UI自动化模式",
        reason="r",
        created_at=datetime.now(timezone.utc),
        metadata=metadata,
    )


def test_create_approval_persists_with_recording_payload_and_gate_snapshot():
    store = FakeStore()
    service = RecordingApprovalService(recorder_service=FakeRecorderService(), session_store=store)

    approval = asyncio.run(service.create_approval(
        session_id="session-1",
        turn_id="turn-1",
        request=_Request(),
        knowledge_gate=KNOWLEDGE_GATE,
    ))

    assert approval.metadata["approval_type"] == "ui_recording"
    assert approval.metadata["recording_request"]["project_id"] == "proj-1"
    assert approval.metadata["recording_request"]["entry_url"] == "https://example.com"
    assert approval.metadata["knowledge_gate"] == KNOWLEDGE_GATE
    assert store.approvals[approval.id].id == approval.id
    assert ("session-1", "approval.created") in store.events


def test_apply_decision_approved_launches_recording_and_emits_event():
    store = FakeStore()
    recorder = FakeRecorderService()
    service = RecordingApprovalService(recorder_service=recorder, session_store=store)
    approval = _make_approval()

    result = asyncio.run(service.apply_decision(approval, decision=ToolApprovalStatus.approved, reason=None))

    assert result["status"] == "launched"
    assert result["recording_id"]
    assert len(recorder.launch_calls) == 1
    launched = recorder.launch_calls[0]
    assert launched.project_id == "proj-1"
    assert launched.entry_url == "https://example.com"
    assert launched.approval_id == "appr-1"
    assert launched.session_id == "session-1"
    assert ("session-1", "recorder.launch_requested") in store.events


def test_apply_decision_denied_emits_decline_event_without_launch():
    store = FakeStore()
    recorder = FakeRecorderService()
    service = RecordingApprovalService(recorder_service=recorder, session_store=store)
    approval = _make_approval()

    result = asyncio.run(service.apply_decision(approval, decision=ToolApprovalStatus.denied, reason="不想录"))

    assert result["status"] == "declined"
    assert recorder.launch_calls == []
    assert ("session-1", "recorder.approval_declined") in store.events


def test_apply_decision_rejects_non_ui_recording_approval():
    service = RecordingApprovalService(recorder_service=FakeRecorderService(), session_store=FakeStore())
    approval = _make_approval(approval_type="tool_call")

    try:
        asyncio.run(service.apply_decision(approval, decision=ToolApprovalStatus.approved, reason=None))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "not a ui_recording approval" in str(exc)


# ------------------------------------------------ SessionService 委托分支


def _build_session_service():
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.application.projects.project_service import ProjectService
    from src.application.sessions.session_service import SessionService
    from src.application.projects.project_store import InMemoryProjectStore
    from src.registry.modes import ModeRegistry
    from src.runtime.store import InMemorySessionStore

    store = InMemorySessionStore()
    projects = ProjectService(store=InMemoryProjectStore())
    asyncio.run(projects.initialize())
    modes = ModeRegistry()
    service = SessionService(
        store=store,
        input_orchestrator_service=InputOrchestratorService(mode_registry=modes),
        runtime_service=object(),
        mode_registry=modes,
        project_service=projects,
    )
    return service, store


def test_resolve_approval_ui_recording_branch_launches_recording():
    from src.schemas.session import ApprovalDecisionRequest, CreateSessionRequest

    service, store = _build_session_service()
    detail = asyncio.run(service.create_session(CreateSessionRequest(title="录制会话")))
    session_id = detail.id
    recorder = FakeRecorderService()
    approval_service = RecordingApprovalService(recorder_service=recorder, session_store=store)
    service.set_recording_approval_service(approval_service)
    approval = _make_approval()
    approval.session_id = session_id
    asyncio.run(store.save_approval(session_id, approval))

    resolved = asyncio.run(
        service.resolve_approval(
            session_id,
            approval.id,
            ApprovalDecisionRequest(decision=ToolApprovalStatus.approved),
        )
    )

    assert resolved.status == ToolApprovalStatus.approved
    assert len(recorder.launch_calls) == 1  # 审批通过 → 自动 launch 录制
    events = [e.type for e in asyncio.run(store.list_events(session_id, limit=None))]
    assert "recorder.launch_requested" in events


def test_resolve_approval_ui_recording_without_service_emits_unavailable_event():
    from src.schemas.session import ApprovalDecisionRequest, CreateSessionRequest

    service, store = _build_session_service()
    detail = asyncio.run(service.create_session(CreateSessionRequest(title="录制会话")))
    session_id = detail.id
    approval = _make_approval()
    approval.session_id = session_id
    asyncio.run(store.save_approval(session_id, approval))

    resolved = asyncio.run(
        service.resolve_approval(
            session_id,
            approval.id,
            ApprovalDecisionRequest(decision=ToolApprovalStatus.denied),
        )
    )

    assert resolved.status == ToolApprovalStatus.denied
    events = [e.type for e in asyncio.run(store.list_events(session_id, limit=None))]
    assert "recorder.approval_unavailable" in events


def test_resolve_approval_regular_tool_approval_keeps_default_resume_path():
    """非 ui_recording 审批仍走既有 graph resume 链路（不破坏工具审批）。"""
    from src.schemas.session import ApprovalDecisionRequest, CreateSessionRequest

    service, store = _build_session_service()
    detail = asyncio.run(service.create_session(CreateSessionRequest(title="工具会话")))
    session_id = detail.id
    approval = _make_approval(approval_type="tool_call")
    approval.session_id = session_id
    asyncio.run(store.save_approval(session_id, approval))

    resolved = asyncio.run(
        service.resolve_approval(
            session_id,
            approval.id,
            ApprovalDecisionRequest(decision=ToolApprovalStatus.approved),
        )
    )

    assert resolved.status == ToolApprovalStatus.approved
    # 默认链路：session 进入 running + resuming_after_approval 控制态
    session = asyncio.run(store.get_session(session_id))
    assert session.status.value in {"running", "interrupted"}
