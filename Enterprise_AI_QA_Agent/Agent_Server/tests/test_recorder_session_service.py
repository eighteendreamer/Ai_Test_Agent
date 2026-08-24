"""P0-6 RecorderSessionService 状态机与编排纯单测（Fake store/graph/driver）。

覆盖：launch→ready 握手、五控制动作全迁移路径、非法迁移拒绝、并发重复
指令占位拒绝、固化成功/失败、destroy 不落图谱不冲刷、paused 事件丢弃、
DB 重试耗尽丢弃计数。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest

from src.application.recorder.drivers import (
    BrowserDriver,
    DriverRegistry,
    EmbeddedBridge,
    EventChannel,
)
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.schemas.recording import (
    RecorderEvent,
    RecordingControlAction,
    RecordingCreateRequest,
    RecordingEventAck,
    RecordingSession,
    RecordingStatus,
)


# ---------------------------------------------------------------- fakes


class FakeDriver(BrowserDriver):
    kind = "embedded"

    def __init__(self) -> None:
        self.opened: list[tuple[str, tuple[int, int]]] = []
        self.capture_calls: list[bool] = []
        self.closed = False
        self.injected = False
        self._ready_event = asyncio.Event()
        self._channel = EventChannel()

    def publish_event(self, payload: dict[str, Any]) -> None:
        """模拟驱动侧事件到达（真实链路 = bridge.ingest_events → channel）。"""
        self._channel.publish(payload)

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        self.opened.append((url, viewport))

    async def inject_recorder(self, binding_name: str = "__qaRecordEmit") -> None:
        self.injected = True

    def mark_ready(self) -> None:
        self._ready_event.set()

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        return self._channel.iterate()

    async def capture_screenshot(self) -> bytes:
        return b""

    async def current_page_info(self) -> dict[str, Any]:
        return {}

    async def set_capture_enabled(self, enabled: bool) -> None:
        self.capture_calls.append(enabled)

    async def close(self) -> None:
        self.closed = True


class FakeStore:
    """内存版 PostgresRecordingStore。"""

    def __init__(self) -> None:
        self.sessions: dict[str, RecordingSession] = {}
        self.events: dict[str, list[RecorderEvent]] = {}
        self.append_failures = 0  # 前 N 次 append_events 抛错（重试测试）
        self.update_error: Exception | None = None

    async def create_session(self, session: RecordingSession) -> RecordingSession:
        self.sessions[session.id] = session
        return session

    async def append_events(self, recording_id: str, events: list[RecorderEvent]) -> RecordingEventAck:
        if self.append_failures > 0:
            self.append_failures -= 1
            raise RuntimeError("simulated pg failure")
        known = self.events.setdefault(recording_id, [])
        existing = {e.seq for e in known}
        accepted = sum(1 for e in events if e.seq not in existing)
        known.extend(e for e in events if e.seq not in existing)
        return RecordingEventAck(accepted=accepted, duplicates=len(events) - accepted)

    async def update_status(
        self,
        recording_id: str,
        status: RecordingStatus,
        *,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        finalize_metrics: dict[str, Any] | None = None,
    ) -> RecordingSession | None:
        if self.update_error is not None:
            raise self.update_error
        session = self.sessions.get(recording_id)
        if session is None:
            return None
        session.status = status
        if started_at:
            session.started_at = started_at
        if ended_at:
            session.ended_at = ended_at
        if finalize_metrics is not None:
            session.finalize_metrics = finalize_metrics
        session.updated_at = datetime.now(timezone.utc)
        return session

    async def get_session(self, recording_id: str) -> RecordingSession | None:
        return self.sessions.get(recording_id)

    async def list_sessions(self, project_id=None, limit=50, offset=0):  # noqa: ANN001
        values = list(self.sessions.values())
        if project_id:
            values = [s for s in values if s.project_id == project_id]
        return values[offset : offset + limit]

    async def get_events(self, recording_id: str) -> list[RecorderEvent]:
        return list(self.events.get(recording_id, []))

    async def discard_session(self, recording_id: str) -> RecordingSession | None:
        session = self.sessions.get(recording_id)
        if session is None:
            return None
        session.status = RecordingStatus.discarded
        session.ended_at = datetime.now(timezone.utc)
        return session


class FakeGraphStore:
    """内存版 RecordingGraphStore：finalize 可控成功/失败/挂起。"""

    def __init__(self) -> None:
        self.finalize_calls: list[tuple[RecordingSession, list[RecorderEvent]]] = []
        self.result: dict[str, Any] = {
            "status": "success",
            "metrics": {"action_vertices": 3},
            "integrity": {"reconciled": True},
        }
        self.hang_seconds: float = 0.0

    async def finalize(self, session: RecordingSession, events: list[RecorderEvent]) -> dict[str, Any]:
        self.finalize_calls.append((session, list(events)))
        if self.hang_seconds > 0:
            await asyncio.sleep(self.hang_seconds)
        return self.result


def _service(store: FakeStore | None = None, graph: FakeGraphStore | None = None):
    store = store or FakeStore()
    graph = graph or FakeGraphStore()
    bridge = EmbeddedBridge()
    registry = DriverRegistry()
    drivers: dict[str, FakeDriver] = {}

    def _factory(_config, *, recording_id: str, **_ctx) -> FakeDriver:
        driver = FakeDriver()
        drivers[recording_id] = driver
        return driver

    registry.register("embedded", _factory)
    service = RecorderSessionService(
        settings=None,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        graph_store=graph,  # type: ignore[arg-type]
        registry=registry,
        bridge=bridge,
    )
    return service, store, graph, bridge, drivers


def _request() -> RecordingCreateRequest:
    return RecordingCreateRequest(
        project_id="proj-1",
        entry_url="https://app.example.com",
        name="登录流程",
    )


def _payload(seq: int) -> dict[str, Any]:
    return {
        "seq": seq,
        "type": "click",
        "timestamp": "2026-08-24T12:00:00Z",
        "page": {"url": "https://app.example.com/home"},
        "target": {"tag": "BUTTON"},
        "value": None,
        "page_effect": {},
    }


async def _launch_until_ready(service, bridge, drivers, request=None) -> str:  # noqa: ANN001
    """launch → 驱动就绪 → 轮询等待内存态到 ready。"""
    session = await service.launch(request or _request(), ready_timeout=2.0)
    drivers[session.id].mark_ready()
    for _ in range(100):  # 最多等 2s
        if service.runtime_status(session.id) == RecordingStatus.ready:
            return session.id
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"recording did not reach ready: status={service.runtime_status(session.id)}"
    )


# ---------------------------------------------------------------- launch


def test_launch_creates_session_and_waits_for_ready() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        session = await service.launch(_request(), ready_timeout=2.0)
        assert session.status == RecordingStatus.launching
        assert service.runtime_status(session.id) == RecordingStatus.launching

        driver = drivers[session.id]
        assert driver.opened == [("https://app.example.com", (1440, 960))]
        assert driver.injected is True

        # Electron 登记 → 后台任务转 ready
        bridge.register_session(session.id, {"window_id": "w1"})
        driver.mark_ready()
        await asyncio.sleep(0.2)
        assert service.runtime_status(session.id) == RecordingStatus.ready
        assert store.sessions[session.id].status == RecordingStatus.ready

    asyncio.run(_scenario())


def test_launch_timeout_marks_failed() -> None:
    async def _scenario() -> None:
        service, store, _, _, _ = _service()
        session = await service.launch(_request(), ready_timeout=0.1)
        await asyncio.sleep(0.3)
        assert service.runtime_status(session.id) == RecordingStatus.failed
        assert store.sessions[session.id].status == RecordingStatus.failed

    asyncio.run(_scenario())


def test_launch_rejects_unregistered_driver_kind() -> None:
    async def _scenario() -> None:
        service, _, _, _, _ = _service()
        request = _request()
        request.driver.kind = "cdp-attach"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="driver kind not available"):
            await service.launch(request)

    asyncio.run(_scenario())


# ---------------------------------------------------------------- 正向全流程


def test_full_control_flow_active_pause_resume_stop_finalizes() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        recording_id = await _launch_until_ready(service, bridge, drivers)
        driver = drivers[recording_id]

        # start → active：采集开启 + 消费循环启动
        session = await service.control(recording_id, RecordingControlAction.start)
        assert session.status == RecordingStatus.active
        assert driver.capture_calls == [True]
        assert session.started_at is not None

        # 事件上报 → 消费循环攒批落库（0.5s 超时 flush）
        driver.publish_event(_payload(0))
        driver.publish_event(_payload(1))
        await asyncio.sleep(0.8)
        assert [e.seq for e in store.events[recording_id]] == [0, 1]

        # pause → paused：后续事件丢弃计数
        await service.control(recording_id, RecordingControlAction.pause)
        assert driver.capture_calls == [True, False]
        driver.publish_event(_payload(2))
        await asyncio.sleep(0.8)
        stats = service.runtime_stats(recording_id)
        assert stats["dropped_while_paused"] == 1
        assert [e.seq for e in store.events[recording_id]] == [0, 1]  # 未入库

        # resume → active
        await service.control(recording_id, RecordingControlAction.resume)
        assert driver.capture_calls == [True, False, True]

        # stop → finalizing → completed：固化调用 + 指标写入 + 驱动关闭
        session = await service.control(recording_id, RecordingControlAction.stop)
        assert session.status == RecordingStatus.completed
        assert session.ended_at is not None
        assert session.finalize_metrics["finalize"]["action_vertices"] == 3
        assert session.finalize_metrics["integrity"]["reconciled"] is True
        assert len(graph.finalize_calls) == 1
        finalized_session, finalized_events = graph.finalize_calls[0]
        assert finalized_session.id == recording_id
        assert [e.seq for e in finalized_events] == [0, 1]  # 固化读 PG 全量
        assert driver.closed is True

    asyncio.run(_scenario())


# ---------------------------------------------------------------- 非法迁移


def test_illegal_transitions_rejected() -> None:
    async def _scenario() -> None:
        service, _, _, bridge, drivers = _service()

        recording_id = await _launch_until_ready(service, bridge, drivers)
        # ready 状态下 pause/stop 之外的动作限制：pause 非法
        with pytest.raises(ValueError, match="illegal transition"):
            await service.control(recording_id, RecordingControlAction.pause)

        await service.control(recording_id, RecordingControlAction.start)
        # active 下 start/resume 非法
        with pytest.raises(ValueError, match="illegal transition"):
            await service.control(recording_id, RecordingControlAction.start)
        with pytest.raises(ValueError, match="illegal transition"):
            await service.control(recording_id, RecordingControlAction.resume)

        await service.control(recording_id, RecordingControlAction.stop)
        # completed 终态：一切控制非法（含 destroy）
        for action in RecordingControlAction:
            with pytest.raises(ValueError, match="illegal transition"):
                await service.control(recording_id, action)

    asyncio.run(_scenario())


def test_control_unknown_recording_rejected() -> None:
    async def _scenario() -> None:
        service, _, _, _, _ = _service()
        with pytest.raises(ValueError, match="runtime not available"):
            await service.control("rec-nope", RecordingControlAction.start)

    asyncio.run(_scenario())


# ---------------------------------------------------------------- 并发与失败


def test_concurrent_duplicate_stop_rejected_by_placeholder() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        recording_id = await _launch_until_ready(service, bridge, drivers)
        await service.control(recording_id, RecordingControlAction.start)
        graph.hang_seconds = 0.5  # 固化挂起，第一个 stop 未返回

        first = asyncio.create_task(service.control(recording_id, RecordingControlAction.stop))
        await asyncio.sleep(0.1)  # 第一个 stop 已占位 finalizing
        with pytest.raises(ValueError, match="illegal transition"):
            await service.control(recording_id, RecordingControlAction.stop)
        session = await first
        assert session.status == RecordingStatus.completed
        assert store.sessions[recording_id].status == RecordingStatus.completed

    asyncio.run(_scenario())


def test_stop_finalize_failure_marks_failed() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        graph.result = {"status": "error", "reason": "memgraph down"}
        recording_id = await _launch_until_ready(service, bridge, drivers)
        await service.control(recording_id, RecordingControlAction.start)

        session = await service.control(recording_id, RecordingControlAction.stop)
        assert session.status == RecordingStatus.failed
        assert "memgraph down" in session.finalize_metrics["finalize_error"]
        assert drivers[recording_id].closed is True

    asyncio.run(_scenario())


def test_destroy_discards_without_finalizing_or_flush() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        recording_id = await _launch_until_ready(service, bridge, drivers)
        await service.control(recording_id, RecordingControlAction.start)

        # 事件在消费循环 buffer 中（未满批、未到 flush 间隔）
        drivers[recording_id].publish_event(_payload(0))
        await asyncio.sleep(0.05)

        session = await service.control(recording_id, RecordingControlAction.destroy)
        assert session.status == RecordingStatus.discarded
        assert session.ended_at is not None
        assert graph.finalize_calls == []  # 不写图谱
        assert store.events.get(recording_id, []) == []  # 未冲刷：丢弃
        assert drivers[recording_id].closed is True

    asyncio.run(_scenario())


def test_consume_flush_retries_then_drops_on_persistent_db_error() -> None:
    async def _scenario() -> None:
        service, store, _, bridge, drivers = _service()
        store.append_failures = 3  # 3 次重试全失败
        recording_id = await _launch_until_ready(service, bridge, drivers)
        await service.control(recording_id, RecordingControlAction.start)

        drivers[recording_id].publish_event(_payload(0))
        await asyncio.sleep(2.5)  # 0.2+0.4+0.8 退避后丢弃
        stats = service.runtime_stats(recording_id)
        assert stats["dropped_on_db_error"] == 1
        assert store.events.get(recording_id) is None

    asyncio.run(_scenario())


def test_stop_flushes_pending_buffer_before_finalize() -> None:
    async def _scenario() -> None:
        service, store, graph, bridge, drivers = _service()
        recording_id = await _launch_until_ready(service, bridge, drivers)
        await service.control(recording_id, RecordingControlAction.start)

        # 事件刚进 buffer（不足一批），stop 取消消费循环时应冲刷
        drivers[recording_id].publish_event(_payload(0))
        await asyncio.sleep(0.05)
        await service.control(recording_id, RecordingControlAction.stop)

        assert [e.seq for e in store.events[recording_id]] == [0]
        _, finalized_events = graph.finalize_calls[0]
        assert [e.seq for e in finalized_events] == [0]

    asyncio.run(_scenario())
