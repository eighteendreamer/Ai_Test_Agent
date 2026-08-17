from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from src.runtime.streaming import format_sse
from src.schemas.session import (
    ApprovalDecisionRequest,
    CreateSessionRequest,
    HeadlessExecutionRequest,
    InterruptSessionRequest,
    ResumeSessionRequest,
    SendMessageRequest,
    UpdateSessionRequest,
)
from src.schemas.tool_job import ToolArtifactRecord, ToolJobDetail, ToolJobRecord


router = APIRouter(prefix="/sessions", tags=["sessions"])

_ARTIFACT_MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}


def _artifact_media_type(filename: str, fallback: str = "application/octet-stream") -> str:
    return _ARTIFACT_MEDIA_TYPES.get(Path(filename).suffix.lower(), fallback)


def _attachment_header(filename: str) -> str:
    resolved = filename or "artifact"
    ascii_name = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\", ";"} else "_"
        for character in resolved
    ).strip(" ._") or "artifact"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(resolved, safe="")}'


@router.get("")
async def list_sessions(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    mode_key: str | None = None,
):
    if limit is None:
        return await request.app.state.session_service.list_sessions()
    return await request.app.state.session_service.list_sessions_page(
        limit=limit,
        offset=offset,
        mode_key=mode_key,
    )


@router.post("")
async def create_session(payload: CreateSessionRequest, request: Request):
    try:
        return await request.app.state.session_service.create_session(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc


@router.post("/headless/execute")
async def execute_headless(payload: HeadlessExecutionRequest, request: Request):
    return await request.app.state.session_service.execute_headless(payload)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.patch("/{session_id}")
async def update_session(session_id: str, payload: UpdateSessionRequest, request: Request):
    try:
        return await request.app.state.session_service.update_session(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "archived" in str(exc) else 400, detail=str(exc)) from exc


@router.get("/{session_id}/events/history")
async def list_events(
    session_id: str,
    request: Request,
    limit: int = Query(500, ge=0, le=5000),
    after_event_id: str | None = None,
):
    try:
        return await request.app.state.session_service.list_events(
            session_id,
            limit=None if limit == 0 else limit,
            after_event_id=after_event_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.post("/{session_id}/messages")
async def send_message(session_id: str, payload: SendMessageRequest, request: Request):
    try:
        return await request.app.state.session_service.send_message(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_id}/snapshots")
async def list_snapshots(
    session_id: str,
    request: Request,
    limit: int = Query(10, ge=0, le=200),
    include_graph_state: bool = False,
):
    try:
        return await request.app.state.session_service.list_snapshots(
            session_id,
            limit=None if limit == 0 else limit,
            include_graph_state=include_graph_state,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.post("/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    payload: InterruptSessionRequest,
    request: Request,
):
    try:
        return await request.app.state.session_service.interrupt_session(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: str,
    payload: ResumeSessionRequest,
    request: Request,
):
    try:
        return await request.app.state.session_service.resume_session(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_id}/replay")
async def replay_session(
    session_id: str,
    request: Request,
    limit: int = Query(500, ge=0, le=5000),
):
    try:
        return await request.app.state.session_service.replay_session(
            session_id,
            limit=None if limit == 0 else limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/tool-jobs", response_model=list[ToolJobRecord])
async def list_tool_jobs(session_id: str, request: Request):
    try:
        await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return await request.app.state.tool_job_service.list_jobs(session_id=session_id)


@router.get("/{session_id}/tool-jobs/{job_id}", response_model=ToolJobDetail)
async def get_tool_job_detail(session_id: str, job_id: str, request: Request):
    try:
        await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    job = await request.app.state.tool_job_service.get_job_detail(job_id)
    if job is None or job.session_id != session_id:
        raise HTTPException(status_code=404, detail="Tool job not found")
    return job


@router.get("/{session_id}/artifacts", response_model=list[ToolArtifactRecord])
async def list_session_artifacts(session_id: str, request: Request):
    try:
        await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return await request.app.state.tool_job_service.list_artifacts(session_id=session_id)


@router.get("/{session_id}/artifacts/{artifact_id}/content")
async def get_session_artifact_content(session_id: str, artifact_id: str, request: Request):
    try:
        await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    artifact = await request.app.state.tool_job_service.get_artifact(artifact_id)
    if artifact is None or artifact.session_id != session_id:
        raise HTTPException(status_code=404, detail="Artifact not found")

    filename = str(
        artifact.metadata.get("filename")
        or artifact.label
        or Path(artifact.path).name
        or artifact.id
    )
    raw_path = str(artifact.path or "").strip()
    if raw_path.startswith("minio://"):
        try:
            stored = await request.app.state.artifact_storage_service.read_object_uri(raw_path)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Artifact content not available") from exc
        media_type = str(stored.get("content_type") or "application/octet-stream")
        if media_type == "application/octet-stream":
            media_type = _artifact_media_type(filename, media_type)
        return Response(
            content=stored.get("content") or b"",
            media_type=media_type,
            headers={"Content-Disposition": _attachment_header(filename)},
        )

    if raw_path and not raw_path.startswith("inline://"):
        try:
            local_path = Path(raw_path).resolve()
            artifact_root = (
                Path(__file__).resolve().parents[2]
                / request.app.state.settings.artifact_root_dir
            ).resolve()
            if artifact_root != local_path and artifact_root not in local_path.parents:
                raise HTTPException(status_code=404, detail="Artifact content not available")
            if local_path.is_file():
                return Response(
                    content=local_path.read_bytes(),
                    media_type=_artifact_media_type(filename),
                    headers={"Content-Disposition": _attachment_header(filename)},
                )
        except (OSError, ValueError):
            pass

    inline_content = str(artifact.metadata.get("__content_text") or "")
    if inline_content:
        return Response(
            content=inline_content.encode("utf-8"),
            media_type=_artifact_media_type(filename, "text/plain"),
            headers={"Content-Disposition": _attachment_header(filename)},
        )
    raise HTTPException(status_code=404, detail="Artifact content not available")


@router.get("/{session_id}/approvals")
async def list_approvals(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_approvals(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/verifications")
async def list_verifications(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_verifications(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/observations")
async def list_observations(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_observations(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.post("/{session_id}/approvals/{approval_id}")
async def resolve_approval(
    session_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
):
    try:
        return await request.app.state.session_service.resolve_approval(session_id, approval_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval or session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_id}/events")
async def stream_events(session_id: str, request: Request):
    try:
        await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    queue = request.app.state.session_service.get_event_queue(session_id)
    last_event_id = (
        request.headers.get("last-event-id")
        or request.query_params.get("last_event_id")
        or request.query_params.get("Last-Event-ID")
        or ""
    ).strip()

    async def event_generator():
        if last_event_id:
            events = await request.app.state.session_service.list_events(
                session_id,
                limit=1000,
                after_event_id=last_event_id,
            )
            for event in events:
                yield format_sse(event)

        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                yield format_sse(event)
            except TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
