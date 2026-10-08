"""Kill/restart the actual planning Worker CLI with a frozen regression scope."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from test_regression_planning_tasks import regression_environment, live
from test_case_planning_tasks import delivery, expire
from test_case_planning import finish
from src.application.models.model_runtime_service import ModelRuntimeService
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
from src.schemas.case_planning_task import CasePlanningTaskRecord
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest


@pytest.mark.asyncio
@live
async def test_production_regression_planning_cli_restart_retains_required_versions_and_failure(tmp_path):
    async with regression_environment(tmp_path) as env:
        calls, blocked, release = [], threading.Event(), threading.Event()
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                context = json.loads(request["messages"][-1]["content"])
                constraints = context["constraints"]
                calls.append({"round": context["round"], "path": self.path,
                    "required_case_versions": constraints["required_case_versions"],
                    "result_ids": constraints["regression"]["selected_result_ids"],
                    "document_read": any(entry["kind"] == "document" for entry in context["evidence"])})
                if len(calls) == 1:
                    decision = {"action": "query", "summary": "读取实际变更文档",
                        "queries": [{"kind": "document", "source_id": env.document.id}]}
                else:
                    if len(calls) == 2: blocked.set(); release.wait(20)
                    doc = next(entry for entry in context["evidence"] if entry["kind"] == "document")
                    case = next(entry for entry in context["evidence"] if entry["kind"] == "case")
                    decision = finish({"key": "prepare", "kind": "prepare", "reason": "原文准备新数据",
                        "instructions": {"action": "POST /fixtures", "output": "body.new_id"}, "evidence_ids": [doc["id"]]},
                        {"key": "target", "kind": "target", "reason": "固定失败版本", "case_id": env.failure.case_id,
                            "case_version_id": env.version.id, "evidence_ids": [case["id"]], "depends_on": ["prepare"]})
                encoded = json.dumps({"id": "local-regression-response", "object": "chat.completion", "model": request["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(decision)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}).encode()
                try:
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError): pass
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider); provider.daemon_threads = True
        serving = threading.Thread(target=provider.serve_forever, daemon=True); serving.start()
        model_table = "up_gr_model_" + uuid4().hex[:12]
        env.settings.database.llm_model_table = model_table
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE %s", (model_table,)); assert cur.fetchone() is None
        models = MySQLModelConfigStore(env.settings); models.initialize()
        models.upsert(ModelConfigUpdateRequest(model_name=env.original.run.plan_snapshot.plan.model_key,
            provider="openai", transport="openai_chat_completions", base_url=f"http://127.0.0.1:{provider.server_port}/v1",
            api_key=uuid4().hex, is_active=True, use_provider_defaults=False,
            capability_overrides=ModelCapabilitiesOverride(streaming=False), max_output_tokens=1024))
        env.service._models = ModelRuntimeService(ModelRegistry(models), env.settings)
        stream = "isolated:regression-worker:" + uuid4().hex + ":case_planning"
        env.tasks._stream = stream
        env.settings.orchestration.redis_task_outbox_streams = stream
        env.settings.orchestration.redis_task_consumer_group = "regression-probe"
        env.settings.orchestration.redis_task_reclaim_idle_ms = 1000
        env.settings.orchestration.redis_task_block_ms = 100
        extra_tables = []
        # The production read runtime initializes parser metadata too; release
        # these owned children before the fixture's document version tables.
        for table in PostgresDocumentParseStore(env.settings).tables:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
            extra_tables.append(table)
        for field in ("postgres_test_case_embedding_table", "postgres_recording_table", "postgres_recording_event_table"):
            table = "up_gr_cli_" + uuid4().hex[:12]
            setattr(env.settings.database, field, table); extra_tables.append(table)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,)); assert cur.fetchone()["existing"] is None
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=stream, group="regression-probe:case_planning")
        await queue.connect()
        process = collecting = None
        async def collect(child):
            out, err = await asyncio.to_thread(child.communicate)
            (tmp_path / f"worker-{child.pid}.stdout").write_bytes(out)
            (tmp_path / f"worker-{child.pid}.stderr").write_bytes(err)
            return out, err
        def start(worker_id):
            fields = env.settings.model_dump(mode="json")
            child_env = {key: value for key, value in os.environ.items() if key.split("__")[0].lower() not in fields}
            for key, value in fields.items():
                if key == "api_auth_token": value = env.settings.api_auth_token.get_secret_value() if env.settings.api_auth_token else ""
                child_env[key.upper()] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
            child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            return subprocess.Popen([sys.executable, "-X", "utf8", "-m", "src.cli.run_case_planning_worker", "--once", "--worker-id", worker_id],
                cwd=Path(__file__).resolve().parents[1], env=child_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            response = await env.client.post(f"/runs/{env.parent.run.id}/regression-planning-tasks", json={
                "creation_key": "regression-cli", "result_ids": [env.failure.id], "version_overrides": {env.failure.case_id: env.version.id}})
            assert response.status_code == 202, response.text
            task = CasePlanningTaskRecord.model_validate(response.json())
            assert await PostgresTaskOutbox(env.settings).relay_once(queue) == 1
            process = start("regression-before-kill"); collecting = asyncio.create_task(collect(process))
            async with asyncio.timeout(25):
                while not blocked.is_set():
                    if process.poll() is not None:
                        out, err = await collecting; raise AssertionError((out + err).decode(errors="replace"))
                    await asyncio.sleep(.05)
            before = await env.jobs.get(task.project_id, task.id)
            assert before.phase == "invoking" and before.round_count == 2
            process.kill(); await asyncio.wait_for(collecting, timeout=5); collecting = None
            release.set(); expire(env, task)
            process = start("regression-after-kill"); collecting = asyncio.create_task(collect(process))
            out, err = await asyncio.wait_for(collecting, timeout=30); collecting = None
            assert process.returncode == 0 and b"processed=1" in out, (out + err).decode(errors="replace")
            completed = await env.jobs.get(task.project_id, task.id)
            plan = await env.service.get(task.project_id, completed.plan_id)
            assert completed.status == "completed" and completed.attempt_no == 2 and completed.round_count == 3
            assert completed.deadline_at == before.deadline_at
            assert plan.regression.required_case_versions == {env.failure.case_id: env.version.id}
            assert plan.regression.source_results_by_case == {env.failure.case_id: env.failure.id}
            assert plan.rounds[1]["status"] == "outcome_unknown"
            assert len(calls) == 3 and [call["round"] for call in calls] == [1, 2, 3]
            assert all(call["required_case_versions"] == {env.failure.case_id: env.version.id}
                and call["result_ids"] == [env.failure.id] and call["path"] == "/v1/chat/completions" for call in calls)
            assert calls[-1]["document_read"]
            await queue.enqueue(delivery(task).payload)
            process = start("regression-terminal"); collecting = asyncio.create_task(collect(process))
            out, err = await asyncio.wait_for(collecting, timeout=20); collecting = None
            assert process.returncode == 0 and b"processed=1" in out, (out + err).decode(errors="replace")
            assert len(calls) == 3 and await env.run_store.get_run(env.parent.run.id) == env.original
            directory = Path(os.getenv("QA_PLANNING_TASK_EVIDENCE_DIR") or str(tmp_path / "evidence"))
            directory.mkdir(parents=True, exist_ok=True)
            evidence = directory / "p2-regression-planning-worker-cli.json"
            evidence.write_text(json.dumps({"scope": "production planning Worker CLI, real MySQL/PG/RustFS/Redis/local model HTTP; not full API lifespan or target Agent execution",
                "task_id": task.id, "plan_id": plan.id, "attempt_count": completed.attempt_no, "calls": calls,
                "original_failure_preserved": True, "terminal_redelivery_no_call": True}, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            if process and process.poll() is None:
                process.kill()
                if collecting: await asyncio.wait_for(collecting, timeout=5)
                else: await asyncio.to_thread(process.communicate, timeout=5)
            release.set(); await asyncio.to_thread(provider.shutdown); provider.server_close(); serving.join(timeout=2)
            await queue._client.delete(stream, stream + ":dlq"); await queue.close()
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in extra_tables: cur.execute(f'DROP TABLE IF EXISTS "{table}"')
            with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE `{model_table}`"); conn.commit()
