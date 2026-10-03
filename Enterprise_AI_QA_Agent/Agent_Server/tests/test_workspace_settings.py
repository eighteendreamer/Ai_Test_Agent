from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from src.api.routes.settings import router
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.settings.settings_service import SettingsService
from src.application.settings.workspace_settings_service import CHANNEL_ADVANCED_KEY, GENERAL_SETTINGS_KEY, WorkspaceSettingsService
from src.containers import AppContainer
from src.core.config import get_settings
from src.infrastructure.application_config_store import ApplicationConfigRecord, MySQLApplicationConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.settings import GeneralSettingsPayload


class _MemoryConfigs:
    def __init__(self):
        self.records = {}

    def initialize(self):
        pass

    def get(self, key, *, scope_key="global"):
        return deepcopy(self.records.get((scope_key, key)))

    def save(self, key, payload, *, scope_key="global", only_if_missing=False):
        old = self.get(key, scope_key=scope_key)
        if old and only_if_missing:
            return old
        record = ApplicationConfigRecord(key, scope_key, deepcopy(payload), old.revision + 1 if old else 1)
        self.records[scope_key, key] = record
        return deepcopy(record)


def test_existing_files_migrate_once_and_never_replace_database_changes(tmp_path):
    general = tmp_path / "general_settings.json"
    channels = tmp_path / "channel_advanced_settings.json"
    general.write_text(json.dumps({"language": "ja-JP", "reduceMotion": True}), encoding="utf-8")
    channels.write_text(json.dumps({"max_steps": 7, "allowlist": {"qq_users": ["u1", "u1", " u2 "]}}), encoding="utf-8")
    originals = (general.read_bytes(), channels.read_bytes())
    store = _MemoryConfigs()
    service = WorkspaceSettingsService(store, tmp_path)
    service.initialize()
    assert service.get_general_settings().language == "ja-JP"
    assert service.get_channel_advanced_settings().allowlist.qq_users == ["u1", "u2"]
    assert originals == (general.read_bytes(), channels.read_bytes())
    service.update_general_settings(GeneralSettingsPayload(language="en-US"))
    # Stale and even corrupted legacy files are not reread after migration.
    general.write_text("{", encoding="utf-8")
    restarted = WorkspaceSettingsService(store, tmp_path)
    restarted.initialize()
    assert restarted.get_general_settings().language == "en-US"
    assert store.get(GENERAL_SETTINGS_KEY).revision == 2
    assert store.get(CHANNEL_ADVANCED_KEY).revision == 1


@pytest.mark.parametrize("content", ["{", "[]", '{"reduceMotion": []}'])
def test_invalid_legacy_settings_are_visible_and_not_replaced_by_defaults(tmp_path, content):
    (tmp_path / "general_settings.json").write_text(content, encoding="utf-8")
    store = _MemoryConfigs()
    with pytest.raises(ValueError, match="Cannot migrate"):
        WorkspaceSettingsService(store, tmp_path).initialize()
    assert store.get(GENERAL_SETTINGS_KEY) is None


def test_preferences_change_prompt_without_reading_legacy_files(tmp_path, monkeypatch):
    service = WorkspaceSettingsService(_MemoryConfigs(), tmp_path)
    service.initialize()
    prompt = PromptAssemblyService(service.get_general_settings)
    service.update_general_settings(GeneralSettingsPayload(language="ja-JP"))
    assert "ja-JP" in prompt.build_for_turn({}, []).system_prompt
    service.update_general_settings(GeneralSettingsPayload(language="ja-JP", modelOutputLanguage="en-US"))
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("runtime settings must not read files"))
    assert "en-US" in prompt.build_for_turn({}, []).system_prompt


