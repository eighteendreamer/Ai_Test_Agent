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
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright

from src.application.recorder.drivers.base import CaptureCheckpoint
from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecorderEvent, RecordingDriverConfig, RecordingCaptureSettings
from test_case_planning import environment
from src.api.routes.recordings import router
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import EmbeddedBridge
from src.application.recorder.finish_config_service import RecordingFinishConfigService
from src.application.recorder.capture_config_service import RecordingCaptureConfigService, CONFIG_KEY as CAPTURE_KEY
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.case_planning import PlanningQuery
from src.schemas.project import ProjectCreateRequest

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_CDP_RECORDING") != "1", reason="actual Chromium CDP required")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["navigation", "cross-origin", "nested-frame", "popup", "popup-close", "frame-remove", "history-back", "response-dom"])
async def test_actual_browser_retains_operations_across_document_scopes(tmp_path, scenario):
    await _scope_scenario(tmp_path, scenario)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_BFCACHE") != "1", reason="requires full Chromium with actual BFCache enabled")
async def test_actual_browser_cache_return_retains_capture_state(tmp_path):
    await _scope_scenario(tmp_path, "cache-back")


async def _scope_scenario(tmp_path, scenario, *, services=None, driver_kind="cdp-attach"):
    cache_return = scenario in ("cache-back", "cache-back-paused")
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
        "popup": '<!doctype html><meta charset="utf-8"><button id="popup-target">弹窗操作</button><input id="popup-tail">',
    }
    if scenario not in ("nested-frame", "frame-remove"):
        files["root"] = files["root"].split('<iframe')[0]
    if scenario in ("nested-frame", "frame-remove"):
        files["inner"] += """<script>document.querySelector('#inner-target').onclick=()=>{
          const response=document.createElement('p');response.id='inner-response';response.textContent='嵌套操作后响应';document.body.append(response);
        };</script>"""
    if scenario == "response-dom":
        files["root"] = '''<!doctype html><meta charset="utf-8"><button id="root">触发动态响应</button>
        <script>
        window.__fixtureMutations=0;
        new MutationObserver(records=>window.__fixtureMutations+=records.length).observe(document,{childList:true,subtree:true});
        document.querySelector('#root').onclick=()=>setTimeout(()=>{
          const response=document.createElement('section');response.id='response-ready';
          response.innerHTML='<button id="response-action">操作后新增按钮</button><p>本次页面已更新</p>';
          document.body.append(response);
        },50);
        </script>'''
    if scenario in ("response-input", "response-none", "response-stop", "response-limit"):
        if scenario in ("response-stop", "response-limit"):
            files["root"] = '''<!doctype html><meta charset="utf-8"><button id="root">触发响应</button><section id="response-ready"></section>
            <script>document.querySelector('#root').onclick=()=>{
              document.querySelector('#response-ready').innerHTML='<button id="response-action">操作后新增按钮</button><p>本次页面已更新</p>';
            };</script>'''
        else:
            files["root"] = '''<!doctype html><meta charset="utf-8"><button id="root">独立操作</button><input id="search"><section id="response-ready"></section>
            <script>document.querySelector('#search').oninput=()=>{document.querySelector('#response-ready').textContent='输入后立即出现的结果';};</script>'''
    if scenario in ("popup-close", "frame-remove", "history-back") or cache_return:
        diagnostic = """<script>
        window.__testLifecycle=[];
        for(const type of ['pageshow','pagehide','beforeunload'])addEventListener(type,event=>{
          const row={type,persisted:event.persisted??null,url:location.href,time:performance.now()};
          window.__testLifecycle.push(row);
          navigator.sendBeacon('/lifecycle',JSON.stringify(row));
        });</script>"""
        files = {name: html.replace('<meta charset="utf-8">', '<meta charset="utf-8">' + diagnostic) for name, html in files.items()}
    for name, html in files.items():
        (evidence / f"{name}.html").write_text(html, encoding="utf-8")
    app = FastAPI()
    if services:
        env, store, graph, bridge, service, project = services
        app.state.recorder_service, app.state.recording_store = service, store
        app.state.recording_graph_store, app.state.embedded_bridge = graph, bridge
        app.state.settings, app.state.artifact_storage_service = env.settings, env.objects
        app.include_router(router, prefix="/api/v1")
        if scenario == "ready-start-gap":
            ready_release, start_arrived = asyncio.Event(), asyncio.Event()
            original_update, original_control = store.update_status, service.control

            async def delayed_ready_reply(recording_id, status, **fields):
                result = await original_update(recording_id, status, **fields)
                if status.value == "ready":
                    await ready_release.wait()
                return result

            async def observed_control(recording_id, action):
                if action.value == "start":
                    start_arrived.set()
                return await original_control(recording_id, action)

            store.update_status, service.control = delayed_ready_reply, observed_control

    @app.get("/{name}")
    async def fixture(name: str):
        if name not in files:
            return HTMLResponse("", status_code=404)
        return HTMLResponse((evidence / f"{name}.html").read_bytes())

    @app.post("/lifecycle")
    async def lifecycle(request: Request):
        with (evidence / "lifecycle.jsonl").open("a", encoding="utf-8") as handle:
            handle.write((await request.body()).decode() + "\n")
        return {"received": True}

    servers = [uvicorn.Server(uvicorn.Config(app, log_level="warning")) for _ in range(2)]
    serving = [asyncio.create_task(server.serve(sockets=[listener])) for server, listener in zip(servers, (first, second))]
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0)); port = reserved.getsockname()[1]
    events, states, deliveries, consumer, driver, screenshot_uri = [], [], [], None, None, None
    async with async_playwright() as playwright:
        launch_options = {"headless": True, "args": [f"--remote-debugging-port={port}"]}
        if cache_return:
            # Installed Playwright disables BFCache by default; filter only that flag.
            launch_options["ignore_default_args"] = ["--disable-back-forward-cache"]
            launch_options["channel"] = "chromium"
        save("launch-options.json", launch_options)
        browser = await playwright.chromium.launch(**launch_options)
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
                    if scenario == "ready-start-gap":
                        pending_start = asyncio.create_task(client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "start"}))
                        try:
                            await asyncio.wait_for(start_arrived.wait(), timeout=3)
                            await asyncio.sleep(.05)
                            save("ready-start-gap.json", {"pg_status": (await store.get_session(recording_id)).status.value,
                                "runtime_status": service.runtime_status(recording_id).value,
                                "http_start_done_before_ready_reply": pending_start.done()})
                        finally:
                            ready_release.set()
                        response = await pending_start
                        save("ready-start-response.json", {"status": response.status_code, "body": response.json()})
                    else:
                        response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "start"})
                    response.raise_for_status()
            else:
                driver = CdpAttachDriver(config=RecordingDriverConfig(kind="cdp-attach", endpoint=f"http://127.0.0.1:{port}"), recording_id="scope-" + scenario)
                driver.configure_capture(RecordingCaptureSettings().model_dump(mode="json"))
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
            original_scope_event = driver._scopes.event

            def observe_scoped(raw, **native_source):
                deliveries.append(dict(raw))
                return original_scope_event(raw, **native_source)

            driver._scopes.event = observe_scoped
            if cache_return:
                cache_diagnostics = await driver._context.new_cdp_session(page)
                cache_diagnostics.on("Page.backForwardCacheNotUsed", lambda details: save("cache-not-used.json", details))
                await cache_diagnostics.send("Page.enable")
            if scenario.startswith("response-"):
                async with asyncio.timeout(4):
                    while not any(event["type"] == "page_scan" for event in events):
                        await asyncio.sleep(.02)
            await page.click("#root")
            expected, expected_values = ["root"], []
            if scenario == "response-dom":
                await page.locator("#response-action").wait_for(state="visible")
                await asyncio.sleep(.7)
                save("response-dom.json", {"actual_response": await page.locator("#response-ready").inner_text(),
                    "actual_dom_mutations": await page.evaluate("window.__fixtureMutations")})
            elif scenario in ("response-input", "response-none", "response-stop", "response-limit"):
                if scenario == "response-input":
                    # Finish the unrelated click's observation first: this
                    # response must come from input acceptance before fill.
                    await asyncio.sleep(1.7)
                    await page.locator("#search").fill("实际查询")
                    expected_values.append("实际查询")
                    await page.wait_for_function("document.querySelector('#response-ready').textContent === '输入后立即出现的结果'")
                    await asyncio.sleep(.8)
                elif scenario == "response-none":
                    await asyncio.sleep(1.7)
                else:
                    await page.locator("#response-action").wait_for(state="visible")
                save("response-dom.json", {"actual_response": await page.locator("#response-ready").inner_text(),
                    "recorder_state": await page.evaluate("window.__qaRecorderGetState()")})
            elif scenario in ("navigation", "cross-origin", "history-back") or cache_return:
                await page.click("#next")
                await page.wait_for_url("**/next")
                states.append({"url": page.url, "state": await page.evaluate("window.__qaRecorderGetState()")})
                await page.click("#next-target")
                expected.append("next-target")
                if scenario == "history-back" or cache_return:
                    if scenario == "cache-back-paused":
                        async with httpx.AsyncClient(base_url=origin) as client:
                            response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "pause"})
                            response.raise_for_status()
                            save("cached-pause.json", response.json())
                    await page.go_back(wait_until="commit" if cache_return else "load")
                    if cache_return:
                        await page.wait_for_function("window.__testLifecycle.some(row=>row.type==='pageshow'&&row.persisted===true)")
                    await page.evaluate("() => window.__qaRecorderReady")
                    states.append({"url": page.url, "state": await page.evaluate("window.__qaRecorderGetState()"),
                        "lifecycle": await page.evaluate("window.__testLifecycle"),
                        "navigation": await page.evaluate("performance.getEntriesByType('navigation').map(row=>({type:row.type,notRestoredReasons:row.notRestoredReasons?.toJSON?.()??null}))")})
                    if scenario == "cache-back-paused":
                        assert states[-1]["state"]["enabled"] is False
                        before = len(events)
                        await page.click("#root")
                        assert len(events) == before
                        async with httpx.AsyncClient(base_url=origin) as client:
                            response = await client.post(f"/api/v1/recordings/{recording_id}/control", json={"action": "resume"})
                            response.raise_for_status()
                            save("cached-resume.json", response.json())
                    await page.click("#root")
                    expected.append("root")
            elif scenario in ("nested-frame", "frame-remove"):
                frame = page.frame_locator("#outer")
                await frame.locator("#outer-target").click()
                await frame.frame_locator("#inner").locator("#inner-target").click()
                await frame.frame_locator("#inner").locator("#tail").fill("嵌套最后输入")
                for native in page.frames:
                    states.append({"url": native.url, "state": await native.evaluate("window.__qaRecorderGetState && window.__qaRecorderGetState()")})
                expected.extend(("outer-target", "inner-target"))
                if scenario == "frame-remove":
                    expected_values.append("嵌套最后输入")
                    await page.evaluate("document.querySelector('#outer').remove()")
                    assert len(page.frames) == 1
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
                    if scenario == "popup-close":
                        await popup.fill("#popup-tail", "关闭前最后输入")
                        expected_values.append("关闭前最后输入")
                        async with popup.expect_event("close"):
                            await popup.evaluate("window.close()")
                        assert popup.is_closed()
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
                    actions = [node for node in knowledge[0].payload["graph"]["nodes"] if node["kind"] == "action"]
                    assert [node.get("event") for node in actions] == [graph._mask_sensitive_payload(event)["payload"] for event in saved], "knowledge query omitted the full recorded steps"
                    assert knowledge[0].payload["graph"]["event_payloads"]["status"] == "complete"
                    async with httpx.AsyncClient(base_url=origin) as client:
                        response = await client.get(f"/api/v1/recordings/{recording_id}/graph")
                        response.raise_for_status()
                        save("projection-http.json", response.json())
                        assert response.json() == knowledge[0].payload["graph"]
                    assert (await graph.get_recording_subgraph(recording_id, project_id=str(uuid4())))["status"] == "not_found"
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
            save("scoped-deliveries.json", deliveries)
            save("states.json", states)
            save("receipt.json", receipt)
            actual = [(event.get("target") or {}).get("locators", {}).get("id") for event in events if event["type"] == "click"]
            save("verification.json", {"expected_target_ids": expected, "actual_target_ids": actual,
                "expected_values": expected_values, "actual_values": [event.get("value") for event in events if event["type"] == "fill"],
                "physical_browser_operations": True, "model_calls": 0, "full_recording_acceptance": False})
            if completion_error is not None:
                raise completion_error
            assert all(actual.count(target) >= expected.count(target) for target in expected), "real browser operations were lost across scopes"
            assert all(value in [event.get("value") for event in events if event["type"] == "fill"] for value in expected_values), "last accepted input was lost on document departure"
            assert receipt is not None and not receipt["frames_unconfirmed"], "capture completion did not confirm every scope"
            if scenario == "response-dom":
                assert any("操作后新增按钮" in json.dumps(event.get("page_effect"), ensure_ascii=False)
                    for event in events), "actual post-action DOM response was not recorded"
            if scenario in ("nested-frame", "frame-remove"):
                assert any(event["type"] == "page_response" and "嵌套操作后响应" in json.dumps(event["page_effect"], ensure_ascii=False)
                    and len(event["page"]["recording_scope"]["frame_path"]) == 2 for event in events)
                for event in events:
                    if event["type"] == "page_response" and len(event["page"]["recording_scope"]["frame_path"]):
                        assert all(element["target"]["in_iframe"] is True for element in event["page_effect"]["response"]["elements"] if "target" in element)
            if scenario in ("response-input", "response-none", "response-stop", "response-limit"):
                responses = [event for event in events if event["type"] == "page_response"]
                assert responses
                last = responses[-1]
                observed = last["page_effect"]["response"]
                related = [event for event in events if event["page"]["recording_scope"]["document_id"] == last["page"]["recording_scope"]["document_id"]
                    and event["page"]["recording_scope"]["local_seq"] in observed["action_local_seqs"]]
                assert related and all(event["seq"] < last["seq"] for event in related)
                if scenario == "response-input":
                    assert len(related) == 1 and related[0]["type"] == "fill"
                    assert "输入后立即出现的结果" in json.dumps(observed, ensure_ascii=False)
                elif scenario == "response-none":
                    assert observed["observation_status"] == "no_changes_observed" and observed["mutation_count"] == 0
                elif scenario == "response-stop":
                    assert observed["reason"] == "stop" and "操作后新增按钮" in json.dumps(observed, ensure_ascii=False)
                else:
                    assert observed["truncated"] and len(observed["elements"]) <= 2
                    assert "node_limit" in observed["truncation_reasons"]
                    assert all(len(element.get("text", "")) <= 5 for element in observed["elements"])
            if cache_return:
                assert any(row["type"] == "pageshow" and row["persisted"] is True for row in states[-1]["lifecycle"]), "real BFCache restoration was not exercised"
                root_documents = {event["page"]["recording_scope"]["document_id"] for event in events if (event.get("target") or {}).get("locators", {}).get("id") == "root"}
                assert len(root_documents) == 1, "BFCache return replaced the original document identity"
                assert [event["seq"] for event in events] == list(range(len(events)))
                for scope in receipt["scopes"]:
                    assert scope["received_count"] == scope["next_seq"]
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
@pytest.mark.parametrize("scenario", ["navigation", "cross-origin", "nested-frame", "popup", "pause-new-frame", "popup-close", "frame-remove", "history-back", "ready-start-gap", "response-dom", "response-input", "response-none", "response-stop", "response-limit"])
async def test_actual_scopes_through_api_and_pg(tmp_path, scenario, driver_kind):
    await _live_scope_scenario(tmp_path, scenario, driver_kind)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_SCOPE_STORAGE") != "1" or os.getenv("RUN_LIVE_RECORDING_BFCACHE") != "1", reason="requires real stores and full Chromium BFCache")
