"""Recorded evidence must survive conversion, not just a truncated action label."""
from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.recorder.recording_case_draft_service import (
    RecordingCaseDraftService, build_recording_draft_payload,
)
from src.schemas.recording import RecorderEvent, RecordingSession

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def captured_events(relative):
    return [RecorderEvent.model_validate(event) for event in json.loads(
        (DOCS / relative / "pg-events.json").read_text(encoding="utf-8"))]


def test_actual_nested_frame_events_survive_draft_conversion():
    events = captured_events("p0-recording-knowledge-reproduction-python/cdp-attach/nested-frame")
    session = RecordingSession(project_id="captured-project", entry_url=events[0].page["url"])
    payload = build_recording_draft_payload(session, events)
    for step in payload.steps:
        original = next(event for event in events if event.seq == step.data["seq"])
        assert step.data.get("event") == original.model_dump(mode="json"), "draft discarded original action evidence"
    assert payload.test_data["recording_observations"] == [
        event.model_dump(mode="json") for event in events if event.type == "page_scan"]


def test_actual_truncated_response_retains_limits_without_becoming_an_action():
    events = captured_events("p0-recording-response-final-python/cdp-attach/response-limit")
    response = next(event for event in events if event.type == "page_response")
    assert response.page_effect["response"]["truncated"] is True
    session = RecordingSession(project_id="captured-project", entry_url=events[0].page["url"])
    payload = build_recording_draft_payload(session, events)
    assert response.model_dump(mode="json") in payload.test_data.get("recording_observations", []), "draft discarded omission evidence"
    assert all(step.data["recorder_type"] != "page_response" for step in payload.steps)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_DRAFT") != "1", reason="requires real Chromium, MySQL/PG/RustFS/Memgraph")
