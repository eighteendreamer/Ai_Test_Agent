"""Local-only deterministic backend for the assistant stream browser probe."""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI, HTTPException

from src.api.routes.sessions import router as sessions_router
from src.application.runtime.tool_job_service import ToolJobService
from src.application.sessions.session_service import SessionService
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.runtime.store import InMemorySessionStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.session import (
    ChatMessage,
    MessageRole,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
)

logger = logging.getLogger("assistant_stream_browser_probe")
SESSION_ID = "assistant-stream-browser-probe"
TURN_ID = "turn-assistant-stream-browser-probe"
ASSISTANT_MESSAGE_ID = "assistant-stream-browser-answer"
USER_TEXT = "验证后端 AI 分片到前端的传输。"
CHUNKS = [
    "这是本地确定性模型桩生成的回答：\n\n",
    "1. 后端逐片发送 AI 文本。\n",
    "2. SSE 实时送达浏览器。\n\n",
    "最终正文从持久化 transcript 对账。",
]


def create_app() -> FastAPI:
    settings = get_settings()
    settings.orchestration.session_sse_poll_seconds = 0.02
    store = InMemorySessionStore()
    service = SessionService(store, object(), object(), object())
    tool_job_service = ToolJobService(InMemoryToolJobStore())
    start_lock = asyncio.Lock()
    started = False
    active_tasks: set[asyncio.Task[None]] = set()

    async def seed_probe_session() -> None:
        now = datetime.now(UTC)
        await store.save_session(SessionRecord(
            id=SESSION_ID,
            title="Assistant stream browser probe",
            status=SessionStatus.running,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=now,
            updated_at=now,
            messages=[ChatMessage(
                id="assistant-stream-browser-user",
                role=MessageRole.user,
                content=USER_TEXT,
                created_at=now,
                metadata={"turn_id": TURN_ID},
            )],
        ))

    async def cancel_stream_tasks() -> None:
        for task in tuple(active_tasks):
            task.cancel()
        if active_tasks:
            await asyncio.gather(*tuple(active_tasks), return_exceptions=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await seed_probe_session()
        try:
            yield
        finally:
            await cancel_stream_tasks()

    app = FastAPI(title="Assistant stream browser probe", lifespan=lifespan)
    app.state.session_service = service
    app.state.tool_job_service = tool_job_service
    app.state.settings = settings
    app.include_router(sessions_router, prefix="/api/v1")

    async def stream_deterministic_answer() -> None:
        session = await store.get_session(SESSION_ID)
        if session is None:
            raise RuntimeError(f"Probe session disappeared: {SESSION_ID}")
        on_text_delta = service._build_stream_chunk_handler(
            SESSION_ID,
            TURN_ID,
            ASSISTANT_MESSAGE_ID,
        )
        for chunk in CHUNKS:
            await on_text_delta(chunk)
            await asyncio.sleep(0.2)

        now = datetime.now(UTC)
        runtime_result = SimpleNamespace(
            events=[],
            tool_messages=[],
            approvals=[],
            pending_turn={},
            output_text="".join(CHUNKS),
            snapshot=SessionSnapshot(
                id="snapshot-assistant-stream-browser-probe",
                session_id=SESSION_ID,
                version=1,
                stage="completed",
                created_at=now,
                graph_state={"turn_id": TURN_ID},
            ),
            state={
                "turn_id": TURN_ID,
                "trace_id": "trace-assistant-stream-browser-probe",
                "selected_agent_key": "coordinator",
                "selected_agent_name": "Coordinator",
                "selected_model_key": "deterministic-browser-probe",
                "selected_model_name": "Deterministic browser probe",
                "model_response_summary": {"mode": "ok"},
                "tool_results": [],
                "context_bundle": {},
                "termination_reason": "assistant_final",
            },
        )
        await service._finalize_runtime_result(
            session,
            runtime_result,
            ASSISTANT_MESSAGE_ID,
            USER_TEXT,
        )

    @app.post("/api/v1/__test__/assistant-stream/start", status_code=202)
    async def start_stream() -> dict[str, str]:
        nonlocal started
        async with start_lock:
            if started:
                return {"status": "already_started"}
            started = True
            task = asyncio.create_task(stream_deterministic_answer())
            active_tasks.add(task)

            def task_finished(finished: asyncio.Task[None]) -> None:
                active_tasks.discard(finished)
                if finished.cancelled():
                    return
                error = finished.exception()
                if error is not None:
                    logger.error(
                        "assistant_stream_browser_probe_failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

            task.add_done_callback(task_finished)
        return {"status": "started"}

    @app.get("/api/v1/__test__/health")
    async def probe_health() -> dict[str, str]:
        if await store.get_session(SESSION_ID) is None:
            raise HTTPException(status_code=503, detail="Probe session is not initialized")
        return {"status": "ready", "session_id": SESSION_ID}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=13032)
    args = parser.parse_args()
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
