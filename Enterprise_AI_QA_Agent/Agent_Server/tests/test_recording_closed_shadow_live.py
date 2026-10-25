"""Real recording boundary when the page genuinely contains a closed shadow root."""
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


HTML = '''<!doctype html><meta charset="utf-8"><title>Closed shadow boundary fixture</title>
<button id="light">Light action</button><p id="light-result"></p>
<div id="closed-host" style="width:120px;height:40px"></div><p id="closed-echo"></p>
<script>
const closedRoot=document.querySelector('#closed-host').attachShadow({mode:'closed'});
closedRoot.innerHTML='<button id="closed-action" style="width:120px;height:40px">Closed</button><p id="closed-inner">closed-private-marker</p>';
closedRoot.querySelector('#closed-action').onclick=()=>{
  document.querySelector('#closed-echo').textContent='closed button clicked';
};
document.querySelector('#light').onclick=()=>{
  document.querySelector('#light-result').textContent='light response';
};
</script>'''


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_CLOSED_SHADOW") != "1",
    reason="actual Chromium/API/MySQL/PG/Redis/Memgraph with a real closed shadow root")
async def test_closed_shadow_root_records_honestly_without_fabrication(tmp_path, request):
    evidence = Path(os.environ["RECORDING_CLOSED_SHADOW_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    fixture = tmp_path / "closed-shadow.html"
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
        api = processes.start("closed-shadow-api", "-m", "uvicorn", "src.main:app",
            "--host", "127.0.0.1", "--port", str(port))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=60) as client:
            await wait_api(client, api)
            response = await client.post("/projects", json={"project_key": "closed-shadow-" + uuid4().hex[:12],
                "name": "Closed shadow boundary acceptance"})
            response.raise_for_status()
            project_id = response.json()["id"]
            async with async_playwright() as playwright:
                cdp = free_port()
                browser = await playwright.chromium.launch(headless=True,
                    args=[f"--remote-debugging-port={cdp}"])
                try:
                    page = await browser.new_page()
                    response = await client.post("/recordings", json={"project_id": project_id,
                        "entry_url": fixture.resolve().as_uri(),
                        "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp}"}})
                    response.raise_for_status()
                    recording_id = response.json()["id"]
                    async with asyncio.timeout(15):
                        while (await client.get(f"/recordings/{recording_id}")).json()["status"] != "ready":
                            await asyncio.sleep(.1)
                    (await client.post(f"/recordings/{recording_id}/control", json={"action": "start"})).raise_for_status()
                    await page.goto(fixture.resolve().as_uri())
                    await page.evaluate("() => window.__qaRecorderReady")
                    await page.wait_for_timeout(800)
                    # Control action in the light DOM.
                    await page.locator("#light").click()
                    await page.wait_for_timeout(800)
                    # The closed-root button cannot be located through selectors;
                    # click it physically by coordinates inside its host box.
                    host = await page.locator("#closed-host").bounding_box()
                    assert host, "closed host element not rendered"
                    save("closed-host-box.json", host)
                    await page.mouse.click(host["x"] + host["width"] / 2, host["y"] + host["height"] / 2)
                    await page.wait_for_timeout(800)
                    # Prove the closed button physically ran; its marker lives in light DOM.
                    save("actual-dom.json", {
                        "light": await page.locator("#light-result").inner_text(),
                        "closed_echo": await page.locator("#closed-echo").inner_text()})
                    assert await page.locator("#closed-echo").inner_text() == "closed button clicked"
                    response = await client.post(f"/recordings/{recording_id}/control", json={"action": "stop"})
                    response.raise_for_status()
                    save("recording.json", response.json())
                finally:
                    await browser.close()
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT seq,type,payload FROM {settings.database.postgres_recording_event_table} "
                    "WHERE recording_id=%s ORDER BY seq", (recording_id,))
                rows = [dict(row) for row in cursor.fetchall()]
            save("pg-events.json", rows)
            actions = [row for row in rows if row["type"] == "click"]
            responses = [row for row in rows if row["type"] == "page_response"]
            corpus = json.dumps(rows, ensure_ascii=False)

            facts = {
                "light_action_recorded": any(row["payload"].get("target", {}).get("locators", {}).get("id") == "light"
                    for row in actions),
                "light_response_recorded": "light response" in corpus,
                "closed_inner_fabricated": "closed-private-marker" in corpus,
                "closed_action_located_as_inner": "#closed-action" in corpus or '"closed-action"' in corpus,
                "response_count": len(responses),
                "action_count": len(actions),
            }
            save("facts.json", facts)

            # Control path must keep working alongside the closed root.
            assert facts["light_action_recorded"], actions
            assert facts["light_response_recorded"], responses
            # Honesty boundaries: closed-root internals must not be fabricated into
            # evidence, and the closed inner button must never be claimed as a
            # located target (the recorder either drops the unlocatable click or
            # attributes it to the host — both honest outcomes).
            assert not facts["closed_inner_fabricated"], "closed root content leaked into evidence"
            assert not facts["closed_action_located_as_inner"], "closed inner locator fabricated"
            for row in responses:
                observation = row["payload"]["page_effect"]["response"]
                assert observation["closed_shadow_dom"] == "unobserved", observation
