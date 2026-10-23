"""Actual recording, drifted replay, durable report, restart and built product UI."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Lock, Thread
from uuid import uuid4

import httpx
from playwright.async_api import async_playwright, expect
import pytest

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from test_document_full_product import product_browser
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_RECORDING_REPLAY_PRODUCT") != "1",
    reason="requires actual Chromium/main/MySQL/PostgreSQL/RustFS/Memgraph and an existing Redis",
)]


class MutableTarget:
    def __init__(self) -> None:
        self._lock = Lock()
        self.drifted = False
        self.loads = 0
        self.effects = 0

    def reset_counts(self) -> None:
        with self._lock:
            self.loads = 0
            self.effects = 0

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"loads": self.loads, "effects": self.effects}

    def page(self) -> bytes:
        with self._lock:
            self.loads += 1
            drifted = self.drifted
        button = (
            '<button id="decoy" type="button">取消操作</button>' if drifted else
            '<button id="save-target" type="button" '
            'onclick="fetch(\'/effect\',{method:\'POST\'}).then(()=>document.getElementById(\'status\').textContent=\'已保存\')">执行保存</button>'
        )
        return (
            '<!doctype html><meta charset="utf-8"><title>回放漂移验收</title>'
            '<style>body{font-family:sans-serif;padding:40px}input,button{display:block;margin:16px;padding:8px}</style>'
            '<label>业务编号<input id="business-key" placeholder="业务编号"></label>'
            f'{button}<p id="status">待执行</p>'
        ).encode("utf-8")

    def effect(self) -> bytes:
        with self._lock:
            self.effects += 1
        return b'{"ok":true}'


@asynccontextmanager
async def controlled_target():
    state = MutableTarget()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.reply(200, state.page(), "text/html; charset=utf-8")
            else:
                self.reply(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/effect":
                self.reply(200, state.effect(), "application/json")
            else:
                self.reply(404, b"not found", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_actual_recording_replay_failure_report_survives_restart_and_renders(tmp_path):
    evidence = Path(os.getenv("RECORDING_REPLAY_PRODUCT_EVIDENCE", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=False)
    shared_redis = os.getenv("QA_REPLAY_SHARED_REDIS_URL", "redis://127.0.0.1:6379/15")
    graph = None
    project_ids: list[str] = []

    def save(name: str, value) -> None:
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    try:
        async with controlled_target() as (target, target_url):
            async with isolated_runtime(tmp_path, redis_url=shared_redis) as (settings, processes, _, objects, owned):
                graph = MemgraphRuntimeProvider(settings)
                api_port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
                api = processes.start("recording-replay-api", *args)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=90) as client:
                    await wait_api(client, api)
                    for name in ("回放失败定位项目", "隔离项目"):
                        response = await client.post("/projects", json={
                            "project_key": "replay-" + uuid4().hex[:12], "name": name,
                        })
                        response.raise_for_status()
                        project_ids.append(response.json()["id"])
                    project_id = project_ids[0]

                    async with async_playwright() as playwright:
                        cdp_port = free_port()
                        browser = await playwright.chromium.launch(
                            headless=True, args=[f"--remote-debugging-port={cdp_port}"])
                        try:
                            page = await browser.new_page()
                            created = await client.post("/recordings", json={
                                "project_id": project_id,
                                "name": "真实漂移回放",
                                "entry_url": target_url,
                                "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp_port}"},
                            })
                            created.raise_for_status()
                            recording_id = created.json()["id"]
                            async with asyncio.timeout(20):
                                while (await client.get(f"/recordings/{recording_id}")).json()["status"] != "ready":
                                    await asyncio.sleep(.1)
                            (await client.post(f"/recordings/{recording_id}/control", json={"action": "start"})).raise_for_status()
                            await page.goto(target_url)
                            await page.locator("#business-key").fill("CASE-2026-001")
                            await page.locator("#save-target").click()
                            await expect(page.locator("#status")).to_have_text("已保存")
                            await page.wait_for_timeout(800)
                            stopped = await client.post(f"/recordings/{recording_id}/control", json={"action": "stop"})
                            stopped.raise_for_status()
                            recording = stopped.json()
                            assert recording["status"] == "completed"
                        finally:
                            await browser.close()

                    detail = (await client.get(f"/recordings/{recording_id}")).json()
                    save("recording.json", detail)
                    for event in detail["events"]:
                        if event.get("screenshot_ref"):
                            owned.append(event["screenshot_ref"])
                    assert any(event["type"] == "fill" for event in detail["events"])
                    assert any(event["type"] == "click" and
                               (event.get("target") or {}).get("locators", {}).get("id") == "save-target"
                               for event in detail["events"])

                    target.drifted = True
                    target.reset_counts()
                    request_id = str(uuid4())
                    first_response = await client.post(f"/recordings/{recording_id}/replays", json={
                        "project_id": project_id, "request_id": request_id,
                    })
                    first_response.raise_for_status()
                    first = first_response.json()
                    save("first-replay.json", first)
                    failed = [step for step in first["report"]["steps"] if step["status"] == "failed"]
                    assert first["status"] == "completed" and failed
                    assert any(step["action"] == "click" and "recorded target" in step["error"]
                               for step in failed), failed
                    assert target.counts()["effects"] == 0
                    counts_after_first = target.counts()

                    duplicate = await client.post(f"/recordings/{recording_id}/replays", json={
                        "project_id": project_id, "request_id": request_id,
                    })
                    duplicate.raise_for_status()
                    assert duplicate.json() == first
                    assert target.counts() == counts_after_first
                    assert (await client.post(f"/recordings/{recording_id}/replays", json={
                        "project_id": project_ids[1], "request_id": str(uuid4()),
                    })).status_code == 404

                    table = f"{settings.database.postgres_recording_table}_replay"
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} WHERE recording_id=%s ORDER BY created_at", (recording_id,))
                        rows = [dict(row) for row in cur.fetchall()]
                    assert len(rows) == 1 and rows[0]["id"] == first["id"]
                    assert rows[0]["report"]["summary"] == first["report"]["summary"]
                    save("postgres-replay-before-restart.json", rows)

                    await processes.stop(api)
                    api = processes.start("recording-replay-api-restarted", *args)
                    await wait_api(client, api)
                    restored = await client.get(f"/recordings/{recording_id}/replays",
                                                params={"project_id": project_id})
                    restored.raise_for_status()
                    assert restored.json()[0] == first

                    before_ui = target.counts()
                    async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                        await expect(page.locator(".left-nav")).to_be_visible()
                        await page.goto(page.url.split("/home")[0] + f"/projects/{project_id}")
                        await page.get_by_role("link", name="录制场景", exact=True).click()
                        await expect(page.get_by_role("heading", name="录制场景 · 回放失败定位项目")).to_be_visible()
                        await expect(page.locator(".replay-report")).to_contain_text("回放诊断 · completed")
                        await expect(page.locator(".replay-report")).to_contain_text("failed")
                        async with page.expect_response(lambda response: response.request.method == "POST" and
                                                        response.url.endswith(f"/recordings/{recording_id}/replays"),
                                                        timeout=30000) as replayed:
                            await page.get_by_role("button", name="运行回放诊断", exact=True).click()
                        response = await replayed.value
                        assert response.status == 201, await response.text()
                        await expect(page.get_by_role("status")).to_contain_text("失败")
                        await expect(page.locator(".replay-report tbody tr").filter(has_text="click")).to_contain_text("failed")
                        await page.screenshot(path=str(evidence / "replay-failure-report.png"), full_page=True)
                        assert not errors, errors
                        unexpected = [item for item in network if "/api/v1/" in item["url"] and item["status"] >= 400]
                        assert not unexpected, unexpected
                    assert target.counts()["loads"] > before_ui["loads"]
                    assert target.counts()["effects"] == 0

                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} WHERE recording_id=%s ORDER BY created_at", (recording_id,))
                        final_rows = [dict(row) for row in cur.fetchall()]
                    assert len(final_rows) == 2
                    save("postgres-replay-final.json", final_rows)
                    save("facts.json", {
                        "recording_id": recording_id,
                        "first_replay_id": first["id"],
                        "failed_steps": failed,
                        "duplicate_request_counts_unchanged": True,
                        "duplicate_request_identity": True,
                        "restart_readback": True,
                        "built_ui_rendered_failed_step": True,
                        "cross_project_status": 404,
                        "postgres_rows": len(final_rows),
                        "target_effects_after_drift": target.counts()["effects"],
                        "redis_url": shared_redis.rsplit("/", 1)[-1],
                        "created_test_container": False,
                    })
    finally:
        if graph:
            for project_id in project_ids:
                graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id": project_id})
            graph.close()
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)