def test_configuration_failure_is_not_silently_converted_to_prompt_defaults():
    def failed_read():
        raise RuntimeError("configured settings unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        PromptAssemblyService(failed_read).build_for_turn({}, [])


def test_table_identifier_is_validated_before_sql():
    settings = get_settings().model_copy(deep=True)
    settings.database.application_config_table = "unsafe`; SELECT 1"
    with pytest.raises(ValueError, match="identifier"):
        MySQLApplicationConfigStore(settings)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1", reason="requires configured MySQL; isolated test configuration table")
async def test_real_mysql_preferences_http_restart_and_prompt_share_one_source(tmp_path):
    settings = get_settings().model_copy(deep=True)
    table = f"up_c_{uuid4().hex[:12]}"
    settings.database.application_config_table = table
    with mysql_raw_connection(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE %s", (table,))
            assert cursor.fetchone() is None
    (tmp_path / "general_settings.json").write_text(json.dumps({"language": "ja-JP"}), encoding="utf-8")
    store = MySQLApplicationConfigStore(settings)
    service = WorkspaceSettingsService(store, tmp_path)
    listener = None
    server = None
    serving = None
    try:
        await asyncio.to_thread(service.initialize)
        # Production dependency-injector wiring must give Prompt the same source.
        container = AppContainer()
        container.settings.override(settings)
        container.workspace_settings_service.override(service)
        prompt = container.prompt_assembly_service()
        assert "ja-JP" in (await asyncio.to_thread(prompt.build_for_turn, {}, [])).system_prompt
        settings_service = object.__new__(SettingsService)
        settings_service._workspace_settings_service = service
        app = FastAPI()
        app.state.settings_service = settings_service
        app.include_router(router, prefix="/api/v1")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        async def started():
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        await asyncio.wait_for(started(), 10)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1/settings") as client:
            response = await client.get("/general")
            response.raise_for_status()
            assert response.json()["language"] == "ja-JP"
            response = await client.put("/general", json={"language": "en-US", "modelOutputLanguage": "en-US", "reduceMotion": True})
            response.raise_for_status()
            assert response.json()["lastSavedAt"]
            response = await client.put("/channels/advanced", json={"max_steps": 7, "allowlist": {"qq_users": ["u1", "u1", " u2 "]}})
            response.raise_for_status()
            assert response.json()["allowlist"]["qq_users"] == ["u1", "u2"]
            response = await client.get("/channels/advanced")
            response.raise_for_status()
            assert response.json()["max_steps"] == 7
        # A different service/store reads durable changes, even with a bad file.
        (tmp_path / "general_settings.json").write_text("{", encoding="utf-8")
        restarted = WorkspaceSettingsService(MySQLApplicationConfigStore(settings), tmp_path)
        await asyncio.to_thread(restarted.initialize)
        assert restarted.get_general_settings().language == "en-US"
        assert restarted.get_general_settings().reduceMotion is True
        assert restarted.get_channel_advanced_settings().max_steps == 7
        assert "en-US" in (await asyncio.to_thread(prompt.build_for_turn, {}, [])).system_prompt
        assert store.get(GENERAL_SETTINGS_KEY).revision == 2
        # The scoped store will also support project configuration isolation.
        store.save("example", {"value": "first"}, scope_key="project-one")
        store.save("example", {"value": "second"}, scope_key="project-two")
        assert store.get("example", scope_key="project-one").payload == {"value": "first"}
        assert store.get("example", scope_key="project-two").payload == {"value": "second"}
        await asyncio.gather(*[asyncio.to_thread(store.save, "example", {"value": "stale"}, scope_key="project-one", only_if_missing=True) for _ in range(4)])
        assert store.get("example", scope_key="project-one").revision == 1
        assert store.get("example", scope_key="project-one").payload == {"value": "first"}
        updates = await asyncio.gather(*[
            asyncio.to_thread(store.save, "example", {"value": index}, scope_key="project-one")
            for index in range(4)
        ])
        assert sorted(record.revision for record in updates) == [2, 3, 4, 5]
        assert store.get("example", scope_key="project-one").revision == 5
        with mysql_raw_connection(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE `{table}` SET payload_json=%s WHERE scope_key=%s AND config_key=%s",
                               ("[]", "project-two", "example"))
            connection.commit()
        with pytest.raises(ValueError, match="Invalid stored"):
            store.get("example", scope_key="project-two")
    finally:
        if server:
            server.should_exit = True
        if serving:
            await asyncio.wait_for(serving, 10)
        if listener:
            listener.close()
        # Only the fresh UUID-named configuration table belongs to this test.
        with mysql_raw_connection(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
