"""Original Electron + recording HTTP API + isolated real stores; no event fakes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from playwright.async_api import async_playwright
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from test_case_planning import environment
from src.api.routes.recordings import router
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import EmbeddedBridge
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.application.recorder.finish_config_service import RecordingFinishConfigService, CONFIG_KEY
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.case_planning import PlanningQuery
from src.schemas.project import ProjectCreateRequest
from src.schemas.recording import RecordingControlAction

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_PRODUCT") != "1", reason="requires actual Electron/MySQL/PG/RustFS/Memgraph")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["settled", "tail-input", "finish-timeout", "pause-input", "bridge-loss", "navigation", "cross-origin", "nested-frame", "popup", "pause-frame", "same-url-navigation", "identity-conflict"])
async def test_original_electron_controls_and_small_batches_reach_project_knowledge(tmp_path, scenario):
    root = Path(__file__).resolve().parents[2]
    evidence = Path(os.getenv("RECORDING_PRODUCT_EVIDENCE", str(tmp_path / "evidence"))).resolve() / scenario
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    fixture = evidence / "fixture.html"
    fixture.write_text("""<!doctype html><html><head><meta charset='utf-8'><title>实际录制夹具</title></head>
<body><button id='target' onclick='window.fixtureClicks=(window.fixtureClicks||0)+1'>创建订单</button>
<p>此页面只用于本次系统录制验收。</p></body></html>""", encoding="utf-8")
    if scenario in ("tail-input", "pause-input"):
        fixture.write_text(fixture.read_text(encoding="utf-8").replace("</body>", "<input id='tail' placeholder='最后一步内容'></body>"), encoding="utf-8")
    async with environment(tmp_path, live=True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="recording-" + uuid4().hex[:12], name="原窗口录制验收"))
        suffix = uuid4().hex[:12]
        tables = ["up_re_" + suffix, "up_rs_" + suffix]
        env.settings.database.postgres_recording_event_table, env.settings.database.postgres_recording_table = tables
        save("owned-recording-tables.json", {"tables": tables, "project_id": project.id})
        with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
        store = PostgresRecordingStore(env.settings)
        await store.initialize()
        graph = RecordingGraphStore(env.settings)
        bridge = EmbeddedBridge()
        finish_configs = RecordingFinishConfigService(config_store=env.configs, project_service=env.projects)
        await finish_configs.initialize()
        if scenario == "finish-timeout":
            await asyncio.to_thread(env.configs.save, CONFIG_KEY, {"timeout_seconds": 1.0}, scope_key=project.id)
        service = RecorderSessionService(env.settings, store, graph, bridge=bridge, finish_config_service=finish_configs)
        env.sources.set_recording_sources(recording_store=store, recording_graph_store=graph)
        app = FastAPI()
        app.state.recorder_service, app.state.recording_store = service, store
        app.state.recording_graph_store, app.state.embedded_bridge = graph, bridge
        app.state.settings, app.state.artifact_storage_service = env.settings, env.objects
        app.include_router(router, prefix="/api/v1")

        @app.middleware("http")
        async def actual_receipt_outage(request, call_next):
            if scenario == "finish-timeout" and request.url.path.endswith("/capture-finished"):
                body = await request.json()
                if body.get("operation", "stop") == "stop":
                    return JSONResponse({"error": "controlled actual HTTP finish outage"}, status_code=503)
            return await call_next(request)

        @app.get("/fixture")
        async def actual_fixture():
            return HTMLResponse(fixture.read_bytes())

        @app.get("/recorder-window")
        async def minimal_control_bar():
            return HTMLResponse("本次用原HTTP接口测试控制；此静态夹具不代表Vue页面验收。")

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        other = origin.replace("127.0.0.1", "localhost")
        scope_files = {
            "unrelated": '<button id="unrelated-target">无关窗口操作</button>',
            "next": '<button id="next-target">导航后操作</button>',
            "outer": f'<button id="outer-target">外层操作</button><iframe id="inner" src="{origin}/scope/inner"></iframe>',
            "inner": '<button id="inner-target">嵌套操作</button><input id="inner-tail">',
            "popup": '<button id="popup-target">弹窗操作</button><input id="popup-tail">',
        }
        if scenario in ("navigation", "cross-origin"):
            destination = other if scenario == "cross-origin" else origin
            fixture.write_text(fixture.read_text(encoding="utf-8").replace("</body>", f'<a id="scope-next" href="{destination}/scope/next">下一页</a></body>'), encoding="utf-8")
        elif scenario == "nested-frame":
            fixture.write_text(fixture.read_text(encoding="utf-8").replace("</body>", f'<iframe id="outer" src="{other}/scope/outer"></iframe></body>'), encoding="utf-8")
        elif scenario == "popup":
            fixture.write_text(fixture.read_text(encoding="utf-8").replace("</body>", f"<button id='scope-popup' onclick=\"window.open('{other}/scope/popup')\">打开弹窗</button></body>"), encoding="utf-8")
        elif scenario == "pause-frame":
            fixture.write_text(fixture.read_text(encoding="utf-8").replace("</body>", f"<button id='add-frame' onclick=\"var f=document.createElement('iframe');f.id='outer';f.src='{other}/scope/outer';document.body.appendChild(f)\">添加框架</button></body>"), encoding="utf-8")
        for name, content in scope_files.items():
            (evidence / f"scope-{name}.html").write_text('<!doctype html><meta charset="utf-8">' + content, encoding="utf-8")

        @app.get("/scope/{name}")
        async def scope_fixture(name: str):
            return HTMLResponse((evidence / f"scope-{name}.html").read_bytes())

        process = None
        playwright = None
        attached = None
        session_id = None
        commands = tmp_path / "commands.json"
        command_id = 0

        async def wait_json(path, timeout=15):
            async with asyncio.timeout(timeout):
                while not path.exists():
                    if process and process.poll() is not None:
                        raise AssertionError("original Electron exited; see saved stdout/stderr/error")
                    await asyncio.sleep(.05)
            return json.loads(path.read_text(encoding="utf-8"))

        async def command(action="state", **kwargs):
            nonlocal command_id
            command_id += 1
            staged = commands.with_suffix(".tmp")
            staged.write_text(json.dumps({"id": command_id, "action": action, **kwargs}), encoding="utf-8")
            staged.replace(commands.with_name(f"command-{command_id}.json"))
            return await wait_json(evidence / f"reply-{command_id}.json")

        async def enabled(expected):
            result = None
            for _ in range(30):
                result = await command()
                if result.get("recorder", {}).get("enabled") is expected:
                    return result
                await asyncio.sleep(.1)
            save("control-mismatch.json", {"expected_enabled": expected, "actual": result})
            raise AssertionError(f"original command did not set capture={expected}; actual={result}")

        try:
            async with httpx.AsyncClient(base_url=origin) as client:
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(.05)
                created = await client.post("/api/v1/recordings", json={"project_id": project.id, "entry_url": origin + "/fixture", "driver": {"kind": "embedded"}})
                created.raise_for_status()
                session_id = created.json()["id"]
                save("created.json", created.json())
                frozen_finish_settings = created.json()["metadata"]["finish_settings"]
                assert created.json()["metadata"]["finish_settings"]["revision"] >= 1
                # Actual MySQL change after creation must not change this session's policy.
                await asyncio.to_thread(env.configs.save, CONFIG_KEY, {"timeout_seconds": 1.0}, scope_key=project.id)
                current_finish_settings = await finish_configs.capture(project.id)
                save("finish-settings-current.json", current_finish_settings)
                assert current_finish_settings["payload"]["timeout_seconds"] == 1
                config = tmp_path / "electron.json"
                config.write_text(json.dumps({"origin": origin, "entryUrl": origin + "/fixture", "recordingId": session_id,
                    "module": str(root / "agent_web/electron/recorder-window.mjs"), "userData": str(tmp_path / "electron-user-data"),
                    "evidence": str(evidence), "commandsDirectory": str(commands.parent)}), encoding="utf-8")
                electron = root / "agent_web/node_modules/electron/dist/electron.exe"
                assert electron.is_file()
                with socket.socket() as reserved:
                    reserved.bind(("127.0.0.1", 0))
                    cdp_port = reserved.getsockname()[1]
                with (evidence / "electron.stdout").open("wb") as out, (evidence / "electron.stderr").open("wb") as err:
                    process = subprocess.Popen([str(electron), f"--remote-debugging-port={cdp_port}", str(root / "agent_web/tests/fixtures/recorder-live.mjs"), str(config)],
                        stdout=out, stderr=err, stdin=subprocess.DEVNULL, env={key: value for key, value in os.environ.items() if key != "ELECTRON_RUN_AS_NODE"},
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    await wait_json(evidence / "harness-ready.json", timeout=30)
                    playwright = await async_playwright().start()
                    attached = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                    page = next(page for context in attached.contexts for page in context.pages if page.url == origin + "/fixture")

                    async def clicks(count):
                        initial = await page.evaluate("window.fixtureClicks || 0")
                        for _ in range(count):
                            await page.locator("#target").click()
                        await page.wait_for_function("expected => window.fixtureClicks === expected", arg=initial + count)
                        return await command()
                    async with asyncio.timeout(10):
                        while service.runtime_status(session_id).value != "ready":
                            await asyncio.sleep(.05)

                    async def control(action):
                        response = await client.post(f"/api/v1/recordings/{session_id}/control", json={"action": action})
                        save(f"control-{action}.json", {"status": response.status_code, "body": response.json()})
                        response.raise_for_status()
                        return response.json()

                    await control("start")
                    await enabled(True)
                    await clicks(2)
                    await command("unrelated")
                    unrelated = next(p for c in attached.contexts for p in c.pages if p.url == origin + "/scope/unrelated")
                    await unrelated.locator("#unrelated-target").click()
                    assert await unrelated.evaluate("window.__qaRecorderInstalled || false") is False
                    async with asyncio.timeout(6):
                        while len([item for item in await store.get_events(session_id) if item.type == "click"]) < 2:
                            await asyncio.sleep(.1)
                    await command("snapshot")
                    screenshot_refs = [json.loads(path.read_text())["ref"] for path in evidence.glob("screenshot-[0-9]*.json")]
                    assert len(screenshot_refs) == 1 and screenshot_refs[0].startswith("rustfs://")
                    stored_screenshot = await env.objects.read_object_uri(screenshot_refs[0])
                    screenshot_bytes = stored_screenshot["content"]
                    assert screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
                    (evidence / "screenshot-rustfs.png").write_bytes(screenshot_bytes)
                    save("screenshot-storage.json", {"uri": screenshot_refs[0], "sha256": hashlib.sha256(screenshot_bytes).hexdigest(), "size_bytes": len(screenshot_bytes)})
                    if scenario == "pause-input":
                        await page.locator("#tail").fill("暂停前最后输入")
                        save("pause-input-before.json", {"value": await page.locator("#tail").input_value(),
                            "recorder": await page.evaluate("window.__qaRecorderGetState()")})
                    initial_bindings = [json.loads(line) for line in (evidence / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
                    original_click = next(event for event in initial_bindings if event["type"] == "click")
                    host_before = (await command())["host"]
                    # Replay the actual captured event through the native binding,
                    # including different JSON key order; no synthetic browser event.
                    replay_raw = dict(reversed(list(original_click.items())))
                    assert await page.evaluate("raw => window.__qaRecordEmit(JSON.stringify(raw))", replay_raw) is True
                    host_after = (await command())["host"]
                    save("native-replay.json", {"original": original_click, "host_before": host_before, "host_after": host_after})
                    assert host_before["forwardedEvents"] == host_after["forwardedEvents"]
                    assert host_before["bufferedEvents"] == host_after["bufferedEvents"]
                    assert host_after["droppedMalformed"] == 0
                    paused = await control("pause")
                    await enabled(False)
                    if scenario == "pause-input":
                        for _ in range(60):
                            paused_events = await store.get_events(session_id)
                            if any(event.type == "fill" and event.value == "暂停前最后输入" for event in paused_events):
                                break
                            await asyncio.sleep(.1)
                        save("pause-input-after.json", {"session": (await store.get_session(session_id)).model_dump(mode="json"),
                            "events": [event.model_dump(mode="json") for event in paused_events]})
                        assert any(event.type == "fill" and event.value == "暂停前最后输入" for event in paused_events), "pause lost the actually accepted final input"
                    before = len(await store.get_events(session_id))
                    if scenario == "pause-frame":
                        await page.locator("#add-frame").click()
                        await page.frame_locator("#outer").locator("#outer-target").click()
                        child = next(f for f in page.frames if f.url == other + "/scope/outer")
                        assert (await child.evaluate("window.__qaRecorderGetState()")).get("enabled") is False
                    await clicks(2)
                    await asyncio.sleep(.7)
                    assert len(await store.get_events(session_id)) == before
                    await control("resume")
                    await enabled(True)
                    await clicks(2)
                    async with asyncio.timeout(6):
                        while len([item for item in await store.get_events(session_id) if item.type == "click"]) < 4:
                            await asyncio.sleep(.1)
                    # Duplicate physical delivery is distinct from repeated legitimate clicks.
                    events = await store.get_events(session_id)
                    replay = await client.post(f"/api/v1/recordings/{session_id}/events:batch", json={"events": [item.model_dump(mode="json") for item in events]})
                    save("duplicate-delivery.json", replay.json())
                    assert replay.json()["accepted"] == 0 and replay.json()["duplicates"] == len(events)
                    if scenario == "tail-input":
                        await page.locator("#tail").fill("最后一步实际输入")
                        save("tail-input-before-stop.json", {"value": await page.locator("#tail").input_value(),
                            "recorder": await page.evaluate("window.__qaRecorderGetState()")})
                    expected_scope_targets = []
                    if scenario == "same-url-navigation":
                        await page.goto(origin + "/fixture")
                        await page.locator("#target").click()
                        expected_scope_targets = ["target"]
                    elif scenario in ("navigation", "cross-origin"):
                        await page.locator("#scope-next").click()
                        await page.locator("#next-target").click()
                        expected_scope_targets = ["next-target"]
                    elif scenario in ("nested-frame", "pause-frame"):
                        outer = page.frame_locator("#outer")
                        await outer.locator("#outer-target").click()
                        await outer.frame_locator("#inner").locator("#inner-target").click()
                        await outer.frame_locator("#inner").locator("#inner-tail").fill("跨来源嵌套最后输入")
                        expected_scope_targets = ["outer-target", "inner-target", "inner-tail"]
                    elif scenario == "popup":
                        async with page.expect_popup() as opened:
                            await page.locator("#scope-popup").click()
                        popup = await opened.value
                        await popup.locator("#popup-target").click()
                        await popup.locator("#popup-tail").fill("弹窗最后输入")
                        expected_scope_targets = ["popup-target", "popup-tail"]
                    if scenario == "identity-conflict":
                        current_raw = next(event for event in initial_bindings if event["type"] == "click")
                        altered = {**current_raw, "value": "changed actual replay value"}
                        rejected = await page.evaluate("async raw => {try {await window.__qaRecordEmit(JSON.stringify(raw)); return {accepted:true};} catch(error) {return {accepted:false,error:error.message};}}", altered)
                        save("identity-conflict.json", {"original": current_raw, "altered": altered, "response": rejected})
                        assert rejected["accepted"] is False and "identity cannot change" in rejected["error"]
                    save("scope-targets.json", {"expected": expected_scope_targets, "pages": [p.url for c in attached.contexts for p in c.pages]})
                    final = await control("stop")
                    assert final["metadata"]["finish_settings"] == frozen_finish_settings
                    if scenario == "finish-timeout":
                        assert final["status"] == "failed"
                        assert final["finalize_metrics"]["failure_reason"] == "finish_timeout"
                        assert final["finalize_metrics"]["finish_receipt"] is None
                        assert final["metadata"]["finish_settings"]["payload"]["timeout_seconds"] == 1
                        with pytest.raises(ValueError, match="illegal transition"):
                            await service.control(session_id, RecordingControlAction.start)
                        return
                    if scenario == "identity-conflict":
                        assert final["status"] == "failed"
                        assert final["finalize_metrics"]["failure_reason"] == "finish_unconfirmed"
                        assert final["finalize_metrics"]["finish_receipt"]["transport_dropped"] == 1
                        return
                    assert final["status"] == "completed"
                    assert final["finalize_metrics"]["finish_receipt"]["pg_verified"]
                    saved = await store.get_events(session_id)
                    bindings = [json.loads(line) for line in (evidence / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
                    assert not any((item.target or {}).get("locators", {}).get("id") == "unrelated-target" for item in saved)
                    for target in expected_scope_targets:
                        assert any((item.target or {}).get("locators", {}).get("id") == target for item in saved), f"actual scope operation missing: {target}"

                    for item in saved:
                        scope = item.page.get("recording_scope")
                        original = next(event for event in bindings if ((event.get("recording_document_id") == scope["document_id"] and event["seq"] == scope["local_seq"]) if scope else event["seq"] == item.seq))
                        expected_page = {**original.get("page", {}), "recording_scope": scope} if scope else original.get("page")
                        expected_target = {**original["target"], "in_iframe": True} if scope and scope["parent_frame_id"] and original.get("target") else original.get("target")
                        assert item.page == expected_page and item.target == expected_target
                        for field in ("type", "pixel", "value", "page_effect"):
                            assert item.model_dump(mode="json")[field] == original.get(field)
                    if scenario == "tail-input":
                        tail = [item for item in saved if item.type == "fill" and (item.target or {}).get("locators", {}).get("id") == "tail"]
                        save("tail-input-result.json", {"status": final["status"], "finalize_metrics": final["finalize_metrics"],
                            "tail_events": [item.model_dump(mode="json") for item in tail], "expected_value": "最后一步实际输入"})
                        assert len(tail) == 1 and tail[0].value == "最后一步实际输入", "stop lost the actual final input while reporting completed"
                    observed_clicks = [item for item in saved if item.type == "click"]
                    assert len(observed_clicks) >= 4
                    unique_binding_clicks = {(item.get("recording_document_id"), item["seq"]) for item in bindings if item["type"] == "click"}
                    assert len(observed_clicks) == len(unique_binding_clicks)
                    assert [item.seq for item in saved] == list(range(len(saved)))
                    for scope in final["finalize_metrics"]["finish_receipt"].get("scopes", []):
                        assert scope["pg_verified"]
                    scopes = final["finalize_metrics"]["finish_receipt"].get("scopes", [])
                    if scenario in ("nested-frame", "pause-frame"):
                        assert {len(scope["frame_path"]) for scope in scopes} >= {0, 1, 2}
                        assert any(item.type == "fill" and item.value == "跨来源嵌套最后输入" for item in saved)
                    if scenario == "popup":
                        assert len({scope["page_id"] for scope in scopes}) == 2
                        assert any(item.type == "fill" and item.value == "弹窗最后输入" for item in saved)
                    projection = await graph.get_recording_subgraph(session_id, project_id=project.id)
                    save("projection.json", projection)
                    knowledge, _ = await env.sources.query(project.id, PlanningQuery(kind="recording", source_id=session_id), 50)
                    save("planning-knowledge.json", [item.model_dump(mode="json") for item in knowledge])
                    assert knowledge and knowledge[0].payload["graph"]["status"] == "success"
                    alien = await env.projects.create(ProjectCreateRequest(project_key="alien-" + suffix, name="其他项目"))
                    with pytest.raises(ValueError, match="不属于当前项目"):
                        await env.sources.query(alien.id, PlanningQuery(kind="recording", source_id=session_id), 50)
                    async with asyncio.timeout(8):
                        while bridge.session_stats(session_id).get("closed") and not any("close command from backend" in line for line in (evidence / "electron.stdout").read_text(errors="replace").splitlines()):
                            await asyncio.sleep(.1)
                    if scenario == "bridge-loss":
                        assert bridge.detach(session_id)
                        before_terminal = (await store.get_session(session_id)).model_dump(mode="json")
                        before_events = [event.model_dump(mode="json") for event in await store.get_events(session_id)]
                        late = await client.post(f"/api/v1/recordings/{session_id}/events:batch",
                            json={"events": before_events})
                        after_terminal = (await store.get_session(session_id)).model_dump(mode="json")
                        after_events = [event.model_dump(mode="json") for event in await store.get_events(session_id)]
                        save("late-terminal.json", {"http_status": late.status_code, "response": late.json(),
                            "before_session": before_terminal, "after_session": after_terminal,
                            "before_events": before_events, "after_events": after_events})
                        assert late.status_code == 409, "bridge loss allowed late writes to completed recording"
                        assert before_terminal == after_terminal and before_events == after_events
                    save("verification.json", {"real_electron": True, "real_http": True, "pg_events": len(saved), "legitimate_clicks": 4,
                        "observed_clicks": len(observed_clicks), "additional_observed_clicks": len(observed_clicks) - 4,
                        "all_binding_clicks_preserved": True,
                        "duplicate_delivery_deduplicated": True, "paused_clicks_not_recorded": True, "project_scoped_knowledge_read": True,
                        "rustfs_screenshot_readback": True, "manual_deepseek": False, "ai_model_use": False,
                        "end_drain_verified": True, "full_recording_acceptance": False})
        finally:
            if session_id:
                current = await store.get_session(session_id)
                save("pg-session-final.json", current.model_dump(mode="json") if current else None)
                save("pg-events-final.json", [item.model_dump(mode="json") for item in await store.get_events(session_id)])
                save("bridge-final.json", bridge.session_stats(session_id))
            if attached:
                await attached.close()
            if playwright:
                await playwright.stop()
            if process and process.poll() is None:
                commands.with_name(f"command-{command_id + 1}.json").write_text(json.dumps({"id": command_id + 1, "action": "quit"}), encoding="utf-8")
                try:
                    await asyncio.to_thread(process.wait, timeout=12)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    await asyncio.to_thread(process.wait, timeout=10)
            await service.close()
            if session_id:
                save("pg-session-shutdown.json", (await store.get_session(session_id)).model_dump(mode="json"))
                for path in evidence.glob("screenshot-[0-9]*.json"):
                    ref = json.loads(path.read_text())["ref"]
                    assert f"/recordings/{session_id}/" in ref
                    await env.objects.delete_object_uri(ref)
            server.should_exit = True
            await serving
            listener.close()
            await asyncio.to_thread(graph._provider.execute_write, "MATCH (n {project_id: $project_id}) DETACH DELETE n", {"project_id": project.id})
            await graph.close()
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f'DROP TABLE "{table}"')
