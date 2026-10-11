"""Real Chromium recording quality projection and draft handoff."""

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

pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_QUALITY") != "1", reason="real Chromium/main/PG/MySQL/Redis")
async def test_actual_recording_quality_and_case_draft(tmp_path, request):
    evidence = Path(os.environ["RECORDING_QUALITY_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    html = (
        '<!doctype html><meta charset="utf-8"><title>质量诊断</title>'
        '<button id="save">保存</button><p id="count">0</p>'
        '<script>save.onclick=()=>count.textContent=String(Number(count.textContent)+1)</script>'
    )
    fixture = tmp_path / "quality.html"
    fixture.write_text(html, encoding="utf-8")
    (evidence / "fixture.html").write_text(html, encoding="utf-8")
    graph = None
    project_id = None
    recording_id = None
    def finalize_evidence():
        try:
            if graph and project_id:
                graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id": project_id})
        finally:
            if graph:
                graph.close()
            for path in tmp_path.iterdir():
                if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                    shutil.copyfile(path, evidence / path.name)
    request.addfinalizer(finalize_evidence)
    async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
        graph = MemgraphRuntimeProvider(settings)
        port = free_port()
        api = processes.start("recording-quality-api", "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=60) as client:
            await wait_api(client, api)
            project = await client.post("/projects", json={"project_key": "quality-" + uuid4().hex[:12], "name": "质量诊断项目"})
            project.raise_for_status()
            project_id = project.json()["id"]
            async with async_playwright() as playwright:
                cdp = free_port()
                browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp}"])
                try:
                    page = await browser.new_page()
                    created = await client.post("/recordings", json={
                        "project_id": project_id,
                        "name": "质量诊断录制",
                        "entry_url": fixture.resolve().as_uri(),
                        "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp}"},
                    })
                    created.raise_for_status()
                    recording_id = created.json()["id"]
                    async with asyncio.timeout(15):
                        while (await client.get(f"/recordings/{recording_id}")).json()["status"] != "ready":
                            await asyncio.sleep(0.1)
                    (await client.post(f"/recordings/{recording_id}/control", json={"action": "start"})).raise_for_status()
                    await page.goto(fixture.resolve().as_uri())
                    await page.locator("#save").click()
                    await page.locator("#save").click()
                    await page.wait_for_timeout(800)
                    assert await page.locator("#count").inner_text() == "2"
                    stopped = await client.post(f"/recordings/{recording_id}/control", json={"action": "stop"})
                    stopped.raise_for_status()
                    detail = stopped.json()
                finally:
                    await browser.close()
            quality = detail["finalize_metrics"]["recording_quality"]
            assert quality["protocol"] == "recording-quality-v1"
            assert quality["omission"]["status"] == "verified"
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording_id,))
                rows = [dict(row) for row in cur.fetchall()]
            (evidence / "debug-events.json").write_text(json.dumps({"quality": quality, "events": rows}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            assert rows
            assert quality["duplicate_candidates"]
            assert quality["removed_event_sequences"] == []
            click_seqs = [row["seq"] for row in rows if row["type"] == "click"]
            assert len(click_seqs) >= 2
            assert all(seq in quality["cleaned_event_sequences"] for seq in click_seqs)
            draft = await client.post(f"/recordings/{recording_id}/case-draft")
            draft.raise_for_status()
            payload = draft.json()
            (evidence / "recording.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            (evidence / "case-draft.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            assert payload["version"]["test_data"]["recording_quality"] == quality
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seq, type, payload FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq", (recording_id,))
                after = [dict(row) for row in cur.fetchall()]
            assert [(row["seq"], row["type"], row["payload"]) for row in after] == [(row["seq"], row["type"], row["payload"]) for row in rows]
            (evidence / "facts.json").write_text(json.dumps({
                "project_id": project_id,
                "recording_id": recording_id,
                "quality": quality,
                "raw_event_count": len(rows),
                "click_sequences": click_seqs,
                "original_events_unchanged": True,
            }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

