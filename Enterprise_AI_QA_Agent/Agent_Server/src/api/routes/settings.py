from __future__ import annotations

import asyncio
import html
import json
import logging
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta, timezone
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from src.domain.models import SessionImportBundle, SessionRecord
from src.runtime.store import validate_session_import_bundle
from src.schemas.channel_config import (
    ChannelAdvancedSettings,
    ChannelConfigCreateRequest,
    ChannelConfigUpdateRequest,
    ChannelGatewaySessionReleaseRequest,
    ChannelInboundMessage,
    ChannelPairingApproveRequest,
    ChannelPairingStartRequest,
)
from src.schemas.email_config import EmailConfigCreateRequest, EmailConfigUpdateRequest
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
)
from src.schemas.settings import GeneralSettingsPayload, ModelConfigUpdateRequest

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)
# No built-in file-part limit counterpart (checked: Starlette's installed
# MultiPartParser.on_part_data only caps non-file parts); bound input before
# FastAPI spools a file upload to temporary storage.
_IMPORT_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class _DataImportBodyLimitRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def limited_handler(request: Request):
            max_file_bytes = request.app.state.settings.security.data_import_max_bytes
            max_request_bytes = max_file_bytes + _IMPORT_MULTIPART_OVERHEAD_BYTES
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except ValueError:
                    return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
                if declared_bytes < 0:
                    return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
                if declared_bytes > max_request_bytes:
                    logger.warning(
                        "settings_data_import_request_too_large",
                        extra={"declared_bytes": declared_bytes, "max_request_bytes": max_request_bytes},
                    )
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Backup request exceeds the configured limit of {max_request_bytes} bytes."},
                    )

            received_bytes = 0

            async def limited_receive():
                nonlocal received_bytes
                message = await request.receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > max_request_bytes:
                        logger.warning(
                            "settings_data_import_request_too_large",
                            extra={"received_bytes": received_bytes, "max_request_bytes": max_request_bytes},
                        )
                        raise HTTPException(
                            status_code=413,
                            detail=f"Backup request exceeds the configured limit of {max_request_bytes} bytes.",
                        )
                return message

            bounded_request = Request(request.scope, receive=limited_receive)
            return await original_handler(bounded_request)

        return limited_handler


@router.get("/models")
async def list_model_configs(request: Request):
    return await asyncio.to_thread(
        request.app.state.settings_service.list_model_configs
    )


@router.put("/models")
async def update_model_config(payload: ModelConfigUpdateRequest, request: Request):
    return await asyncio.to_thread(
        request.app.state.settings_service.update_model_config,
        payload,
    )


