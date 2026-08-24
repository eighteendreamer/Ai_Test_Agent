"""recordings API 路由契约测试（方案第 8 章 / P0-7）。

不连 PG / Memgraph / MinIO：recorder_service / recording_store /
recording_graph_store / artifact_storage_service 均为 Fake；embedded_bridge
用真实 EmbeddedBridge（纯内存，验证桥接通道语义：登记 / 事件幂等收敛 /
指令 long-poll / 会话关闭拒绝）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.recordings import router
from src.application.recorder.drivers.embedded_bridge import EmbeddedBridge
from src.schemas.recording import (
    RecorderEvent,
    RecordingControlAction,
    RecordingCreateRequest,
    RecordingEventAck,
    RecordingSession,
    RecordingStatus,
)


# ------------------------------------------------------------------ Fakes


class FakeRecorderService:
    """RecorderSessionService 替身：记录调用、按脚本返回/抛错。"""

    def __init__(self) -> None:
        self.sessions: dict[str, RecordingSession] = {}
        self.events: dict[str, list[RecorderEvent]] = {}
        self.launch_calls: list[RecordingCreateRequest] = []
        self.launch_error: ValueError | None = None
        self.control_calls: list[tuple[str, RecordingControlAction]] = []
        self.control_error: ValueError | None = None

    async def launch(self, payload: RecordingCreateRequest) -> RecordingSession:
        self.launch_calls.append(payload)
        if self.launch_error is not None:
            raise self.launch_error
        session = RecordingSession(
            project_id=payload.project_id,
            name=payload.name,
            entry_url=payload.entry_url,
            session_id=payload.session_id,
            approval_id=payload.approval_id,
        )
        self.sessions[session.id] = session
        return session

    async def list_sessions(
        self,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RecordingSession]:
        items = list(self.sessions.values())
        if project_id:
            items = [s for s in items if s.project_id == project_id]
        return items[offset : offset + limit]

    async def get_session(self, recording_id: str) -> RecordingSession | None:
        return self.sessions.get(recording_id)

    async def get_events(self, recording_id: str) -> list[RecorderEvent]:
        return self.events.get(recording_id, [])

    async def control(
        self, recording_id: str, action: RecordingControlAction
    ) -> RecordingSession:
        self.control_calls.append((recording_id, action))
        if self.control_error is not None:
            raise self.control_error
        session = self.sessions[recording_id]
        if action is RecordingControlAction.start:
            session.status = RecordingStatus.active
        return session


class FakeRecordingStore:
    def __init__(self) -> None:
        self.append_calls: list[tuple[str, list[RecorderEvent]]] = []
        self.append_ack = RecordingEventAck(accepted=0, duplicates=0)
        self.deleted: list[str] = []

    async def append_events(
        self, recording_id: str, events: list[RecorderEvent]
    ) -> RecordingEventAck:
        self.append_calls.append((recording_id, list(events)))
        return self.append_ack

    async def delete_session(self, recording_id: str) -> bool:
        self.deleted.append(recording_id)
        return True


class FakeGraphStore:
    def __init__(self) -> None:
        self.delete_calls: list[tuple[str, str]] = []
        self.subgraph_calls: list[tuple[str, str]] = []
        self.subgraph_result: dict[str, Any] = {"status": "success", "nodes": [], "edges": []}

    async def delete_recording(self, recording_id: str, *, project_id: str) -> dict[str, Any]:
        self.delete_calls.append((recording_id, project_id))
        return {"status": "success", "deleted": 1}

    async def get_recording_subgraph(
        self, recording_id: str, *, project_id: str
    ) -> dict[str, Any]:
        self.subgraph_calls.append((recording_id, project_id))
        return dict(self.subgraph_result)


class FakeArtifactService:
    """enabled=False → 路由走本地产物目录分支。"""

    enabled = False


def _make_app(
    *,
    service: FakeRecorderService | None = None,
    bridge: EmbeddedBridge | None = None,
    store: FakeRecordingStore | None = None,
    graph: FakeGraphStore | None = None,
    artifact_root: str = ".artifacts-test",
    with_service: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    if with_service:
        app.state.recorder_service = service or FakeRecorderService()
    app.state.embedded_bridge = bridge or EmbeddedBridge()
    app.state.recording_store = store or FakeRecordingStore()
    app.state.recording_graph_store = graph or FakeGraphStore()
    app.state.artifact_storage_service = FakeArtifactService()
    app.state.settings = SimpleNamespace(artifact_root_dir=artifact_root)
    return app


def _make_session(service: FakeRecorderService, **overrides: Any) -> RecordingSession:
    session = RecordingSession(
        project_id=overrides.pop("project_id", "proj-1"),
        entry_url=overrides.pop("entry_url", "https://example.com"),
        **overrides,
    )
    service.sessions[session.id] = session
    return session


# ------------------------------------------------------------------ 注入脚本


def test_recorder_script_served_with_js_media_type():
    client = TestClient(_make_app())

    response = client.get("/api/v1/recordings/recorder.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "__qaRecorderSetEnabled" in response.text


# ------------------------------------------------------------------ 会话管理


def test_create_recording_returns_201_with_public_projection():
    service = FakeRecorderService()
    client = TestClient(_make_app(service=service))

    response = client.post(
        "/api/v1/recordings",
        json={"project_id": "proj-1", "entry_url": "https://example.com", "name": "冒烟"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == "proj-1"
    assert body["entry_url"] == "https://example.com"
    assert body["status"] == RecordingStatus.launching.value
    assert "events" not in body  # 公开投影不含事件流
    assert len(service.launch_calls) == 1


def test_create_recording_launch_value_error_maps_400():
    service = FakeRecorderService()
    service.launch_error = ValueError("driver unavailable")
    client = TestClient(_make_app(service=service))

    response = client.post(
        "/api/v1/recordings", json={"project_id": "proj-1", "entry_url": "https://example.com"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "driver unavailable"


def test_create_recording_blank_project_id_rejected_422():
    client = TestClient(_make_app())

    response = client.post(
        "/api/v1/recordings", json={"project_id": "  ", "entry_url": "https://example.com"}
    )

    assert response.status_code == 422


def test_list_recordings_returns_items_and_count():
    service = FakeRecorderService()
    _make_session(service, project_id="proj-1")
    _make_session(service, project_id="proj-2")
    client = TestClient(_make_app(service=service))

    response = client.get("/api/v1/recordings", params={"project_id": "proj-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["project_id"] == "proj-1"


def test_get_recording_detail_includes_events():
    service = FakeRecorderService()
    session = _make_session(service)
    service.events[session.id] = [
        RecorderEvent(seq=0, type="click"),
        RecorderEvent(seq=1, type="fill"),
    ]
    client = TestClient(_make_app(service=service))

    response = client.get(f"/api/v1/recordings/{session.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session.id
    assert [e["seq"] for e in body["events"]] == [0, 1]


def test_get_recording_unknown_returns_404():
    client = TestClient(_make_app())

    response = client.get("/api/v1/recordings/no-such-id")

    assert response.status_code == 404


def test_delete_recording_removes_graph_before_store():
    service = FakeRecorderService()
    store = FakeRecordingStore()
    graph = FakeGraphStore()
    session = _make_session(service)
    client = TestClient(_make_app(service=service, store=store, graph=graph))
    order: list[str] = []
    original_delete = graph.delete_recording

    async def traced_delete(recording_id: str, *, project_id: str) -> dict[str, Any]:
        order.append("graph")
        return await original_delete(recording_id, project_id=project_id)

    original_store_delete = store.delete_session

    async def traced_store_delete(recording_id: str) -> bool:
        order.append("store")
        return await original_store_delete(recording_id)

    graph.delete_recording = traced_delete  # type: ignore[method-assign]
    store.delete_session = traced_store_delete  # type: ignore[method-assign]

    response = client.delete(f"/api/v1/recordings/{session.id}")

    assert response.status_code == 200
    assert response.json()["graph"]["status"] == "success"
    assert response.json()["store_deleted"] is True
    assert order == ["graph", "store"]  # 图谱失败可重试 → 图先删、库后删
    assert graph.delete_calls == [(session.id, "proj-1")]
    assert store.deleted == [session.id]


def test_delete_recording_unknown_returns_404():
    store = FakeRecordingStore()
    graph = FakeGraphStore()
    client = TestClient(_make_app(store=store, graph=graph))

    response = client.delete("/api/v1/recordings/no-such-id")

    assert response.status_code == 404
    assert store.deleted == []
    assert graph.delete_calls == []


# ------------------------------------------------------------------ Electron 桥接


def test_attach_registry_registers_known_session():
    bridge = EmbeddedBridge()
    bridge.attach("rec-1")
    client = TestClient(_make_app(bridge=bridge))

    response = client.post(
        "/api/v1/recordings/rec-1/attach-registry",
        json={"capabilities": {"cdp": True}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "registered", "recording_id": "rec-1"}
    assert bridge.is_registered("rec-1")


def test_attach_registry_unknown_session_returns_404():
    client = TestClient(_make_app())

    response = client.post("/api/v1/recordings/unknown/attach-registry", json={})

    assert response.status_code == 404


def test_events_batch_empty_is_noop():
    client = TestClient(_make_app())

    response = client.post(
        "/api/v1/recordings/rec-1/events:batch",
        json={"events": [], "client_batch_id": "b-0"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": 0,
        "duplicates": 0,
        "sink": "noop",
        "client_batch_id": "b-0",
    }


def test_events_batch_active_session_forwards_via_bridge_with_dedupe():
    bridge = EmbeddedBridge()
    bridge.attach("rec-1")
    bridge.register_session("rec-1", {})
    store = FakeRecordingStore()
    client = TestClient(_make_app(bridge=bridge, store=store))

    # 批内重复：seq=1 出现两次 → duplicates_in_batch=1
    response = client.post(
        "/api/v1/recordings/rec-1/events:batch",
        json={
            "events": [
                {"seq": 0, "type": "click"},
                {"seq": 1, "type": "fill"},
                {"seq": 1, "type": "fill"},
            ],
            "client_batch_id": "b-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sink"] == "bridge"
    assert body["accepted"] == 2
    assert body["duplicates_in_batch"] == 1
    assert body["duplicates"] == 1
    assert store.append_calls == []  # 活跃会话不直写 store

    # 网络重试重复批次：全部命中幂等预收敛 → duplicates_retry
    retry = client.post(
        "/api/v1/recordings/rec-1/events:batch",
        json={"events": [{"seq": 0, "type": "click"}], "client_batch_id": "b-2"},
    )
    assert retry.json()["duplicates_retry"] == 1
    assert retry.json()["accepted"] == 0


def test_events_batch_unknown_session_falls_back_to_store():
    store = FakeRecordingStore()
    store.append_ack = RecordingEventAck(accepted=2, duplicates=1)
    client = TestClient(_make_app(store=store))

    response = client.post(
        "/api/v1/recordings/ghost/events:batch",
        json={"events": [{"seq": 0, "type": "click"}, {"seq": 1, "type": "key"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sink"] == "store"
    assert body["accepted"] == 2
    assert body["duplicates"] == 1
    assert len(store.append_calls) == 1
    assert store.append_calls[0][0] == "ghost"


def test_events_batch_closed_session_returns_409():
    bridge = EmbeddedBridge()
    bridge.attach("rec-1")
    bridge.close_session("rec-1")
    client = TestClient(_make_app(bridge=bridge))

    response = client.post(
        "/api/v1/recordings/rec-1/events:batch",
        json={"events": [{"seq": 0, "type": "click"}]},
    )

    assert response.status_code == 409


def test_poll_commands_returns_enqueued_command():
    bridge = EmbeddedBridge()
    bridge.attach("rec-1")
    bridge.enqueue_command("rec-1", "navigate", {"url": "https://example.com"})
    client = TestClient(_make_app(bridge=bridge))

    response = client.get("/api/v1/recordings/rec-1/commands", params={"wait_seconds": 0})

    assert response.status_code == 200
    body = response.json()
    assert body["recording_id"] == "rec-1"
    assert [c["action"] for c in body["commands"]] == ["navigate"]
    assert body["commands"][0]["payload"]["url"] == "https://example.com"


def test_poll_commands_unknown_session_returns_empty():
    client = TestClient(_make_app())

    response = client.get("/api/v1/recordings/ghost/commands", params={"wait_seconds": 0})

    assert response.status_code == 200
    assert response.json()["commands"] == []


def test_screenshot_upload_falls_back_to_local_backend(tmp_path):
    bridge = EmbeddedBridge()
    bridge.attach("rec-1")
    client = TestClient(_make_app(bridge=bridge, artifact_root=str(tmp_path)))

    response = client.post(
        "/api/v1/recordings/rec-1/screenshots",
        files={"file": ("frame.png", b"\x89PNG-fake-bytes", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "local"
    assert body["size_bytes"] == len(b"\x89PNG-fake-bytes")
    assert body["ref"].startswith("local://recordings/rec-1/")
    # 产物真实落盘且内容一致；最近帧已缓存到 bridge（capture_screenshot 数据源）
    object_name = body["ref"].rsplit("/", 1)[-1]
    stored = (tmp_path / "recordings" / "rec-1" / object_name).read_bytes()
    assert stored == b"\x89PNG-fake-bytes"
    assert bridge._sessions["rec-1"].last_screenshot == b"\x89PNG-fake-bytes"


def test_screenshot_upload_rejects_oversize(tmp_path):
    client = TestClient(_make_app(artifact_root=str(tmp_path)))
    oversize = b"x" * (10 * 1024 * 1024 + 1)

    response = client.post(
        "/api/v1/recordings/rec-1/screenshots",
        files={"file": ("big.png", oversize, "image/png")},
    )

    assert response.status_code == 413


def test_screenshot_upload_rejects_non_image(tmp_path):
    client = TestClient(_make_app(artifact_root=str(tmp_path)))

    response = client.post(
        "/api/v1/recordings/rec-1/screenshots",
        files={"file": ("log.txt", b"not-an-image", "text/plain")},
    )

    assert response.status_code == 415


# ------------------------------------------------------------------ 数据面


def test_control_maps_errors_to_status_codes():
    service = FakeRecorderService()
    session = _make_session(service)
    client = TestClient(_make_app(service=service))

    ok = client.post(f"/api/v1/recordings/{session.id}/control", json={"action": "start"})
    assert ok.status_code == 200
    assert ok.json()["status"] == RecordingStatus.active.value
    assert service.control_calls == [(session.id, RecordingControlAction.start)]

    service.control_error = ValueError("illegal transition: action=pause current=active-x")
    conflict = client.post(f"/api/v1/recordings/{session.id}/control", json={"action": "pause"})
    assert conflict.status_code == 409

    service.control_error = ValueError("runtime not available: rec gone")
    missing = client.post(f"/api/v1/recordings/{session.id}/control", json={"action": "pause"})
    assert missing.status_code == 404

    service.control_error = ValueError("some other invalid input")
    bad = client.post(f"/api/v1/recordings/{session.id}/control", json={"action": "pause"})
    assert bad.status_code == 400


def test_control_invalid_action_rejected_422():
    client = TestClient(_make_app())

    response = client.post("/api/v1/recordings/rec-1/control", json={"action": "rewind"})

    assert response.status_code == 422


def test_recording_graph_returns_subgraph():
    service = FakeRecorderService()
    graph = FakeGraphStore()
    graph.subgraph_result = {
        "status": "success",
        "nodes": [{"id": "rec-1", "kind": "recording"}],
        "edges": [],
    }
    session = _make_session(service)
    client = TestClient(_make_app(service=service, graph=graph))

    response = client.get(f"/api/v1/recordings/{session.id}/graph")

    assert response.status_code == 200
    assert response.json()["nodes"] == [{"id": "rec-1", "kind": "recording"}]
    assert graph.subgraph_calls == [(session.id, "proj-1")]


def test_recording_graph_unknown_session_returns_404():
    graph = FakeGraphStore()
    client = TestClient(_make_app(graph=graph))

    response = client.get("/api/v1/recordings/ghost/graph")

    assert response.status_code == 404
    assert graph.subgraph_calls == []


def test_endpoints_return_503_when_service_uninitialized():
    client = TestClient(_make_app(with_service=False))

    response = client.post(
        "/api/v1/recordings", json={"project_id": "p", "entry_url": "https://e.com"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "recorder service not initialized"
