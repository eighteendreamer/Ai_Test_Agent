"""Real production recording of open Shadow DOM action responses."""
import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import async_playwright

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


HTML = '''<!doctype html><meta charset="utf-8"><title>Shadow response fixture</title>
<div id="host"></div><div id="late"></div><button id="create">Create root</button>
<iframe id="frame" srcdoc="&lt;div id='frame-host'&gt;&lt;/div&gt;&lt;script&gt;
const r=document.querySelector('#frame-host').attachShadow({mode:'open'});
r.innerHTML='&lt;button id=frame-action&gt;Frame action&lt;/button&gt;&lt;p id=frame-result&gt;&lt;/p&gt;';
r.querySelector('#frame-action').onclick=()=>r.querySelector('#frame-result').textContent='frame response';
&lt;/script&gt;"></iframe>
<script>
const r=document.querySelector('#host').attachShadow({mode:'open'});
r.innerHTML='<section id=nest></section><section id=password-zone><div id=private></div></section>';
const n=r.querySelector('#nest').attachShadow({mode:'open'});
n.innerHTML='<button id=action>Nested action</button><p id=result></p>';
n.querySelector('#action').onclick=()=>{
  n.querySelector('#result').textContent='nested response';
  const privateRoot=r.querySelector('#private').shadowRoot || r.querySelector('#private').attachShadow({mode:'open'});
  privateRoot.innerHTML='<p>fixture-sensitive-marker</p>';
};
document.querySelector('#create').onclick=()=>{
 const late=document.querySelector('#late').attachShadow({mode:'open'});
 late.innerHTML='<p id=late-result>late root response</p>';
};
</script>'''


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_SHADOW") != "1", reason="actual Chromium/API/MySQL/PG/Redis/Memgraph")
async def test_open_shadow_responses_and_sensitive_ancestors(tmp_path, request):
    evidence = Path(os.environ["RECORDING_SHADOW_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    fixture = tmp_path / "shadow.html"
    fixture.write_text(HTML, encoding="utf-8")
    (evidence / "fixture.html").write_text(HTML, encoding="utf-8")
    graph, project_id = None, None

    def finalize():
        try:
            if graph and project_id:
                graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id": project_id})
        finally:
            if graph:
                graph.close()
            for path in tmp_path.iterdir():
                if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                    shutil.copyfile(path, evidence / path.name)
    request.addfinalizer(finalize)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
        graph = MemgraphRuntimeProvider(settings)
        port = free_port()
        api = processes.start("shadow-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=60) as client:
            await wait_api(client, api)
            response = await client.post("/projects", json={"project_key": "shadow-" + uuid4().hex[:12], "name": "Shadow response acceptance"})
            response.raise_for_status()
            project_id = response.json()["id"]
            async with async_playwright() as playwright:
                cdp = free_port()
                browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp}"])
                try:
                    page = await browser.new_page()
                    response = await client.post("/recordings", json={"project_id": project_id,
                        "entry_url": fixture.resolve().as_uri(), "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp}"}})
                    response.raise_for_status()
                    recording_id = response.json()["id"]
                    async with asyncio.timeout(15):
                        while (await client.get(f"/recordings/{recording_id}")).json()["status"] != "ready":
                            await asyncio.sleep(.1)
                    (await client.post(f"/recordings/{recording_id}/control", json={"action": "start"})).raise_for_status()
                    await page.goto(fixture.resolve().as_uri())
                    await page.evaluate("() => window.__qaRecorderReady")
                    await page.wait_for_timeout(800)
                    save("before-dom.json", {"button": await page.locator("#action").inner_text(), "frame_button": await page.frame_locator("#frame").locator("#frame-action").inner_text()})
                    await page.locator("#action").click()
                    await page.wait_for_timeout(800)
                    await page.locator("#create").click()
                    await page.wait_for_timeout(800)
                    await page.frame_locator("#frame").locator("#frame-action").click()
                    await page.wait_for_timeout(800)
                    save("actual-dom.json", {"nested": await page.locator("#result").inner_text(),
                        "late": await page.locator("#late-result").inner_text(),
                        "frame": await page.frame_locator("#frame").locator("#frame-result").inner_text()})
                    response = await client.post(f"/recordings/{recording_id}/control", json={"action": "stop"})
                    response.raise_for_status()
                    detail = response.json()
                    save("recording.json", detail)
                finally:
                    await browser.close()
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT seq,type,payload FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording_id,))
                rows = [dict(row) for row in cursor.fetchall()]
            save("pg-events.json", rows)
            actions = [row for row in rows if row["type"] == "click"]
            responses = [row for row in rows if row["type"] == "page_response"]
            text = json.dumps(responses, ensure_ascii=False)
            facts = {"recorded_action_ids": [row["payload"]["target"]["locators"]["id"] for row in actions],
                "nested_observed": "nested response" in text, "late_observed": "late root response" in text,
                "frame_observed": "frame response" in text, "sensitive_leaked": "fixture-sensitive-marker" in text,
                "model_calls": 0}
            save("facts.json", facts)
            assert set(facts["recorded_action_ids"]) == {"action", "create", "frame-action"}
            assert facts["nested_observed"] and facts["late_observed"] and facts["frame_observed"], "real shadow response omitted despite recorded action"
            assert not facts["sensitive_leaked"], "sensitive shadow ancestor leaked in response evidence"
            for row in responses:
                event = row["payload"]
                observation = event["page_effect"]["response"]
                assert observation["scope"] == "document_light_dom"
                assert observation["shadow_dom"] == "open_roots"
                assert observation["closed_shadow_dom"] == "unobserved"
                related = [action for action in actions if action["payload"]["page"]["recording_scope"]["document_id"] == event["page"]["recording_scope"]["document_id"]
                    and action["payload"]["page"]["recording_scope"]["local_seq"] in observation["action_local_seqs"]]
                assert related and all(action["seq"] < row["seq"] for action in related)
            response = await client.post(f"/recordings/{recording_id}/case-draft")
            response.raise_for_status()
            save("case-draft.json", response.json())
            assert all(value in json.dumps(response.json(), ensure_ascii=False) for value in ("nested response", "late root response", "frame response"))
