from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.runtime.streaming import format_sse
from src.schemas.session import (
    ApprovalDecisionRequest,
    CreateSessionRequest,
    HeadlessExecutionRequest,
    SendMessageRequest,
)


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(request: Request):
    return await request.app.state.session_service.list_sessions()


@router.post("")
async def create_session(payload: CreateSessionRequest, request: Request):
    return await request.app.state.session_service.create_session(payload)


@router.post("/headless/execute")
async def execute_headless(payload: HeadlessExecutionRequest, request: Request):
    return await request.app.state.session_service.execute_headless(payload)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/events/history")
async def list_events(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_events(session_id)
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
async def list_snapshots(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_snapshots(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/approvals")
async def list_approvals(session_id: str, request: Request):
    try:
        return await request.app.state.session_service.list_approvals(session_id)
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

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                yield format_sse(event)
            except TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
