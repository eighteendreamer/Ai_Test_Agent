from __future__ import annotations

import asyncio
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from src.api.routes.settings import router as settings_router
from src.application.settings.settings_service import SettingsService
from src.core.config import Settings
from src.infrastructure.channel_config_store import MySQLChannelConfigStore
from src.schemas.model_config import ModelConfigPublic
from src.schemas.settings import GeneralSettingsPayload


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["channels", "general", "models/sample/test-connection"])
async def test_settings_database_reads_do_not_block_event_loop(monkeypatch, route: str) -> None:
    entered = Event()
    release = Event()
    timed_out = False

    def wait_for_peer() -> None:
        nonlocal timed_out
        entered.set()
        timed_out = not release.wait(timeout=1.0)

    service = object.__new__(SettingsService)
    if route == "general":
        def slow_preferences():
            wait_for_peer()
            return GeneralSettingsPayload()

        service._workspace_settings_service = SimpleNamespace(get_general_settings=slow_preferences)
    elif route == "channels":
        channel_store = MySQLChannelConfigStore(Settings())

        class EmptyConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return self

            def execute(self, *_):
                return None

            def fetchall(self):
                return []

        def slow_connect():
            wait_for_peer()
            return EmptyConnection()

        monkeypatch.setattr(channel_store, "_connect", slow_connect)
        service._channel_config_store = channel_store
    else:
        record = SimpleNamespace(
            applications=[], auth_type="api_key", api_key="", name="sample",
            provider="openai", api_base_url="https://example.test/v1",
        )
        public_item = ModelConfigPublic(
            key="sample", name="sample", provider="openai",
            transport="openai_chat_completions", model_id="sample",
            api_base_url="https://example.test/v1",
        )

        class ModelStore:
            def get_by_name(self, name: str):
                assert name == "sample"
                wait_for_peer()
                return record

            def to_public(self, item):
                assert item is record
                return public_item

        service._model_config_store = ModelStore()

    app = FastAPI()
    app.state.settings_service = service
    app.include_router(settings_router, prefix="/api/v1")

    async def release_after_read_starts() -> None:
        while not entered.is_set():
            await asyncio.sleep(0)
        release.set()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response, _ = await asyncio.gather(
            client.request(
                "POST" if route.startswith("models/") else "GET",
                f"/api/v1/settings/{route}",
            ),
            release_after_read_starts(),
        )

    assert response.status_code == 200
    assert timed_out is False
