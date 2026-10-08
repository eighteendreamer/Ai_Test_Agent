"""Fixed capture policy: real configuration/storage and per-session script reads."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from test_case_planning import environment, LIVE
from test_recorder_session_service import _service
from test_recording_routes import _make_app, _make_session, FakeRecorderService
from src.api.routes.recordings import router
from src.application.recorder.capture_config_service import RecordingCaptureConfigService, CONFIG_KEY
from src.application.recorder.replay_executor import build_replay_plan
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.project import ProjectCreateRequest
from src.schemas.recording import RecorderEvent, RecordingCaptureSettings, RecordingCreateRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_project_policy_freezes_in_session_and_script_without_cross_session_cache(tmp_path, live):
    async with environment(tmp_path, live=live) as env:
        projects = [await env.projects.create(ProjectCreateRequest(project_key="capture-" + uuid4().hex[:12], name="采集策略")) for _ in range(2)]
        captures = RecordingCaptureConfigService(config_store=env.configs, project_service=env.projects)
        await captures.initialize()
        global_before = env.configs.get(CONFIG_KEY)
        await captures.initialize()
        assert env.configs.get(CONFIG_KEY).revision == global_before.revision
        table_names = []
        if live:
            suffix = uuid4().hex[:12]
            table_names = ["up_ce_" + suffix, "up_cs_" + suffix]
            env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = table_names
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for name in table_names:
                    cursor.execute("SELECT to_regclass(%s) AS existing", (name,))
                    assert cursor.fetchone()["existing"] is None
            store = PostgresRecordingStore(env.settings)
            await store.initialize()
            service, store, graph, bridge, drivers = _service(store=store)
        else:
            service, store, graph, bridge, drivers = _service()
        service._capture_configs = captures
        app = FastAPI()
        app.state.recorder_service = service
        app.include_router(router, prefix="/api/v1")
        first_policy = RecordingCaptureSettings(max_nodes=7, max_text_chars=13).model_dump(mode="json")
        await asyncio.to_thread(env.configs.save, CONFIG_KEY, first_policy, scope_key=projects[0].id)
        try:
            first = await service.launch(RecordingCreateRequest(project_id=projects[0].id, entry_url="https://fixture.example.test",
                metadata={"capture_settings": {"payload": {"max_nodes": 999}}}))
            assert first.metadata["capture_settings"]["payload"] == first_policy
            assert drivers[first.id]._capture_settings == first_policy
            disabled = {**first_policy, "response_enabled": False, "max_nodes": 3}
            await asyncio.to_thread(env.configs.save, CONFIG_KEY, disabled, scope_key=projects[0].id)
            second = await service.launch(RecordingCreateRequest(project_id=projects[0].id, entry_url="https://fixture.example.test"))
            other = await service.launch(RecordingCreateRequest(project_id=projects[1].id, entry_url="https://fixture.example.test"))
            assert second.metadata["capture_settings"]["payload"] == disabled
            assert other.metadata["capture_settings"]["payload"] == global_before.payload
            outputs = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for session in [first, second, other, first]:
                    response = await client.get("/api/v1/recordings/recorder.js", params={"recording_id": session.id})
                    assert response.status_code == 200
                    prefix = response.text.split(";\n", 1)[0]
                    payload = json.loads(prefix.split(" = ", 1)[1])
                    assert payload == session.metadata["capture_settings"]["payload"]
                    outputs.append({"recording_id": session.id, "payload": payload})
                legacy = await client.get("/api/v1/recordings/recorder.js")
                assert legacy.status_code == 200 and not legacy.text.startswith("window.__qaRecorderCaptureSettings = ")
            fresh = await store.get_session(first.id)
            assert fresh.metadata["capture_settings"] == first.metadata["capture_settings"]
            evidence = Path(os.getenv("RECORDING_CAPTURE_EVIDENCE", str(tmp_path / "evidence"))) / ("live" if live else "offline")
            evidence.mkdir(parents=True, exist_ok=False)
            (evidence / "verification.json").write_text(json.dumps({"actual_mysql_postgres": live, "browser_driver": "fake for configuration isolation only",
                "original_session": first.model_dump(mode="json"), "pg_readback": fresh.model_dump(mode="json"),
                "current_configuration": (await captures.capture(projects[0].id)), "script_requests": outputs,
                "global_revision_preserved": True}, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            await service.close()
            if live:
                with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                    for name in table_names:
                        cursor.execute(f'DROP TABLE "{name}"')


@pytest.mark.parametrize("policy", [{"settle_ms": 500, "max_wait_ms": 250}, {"max_nodes": 0}, {"max_text_chars": 0}, {"max_actions": 0}, {"protocol": "unknown-version"}, {"max_nodes": 1001}])
def test_policy_rejects_unbounded_or_inverted_observation(policy):
    with pytest.raises(ValidationError):
        RecordingCaptureSettings.model_validate(policy)


def test_missing_or_corrupt_session_script_has_visible_error(caplog):
    from fastapi.testclient import TestClient
    service = FakeRecorderService()
    session = _make_session(service, metadata={"capture_settings": {"payload": {"max_nodes": 0}}})
    with TestClient(_make_app(service=service)) as client:
        assert client.get("/api/v1/recordings/recorder.js?recording_id=missing").status_code == 404
        response = client.get("/api/v1/recordings/recorder.js", params={"recording_id": session.id})
        assert response.status_code == 500 and "recording_capture_settings_invalid" in caplog.text


def test_actual_response_is_knowledge_and_not_a_replay_action():
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-recording-response-python-fixed/cdp-attach/response-dom/pg-events.json"
    events = [RecorderEvent.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    plan = build_replay_plan(events)
    response = next(event for event in events if event.type == "page_response")
    step = next(step for step in plan if step.seq == response.seq)
    assert step.skip_reason == "not_replayable"