@router.patch("/models/{model_name:path}")
async def edit_model_config(model_name: str, payload: ModelConfigUpdateRequest, request: Request):
    try:
        return await asyncio.to_thread(
            request.app.state.settings_service.edit_model_config,
            model_name,
            payload,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/models/{model_name:path}/activate")
async def activate_model_config(model_name: str, request: Request):
    try:
        return await asyncio.to_thread(
            request.app.state.settings_service.activate_model_config,
            model_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/models/{model_name:path}/test-connection")
async def test_model_config_connection(model_name: str, request: Request):
    try:
        return await request.app.state.settings_service.test_model_config_connection(model_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc


@router.delete("/models/{model_name:path}")
async def delete_model_config(model_name: str, request: Request):
    try:
        return await asyncio.to_thread(
            request.app.state.settings_service.delete_model_config,
            model_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found.") from exc


@router.get("/email")
async def list_email_configs(request: Request):
    return await asyncio.to_thread(
        request.app.state.settings_service.list_email_configs
    )


@router.post("/email")
async def create_email_config(payload: EmailConfigCreateRequest, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.create_email_config, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/email/{config_id}")
async def update_email_config(config_id: int, payload: EmailConfigUpdateRequest, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.update_email_config, config_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Email channel '{config_id}' not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/email/{config_id}/activate")
async def activate_email_config(config_id: int, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.activate_email_config, config_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Email channel '{config_id}' not found.") from exc


@router.delete("/email/{config_id}")
async def delete_email_config(config_id: int, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.delete_email_config, config_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Email channel '{config_id}' not found.") from exc


@router.get("/channels")
async def list_channel_configs(request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.list_channel_configs)


@router.get("/channels/advanced")
async def get_channel_advanced_settings(request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.get_channel_advanced_settings)


@router.put("/channels/advanced")
async def update_channel_advanced_settings(payload: ChannelAdvancedSettings, request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.update_channel_advanced_settings, payload)


@router.post("/channels/gateway/evaluate")
async def evaluate_channel_inbound(payload: ChannelInboundMessage, request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.evaluate_channel_inbound, payload)


@router.get("/channels/gateway/pairing")
async def list_channel_pairing_requests(request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.list_channel_pairing_requests)


@router.post("/channels/gateway/pairing/approve")
async def approve_channel_pairing(payload: ChannelPairingApproveRequest, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.approve_channel_pairing, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pairing request not found or expired.") from exc


@router.post("/channels/gateway/session/release")
async def release_channel_gateway_session(payload: ChannelGatewaySessionReleaseRequest, request: Request):
    return await asyncio.to_thread(request.app.state.settings_service.release_channel_session, payload)


@router.post("/channels")
async def create_channel_config(payload: ChannelConfigCreateRequest, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.create_channel_config, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.patch("/channels/{config_id}")
async def update_channel_config(config_id: int, payload: ChannelConfigUpdateRequest, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.update_channel_config, config_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Communication channel '{config_id}' not found.") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.delete("/channels/{config_id}")
async def delete_channel_config(config_id: int, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.delete_channel_config, config_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Communication channel '{config_id}' not found.") from exc


@router.post("/channels/{domain}/pairing/start")
async def start_channel_pairing(domain: str, payload: ChannelPairingStartRequest, request: Request):
    try:
        return await asyncio.to_thread(
            request.app.state.settings_service.start_channel_pairing,
            domain,
            payload,
            str(request.base_url),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/channels/{domain}/pairing/active")
async def get_active_channel_pairing(domain: str, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.get_active_channel_pairing, domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/channels/pairing/{session_id}")
async def get_channel_pairing(session_id: str, request: Request):
    try:
        return await asyncio.to_thread(request.app.state.settings_service.get_channel_pairing, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pairing session not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/channels/pairing/{session_id}/mobile", response_class=HTMLResponse)
async def channel_pairing_mobile_page(session_id: str, request: Request):
    try:
        session = await asyncio.to_thread(request.app.state.settings_service.get_channel_pairing, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pairing session not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    confirm_url = f"{request.app.state.settings.api_v1_prefix.rstrip()}/settings/channels/pairing/{html.escape(session_id)}/confirm"
    if session.status == "confirmed":
        body = "<h1>已绑定</h1><p>这个通讯渠道已经完成手机扫码绑定。</p>"
    elif session.status == "expired":
        body = "<h1>二维码已过期</h1><p>请回到电脑端重新生成二维码。</p>"
    else:
        domain = html.escape(session.domain)
        body = f"""
        <h1>绑定 {domain} 通讯渠道</h1>
        <p>确认后，此手机扫码会话会被记录到系统的通讯渠道设置中。当前步骤只完成绑定配置，不会启动机器人或接收消息。</p>
        <label>设备名称 <input id="deviceName" value="Mobile device" /></label>
        <button id="confirmBtn">确认绑定</button>
        <p id="result"></p>
        <script>
          const button = document.getElementById("confirmBtn");
          const result = document.getElementById("result");
          button.addEventListener("click", async () => {{
            button.disabled = true;
            const name = encodeURIComponent(document.getElementById("deviceName").value || "Mobile device");
            const response = await fetch("{confirm_url}?device_name=" + name, {{ method: "POST" }});
            result.textContent = response.ok ? "绑定完成，可以回到电脑端继续。" : "绑定失败，请重新生成二维码。";
          }});
        </script>
        """
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <title>通讯渠道扫码绑定</title>
          <style>
            body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #0f172a; }}
            main {{ max-width: 520px; margin: 0 auto; padding: 32px 20px; }}
            h1 {{ font-size: 24px; margin: 0 0 14px; }}
            p {{ color: #475569; line-height: 1.7; }}
            label {{ display: flex; flex-direction: column; gap: 8px; margin: 20px 0; color: #334155; }}
            input {{ border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px; font: inherit; }}
            button {{ width: 100%; border: 0; border-radius: 8px; padding: 13px 16px; background: #2563eb; color: white; font: inherit; font-weight: 700; }}
            button:disabled {{ opacity: .65; }}
          </style>
        </head>
        <body><main>{body}</main></body>
        </html>
        """
    )


@router.post("/channels/pairing/{session_id}/confirm")
async def confirm_channel_pairing(session_id: str, request: Request, device_name: str | None = None):
    try:
        return await asyncio.to_thread(
            request.app.state.settings_service.confirm_channel_pairing,
            session_id,
            device_name=device_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pairing session not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# General Settings (user workspace preferences)
# ---------------------------------------------------------------------------

class DataManagementRequest(BaseModel):
    action: Literal["cleanup"]
    dry_run: bool = True
    time_range_days: int = Field(ge=0)
    confirm: bool = False
    confirm_all_sessions: bool = False


class DataManagementResponse(BaseModel):
    ok: bool = True
    action: str = ""
    dry_run: bool = True
    summary: str = ""
    affected_count: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


@router.get("/general")
async def get_general_settings(request: Request):
    stored = await asyncio.to_thread(request.app.state.settings_service.get_general_settings)
    return stored.model_dump(mode="json")


@router.put("/general")
async def save_general_settings(payload: GeneralSettingsPayload, request: Request):
    stored = await asyncio.to_thread(request.app.state.settings_service.update_general_settings, payload)
    return stored.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Data Management (backup / import / cleanup)
# ---------------------------------------------------------------------------


@router.post("/data/export/start")
async def export_start(request: Request):
    """Start a background export task, return task_id for progress polling."""
    store = request.app.state.store
    total = await store.count_sessions()
    task_id = uuid4().hex
    task_store = request.app.state.export_task_store
    try:
        await task_store.create_task(task_id, total=total)
    except Exception as exc:
        logger.exception(
            "settings_export_task_create_failed",
            extra={"task_id": task_id, "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail="Export service temporarily unavailable.") from exc

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        progress_futures: list[Future] = []
        progress_lock = Lock()

        try:
            def _on_progress(n: int) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    task_store.update_progress(task_id, int(n)),
                    loop,
                )
                with progress_lock:
                    progress_futures.append(future)

            bundle = await store.bulk_export(progress_fn=_on_progress)
            with progress_lock:
                pending_progress = list(progress_futures)
            for future in pending_progress:
                await asyncio.wrap_future(future)
            await task_store.complete_task(task_id, bundle)
            logger.info(
                "settings_export_completed",
                extra={"task_id": task_id, "session_count": len(bundle)},
            )
        except Exception as exc:
            logger.exception(
                "settings_export_failed",
                extra={"task_id": task_id, "error_type": type(exc).__name__},
            )
            try:
                await task_store.fail_task(
                    task_id,
                    message="Export failed. Check server logs.",
                )
            except Exception as status_exc:
                logger.exception(
                    "settings_export_failure_status_write_failed",
                    extra={"task_id": task_id, "error_type": type(status_exc).__name__},
                )

    background_tasks = getattr(request.app.state, "export_background_tasks", None)
    if background_tasks is None:
        background_tasks = set()
        request.app.state.export_background_tasks = background_tasks
    task = asyncio.create_task(_run(), name=f"settings-export:{task_id}")
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return {"ok": True, "task_id": task_id, "total": total}


@router.get("/data/export/progress/{task_id}")
async def export_progress(task_id: str, request: Request):
    """Poll export progress."""
    try:
        task = await request.app.state.export_task_store.get_task(task_id)
    except ValueError:
        task = None
    if not task:
        raise HTTPException(status_code=404, detail="Export task not found.")
    return {
        "progress": task["progress"],
        "total": task["total"],
        "status": task["status"],
        "error": task.get("error"),
    }


@router.get("/data/export/download/{task_id}")
async def export_download(task_id: str, request: Request):
    """Download completed export data via streaming JSON."""
    task_store = request.app.state.export_task_store
    try:
        task = await task_store.get_task(task_id)
    except ValueError:
        task = None
    if not task:
        raise HTTPException(status_code=404, detail="Export task not found.")
    if task["status"] != "done":
        raise HTTPException(status_code=409, detail="Export not yet complete.")

    session_count = int(task["session_count"])
    if await task_store.result_count(task_id) != session_count:
        logger.error(
            "settings_export_result_missing",
            extra={"task_id": task_id, "session_count": session_count},
        )
        raise HTTPException(status_code=410, detail="Export data is no longer available.")
    exported_at = str(task.get("exported_at") or datetime.now(UTC).isoformat())

    async def _stream():
        header = json.dumps({
            "version": "1.0",
            "exported_at": exported_at,
            "session_count": session_count,
        }, ensure_ascii=False)
        yield header[:-1].encode("utf-8")
        yield b',"sessions":['

        sent = 0
        while sent < session_count:
            try:
                batch = await task_store.read_result_batch(
                    task_id,
                    start=sent,
                    count=min(64, session_count - sent),
                )
            except Exception as exc:
                logger.exception(
                    "settings_export_stream_failed",
                    extra={"task_id": task_id, "session_offset": sent, "error_type": type(exc).__name__},
                )
                raise
            if not batch:
                logger.error(
                    "settings_export_stream_truncated",
                    extra={"task_id": task_id, "session_offset": sent, "session_count": session_count},
                )
                raise RuntimeError(f"Export task '{task_id}' result ended before session {sent}.")
            for session in batch:
                if sent > 0:
                    yield b","
                yield session
                sent += 1

        yield b"]}"

    filename = f"qa-agent-backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(
        _stream(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/data/export/preview")
async def export_data_preview(request: Request):
    """Preview export: return session count without downloading."""
    store = request.app.state.store
    count = await store.count_sessions()
    return {"ok": True, "session_count": count}


def _import_list(value: dict, field_name: str) -> list:
    result = value.get(field_name, [])
    if result is None:
        return []
    if not isinstance(result, list):
        raise TypeError(f"{field_name} must be an array.")
    return result


def _import_mapping(value: dict, field_name: str) -> dict:
    result = value.get(field_name, {})
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise TypeError(f"{field_name} must be an object.")
    return result


def _import_string(value: object, field_name: str, *, default: str | None = None) -> str | None:
    if value is None:
        if default is not None:
            return default
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    return value


def _import_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string timestamp.")
    if not value.strip():
        raise ValueError(f"{field_name} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp.") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _import_count(value: dict, field_name: str) -> int:
    count = value.get(field_name, 0)
    if type(count) is not int:
        raise TypeError(f"{field_name} must be an integer.")
    if count < 0 or count > 2_147_483_647:
        raise ValueError(f"{field_name} must be a non-negative PostgreSQL integer.")
    return count


def _session_import_bundle(item: object) -> SessionImportBundle:
    if not isinstance(item, dict):
        raise TypeError("Each session entry must be an object.")
    session_id = item.get("id")
    if not isinstance(session_id, str):
        raise TypeError("Session ID must be a string.")
    if not session_id.strip():
        raise ValueError("Session ID must be a non-empty string.")

    created_at = _import_datetime(item.get("created_at"), "created_at")
    updated_at = _import_datetime(item.get("updated_at"), "updated_at")
    messages: list[ChatMessage] = []
    for message_data in _import_list(item, "messages"):
        if not isinstance(message_data, dict):
            raise TypeError("Each message entry must be an object.")
        normalized_message = dict(message_data)
        normalized_message["created_at"] = _import_datetime(
            message_data.get("created_at"), "messages.created_at"
        )
        normalized_message["metadata"] = _import_mapping(message_data, "metadata")
        message = ChatMessage.model_validate(normalized_message)
        if not message.id.strip():
            raise ValueError("Message ID must be a non-empty string.")
        messages.append(message)

    events: list[ExecutionEvent] = []
    for event_data in _import_list(item, "events"):
        if not isinstance(event_data, dict):
            raise TypeError("Each event entry must be an object.")
        normalized_event = dict(event_data)
        normalized_event["session_id"] = session_id
        normalized_event["timestamp"] = _import_datetime(
            event_data.get("timestamp"), "events.timestamp"
        )
        normalized_event["payload"] = _import_mapping(event_data, "payload")
        event = ExecutionEvent.model_validate(normalized_event)
        if not event.type.strip():
            raise ValueError("Event type must be a non-empty string.")
        events.append(event)

    snapshots: list[SessionSnapshot] = []
    for snapshot_data in _import_list(item, "snapshots"):
        if not isinstance(snapshot_data, dict):
            raise TypeError("Each snapshot entry must be an object.")
        normalized_snapshot = dict(snapshot_data)
        normalized_snapshot["session_id"] = session_id
        normalized_snapshot["created_at"] = _import_datetime(
            snapshot_data.get("created_at"), "snapshots.created_at"
        )
        version = snapshot_data.get("version", 1)
        if type(version) is not int:
            raise TypeError("Snapshot version must be an integer.")
        if version < 1 or version > 2_147_483_647:
            raise ValueError("Snapshot version must be a positive PostgreSQL integer.")
        normalized_snapshot["version"] = version
        normalized_snapshot["graph_state"] = _import_mapping(snapshot_data, "graph_state")
        snapshot = SessionSnapshot.model_validate(normalized_snapshot)
        snapshots.append(snapshot)

    approvals: list[ToolApprovalRequest] = []
    for approval_data in _import_list(item, "approvals"):
        if not isinstance(approval_data, dict):
            raise TypeError("Each approval entry must be an object.")
        normalized_approval = dict(approval_data)
        normalized_approval["session_id"] = session_id
        normalized_approval["created_at"] = _import_datetime(
            approval_data.get("created_at"), "approvals.created_at"
        )
        if approval_data.get("resolved_at") is not None:
            normalized_approval["resolved_at"] = _import_datetime(
                approval_data.get("resolved_at"), "approvals.resolved_at"
            )
        normalized_approval["metadata"] = _import_mapping(approval_data, "metadata")
        approval = ToolApprovalRequest.model_validate(normalized_approval)
        approvals.append(approval)

    title = _import_string(item.get("title", ""), "title", default="")
    mode_key = _import_string(item.get("mode_key", "default"), "mode_key", default="default")
    preferred_model = _import_string(item.get("preferred_model"), "preferred_model")
    selected_agent = _import_string(item.get("selected_agent"), "selected_agent")
    project_id = _import_string(item.get("project_id"), "project_id")
    metadata = _import_mapping(item, "metadata")

    session = SessionRecord(
        id=session_id,
        title=title or "",
        status=SessionStatus(item.get("status", SessionStatus.completed.value)),
        session_mode=SessionMode(item.get("session_mode", SessionMode.normal.value)),
        runtime_mode=RuntimeMode(item.get("runtime_mode", RuntimeMode.interactive.value)),
        mode_key=mode_key or "default",
        created_at=created_at,
        updated_at=updated_at,
        preferred_model=preferred_model,
        selected_agent=selected_agent,
        project_id=project_id,
        metadata=metadata,
        messages=messages,
        event_count=_import_count(item, "event_count"),
        snapshot_count=_import_count(item, "snapshot_count"),
    )
    bundle = SessionImportBundle(
        session=session,
        events=events,
        snapshots=snapshots,
        approvals=approvals,
    )
    validate_session_import_bundle(bundle)
    return bundle


_data_import_router = APIRouter(route_class=_DataImportBodyLimitRoute)


@_data_import_router.post("/data/import")
async def import_data(request: Request, file: UploadFile = File(...)):
    """Import sessions from a backup JSON file."""
    store = request.app.state.store
    max_import_bytes = request.app.state.settings.security.data_import_max_bytes

    try:
        raw = await file.read(max_import_bytes + 1)
    except Exception as exc:
        logger.error(
            "settings_data_import_file_read_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=400, detail="Unable to read backup file.") from exc

    if len(raw) > max_import_bytes:
        logger.warning(
            "settings_data_import_file_too_large",
            extra={"file_size_at_least": len(raw), "max_import_bytes": max_import_bytes},
        )
        raise HTTPException(
            status_code=413,
            detail=f"Backup file exceeds the configured limit of {max_import_bytes} bytes.",
        )

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        logger.warning(
            "settings_data_import_invalid_json",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=400, detail="Invalid JSON file.") from exc

    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise HTTPException(status_code=400, detail="Invalid backup format: missing 'sessions' array.")
    if data.get("version") not in (None, "1.0"):
        raise HTTPException(status_code=400, detail="Unsupported backup version.")

    bundles: list[SessionImportBundle] = []
    invalid_count = 0
    first_invalid_index: int | None = None
    first_invalid_error_type: str | None = None
    seen_session_ids: set[str] = set()
    seen_child_ids: dict[str, dict[str, str]] = {
        "message": {},
        "event": {},
        "snapshot": {},
        "approval": {},
    }
    for entry_index, item in enumerate(data["sessions"]):
        try:
            bundle = _session_import_bundle(item)
            session_id = bundle.session.id
            if session_id not in seen_session_ids:
                pending_child_ids: dict[str, list[str]] = {
                    "message": [],
                    "event": [],
                    "snapshot": [],
                    "approval": [],
                }
                for label, records in (
                    ("message", bundle.session.messages),
                    ("event", bundle.events),
                    ("snapshot", bundle.snapshots),
                    ("approval", bundle.approvals),
                ):
                    for record in records:
                        existing_owner = seen_child_ids[label].get(record.id)
                        if existing_owner is not None and existing_owner != session_id:
                            raise ValueError(f"Backup contains duplicate {label} IDs.")
                        pending_child_ids[label].append(record.id)
                for label, record_ids in pending_child_ids.items():
                    for record_id in record_ids:
                        seen_child_ids[label][record_id] = session_id
                seen_session_ids.add(session_id)
            bundles.append(bundle)
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
            invalid_count += 1
            if first_invalid_index is None:
                first_invalid_index = entry_index
                first_invalid_error_type = type(exc).__name__

    imported = 0
    duplicate_count = 0
    if bundles:
        try:
            imported, duplicate_count = await store.import_session_bundles(bundles)
        except Exception as exc:
            original = getattr(exc, "orig", exc)
            sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
            logger.error(
                "settings_data_import_persistence_failed",
                extra={
                    "bundle_count": len(bundles),
                    "error_type": type(exc).__name__,
                    "sqlstate": sqlstate,
                },
            )
            raise HTTPException(
                status_code=503,
                detail="Import failed. Check server logs before retrying.",
            ) from exc

    skipped = invalid_count + duplicate_count
    if invalid_count:
        logger.warning(
            "settings_data_import_entries_skipped",
            extra={
                "invalid_count": invalid_count,
                "first_entry_index": first_invalid_index,
                "first_error_type": first_invalid_error_type,
            },
        )
    logger.info(
        "settings_data_import_completed",
        extra={"imported_count": imported, "skipped_count": skipped, "invalid_count": invalid_count},
    )

    return {
        "ok": True,
        "action": "import",
        "summary": f"Imported {imported} sessions, skipped {skipped} (duplicate or invalid).",
        "imported_count": imported,
        "skipped_count": skipped,
    }


router.include_router(_data_import_router)


@router.post("/data/cleanup")
async def cleanup_data(payload: DataManagementRequest, request: Request):
    """Clean up historical session data with dry-run and confirmation support."""
    store = request.app.state.store

    if not payload.dry_run and payload.confirm and payload.time_range_days == 0 and not payload.confirm_all_sessions:
        logger.warning("session_cleanup_all_scope_confirmation_missing")
        raise HTTPException(status_code=400, detail="Cleaning all sessions requires confirm_all_sessions=true.")

    cutoff: datetime | None = None
    if payload.time_range_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=payload.time_range_days)

    total_count = await store.count_sessions()
    retained_count = await store.count_sessions(after=cutoff) if cutoff else 0
    affected_count = total_count - retained_count if cutoff else total_count
    date_range = await store.get_session_date_range()

    if payload.dry_run:
        oldest = date_range.get("oldest")
        newest = date_range.get("newest")
        return DataManagementResponse(
            ok=True,
            action="cleanup",
            dry_run=True,
            summary=f"Dry-run: {retained_count} sessions retained, {affected_count} would be deleted.",
            affected_count=affected_count,
            details={
                "time_range_days": payload.time_range_days,
                "total_sessions": total_count,
                "retained_sessions": retained_count,
                "affected_sessions": affected_count,
                "oldest_session": oldest.isoformat() if oldest else None,
                "newest_session": newest.isoformat() if newest else None,
            },
        )

    if not payload.confirm:
        return DataManagementResponse(
            ok=False,
            action="cleanup",
            dry_run=False,
            summary="Cleanup requires explicit confirmation. Set confirm=true to proceed.",
            affected_count=affected_count,
        )

    deleted_count = await store.delete_sessions_before(cutoff)
    logger.info(
        "session_cleanup_completed",
        extra={"time_range_days": payload.time_range_days, "deleted_count": deleted_count},
    )

    return DataManagementResponse(
        ok=True,
        action="cleanup",
        dry_run=False,
        summary=f"Cleanup completed: {deleted_count} sessions deleted.",
        affected_count=deleted_count,
        details={
            "time_range_days": payload.time_range_days,
            "deleted_count": deleted_count,
        },
    )
