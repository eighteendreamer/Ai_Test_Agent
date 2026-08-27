"""录制会话 API 路由（方案第 8 章 / P0-7）。

端点分三组：
- 会话管理：POST 创建 / GET 列表·详情 / DELETE 删除；
- Electron 桥接（embedded 驱动三通道）：attach-registry 登记、events:batch
  事件上报、commands 指令 long-poll、screenshots 截图上传；
- 数据面：control 控制条指令、graph 子图查询、recorder.js 注入脚本下发。

幂等边界：events:batch 活跃会话走 bridge 预收敛（未知会话直接落 store，
(recording_id, seq) 唯一约束为最终防线）；control 由 RecorderSessionService
状态机权威校验。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile

from src.schemas.recording import (
    RecordingControlRequest,
    RecordingCreateRequest,
    RecordingDetail,
    RecordingEventAck,
    RecordingEventBatchRequest,
    RecordingPublic,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recordings", tags=["recordings"])

# 注入脚本由后端持有统一下发（三端同版本，方案 5.2）
_RECORDER_JS_PATH = (
    Path(__file__).resolve().parents[2] / "application" / "recorder" / "assets" / "recorder.js"
)
_recorder_js_cache: str | None = None

# 截图上传边界：单帧 ≤ 10MB，仅接受图片类型（默认按 PNG 处理）
_SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024
_COMMANDS_MAX_WAIT_SECONDS = 30.0


def _recorder_service(request: Request) -> Any:
    service = getattr(request.app.state, "recorder_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="recorder service not initialized")
    return service


def _bridge(request: Request) -> Any:
    bridge = getattr(request.app.state, "embedded_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="embedded bridge not initialized")
    return bridge


# ------------------------------------------------------------ 注入脚本


@router.get("/recorder.js")
def get_recorder_script() -> Response:
    """三端共用的 recorder.js（Electron attach-debugger 时拉取注入）。"""
    global _recorder_js_cache
    if _recorder_js_cache is None:
        if not _RECORDER_JS_PATH.is_file():
            raise HTTPException(status_code=404, detail="recorder.js asset missing")
        _recorder_js_cache = _RECORDER_JS_PATH.read_text(encoding="utf-8")
    return Response(_recorder_js_cache, media_type="text/javascript; charset=utf-8")


# ------------------------------------------------------------ 会话管理


@router.post("", status_code=201)
async def create_recording(payload: RecordingCreateRequest, request: Request) -> RecordingPublic:
    """创建录制会话（编排层审批通过后调用；保留手动创建用于调试）。"""
    service = _recorder_service(request)
    try:
        session = await service.launch(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordingPublic.from_session(session)


@router.get("")
async def list_recordings(
    request: Request,
    project_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    service = _recorder_service(request)
    sessions = await service.list_sessions(project_id=project_id, limit=limit, offset=offset)
    return {
        "items": [RecordingPublic.from_session(s).model_dump(mode="json") for s in sessions],
        "count": len(sessions),
    }


@router.get("/{recording_id}")
async def get_recording(recording_id: str, request: Request) -> RecordingDetail:
    """详情：会话元数据 + 事件流 + 固化指标。"""
    service = _recorder_service(request)
    session = await service.get_session(recording_id)
    if session is None:
        raise HTTPException(status_code=404, detail="recording not found")
    events = await service.get_events(recording_id)
    detail = RecordingDetail.from_session(session)
    detail.events = events
    return detail


@router.delete("/{recording_id}")
async def delete_recording(recording_id: str, request: Request) -> dict[str, Any]:
    """删除：先 Memgraph 删 Recording/Action 子图（Page/Element 保留），
    再删 PG 行（会话 + 事件流水）；图谱失败时 PG 保留可重试。"""
    service = _recorder_service(request)
    graph_store = request.app.state.recording_graph_store
    store = request.app.state.recording_store
    session = await service.get_session(recording_id)
    if session is None:
        raise HTTPException(status_code=404, detail="recording not found")

    graph_result = await graph_store.delete_recording(
        recording_id, project_id=session.project_id
    )
    store_deleted = await store.delete_session(recording_id)
    logger.info(
        "recording deleted via api: recording_id=%s graph_status=%s store_deleted=%s",
        recording_id,
        graph_result.get("status"),
        store_deleted,
    )
    return {
        "status": "success",
        "recording_id": recording_id,
        "graph": graph_result,
        "store_deleted": store_deleted,
    }


# ------------------------------------------------------------ Electron 桥接


@router.post("/{recording_id}/attach-registry")
async def attach_registry(
    recording_id: str, request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Electron 窗口创建 + 注入完成后的登记握手（launching → ready 判定）。"""
    bridge = _bridge(request)
    capabilities = dict((payload or {}).get("capabilities") or {})
    ok = bridge.register_session(recording_id, capabilities)
    if not ok:
        raise HTTPException(status_code=404, detail="recording session unknown or closed")
    return {"status": "registered", "recording_id": recording_id}


