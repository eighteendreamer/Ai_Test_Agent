"""Real default model, physical recording, scene knowledge and formal UI result."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread, Lock
from uuid import uuid4

import httpx
from playwright.async_api import async_playwright
from pydantic import SecretStr
import pytest

from test_default_receipt_process_recovery_live import _kill_owned_process_tree
from test_execution_worker_cli import isolated_runtime, free_port, child_environment, wait_api
from test_recording_agent_production import verify_owned_approval
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import model_invocation_parameters
from src.schemas.session import SessionStatus, SessionMode, RuntimeMode
from src.schemas.settings import ModelConfigUpdateRequest


pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_UI_SCENE") != "1",
    reason="real default provider, owned HTTP/Chromium and isolated MySQL/PG/Redis/RustFS")


class CountPage:
    def __init__(self):
        self.events, self.counts, self.lock = [], {}, Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                if self.path != "/":
                    self.send_error(404)
                    return
                page_id = uuid4().hex
                with owner.lock:
                    owner.counts[page_id] = 0
                    owner.events.append({"method": "GET", "page_id": page_id})
                body = ('''<!doctype html><html lang="zh"><meta charset="utf-8"><title>场景计数验收</title>
<main><button id="increment">增加一次</button><output id="count">0</output>
<output id="page-id">PAGE_ID</output></main><script>
document.querySelector('#increment').onclick=async()=>{
 const r=await fetch('/increment/PAGE_ID',{method:'POST'});const d=await r.json();
 document.querySelector('#count').textContent=String(d.count);
};</script></html>'''.replace("PAGE_ID", page_id)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                page_id = self.path.removeprefix("/increment/")
                with owner.lock:
                    if page_id not in owner.counts:
                        self.send_error(404)
                        return
                    owner.counts[page_id] += 1
                    body = {"page_id": page_id, "count": owner.counts[page_id]}
                    owner.events.append({"method": "POST", **body})
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", ["1", "2"], ids=["positive", "negative"])
async def test_default_ui_reads_fixed_scene_and_verifies_actual_page(tmp_path, expected):
    output = Path(os.environ["QA_DEFAULT_UI_SCENE_DIR"]) / ("positive" if expected == "1" else "negative")
    output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    target = CountPage()
    origin = target.start()
    original = MySQLModelConfigStore(get_settings()).get_default_active()
    save("model-parameters.json", model_invocation_parameters(original))
    api = cleanup = recording_id = project_id = None
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, queue, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
            models = MySQLModelConfigStore(settings); models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(model_name=original.model_id,
                provider=original.provider, transport=original.transport, request_profile=original.request_profile,
                base_url=original.api_base_url, api_key=original.api_key, auth_type=original.auth_type,
                oauth_provider=original.oauth_provider, oauth_refresh_token=original.oauth_refresh_token,
                applications=original.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=original.capability_overrides, context_window=original.context_window,
                max_output_tokens=original.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == model_invocation_parameters(original)
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(output / "physical")
            port = free_port()
            api = processes.start("ui-api", "-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(port))
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=300,
                    headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                    await wait_api(client, api)
                    cleanup = processes.start("ui-resource-cleanup", "-m", "src.cli.run_resource_cleanup_worker",
                        "--worker-id", "ui-cleanup")
                    async def post(path, data=None, status=200):
                        r = await client.post(path, json=data)
                        assert r.status_code == status, (path, r.status_code, r.text)
                        return r.json()
                    async def get(path):
                        r = await client.get(path); assert r.status_code == 200, (path, r.text)
                        return r.json()
                    project = await post("/projects", {"project_key": "default-ui-" + uuid4().hex,
                        "name": "默认UI固定场景验收", "base_url": origin}, 201)
                    project_id = project["id"]; root = "/projects/" + project_id
                    async with async_playwright() as playwright:
                        cdp = free_port()
                        browser = await playwright.chromium.launch(headless=True, args=[f"--remote-debugging-port={cdp}"])
                        operator = None
                        try:
                            recording = await post("/recordings", {"project_id": project_id, "name": "物理增加一次",
                                "entry_url": origin, "driver": {"kind": "cdp-attach", "endpoint": f"http://127.0.0.1:{cdp}"}}, 201)
                            recording_id = recording["id"]
                            async with asyncio.timeout(30):
                                while (await get("/recordings/" + recording_id))["status"] != "ready":
                                    await asyncio.sleep(.1)
                            await post(f"/recordings/{recording_id}/control", {"action": "start"})
                            operator = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp}")
                            page = next(p for context in operator.contexts for p in context.pages if p.url.rstrip("/") == origin)
                            recording_page = await page.locator("#page-id").inner_text()
                            await page.get_by_role("button", name="增加一次").click()
                            await page.locator("#count").filter(has_text="1").wait_for()
                            await asyncio.sleep(.4)
                            await post(f"/recordings/{recording_id}/control", {"action": "stop"})
                            recorded = await get("/recordings/" + recording_id)
                            save("recording.json", recorded)
                            assert recorded["status"] == "completed"
                        finally:
                            if operator: await operator.close()
                            await browser.close()
                    scene_root = f"/recordings/scenes/projects/{project_id}"
                    scene = await post(scene_root, {"request_id": str(uuid4()), "recording_id": recording_id}, 201)
                    content = scene["version"]["content"]
                    click_seq = next(s["data"]["seq"] for s in scene["version"]["source"]["original_draft"]["steps"]
                        if s["data"]["recorder_type"] == "click")
                    content.update(name="增加一次场景", status="organized",
                        aliases=[{"name": "计数按钮", "source_seq": click_seq, "description": "增加一次"}],
                        assertions=[{"kind": "page", "target": "#count", "operator": "equals", "expected": expected}])
                    next(s for s in content["steps"] if s["source_seq"] == click_seq).update(alias="计数按钮", expected="计数增加")
                    scene = await post(scene_root + "/" + scene["scene_id"] + "/versions", {
                        "request_id": str(uuid4()), "parent_version_id": scene["version"]["id"],
                        "change_reason": "整理业务别名与预期，保留原录制", "content": content}, 201)
                    save("fixed-scene.json", scene)
                    scene_read = {"query": "读取固定增加一次场景", "operation": "scene_read", "scene_id": scene["scene_id"],
                        "version_id": scene["version"]["id"], "content_hash": scene["version"]["content_hash"]}
                    business = {"objective": "实际点击计数按钮一次并验证当前计数", "scene_read": scene_read,
                        "target_url": origin, "initial_count": "0", "expected_count": expected,
                        "steps": [{"action": "先读取固定场景的别名和真实定位，再打开新的项目页面（初始计数必须0），真实点击增加一次并读取当前页面计数",
                            "expected": expected}], "metadata": {"false": False, "zero": 0, "nullable": None}}
                    case = await post(root + "/test-cases", {"case_key": "count-one", "title": "增加一次真实验证",
                        "mode_key": "ui_automation", "case_type": "json", "content_json": business,
                        "execution_notes": "先调用JSON中scene_read指定的knowledge-rag读取固定场景。录制只作操作知识，不能当本次实际值。"
                            "然后使用现有browser-control的command/args打开项目页面并真实click一次，最后只读eval取得#count和#page-id。"
                            "command调用共享会话；不要在读结果时重新打开页面，不得赋值DOM或改点击次数去满足预期。实际值与expected_count比较，"
                            "初始计数固定0，不能增加准备动作改变该条件；不符则正式failed，遵守原回执协议与权限。",
                        "source_refs": [{"source_type": "ui_recording_scene", "source_id": scene["scene_id"],
                            "version": scene["version"]["id"], "metadata": {"content_hash": scene["version"]["content_hash"]}}],
                        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"},
                        "generation_metadata": {"template": {"writing_guidance": "DO-NOT-INJECT-TEMPLATE"}}}, 201)
                    case_id = case["case"]["id"]
                    await post(f"/test-cases/{case_id}/submit-review", {}); await post(f"/test-cases/{case_id}/activate", {})
                    r = await client.put(root + "/case-plans/policy", json={"receipt_correction_protocol": "case-receipt-correction-v1"})
                    assert r.status_code == 200, r.text
                    task = await post(root + "/case-planning-tasks", {"creation_key": "default-ui-plan",
                        "objective": business["objective"], "model_key": model.key, "candidate_case_ids": [case_id]}, 202)
                    planning_queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.case_planning_task_stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await planning_queue.connect()
                    try:
                        assert await PostgresTaskOutbox(settings).relay_once(planning_queue, limit=1) == 1
                        worker = processes.start("ui-planning", "-m", "tests.default_model_probe", "src.cli.run_case_planning_worker", "--once", "--worker-id", "ui-plan")
                        assert await processes.wait(worker, timeout=240) == 0
                    finally:
                        await planning_queue.close()
                    planned = await get(root + "/case-planning-tasks/" + task["id"]); save("planning-task.json", planned)
                    assert planned["status"] == "completed", planned
                    plan = await get(root + "/case-plans/" + planned["plan_id"]); save("plan.json", plan)
                    now = datetime.now(timezone.utc)
                    parent = SessionRecord(id=str(uuid4()), title="独占页面授权来源", status=SessionStatus.idle,
                        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key="ui_automation",
                        project_id=project_id, created_at=now, updated_at=now, preferred_model=model.key,
                        metadata={"source": "owned_test_authority", "resource_scope": {"project_id": project_id, "project_url": origin}})
                    await PostgresSessionStore(settings).save_session(parent)
                    detail = await post(root + "/case-plans/" + plan["id"] + "/runs", {
                        "creation_key": "default-ui-run", "mode_key": "ui_automation", "session_id": parent.id}, 201)
                    run_id = detail["run"]["id"]
                    assert await PostgresTaskOutbox(settings).relay_once(queue, limit=1) == 1
                    worker = processes.start("ui-execution", "-m", "tests.default_model_probe", "src.cli.run_redis_worker", "--once", "--worker-id", "ui-execute")
                    assert await processes.wait(worker, timeout=420) == 0
                    for index in range(12):
                        detail = await get("/runs/" + run_id); save(f"run-{index}.json", detail)
                        waiting = next((i for i in detail["items"] if i["status"] == "waiting_approval"), None)
                        if not waiting: break
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT id,tool_key,metadata FROM {settings.database.postgres_approval_table} WHERE id=%s", (waiting["approval_id"],))
                            approval = cur.fetchone()
                        verify_owned_approval(approval, project_id=project_id, run_id=run_id, item_id=waiting["id"], origin=origin)
                        await post(f"/run-items/{waiting['id']}/approval", {"approval_id": waiting["approval_id"], "decision": "approved"})
                    detail = await get("/runs/" + run_id); save("final-run.json", detail)
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table}")
                        jobs = [dict(r) for r in cur.fetchall()]
                    save("tool-jobs.json", jobs)
                    downloads = []
                    for job in jobs:
                        job_detail = await get(f"/sessions/{job['session_id']}/tool-jobs/{job['id']}")
                        for artifact in job_detail["artifacts"]:
                            uri = artifact["path"]
                            if not uri or not uri.startswith("rustfs://"): continue
                            raw = (await objects.read_object_uri(uri))["content"]
                            r = await client.get(f"/sessions/{job['session_id']}/artifacts/{artifact['id']}/content")
                            assert r.status_code == 200 and r.content == raw
                            if artifact["artifact_type"] == "transcript" and job["input_payload"].get("action") == "command":
                                assert json.loads(raw)[-1]["stdout"] == job["output_payload"]["stdout"]
                            dest = output / "browser-originals" / job["call_id"] / uri.rsplit("/", 1)[-1]
                            dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
                            if uri not in owned: owned.append(uri)
                            downloads.append({"uri": uri, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                                "saved_relative_path": dest.relative_to(output).as_posix(), "call_id": job["call_id"]})
                    save("downloads.json", downloads)
                    assert downloads, "实际浏览器证据必须持久保存并可读"
                    knowledge_jobs = [j for j in jobs if j["tool_key"] == "knowledge-rag"]
                    assert knowledge_jobs and any(scene["version"]["content_hash"] in json.dumps(j, default=str) for j in knowledge_jobs)
                    assert any("计数按钮" in json.dumps(j, ensure_ascii=False, default=str) for j in knowledge_jobs)
                    effects = [e for e in target.events if e["method"] == "POST"]
                    assert len(effects) == 2 and effects[0]["page_id"] == recording_page
                    assert effects[1]["page_id"] != recording_page and effects[1]["count"] == 1, effects
                    assert len(detail["results"]) == 1 and detail["results"][0]["status"] == ("passed" if expected == "1" else "failed"), detail
                    assert effects[1]["page_id"] in json.dumps(detail), "正式结果必须引用本次页面，不能复用录制结果"
                    sends = len(list((output / "physical").glob("*.request.json")))
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE payload->>'run_id'=%s", (run_id,))
                        dispatch = cur.fetchone()["payload"]
                    await queue.enqueue(dispatch)
                    worker = processes.start("ui-terminal", "-m", "tests.default_model_probe", "src.cli.run_redis_worker", "--once", "--worker-id", "ui-terminal")
                    assert await processes.wait(worker, timeout=90) == 0
                    assert await get("/runs/" + run_id) == detail
                    assert len([e for e in target.events if e["method"] == "POST"]) == 2
                    assert len(list((output / "physical").glob("*.request.json"))) == sends
                    save("verification.json", {"controlled_model": False, "default_planning": True,
                        "default_execution": True, "scene_expected_receipt_v2": False,
                        "authority": "server-owned controlled-page fixture, not public authorization workflow",
                        "status": detail["results"][0]["status"], "expected": expected, "events": target.events,
                        "terminal_extra_business_requests": 0, "terminal_extra_model_requests": 0})
            finally:
                if cleanup and cleanup.poll() is None:
                    save("cleanup-worker-stop.json", await asyncio.to_thread(_kill_owned_process_tree, cleanup.pid))
                    await processes.wait(cleanup, timeout=15)
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    public = {}
                    for field in ("postgres_tool_job_table", "postgres_snapshot_table", "postgres_test_case_result_table",
                        "postgres_browser_allocation_table", "postgres_resource_cleanup_table"):
                        table = getattr(settings.database, field)
                        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                        if not cur.fetchone()["existing"]: continue
                        cur.execute(f"SELECT to_jsonb(t) AS record FROM {table} t")
                        rows = [r["record"] for r in cur.fetchall()]
                        if field == "postgres_snapshot_table":
                            for row in rows:
                                graph = row.pop("graph_state", {})
                                row["public_graph"] = {k: graph[k] for k in ("model_response_text", "final_response", "model_response_summary",
                                    "tool_results", "available_tool_keys", "model_visible_tool_keys", "loop_iteration") if k in graph}
                        public[field] = rows
                save("postgres-public.json", public)
                # The venv launcher has an API child; terminate the owned tree
                # before the fixture's database/Redis cleanup.
                if api and api.poll() is None:
                    save("api-cleanup.json", await asyncio.to_thread(_kill_owned_process_tree, api.pid))
                    await processes.wait(api, timeout=15)
    finally:
        save("target-events-finally.json", target.events)
        if recording_id and project_id:
            graphs = RecordingGraphStore(get_settings())
            try: save("graph-cleanup.json", await graphs.delete_recording(recording_id, project_id=project_id))
            finally: await graphs.close()
        for p in tmp_path.iterdir():
            if p.is_file() and (p.suffix in {".stdout", ".stderr"} or p.name == "docker.json"):
                shutil.copyfile(p, output / p.name)
        target.close()
