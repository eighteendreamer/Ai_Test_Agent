"""New browser capture through independent main, PG, graph and built App."""
import asyncio
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
from playwright.async_api import async_playwright, expect
import pytest

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.session import CreateSessionRequest
from test_document_full_product import product_browser
from test_execution_worker_cli import free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_KNOWLEDGE_PRODUCT") != "1",
    reason="real Chromium capture, independent main, stores/Memgraph and built App; no model")]


@pytest.mark.parametrize("bind_legacy_scope", [False, True])
async def test_new_recording_steps_visible_in_project_knowledge(tmp_path, bind_legacy_scope):
    evidence = Path(os.environ["RECORDING_KNOWLEDGE_PRODUCT_EVIDENCE"]) / ("bound" if bind_legacy_scope else "formal-id")
    evidence.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    html = '<!doctype html><meta charset="utf-8"><title>实际录制知识</title><input id="value"><button id="repeat">增加计数</button><p id="count">0</p><script>document.querySelector("#repeat").onclick=()=>{document.querySelector("#count").textContent=String(Number(document.querySelector("#count").textContent)+1)};</script>'
    fixture = tmp_path / "recorded-knowledge.html"
    fixture.write_text(html, encoding="utf-8")
    (evidence / "fixture.html").write_text(html, encoding="utf-8")
    graph = None
    project_ids = []
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
            graph = MemgraphRuntimeProvider(settings)
            api_port = free_port()
            api = processes.start("recording-knowledge-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=60) as client:
                    await wait_api(client, api)
                    for name in ("录制知识项目", "隔离项目"):
                        payload = {"project_key": "knowledge-" + uuid4().hex[:12], "name": name}
                        if bind_legacy_scope:
                            payload["graph_scope_key"] = payload["project_key"]
                        response = await client.post("/projects", json=payload)
                        response.raise_for_status()
                        project_ids.append(response.json()["id"])
                    project_id = project_ids[0]
                    assert graph.execute("MATCH (n {project_id:$project_id}) RETURN count(n) AS total", {"project_id": project_id})[0]["total"] == 0
                    response = await client.post("/sessions", json=CreateSessionRequest(project_id=project_id, mode_key="ui_automation").model_dump(mode="json"))
                    response.raise_for_status()
                    session = response.json()
                    async with async_playwright() as playwright:
                        cdp_port = free_port()
                        browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp_port}"])
                        try:
                            page = await browser.new_page()
                            response = await client.post("/recordings", json={"project_id": project_id, "session_id": session["id"],
                                "name": "两次真实点击", "entry_url": fixture.resolve().as_uri(),
                                "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp_port}"}})
                            response.raise_for_status()
                            recording = response.json()
                            async with asyncio.timeout(15):
                                while True:
                                    response = await client.get(f"/recordings/{recording['id']}")
                                    response.raise_for_status()
                                    if response.json()["status"] == "ready": break
                                    await asyncio.sleep(.1)
                            response = await client.post(f"/recordings/{recording['id']}/control", json={"action": "start"})
                            response.raise_for_status()
                            value = "完整知识输入😀" * 40
                            await page.locator("#value").fill(value)
                            await page.wait_for_timeout(600)
                            await page.locator("#repeat").click()
                            await page.locator("#repeat").click()
                            assert await page.locator("#count").inner_text() == "2"
                            response = await client.post(f"/recordings/{recording['id']}/control", json={"action": "stop"})
                            response.raise_for_status()
                            assert response.json()["status"] == "completed"
                        finally:
                            await browser.close()
                    response = await client.get(f"/recordings/{recording['id']}")
                    response.raise_for_status()
                    detail = response.json()
                    save("pg-recording-detail.json", detail)
                    assert len([event for event in detail["events"] if event["type"] == "click"]) == 2
                    assert any(event["type"] == "fill" and event["value"] == value for event in detail["events"])
                    response = await client.get(f"/recordings/{recording['id']}/graph")
                    response.raise_for_status()
                    recorded_graph = response.json()
                    save("recording-graph.json", recorded_graph)
                    response = await client.get("/knowledge/graph", params={"project_id": project_id})
                    save("knowledge-http.json", {"status": response.status_code, "payload": response.json()})
                    response.raise_for_status()
                    knowledge = response.json()
                    save("knowledge-graph.json", knowledge)
                    response = await client.get("/knowledge/projects")
                    response.raise_for_status()
                    summaries = response.json()
                    save("knowledge-projects.json", summaries)
                    summary = next(item for item in summaries if item["project_id"] == project_id)
                    assert summary["project_name"] == "录制知识项目"
                    assert summary["recording_count"] == 1 and summary["action_count"] == len(detail["events"])
                    assert not any(item["project_id"] == project_ids[1] for item in summaries)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording["id"],))
                        pg_rows = [dict(row) for row in cur.fetchall()]
                        save("pg-event-rows.json", pg_rows)
                    ids = {node["id"] for node in knowledge["nodes"]}
                    missing = [{"id": node["id"], "kind": node["kind"]} for node in recorded_graph["nodes"]
                        if node["kind"] in {"recording", "action"} and node["id"] not in ids]
                    dangling = [edge for edge in knowledge["edges"] if edge["source"] not in ids or edge["target"] not in ids]
                    save("projection-problems.json", {"missing_recorded_nodes": missing, "dangling_edges": dangling})
                    assert not missing and not dangling, "project knowledge omits recorded nodes but returns their relations"
                    async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                        await expect(page.locator(".left-nav")).to_be_visible()
                        await page.locator('.left-nav a[href="/knowledge"]').click()
                        await page.locator(".knowledge-select").select_option(project_id)
                        await page.locator(".knowledge-kind-tabs button").filter(has_text="录制").click()
                        await expect(page.locator(".node-list .node-row").filter(has_text="两次真实点击")).to_be_visible()
                        await page.locator(".knowledge-kind-tabs button").filter(has_text="动作").click()
                        assert await page.locator(".node-list .node-row").count() == len(detail["events"]) + 1
                        fill = next(event for event in detail["events"] if event["type"] == "fill")
                        await page.locator(".node-list .node-row").filter(has_text=f"fill #{fill['seq']}").click()
                        await expect(page.locator(".detail-drawer")).to_be_visible()
                        await expect(page.locator(".property-item").filter(has=page.locator(".property-key", has_text="value"))
                            .locator(".property-value")).to_have_attribute("title", value)
                        await expect(page.locator(".detail-drawer")).to_contain_text("两次真实点击")
                        await page.screenshot(path=str(evidence / "recorded-actions.png"), full_page=True)
                        response = await client.get("/knowledge/graph", params={"project_id": project_ids[1]})
                        assert response.status_code == 404
                        before_restart_failures = [item for item in network if "/api/v1/" in item["url"] and item["status"] >= 400]
                        assert not before_restart_failures, before_restart_failures
                        restart_start = len(network)
                        await processes.stop(api)
                        restarted = processes.start("recording-knowledge-api-restarted", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                        await wait_api(client, restarted)
                        response = await client.get("/knowledge/graph", params={"project_id": project_id})
                        response.raise_for_status()
                        assert response.json() == knowledge
                        # Allow the existing polling request already in flight during the intentional outage to settle.
                        await page.wait_for_timeout(800)
                        restart_end = len(network)
                        await page.reload()
                        await expect(page.locator(".knowledge-select")).to_be_visible()
                        await page.locator(".knowledge-select").select_option(project_id)
                        await page.locator(".knowledge-kind-tabs button").filter(has_text="录制").click()
                        await expect(page.locator(".node-list .node-row").filter(has_text="两次真实点击")).to_be_visible()
                        response = await client.get(f"/recordings/{recording['id']}")
                        response.raise_for_status()
                        assert response.json()["events"] == detail["events"]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT * FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording["id"],))
                            after_pg_rows = [dict(row) for row in cur.fetchall()]
                        save("pg-event-rows-after-restart.json", after_pg_rows)
                        assert after_pg_rows == pg_rows
                        planned_failures = [item for item in network[restart_start:restart_end] if "/api/v1/" in item["url"] and item["status"] >= 400]
                        unplanned_failures = [item for item in network[:restart_start] + network[restart_end:] if "/api/v1/" in item["url"] and item["status"] >= 400]
                        save("facts.json", {"scope": "new actual CDP capture and project knowledge built App/main; no AI, no scene/alias editing",
                            "events": len(detail["events"]), "missing_recorded_nodes": missing, "dangling_edges": dangling,
                            "restart_equal": True, "browser_restored": True, "pg_events_unchanged": True,
                            "other_project_status": 404, "page_errors": errors, "network": network,
                            "planned_restart_failures": planned_failures, "unplanned_api_failures": unplanned_failures})
                        assert not unplanned_failures, unplanned_failures
                        assert not errors, errors
            finally:
                if graph:
                    cleanup = []
                    for identity in project_ids:
                        before = graph.execute("MATCH (n {project_id:$project_id}) RETURN count(n) AS total", {"project_id": identity})[0]["total"]
                        graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id": identity})
                        after = graph.execute("MATCH (n {project_id:$project_id}) RETURN count(n) AS total", {"project_id": identity})[0]["total"]
                        cleanup.append({"project_id": identity, "before": before, "after": after})
                    save("owned-graph-cleanup.json", cleanup)
                    await asyncio.to_thread(graph.close)
                    assert all(item["after"] == 0 for item in cleanup), cleanup
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)
