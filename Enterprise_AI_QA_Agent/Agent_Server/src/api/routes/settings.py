from __future__ import annotations

import asyncio
import html
import json
import logging
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

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
from src.schemas.settings import ModelConfigUpdateRequest

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


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

class GeneralSettingsPayload(BaseModel):
    language: str = "zh-CN"
    modelOutputLanguage: str = "follow-system"
    notifySessionCompleteWhenAway: bool = True
    notifyApprovalRequiredWhenAway: bool = True
    notificationsAwayOnly: bool = True
    fontFamily: str = "system"
    fontSize: str = "standard"
    reduceMotion: bool = False
    lastSavedAt: str = ""


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


_GENERAL_SETTINGS_FILE = Path(__file__).resolve().parents[3] / "src" / "data" / "general_settings.json"


def _read_general_settings() -> dict[str, Any]:
    if _GENERAL_SETTINGS_FILE.exists():
        try:
            return json.loads(_GENERAL_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_general_settings(data: dict[str, Any]) -> None:
    _GENERAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GENERAL_SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/general")
async def get_general_settings(request: Request):
    """Read the current general settings (workspace preferences)."""
    stored = await asyncio.to_thread(_read_general_settings)
    if not stored:
        return GeneralSettingsPayload().model_dump()
    return stored


@router.put("/general")
async def save_general_settings(payload: GeneralSettingsPayload, request: Request):
    """Save general settings (workspace preferences)."""
    from datetime import datetime, timezone

    data = payload.model_dump()
    data["lastSavedAt"] = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_write_general_settings, data)
    return data


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


@router.post("/data/import")
async def import_data(request: Request, file: UploadFile = File(...)):
    """Import sessions from a backup JSON file."""
    store = request.app.state.store

    try:
        raw = await file.read()
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {exc}") from exc

    if "sessions" not in data or not isinstance(data["sessions"], list):
        raise HTTPException(status_code=400, detail="Invalid backup format: missing 'sessions' array.")

    imported = 0
    skipped = 0
    for item in data["sessions"]:
        sid = item.get("id")
        if not sid:
            skipped += 1
            continue

        existing = await store.get_session(sid)
        if existing is not None:
            skipped += 1
            continue

        from src.domain.models import SessionRecord
        from src.schemas.session import (
            ChatMessage,
            MessageRole,
            RuntimeMode,
            SessionMode,
            SessionStatus,
        )

        session = SessionRecord(
            id=sid,
            title=item.get("title", ""),
            status=SessionStatus(item.get("status", "completed")),
            session_mode=SessionMode(item.get("session_mode", "chat")),
            runtime_mode=RuntimeMode(item.get("runtime_mode", "auto")),
            mode_key=item.get("mode_key", ""),
            preferred_model=item.get("preferred_model", ""),
            selected_agent=item.get("selected_agent", ""),
            metadata=item.get("metadata") or {},
            event_count=item.get("event_count", 0),
            snapshot_count=item.get("snapshot_count", 0),
            messages=[
                ChatMessage(
                    id=m.get("id", ""),
                    role=MessageRole(m.get("role", "user")),
                    content=m.get("content", ""),
                    metadata=m.get("metadata") or {},
                )
                for m in (item.get("messages") or [])
            ],
        )
        await store.save_session(session)

        from src.schemas.session import (
            ExecutionEvent,
            SessionSnapshot,
            ToolApprovalRequest,
            ToolApprovalStatus,
        )

        for ev in (item.get("events") or []):
            await store.append_event(sid, ExecutionEvent(
                type=ev.get("type", ""),
                session_id=sid,
                payload=ev.get("payload") or {},
            ))

        for snap in (item.get("snapshots") or []):
            await store.save_snapshot(sid, SessionSnapshot(
                id=snap.get("id", ""),
                session_id=sid,
                version=snap.get("version", 1),
                stage=snap.get("stage", ""),
                graph_state=snap.get("graph_state") or {},
            ))

        for ap in (item.get("approvals") or []):
            await store.save_approval(sid, ToolApprovalRequest(
                id=ap.get("id", ""),
                session_id=sid,
                tool_key=ap.get("tool_key", ""),
                tool_name=ap.get("tool_name", ""),
                reason=ap.get("reason", ""),
                status=ToolApprovalStatus(ap.get("status", "pending")),
                decision_note=ap.get("decision_note"),
                metadata=ap.get("metadata") or {},
            ))

        imported += 1

    return {
        "ok": True,
        "action": "import",
        "summary": f"Imported {imported} sessions, skipped {skipped} (duplicate or invalid).",
        "imported_count": imported,
        "skipped_count": skipped,
    }


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