@pytest.mark.parametrize("scenario", ["cache-back", "cache-back-paused"])
async def test_actual_cache_activation_through_api_and_pg(tmp_path, scenario):
    await _live_scope_scenario(tmp_path, scenario, "cdp-attach")


async def _live_scope_scenario(tmp_path, scenario, driver_kind):
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
        capture = RecordingCaptureConfigService(config_store=env.configs, project_service=env.projects)
        await capture.initialize()
        if scenario in ("response-stop", "response-limit"):
            policy = RecordingCaptureSettings(settle_ms=10000, max_wait_ms=10000).model_dump(mode="json")
            if scenario == "response-limit": policy.update(max_nodes=2, max_text_chars=5)
            await asyncio.to_thread(env.configs.save, CAPTURE_KEY, policy, scope_key=project.id)
        service = RecorderSessionService(env.settings, store, graph, bridge=bridge, finish_config_service=config, capture_config_service=capture)
        env.sources.set_recording_sources(recording_store=store, recording_graph_store=graph)
        try:
            await _scope_scenario(tmp_path, scenario, services=(env, store, graph, bridge, service, project), driver_kind=driver_kind)
        finally:
            await service.close()
            await asyncio.to_thread(graph._provider.execute_write, "MATCH (n {project_id: $project_id}) DETACH DELETE n", {"project_id": project.id})
            await graph.close()
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables: cursor.execute(f'DROP TABLE "{table}"')
