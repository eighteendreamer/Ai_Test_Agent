from __future__ import annotations

import asyncio
from threading import Event

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes import health as health_route
from src.core.config import Settings
from src.infrastructure.postgres_runtime import PostgresHealthStatus


@pytest.mark.asyncio
async def test_postgres_healthcheck_does_not_block_event_loop(monkeypatch) -> None:
    entered = Event()
    release = Event()
    timed_out = False

    def slow_healthcheck(settings: Settings) -> PostgresHealthStatus:
        nonlocal timed_out
        entered.set()
        timed_out = not release.wait(timeout=1.0)
        return PostgresHealthStatus(ok=True)

    monkeypatch.setattr(health_route, "postgres_healthcheck", slow_healthcheck)
    app = FastAPI()
    app.state.settings = Settings()
    app.include_router(health_route.router, prefix="/api/v1")

    async def release_after_probe_starts() -> None:
        while not entered.is_set():
            await asyncio.sleep(0)
        release.set()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response, _ = await asyncio.gather(
            client.get("/api/v1/health"), release_after_probe_starts(),
        )

    assert response.status_code == 200
    assert response.json()["postgres_ok"] is True
    assert timed_out is False
