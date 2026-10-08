"""Actual browser scope counterexamples; original drivers and saved HTML."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright

from src.application.recorder.drivers.base import CaptureCheckpoint
from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecorderEvent, RecordingDriverConfig
from test_case_planning import environment
from src.api.routes.recordings import router
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import EmbeddedBridge
from src.application.recorder.finish_config_service import RecordingFinishConfigService
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.case_planning import PlanningQuery
from src.schemas.project import ProjectCreateRequest

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_CDP_RECORDING") != "1", reason="actual Chromium CDP required")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["navigation", "cross-origin", "nested-frame", "popup"])
async def test_actual_browser_retains_operations_across_document_scopes(tmp_path, scenario):
    await _scope_scenario(tmp_path, scenario)


async def _scope_scenario(tmp_path, scenario, *, services=None, driver_kind="cdp-attach"):
    evidence = Path(os.getenv("RECORDING_SCOPE_EVIDENCE", str(tmp_path / "evidence")))
    if services: evidence = evidence / driver_kind
    evidence = evidence / scenario
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    first, second = socket.socket(), socket.socket()
    first.bind(("127.0.0.1", 0)); second.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{first.getsockname()[1]}"
    other = f"http://127.0.0.1:{second.getsockname()[1]}"
    files = {
        "root": f'<!doctype html><meta charset="utf-8"><button id="root">主页面操作</button><a id="next" href="{other if scenario == "cross-origin" else origin}/next">进入下一页</a><button id="popup" onclick="window.open(\'{other}/popup\')">打开窗口</button><iframe id="outer" src="{other}/outer"></iframe>',
        "next": '<!doctype html><meta charset="utf-8"><button id="next-target">导航后操作</button>',
        "outer": f'<!doctype html><meta charset="utf-8"><button id="outer-target">外层操作</button><iframe id="inner" src="{origin}/inner"></iframe>',
        "inner": '<!doctype html><meta charset="utf-8"><button id="inner-target">嵌套操作</button><input id="tail">',
        "popup": '<!doctype html><meta charset="utf-8"><button id="popup-target">弹窗操作</button>',
    }
    if scenario != "nested-frame":
        files["root"] = files["root"].split('<iframe')[0]
    for name, html in files.items():
        (evidence / f"{name}.html").write_text(html, encoding="utf-8")
    app = FastAPI()
    if services:
        env, store, graph, bridge, service, project = services
        app.state.recorder_service, app.state.recording_store = service, store
        app.state.recording_graph_store, app.state.embedded_bridge = graph, bridge
        app.state.settings, app.state.artifact_storage_service = env.settings, env.objects
        app.include_router(router, prefix="/api/v1")

    @app.get("/{name}")
    async def fixture(name: str):
        return HTMLResponse((evidence / f"{name}.html").read_bytes())

    servers = [uvicorn.Server(uvicorn.Config(app, log_level="warning")) for _ in range(2)]
    serving = [asyncio.create_task(server.serve(sockets=[listener])) for server, listener in zip(servers, (first, second))]
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0)); port = reserved.getsockname()[1]
    events, states, consumer, driver, screenshot_uri = [], [], None, None, None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        try:
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    assert not any(task.done() for task in serving)
                    await asyncio.sleep(.05)
            if services:
                async with httpx.AsyncClient(base_url=origin) as client:
                    response = await client.post("/api/v1/recordings", json={"project_id": project.id,
                        "entry_url": origin + "/root", "driver": {"kind": driver_kind, "endpoint": f"http://127.0.0.1:{port}"}})
                    response.raise_for_status()
                    created = response.json()
                    save("created.json", created)
                    recording_id = created["id"]
                    async with asyncio.timeout(10):
                        while (await store.get_session(recording_id)).status.value != "ready": await asyncio.sleep(.05)
                    driver = service._runtimes[recording_id].driver
                    original_publish = driver._channel.publish

                    def observe(event):
                        events.append(event)
                        return original_publish(event)

                    driver._channel.publish = observe
                    response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "start"})
                    response.raise_for_status()
            else:
                driver = CdpAttachDriver(config=RecordingDriverConfig(kind="cdp-attach", endpoint=f"http://127.0.0.1:{port}"), recording_id="scope-" + scenario)
                await driver.open(origin + "/root", viewport=(1440, 960))
                await driver.inject_recorder()

            async def consume():
                stream = await driver.on_recorder_event()
                async for event in stream:
                    if isinstance(event, CaptureCheckpoint):
                        if not event.completed.done(): event.completed.set_result(None)
                    else:
                        RecorderEvent.model_validate(event)
                        events.append(event)

            if not services:
                consumer = asyncio.create_task(consume())
                await driver.set_capture_enabled(True)
            page = driver._page
            await page.click("#root")
            expected = ["root"]
            if scenario in ("navigation", "cross-origin"):
                await page.click("#next")
                await page.wait_for_url("**/next")
                states.append({"url": page.url, "state": await page.evaluate("window.__qaRecorderGetState()")})
                await page.click("#next-target")
                expected.append("next-target")
            elif scenario == "nested-frame":
                frame = page.frame_locator("#outer")
                await frame.locator("#outer-target").click()
                await frame.frame_locator("#inner").locator("#inner-target").click()
                await frame.frame_locator("#inner").locator("#tail").fill("嵌套最后输入")
                for native in page.frames:
                    states.append({"url": native.url, "state": await native.evaluate("window.__qaRecorderGetState && window.__qaRecorderGetState()")})
                expected.extend(("outer-target", "inner-target"))
            else:
                if scenario == "pause-new-frame":
                    async with httpx.AsyncClient(base_url=origin) as client:
                        response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "pause"})
                        response.raise_for_status()
                        save("pause.json", response.json())
                        # Controlled fixture attaches a genuine new document while paused.
                        await page.evaluate("url => {const frame=document.createElement('iframe');frame.id='new-frame';frame.src=url;document.body.appendChild(frame)}", other + "/inner")
                        await page.frame_locator("#new-frame").locator("#inner-target").click()
                        native = next(frame for frame in page.frames if frame != page.main_frame)
                        states.append({"url": native.url, "state": await native.evaluate("window.__qaRecorderGetState()")})
                        assert states[-1]["state"]["enabled"] is False
                        assert not any((event.get("target") or {}).get("locators", {}).get("id") == "inner-target" for event in events)
                        response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "resume"})
                        response.raise_for_status()
                        await page.frame_locator("#new-frame").locator("#inner-target").click()
                        expected.append("inner-target")
                else:
                    async with page.expect_popup() as opening:
                        await page.click("#popup")
                    popup = await opening.value
                    await popup.wait_for_load_state("domcontentloaded")
                    states.append({"url": popup.url, "state": await popup.evaluate("window.__qaRecorderGetState && window.__qaRecorderGetState()")})
                    await popup.click("#popup-target")
                    expected.append("popup-target")
            if services:
                unrelated = await driver._context.new_page()
                await unrelated.goto(other + "/popup")
                await unrelated.click("#popup-target")
                unrelated_state = await unrelated.evaluate("window.__qaRecorderGetState()")
                save("unrelated-page.json", {"state": unrelated_state, "owned": unrelated in driver._owned_pages})
                assert unrelated_state["enabled"] is False and unrelated not in driver._owned_pages
                assert all(event["page"].get("recording_scope", {}).get("document_id") != unrelated_state["document_id"] for event in events)
                await unrelated.close()
            receipt, completion_error = None, None
            try:
                if services:
                    async with httpx.AsyncClient(base_url=origin) as client:
                        screenshot_bytes = await driver.capture_screenshot()
                        response = await client.post(f"/api/v1/recordings/{recording_id}/screenshots",
                            files={"file": ("scope.png", screenshot_bytes, "image/png")})
                        response.raise_for_status()
                        screenshot_uri = response.json()["ref"]
                        assert screenshot_uri.startswith("rustfs://")
                        saved_screenshot = (await env.objects.read_object_uri(screenshot_uri))["content"]
                        assert saved_screenshot == screenshot_bytes
                        (evidence / "screenshot-rustfs.png").write_bytes(saved_screenshot)
                        save("screenshot-storage.json", {"uri": screenshot_uri, "sha256": hashlib.sha256(saved_screenshot).hexdigest(),
                            "size_bytes": len(saved_screenshot)})
                        response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "stop"})
                        response.raise_for_status()
                        final = response.json()
                    saved = await store.get_events(recording_id)
                    save("pg-events.json", [event.model_dump(mode="json") for event in saved])
                    save("pg-session.json", (await store.get_session(recording_id)).model_dump(mode="json"))
                    assert final["status"] == "completed", final.get("finalize_metrics")
                    receipt = final["finalize_metrics"]["finish_receipt"]
                    assert receipt["pg_verified"] and all(scope["pg_verified"] for scope in receipt["scopes"])
                    knowledge, _ = await env.sources.query(project.id, PlanningQuery(kind="recording", source_id=recording_id), 1)
                    save("planning-knowledge.json", [item.model_dump(mode="json") for item in knowledge])
                    assert knowledge[0].payload["graph"]["status"] == "success"
                    assert len(saved) == len(events)
                    for event, observed in zip(saved, events):
                        for field in ("seq", "type", "page", "target", "pixel", "value", "page_effect"):
                            assert event.model_dump(mode="json")[field] == observed.get(field)
                else:
                    receipt = await asyncio.wait_for(driver.finish_capture(), timeout=5)
                    await consumer
            except Exception as exc:
                save("finish-error.json", {"type": type(exc).__name__, "message": str(exc)})
                completion_error = exc
            save("events.json", events)
            save("states.json", states)
            save("receipt.json", receipt)
            actual = [(event.get("target") or {}).get("locators", {}).get("id") for event in events if event["type"] == "click"]
            save("verification.json", {"expected_target_ids": expected, "actual_target_ids": actual,
                "physical_browser_operations": True, "model_calls": 0, "full_recording_acceptance": False})
            if completion_error is not None:
                raise completion_error
            assert all(target in actual for target in expected), "real browser operations were lost across scopes"
            assert receipt is not None and not receipt["frames_unconfirmed"], "capture completion did not confirm every scope"
        finally:
            if consumer and not consumer.done():
                consumer.cancel(); await asyncio.gather(consumer, return_exceptions=True)
            if driver: await driver.close()
            if services and screenshot_uri:
                assert f"/recordings/{recording_id}/" in screenshot_uri
                await env.objects.delete_object_uri(screenshot_uri)
            await browser.close()
            for server in servers: server.should_exit = True
            await asyncio.gather(*serving)
            first.close(); second.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_SCOPE_STORAGE") != "1", reason="actual MySQL/PG/RustFS/Memgraph required")
@pytest.mark.parametrize("driver_kind", ["cdp-attach", "playwright-managed"])
@pytest.mark.parametrize("scenario", ["navigation", "cross-origin", "nested-frame", "popup", "pause-new-frame"])
async def test_actual_scopes_through_api_and_pg(tmp_path, scenario, driver_kind):
    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="scope-" + uuid4().hex[:12], name="文档范围录制验收"))
        suffix = uuid4().hex[:12]
        tables = ["up_se_" + suffix, "up_ss_" + suffix]
        env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = tables
        with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
        store, graph, bridge = PostgresRecordingStore(env.settings), RecordingGraphStore(env.settings), EmbeddedBridge()
        await store.initialize()
        config = RecordingFinishConfigService(config_store=env.configs, project_service=env.projects)
        await config.initialize()
        service = RecorderSessionService(env.settings, store, graph, bridge=bridge, finish_config_service=config)
        env.sources.set_recording_sources(recording_store=store, recording_graph_store=graph)
        try:
            await _scope_scenario(tmp_path, scenario, services=(env, store, graph, bridge, service, project), driver_kind=driver_kind)
        finally:
            await service.close()
            await asyncio.to_thread(graph._provider.execute_write, "MATCH (n {project_id: $project_id}) DETACH DELETE n", {"project_id": project.id})
            await graph.close()
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables: cursor.execute(f'DROP TABLE "{table}"')