@router.post("/{recording_id}/events:batch")
async def append_events_batch(
    recording_id: str, payload: RecordingEventBatchRequest, request: Request
) -> dict[str, Any]:
    """批量事件上报（幂等）。

    活跃 embedded 会话 → bridge 预收敛转发消费循环；未知会话（服务重启、
    Electron 缓冲补投）→ 直接落 PG，唯一约束兜底；已关闭会话 → 409。
    """
    bridge = _bridge(request)
    store = request.app.state.recording_store
    if not payload.events:
        return {"accepted": 0, "duplicates": 0, "sink": "noop", "client_batch_id": payload.client_batch_id}

    result = bridge.ingest_events(recording_id, payload.events)
    if result.rejected_reason == "unknown_recording":
        ack: RecordingEventAck = await store.append_events(recording_id, payload.events)
        return {
            "accepted": ack.accepted,
            "duplicates": ack.duplicates,
            "sink": "store",
            "client_batch_id": payload.client_batch_id,
        }
    if result.rejected_reason == "session_closed":
        raise HTTPException(status_code=409, detail="recording session already closed")
    return {
        "accepted": result.forwarded,
        "duplicates": result.duplicates_in_batch + result.duplicates_retry,
        "duplicates_in_batch": result.duplicates_in_batch,
        "duplicates_retry": result.duplicates_retry,
        "sink": "bridge",
        "client_batch_id": payload.client_batch_id,
    }


@router.get("/{recording_id}/commands")
async def poll_commands(
    recording_id: str,
    request: Request,
    wait_seconds: float = Query(default=25.0, ge=0.0, le=_COMMANDS_MAX_WAIT_SECONDS),
) -> dict[str, Any]:
    """Electron 指令 long-poll：navigate / set_capture_enabled / close。"""
    bridge = _bridge(request)
    commands = await bridge.poll_commands(recording_id, wait_seconds=wait_seconds)
    return {"recording_id": recording_id, "commands": commands}


@router.post("/{recording_id}/screenshots")
async def upload_screenshot(
    recording_id: str, request: Request, file: UploadFile = File(...)
) -> dict[str, Any]:
    """multipart 截图上传：RustFS 优先，本地产物目录兜底；同时缓存最近帧。"""
    bridge = _bridge(request)
    content = await file.read()
    if len(content) > _SCREENSHOT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="screenshot exceeds 10MB limit")
    content_type = file.content_type or "image/png"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail=f"unsupported content type: {content_type}")

    # 最近帧缓存（活跃 embedded 会话；未知/已关会话忽略，产物照存）
    bridge.report_screenshot(recording_id, content)

    artifact_service = request.app.state.artifact_storage_service
    ref: str
    backend: str
    if artifact_service.enabled:
        stored = await artifact_service.store_uploaded_bytes(
            content=content,
            filename=f"{uuid4().hex}.png",
            object_prefix=f"recordings/{recording_id}",
            content_type=content_type,
        )
        ref = str(stored["uri"])
        backend = "rustfs"
    else:
        artifact_root = (
            Path(__file__).resolve().parents[2] / request.app.state.settings.artifact_root_dir
        )
        target_dir = artifact_root / "recordings" / recording_id
        target_dir.mkdir(parents=True, exist_ok=True)
        object_name = f"{uuid4().hex}.png"
        (target_dir / object_name).write_bytes(content)
        ref = f"local://recordings/{recording_id}/{object_name}"
        backend = "local"
    logger.info(
        "recording screenshot stored: recording_id=%s backend=%s size=%s",
        recording_id, backend, len(content),
    )
    return {"recording_id": recording_id, "ref": ref, "backend": backend, "size_bytes": len(content)}


# ------------------------------------------------------------ 数据面


@router.post("/{recording_id}/control")
async def control_recording(
    recording_id: str, payload: RecordingControlRequest, request: Request
) -> RecordingPublic:
    """控制条指令（后端权威状态机）：start/pause/resume/stop/destroy。"""
    service = _recorder_service(request)
    try:
        session = await service.control(recording_id, payload.action)
    except ValueError as exc:
        message = str(exc)
        if "runtime not available" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        if "illegal transition" in message:
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return RecordingPublic.from_session(session)


@router.get("/{recording_id}/graph")
async def get_recording_graph(recording_id: str, request: Request) -> dict[str, Any]:
    """该录制在 Memgraph 中的子图投影（前端可视化）。"""
    service = _recorder_service(request)
    session = await service.get_session(recording_id)
    if session is None:
        raise HTTPException(status_code=404, detail="recording not found")
    graph_store = request.app.state.recording_graph_store
    return await graph_store.get_recording_subgraph(
        recording_id, project_id=session.project_id
    )