async def test_actual_browser_draft_persistence_and_project_read(tmp_path):
    from playwright.async_api import async_playwright
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from httpx import AsyncClient
    import uvicorn
    from test_case_planning import environment
    from src.api.routes.recordings import router as recordings_router
    from src.application.exploration.recording_graph_store import RecordingGraphStore
    from src.application.recorder.drivers import EmbeddedBridge
    from src.application.recorder.recorder_session_service import RecorderSessionService
    from src.application.recorder.finish_config_service import RecordingFinishConfigService
    from src.application.recorder.capture_config_service import RecordingCaptureConfigService
    from src.application.test_cases.case_store import PostgresTestCaseStore
    from src.application.test_cases.case_service import TestCaseService
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.infrastructure.recording_store import PostgresRecordingStore
    from src.schemas.project import ProjectCreateRequest
    from src.schemas.recording import RecordingCreateRequest, RecordingControlAction, RecordingStatus

    evidence = Path(os.environ["RECORDING_DRAFT_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    # Original local page: two real clicks must remain two distinct actions.
    fixture = tmp_path / "recorded-form.html"
    html = '<!doctype html><meta charset="utf-8"><title>录制转草稿</title><input id="long"><input id="password" type="password"><button id="repeat">增加</button><p id="count">0</p><script>document.querySelector("#repeat").onclick=()=>{document.querySelector("#count").textContent=String(Number(document.querySelector("#count").textContent)+1)};</script>'
    fixture.write_text(html, encoding="utf-8")
    (evidence / "fixture.html").write_text(html, encoding="utf-8")
    long_value = "完整输入😀" * 80
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0)); port = reserved.getsockname()[1]
    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="recorded-" + uuid4().hex[:12], name="真实录制草稿"))
        other = await env.projects.create(ProjectCreateRequest(project_key="other-" + uuid4().hex[:12], name="另一项目"))
        suffix = uuid4().hex[:12]
        tables = ["up_de_" + suffix, "up_ds_" + suffix]
        env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = tables
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        store, graph = PostgresRecordingStore(env.settings), RecordingGraphStore(env.settings)
        await store.initialize()
        finish = RecordingFinishConfigService(config_store=env.configs, project_service=env.projects)
        capture = RecordingCaptureConfigService(config_store=env.configs, project_service=env.projects)
        await finish.initialize(); await capture.initialize()
        service = RecorderSessionService(env.settings, store, graph, bridge=EmbeddedBridge(), finish_config_service=finish, capture_config_service=capture)
        server = serving = listener = None
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
                try:
                    session = await service.launch(RecordingCreateRequest(project_id=project.id, session_id=str(uuid4()), entry_url=fixture.resolve().as_uri(), driver={"kind":"cdp-attach", "endpoint":f"http://127.0.0.1:{port}"}))
                    async with asyncio.timeout(10):
                        while (await store.get_session(session.id)).status != RecordingStatus.ready: await asyncio.sleep(.05)
                    await service.control(session.id, RecordingControlAction.start)
                    page = service._runtimes[session.id].driver._page
                    await page.locator("#long").fill(long_value)
                    await page.locator("#password").fill("synthetic-redaction-sentinel")
                    await page.wait_for_timeout(600)
                    await page.locator("#repeat").click(); await page.locator("#repeat").click()
                    assert await page.locator("#count").inner_text() == "2"
                    final = await service.control(session.id, RecordingControlAction.stop)
                    assert final.status == RecordingStatus.completed
                    assert final.step_count == 8
                    assert final.finalize_metrics["integrity"]["pg_step_count"] == 8
                    assert final.finalize_metrics["integrity"]["pg_event_count"] == 8
                    assert final.finalize_metrics["integrity"]["degraded"] is False
                    events = await store.get_events(session.id)
                    save("pg-events.json", [event.model_dump(mode="json") for event in events])
                    save("pg-session.json", final.model_dump(mode="json"))
                    draft_service = RecordingCaseDraftService(recorder_service=service, test_case_service=env.cases)
                    app = FastAPI()
                    app.include_router(recordings_router, prefix="/api/v1")
                    app.state.recording_case_draft_service = draft_service
                    app.state.recorder_service = service
                    app.state.lose_reply = True
                    @app.middleware("http")
                    async def lose_one_committed_reply(request, call_next):
                        result = await call_next(request)
                        if request.method == "POST" and result.status_code == 201 and app.state.lose_reply:
                            app.state.lose_reply = False
                            result = JSONResponse({"detail": "test-owned lost reply after commit"}, status_code=503)
                        with (evidence / "server-http.jsonl").open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps({"method": request.method, "path": request.url.path,
                                "status": result.status_code}) + "\n")
                        return result
                    listener = socket.socket()
                    listener.bind(("127.0.0.1", 0))
                    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
                    serving = asyncio.create_task(server.serve(sockets=[listener]))
                    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
                    async with asyncio.timeout(10):
                        while not server.started:
                            assert not serving.done()
                            await asyncio.sleep(.05)
                    # Later unrelated recordings used to hide this recording from the global top 20.
                    for index in range(25):
                        await store.create_session(RecordingSession(project_id=other.id, session_id="unrelated-session",
                            entry_url=fixture.resolve().as_uri(), name=f"unrelated-{index}"))
                    assert session.id not in [value.id for value in await store.list_sessions(limit=20)]
                    async with AsyncClient(base_url=origin) as client:
                        own = await client.get("/api/v1/recordings", params={"session_id": session.session_id, "project_id": project.id})
                        assert own.status_code == 200 and own.json()["count"] == 1
                        assert [value["id"] for value in own.json()["items"]] == [session.id]
                        wrong_project = await client.get("/api/v1/recordings", params={"session_id": session.session_id, "project_id": other.id})
                        assert wrong_project.json() == {"items": [], "count": 0}
                        save("route-session-filter.json", {"global_top20_missing": True, "own": own.json(), "foreign": wrong_project.json()})
                    async with AsyncClient(base_url=origin) as client:
                        first = await client.get(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": project.id})
                        assert first.status_code == 200 and first.json() is None
                        submitted = await asyncio.gather(*[client.post(f"/api/v1/recordings/{session.id}/case-draft",
                            params={"project_id": project.id}) for _ in range(3)])
                        save("route-concurrent.json", [{"status": value.status_code, "body": value.json()} for value in submitted])
                        assert sorted(value.status_code for value in submitted) == [201, 201, 503]
                        response = await client.get(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": project.id})
                        assert response.status_code == 200
                        assert all(value.json() == response.json() for value in submitted if value.status_code == 201)
                    save("route-response.json", response.json())
                    async with AsyncClient(base_url=origin) as client:
                        replay = await client.post(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": project.id})
                        save("route-replay.json", {"status_code": replay.status_code, "body": replay.json()})
                    assert replay.status_code == 201, replay.text
                    assert replay.json() == response.json(), "retry must recover the original case and version"
                    from src.schemas.recording import RecordingCaseDraftResponse
                    converted = RecordingCaseDraftResponse.model_validate(response.json())
                    version = converted.version
                    save("case-version.json", version.model_dump(mode="json"))
                    long_event = next(event for event in events if event.type == "fill" and event.value == long_value)
                    step = next(step for step in version.steps if step.data["seq"] == long_event.seq)
                    assert step.data.get("event") == long_event.model_dump(mode="json"), "long input and document identity lost in persisted draft"
                    clicks = [step for step in version.steps if step.data["recorder_type"] == "click"]
                    assert len(clicks) == 2 and clicks[0].data["seq"] != clicks[1].data["seq"]
                    assert "synthetic-redaction-sentinel" not in json.dumps(version.model_dump(mode="json"))
                    restarted = TestCaseService(store=PostgresTestCaseStore(env.settings), project_service=env.projects)
                    assert (await restarted.get_version(version.id)).model_dump(mode="json") == version.model_dump(mode="json")
                    page_of_cases = await restarted.list_cases(project_id=project.id, status=None, mode_key=None,
                        priority=None, query=None, limit=50, offset=0)
                    assert len(page_of_cases.items) == 1
                    assert len(await restarted.list_versions(converted.case.id)) == 1
                    app.state.recording_case_draft_service = RecordingCaseDraftService(recorder_service=service, test_case_service=restarted)
                    async with AsyncClient(base_url=origin) as client:
                        restored = await client.get(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": project.id})
                        foreign = await client.get(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": other.id})
                    assert restored.json() == response.json()
                    assert foreign.status_code == 409
                    save("route-restarted.json", {"restored": restored.json(), "foreign_status": foreign.status_code,
                        "project_case_count": len(page_of_cases.items), "version_count": 1})
                    await env.cases.submit_review(converted.case.id); await env.cases.activate(converted.case.id)
                    selected = await env.sources.case(project.id, converted.case.id)
                    save("planning-evidence.json", selected.model_dump(mode="json"))
                    assert selected.payload["content"]["steps"][step.order - 1]["data"]["event"]["value"] == long_value
                    with pytest.raises(ValueError, match="不属于当前项目"):
                        await env.sources.case(other.id, converted.case.id)
                    assert (await graph.get_recording_subgraph(session.id, project_id=other.id))["status"] == "not_found"
                    assert (await store.get_events(session.id)) == events
                    await env.projects.archive(project.id)
                    async with AsyncClient(base_url=origin) as client:
                        historical = await client.get(f"/api/v1/recordings/{session.id}/case-draft", params={"project_id": project.id})
                    assert historical.status_code == 200
                    assert historical.json()["version"] == response.json()["version"]
                    save("route-archived.json", historical.json())
                finally:
                    await browser.close()
        finally:
            if server is not None:
                server.should_exit = True
                async with asyncio.timeout(10):
                    await serving
            if listener is not None:
                listener.close()
            await service.close()
            await asyncio.to_thread(graph._provider.execute_write, "MATCH (n {project_id: $project_id}) DETACH DELETE n", {"project_id": project.id})
            await graph.close()
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in tables: cur.execute(f'DROP TABLE "{table}"')
