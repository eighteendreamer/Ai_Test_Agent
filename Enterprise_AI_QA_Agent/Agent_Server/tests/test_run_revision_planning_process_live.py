"""Production planning CLI recovery across both Run revision commit boundaries.

The HTTP model is controlled. API, SDK, documents, Redis delivery, planner,
finalizer and SQL stores use their actual implementations in isolated storage.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from uuid import uuid4

import httpx
import pytest

from src.application.test_runs.run_store import PostgresTestRunStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_case_json_templates import _draft
from test_case_plan_run_creation import fixture_plan, _wait_for_postgres_backend_exit
from test_case_planning import item
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_RUN_REVISION_PROCESS") != "1",
    reason="production API/CLI, isolated MySQL/PostgreSQL/RustFS and temporary Redis container")]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


@asynccontextmanager
async def model_http():
    calls, errors, source = [], [], {}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                context = json.loads(body["messages"][-1]["content"])
                assert self.path == "/v1/chat/completions" and not body.get("stream")
                calls.append({"path": self.path, "request": body})
                decision = fixture_plan(context)
                if context.get("constraints", {}).get("run_revision"):
                    document = next((entry for entry in context["evidence"] if entry["kind"] == "document"), None)
                    if document is None:
                        decision = {"action": "query", "summary": "读取本次执行缺口的项目原文",
                            "queries": [{"kind": "document", "source_id": source["document_id"]},
                                {"kind": "cases", "query": "item-4"}]}
                    else:
                        assert document["payload"]["content"] == source["content"].rstrip("\n")
                        extra = item(context, "item-4", key="extra", kind="dependency")
                        extra["evidence_ids"].append(document["id"])
                        decision["items"].insert(1, extra)
                        left = next(entry for entry in decision["items"] if entry["key"] == "left")
                        left["depends_on"].append("extra")
                encoded = json.dumps({"id": "local-revision-"+str(len(calls)), "object": "chat.completion",
                    "model": body["model"], "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": json.dumps(decision, ensure_ascii=False)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}).encode()
                self.send_response(200)
            except Exception as exc:
                errors.append({"type": type(exc).__name__, "message": str(exc)})
                encoded = json.dumps({"error": type(exc).__name__}).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, errors, source
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


async def wait_blocked(settings, blocker_pid, process, needle):
    def observe():
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT pid,query,wait_event FROM pg_stat_activity WHERE datname=current_database() "
                "AND state='active' AND %s=ANY(pg_blocking_pids(pid))", (blocker_pid,))
            return [dict(row) for row in cur.fetchall()]

    async with asyncio.timeout(30):
        while True:
            assert process.poll() is None, "planning CLI exited before the observed SQL boundary; see stderr"
            rows = await asyncio.to_thread(observe)
            match = next((row for row in rows if needle in row["query"].replace('"', '').lower()), None)
            if match:
                return match
            await asyncio.sleep(.05)


@pytest.mark.parametrize("boundary", ["before_apply", "after_apply"])
async def test_replanning_cli_kill_preserves_candidate_and_frozen_attempts(tmp_path, boundary):
    folder = Path(os.getenv("QA_RUN_REVISION_PROCESS_EVIDENCE_DIR", str(tmp_path / "evidence"))) / boundary
    folder.mkdir(parents=True, exist_ok=True)
    calls, errors = [], []
    try:
        async with model_http() as (url, calls, errors, source):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = settings.orchestration.case_planning_task_stream
                models = MySQLModelConfigStore(settings)
                models.initialize()
                model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-revision-process", provider="openai",
                    transport="openai_chat_completions", base_url=url, api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False,
                    capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("revision-api-first", *api_args)
                queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.case_planning_task_stream,
                    group=settings.orchestration.redis_task_consumer_group+":case_planning")
                await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=45) as client:
                        await wait_api(client, api)

                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, response.text
                            return response.json()

                        async def get(path):
                            response = await client.get(path)
                            assert response.status_code == 200, response.text
                            return response.json()

                        project = await post("/projects", {"project_key": "revision-process-"+uuid4().hex,
                            "name": "独立规划进程恢复-"+boundary}, 201)
                        root = f"/projects/{project['id']}"
                        candidates = []
                        for number, title in enumerate(("创建订单", "查询左目标", "查询右目标", "独立检查", "新增准备检查")):
                            path = folder / f"item-{number}.json"
                            save(path, {"objective": title, "steps": [{"操作": title, "预期": "保留完整固定版本"}]})
                            bundle = await post(root+"/test-cases", _draft(case_key=f"item-{number}", title=title,
                                content_json=json.loads(path.read_text(encoding="utf-8"))), 201)
                            candidates.append(bundle["case"]["id"])
                            base = f"/test-cases/{bundle['case']['id']}"
                            await post(base+"/submit-review", {"version_id": bundle["version"]["id"], "request_id": "review"})
                            await post(base+"/activate", {"version_id": bundle["version"]["id"], "request_id": "activate"})
                        # This test starts with explicit candidates. Semantic
                        # embedding configuration/quality is a separate acceptance.
                        plan = await post(root+"/case-plans", {"objective": "验证两个查询及独立目标", "model_key": model.key,
                            "candidate_case_ids": candidates}, 201)
                        save(folder/"initial-plan.json", plan)
                        assert plan["status"] == "planned", plan
                        created = await post(root+f"/case-plans/{plan['id']}/runs",
                            {"creation_key": "revision-process-run", "mode_key": "api_testing"}, 201)
                        run_id = created["run"]["id"]
                        claims = (await post(f"/runs/{run_id}/claim", {"worker_id": "original-executor",
                            "limit": 2, "lease_seconds": 3600}))["claims"]
                        assert {claim["item"]["plan_item_key"] for claim in claims} == {"create", "independent"}
                        claim = next(claim for claim in claims if claim["item"]["plan_item_key"] == "create")
                        item_id, lease = claim["item"]["id"], {"lease_token": claim["lease_token"]}
                        await post(f"/run-items/{item_id}/start", lease)
                        await post(f"/run-items/{item_id}/checkpoint", {**lease, "checkpoint_key": "before-replan",
                            "checkpoint_version": 1, "checkpoint_payload": {"original_progress": "saved"}})
                        completed = next(entry for entry in claims if entry["item"]["plan_item_key"] == "independent")
                        completed_id = completed["item"]["id"]
                        await post(f"/run-items/{completed_id}/start", {"lease_token": completed["lease_token"]})
                        result = await post(f"/run-items/{completed_id}/complete", {
                            "lease_token": completed["lease_token"], "status": "failed",
                            "summary": "受控原失败结果；仅用于核验修订不能覆盖历史证据",
                            "actual": {"fixture_observation": {"expected": 1, "actual": 0}},
                            "evidence_refs": [{"evidence_type": "fixture", "evidence_id": "original-failure",
                                "metadata": {"origin": "controlled completion; not actual business execution"}}]})
                        original = await get(f"/runs/{run_id}")
                        assert original["results"] == [result]
                        suite = await get(f"/suites/{created['run']['suite_id']}")
                        document = folder / "execution-gap.md"
                        source["content"] = "# 新发现的执行前置\n左侧查询需要先执行项目用例 item-4；保留原目标、共享准备和清理。\n"
                        document.write_text(source["content"], encoding="utf-8")
                        doc = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": document.name,
                            "content_base64": base64.b64encode(document.read_bytes()).decode()})
                        owned.append(doc["storage_uri"])
                        assert (await objects.read_object_uri(doc["storage_uri"]))["content"] == document.read_bytes()
                        source["document_id"] = doc["id"]
                        submit_path = root+f"/runs/{run_id}/revision-planning-tasks"
                        payload = {"creation_key": "revision-process-task", "expected_revision": 0,
                            "reason": "执行证据说明左侧未领取目标缺少 item-4 准备"}
                        task = await post(submit_path, payload, 202)
                        task_path = root+f"/case-planning-tasks/{task['id']}"
                        history_path = root+f"/runs/{run_id}/plan-revisions"
                        store = PostgresTestRunStore(settings)
                        task_table = settings.database.postgres_case_planning_task_table
                        save(folder/"input.json", {"project": project, "plan": plan, "run": original,
                            "document": doc, "task": task, "submission": payload,
                            "allocations": {"database": settings.database.postgres_database,
                                "mysql_tables": [getattr(settings.database, field) for field in (
                                    "llm_model_table", "application_config_table", "channel_config_table", "email_config_table", "sponsor_config_table")]}})
                        await PostgresTaskOutbox(settings).relay_once(queue)
                        worker_args = ("-m", "src.cli.run_case_planning_worker", "--once")

                        # Use actual SQL waits to observe the exact boundary. The
                        # test-only trigger follows PostgreSQL Trigger Functions
                        # and Advisory Lock Functions docs; it returns NEW unchanged.
                        function = "up_revision_gate_"+uuid4().hex[:12]
                        lock_key = int(uuid4().hex[:14], 16)
                        if boundary == "after_apply":
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                                    BEGIN
                                        IF NEW.checkpoint->'run_revision_outcome'->>'status' = 'applied' THEN
                                            PERFORM pg_advisory_xact_lock({lock_key}::bigint);
                                        END IF;
                                        RETURN NEW;
                                    END $$""")
                                cur.execute(f"CREATE TRIGGER revision_test_gate BEFORE UPDATE OF checkpoint ON {task_table} "
                                    f"FOR EACH ROW EXECUTE FUNCTION {function}()")
                        try:
                            with postgres_connect(settings) as blocker, blocker.cursor() as cur:
                                if boundary == "before_apply":
                                    cur.execute(f"LOCK TABLE {store._plan_revision_table} IN SHARE MODE")
                                    needle = f"insert into {store._plan_revision_table}"
                                else:
                                    cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (lock_key,))
                                    needle = f"update {task_table} set checkpoint="
                                cur.execute("SELECT pg_backend_pid() AS pid")
                                blocker_pid = int(cur.fetchone()["pid"])
                                worker = processes.start("revision-worker-killed", *worker_args, "--worker-id", "before-kill")
                                observed = await wait_blocked(settings, blocker_pid, worker, needle)
                                pending = await get(task_path)
                                assert pending["status"] == "completed" and pending["run_revision"]["status"] == "pending"
                                assert len(calls) == 3 and errors == []
                                before_kill = await get(f"/runs/{run_id}")
                                history = await get(history_path)
                                assert before_kill["run"]["plan_revision"] == (1 if boundary == "after_apply" else 0)
                                assert len(history) == (1 if boundary == "after_apply" else 0)
                                save(folder/"before-kill.json", {"task": pending, "run": before_kill,
                                    "history": history, "blocked_sql": observed, "worker_pid": worker.pid})
                                worker.kill()
                                assert await processes.wait(worker) != 0
                                # A blocked PG backend can survive Windows client
                                # termination. Abort only the observed owned backend.
                                with postgres_connect(settings) as terminator, terminator.cursor() as terminate_cur:
                                    terminate_cur.execute("SELECT pg_terminate_backend(pid) AS stopped FROM pg_stat_activity "
                                        "WHERE pid=%s AND datname=current_database()", (observed["pid"],))
                                    row = terminate_cur.fetchone()
                                    assert row is None or row["stopped"] is True
                                await _wait_for_postgres_backend_exit(settings, observed["pid"])
                        finally:
                            if boundary == "after_apply":
                                with postgres_connect(settings) as conn, conn.cursor() as cur:
                                    cur.execute(f"DROP TRIGGER revision_test_gate ON {task_table}")
                                    cur.execute(f"DROP FUNCTION {function}()")

                        await processes.stop(api)
                        api = processes.start("revision-api-restarted", *api_args)
                        await wait_api(client, api)
                        replay = await post(submit_path, payload, 202)
                        assert replay["id"] == task["id"] and replay["run_revision"]["status"] == "pending"
                        recovered = processes.start("revision-worker-restarted", *worker_args, "--worker-id", "after-kill")
                        assert await processes.wait(recovered, timeout=45) == 0, "see preserved planning CLI stdout/stderr"
                        assert "case_planning_processed=1" in (tmp_path/"revision-worker-restarted.stdout").read_text(encoding="utf-8")
                        saved = await get(task_path)
                        assert saved["status"] == "completed" and saved["run_revision"]["status"] == "applied", saved
                        assert saved["attempt_no"] == 1 and saved["round_count"] == 2
                        revised = await get(f"/runs/{run_id}")
                        history = await get(history_path)
                        assert revised["run"]["plan_revision"] == 1 and len(history) == 1
                        assert len(revised["items"]) == 5 and revised["attempts"] == original["attempts"]
                        assert revised["results"] == original["results"]
                        assert await get(f"/suites/{created['run']['suite_id']}") == suite
                        for entry in original["items"]:
                            if entry["attempt_no"]:
                                current = next(value for value in revised["items"] if value["id"] == entry["id"])
                                assert {key for key in entry if current[key] != entry[key]}.issubset({"position", "updated_at"})
                        for attempt in revised["attempts"]:
                            frozen = await store.get_execution_detail(run_id, attempt["run_item_id"], attempt["id"])
                            assert frozen.run.plan_snapshot.plan.id == plan["id"]
                        left = next(entry for entry in revised["items"] if entry["plan_item_key"] == "left")
                        extra = next(entry for entry in revised["items"] if entry["plan_item_key"] == "extra")
                        assert extra["id"] in {dependency["item_id"] for dependency in left["dependencies"]}
                        next_claims = (await post(f"/runs/{run_id}/claim", {"worker_id": "after-revision",
                            "limit": 10, "lease_seconds": 3600}))["claims"]
                        assert [entry["item"]["plan_item_key"] for entry in next_claims] == ["extra"]
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} "
                                "WHERE payload->>'task_id'=%s", (task["id"],))
                            deliveries = cur.fetchall()
                            assert len(deliveries) == 1
                            cur.execute(f"SELECT plan_snapshots FROM {store._run_table} WHERE id=%s", (run_id,))
                            assert len(cur.fetchone()["plan_snapshots"]) == 2
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} "
                                "WHERE event_key=%s", (f"test_run_dispatch:{run_id}",))
                            assert cur.fetchone()["n"] == 1
                        await queue.enqueue(deliveries[0]["payload"])
                        terminal = processes.start("revision-worker-terminal-replay", *worker_args, "--worker-id", "terminal")
                        assert await processes.wait(terminal, timeout=45) == 0
                        assert "case_planning_processed=1" in (tmp_path/"revision-worker-terminal-replay.stdout").read_text(encoding="utf-8")
                        assert await get(task_path) == saved
                        assert await get(history_path) == history
                        assert len(calls) == 3 and errors == []
                        save(folder/"result.json", {"boundary": boundary, "task": saved, "run": revised,
                            "history": history, "model_http_calls": len(calls), "terminal_replay_extra_http_calls": 0,
                            "attempts_preserved": True, "suite_preserved": True, "original_failed_result_preserved": True,
                            "plan_snapshot_count": 2, "run_dispatch_intent_count": 1,
                            "new_claims": [entry["item"]["plan_item_key"] for entry in next_claims],
                            "scope": "controlled HTTP model; production API and planning CLI; no business execution"})
                finally:
                    await queue.close()
    finally:
        save(folder/"model-http.json", {"calls": calls, "errors": errors})
        for path in tmp_path.iterdir():
            if path.is_file() and (path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}):
                shutil.copy2(path, folder/path.name)
