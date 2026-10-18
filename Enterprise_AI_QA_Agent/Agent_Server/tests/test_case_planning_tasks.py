"""Project planning submissions must survive the HTTP request that created them."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_planning import SequenceModel, active_case, environment, finish, item
from src.api.routes.case_planning_tasks import router
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.schemas.case_planning import CasePlanningPolicy, PlanningDecision, PlanningQuery
from src.schemas.case_planning_task import CasePlanningTaskRequest, planning_model_parameters
from src.schemas.case_planning_task import CasePlanningTaskCommand
from src.schemas.model_config import ModelConfigRecord
from src.schemas.model_config import ModelCapabilities
from src.schemas.project import ProjectCreateRequest


class SubmissionModel(SequenceModel):
    def get_default_model_config(self):
        return ModelConfigRecord(key="fixture", name="Fixture", provider="openai", model_id="fixture",
            transport="openai_chat_completions", api_base_url="http://127.0.0.1/unused",
            capabilities=ModelCapabilities(streaming=False))

    def get_model_config(self, key):
        return self.get_default_model_config()

    async def invoke(self, key, request, *, expected_parameters=None):
        assert planning_model_parameters(self.get_model_config(key)) == expected_parameters
        return await super().invoke(key, request)


@asynccontextmanager
async def tasks_environment(tmp_path):
    model = SubmissionModel()
    async with environment(tmp_path, live=True, model=model) as env:
        suffix = uuid4().hex[:12]
        env.settings.database.postgres_case_planning_task_table = "up_pt_" + suffix
        env.settings.database.postgres_task_outbox_table = "up_po_" + suffix
        owned = [env.settings.database.postgres_case_planning_task_table, env.settings.database.postgres_task_outbox_table]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in owned:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        jobs = PostgresPlanningTaskStore(env.settings, lease_seconds=30)
        outbox = PostgresTaskOutbox(env.settings)
        await outbox.initialize()
        service = CasePlanningTaskService(store=jobs, planning_service=env.service,
            project_service=env.projects, dispatch_stream="isolated:case_planning")
        await service.initialize()
        app = FastAPI(); app.state.case_planning_task_service = service
        app.include_router(router, prefix="/api/v1")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
                env.tasks, env.jobs, env.task_client, env.model = service, jobs, client, model
                yield env
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in owned:
                    cur.execute(f'DROP TABLE "{table}"')


live = pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_TASKS") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS")


@pytest.mark.asyncio
@live
async def test_planning_submission_returns_a_queryable_task_before_model_work(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="durable-plan", name="持久规划"))
        other = await env.projects.create(ProjectCreateRequest(project_key="other-plan", name="隔离项目"))
        root = f"/projects/{project.id}/case-planning-tasks"
        file = tmp_path / "planning-request.json"
        payload = {"creation_key": "lost-ack", "objective": "根据项目用例规划测试", "runtime_context": {"count": 0, "flag": False, "extra": {"labels": ["中文😀"]}}}
        file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        response = await env.task_client.post(root, json=json.loads(file.read_text(encoding="utf-8")))
        assert response.status_code == 202, response.text
        task = response.json()
        assert task["status"] == "queued" and task["plan_id"] is None
        assert task["deadline_at"] is None and task["started_at"] is None
        assert env.model.requests == []
        assert (await env.task_client.get(root + "/" + task["id"])).json() == task
        assert (await env.task_client.get(root + "/by-request", params={"creation_key": "lost-ack"})).json() == task
        assert (await env.task_client.get(root)).json()["items"] == [task]
        assert (await env.task_client.get(f"/projects/{other.id}/case-planning-tasks/{task['id']}")).status_code == 404
        assert (await env.task_client.get(f"/projects/{other.id}/case-planning-tasks")).json()["items"] == []
        for private in ("seed", "checkpoint", "lease_token", "runtime_context", "model_parameters"):
            assert private not in task
        claim = await env.jobs.claim(project.id, task["id"], worker_id="fixture-a")
        assert claim.seed.plan.request.runtime_context == payload["runtime_context"]
        assert claim.seed.plan.model_key == "fixture"
        assert claim.seed.system_prompt and "DO-NOT-INJECT-TEMPLATE" not in claim.seed.system_prompt
        assert claim.task.deadline_at > claim.task.started_at


@pytest.mark.asyncio
@live
async def test_lost_ack_and_concurrent_retries_keep_one_task_and_outbox(tmp_path, monkeypatch):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="lost-plan", name="提交回执丢失"))
        root = f"/projects/{project.id}/case-planning-tasks"
        payload = {"creation_key": "same-request", "objective": "核对查询"}
        original = env.jobs.submit
        async def lose_ack(**kwargs):
            await original(**kwargs)
            raise RuntimeError("controlled failure after committed submission")
        monkeypatch.setattr(env.jobs, "submit", lose_ack)
        response = await env.task_client.post(root, json=payload)
        assert response.status_code == 503
        assert "尚不能确认" in response.json()["detail"]
        monkeypatch.setattr(env.jobs, "submit", original)
        # Replay must work even if dispatch or configuration is temporarily unavailable.
        env.tasks._stream = None
        async def unexpected_read(*args): raise AssertionError("replay must not load current configuration")
        monkeypatch.setattr(env.service, "prepare_task", unexpected_read)
        responses = await asyncio.gather(*(env.task_client.post(root, json=payload) for _ in range(5)))
        assert all(r.status_code == 202 for r in responses)
        ids = {r.json()["id"] for r in responses}
        assert len(ids) == 1
        assert (await env.task_client.get(root + "/by-request", params={"creation_key": payload["creation_key"]})).json()["id"] in ids
        assert (await env.task_client.post(root, json={**payload, "objective": "另一目标"})).status_code == 409
        assert env.model.requests == []
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS count FROM {env.jobs._table}")
            assert cur.fetchone()["count"] == 1
            cur.execute(f"SELECT payload FROM {env.jobs._outbox}")
            deliveries = cur.fetchall()
            assert len(deliveries) == 1 and deliveries[0]["payload"]["task_id"] in ids
            assert "session_id" not in deliveries[0]["payload"]


@pytest.mark.asyncio
@live
async def test_concurrent_initial_submissions_and_project_archival(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="race-plan", name="并发规划提交"))
        root = f"/projects/{project.id}/case-planning-tasks"
        payload = {"creation_key": "race", "objective": "并发首次创建"}
        responses = await asyncio.gather(*(env.task_client.post(root, json=payload) for _ in range(5)))
        assert all(r.status_code == 202 for r in responses)
        assert len({r.json()["id"] for r in responses}) == 1
        await env.projects.archive(project.id)
        assert (await env.task_client.post(root, json=payload)).status_code == 202
        assert (await env.task_client.post(root, json={**payload, "creation_key": "new"})).status_code == 409
        assert (await env.task_client.post(f"/projects/{uuid4()}/case-planning-tasks", json=payload)).status_code == 404
        assert (await env.task_client.post(root, json={**payload, "seed": {}})).status_code == 422


@pytest.mark.asyncio
@live
async def test_outbox_rejection_rolls_back_already_inserted_planning_task(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="rollback-plan", name="任务派发原子性"))
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {env.jobs._outbox} ADD CONSTRAINT probe_reject_planning CHECK ((payload->>'task_type') IS DISTINCT FROM 'case_planning')")
        response = await env.task_client.post(f"/projects/{project.id}/case-planning-tasks", json={"creation_key": "atomic", "objective": "原子提交"})
        assert response.status_code == 503
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS count FROM {env.jobs._table}")
            assert cur.fetchone()["count"] == 0
            cur.execute(f"SELECT COUNT(*) AS count FROM {env.jobs._outbox}")
            assert cur.fetchone()["count"] == 0


@pytest.mark.asyncio
@live
async def test_checkpoint_recovery_preserves_real_document_and_fences_old_attempt(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="restore-plan", name="恢复固定依据"))
        document = tmp_path / "planning-source.md"
        document.write_text("# 实际项目准备方法\nPOST /fixtures 创建数据，返回 body.id。\n保留未登录的负向验证。\n", encoding="utf-8")
        doc = await env.docs.upload_document(filename=document.name,
            content_base64=base64.b64encode(document.read_bytes()).decode(), project_id=project.id)
        assert (await env.objects.read_object_uri(doc.storage_uri))["content"] == document.read_bytes()
        case = await active_case(env, project, "query", {"objective": "查询已有数据", "steps": [{"action": "GET /fixtures/:id", "expected": 200}]})
        request = CasePlanningTaskRequest(creation_key="restore", objective="验证查询", candidate_case_ids=[case.case.id])
        task = await env.tasks.submit(project.id, request)
        a = await env.jobs.claim(project.id, task.id, worker_id="same-worker-id")
        assert await env.jobs.claim(project.id, task.id, worker_id="duplicate") is None
        evidence, _ = await env.sources.query(project.id, PlanningQuery(kind="document", source_id=doc.id), 20)
        checkpoint = a.checkpoint.model_copy(deep=True)
        checkpoint.plan.evidence = [await env.sources.case(project.id, case.case.id), *evidence]
        checkpoint.plan.rounds = [{"round": 1, "status": "invoking", "attempt_id": a.attempt_id}]
        checkpoint.next_round = 1; checkpoint.phase = "invoking"
        await env.jobs.save_checkpoint(a, checkpoint, message="实际依据已读取，模型调用意图已保存")
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1, max_candidates=1))
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {env.jobs._table} SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (task.id,))
        # Same worker name cannot reuse an expired attempt token (the ABA case).
        b = await PostgresPlanningTaskStore(env.settings).claim(project.id, task.id, worker_id="same-worker-id")
        assert b.attempt_id != a.attempt_id and b.lease_token != a.lease_token
        assert b.task.attempt_no == 2 and b.task.deadline_at == a.task.deadline_at
        assert b.checkpoint == checkpoint and b.seed.plan.config_snapshot.policy.max_rounds == 5
        for operation in (
            env.jobs.heartbeat(a), env.jobs.save_checkpoint(a, checkpoint, message="stale response"),
        ):
            with pytest.raises(ContinuationLeaseLostError): await operation
        await env.jobs.heartbeat(b)
        tampered = checkpoint.model_copy(deep=True)
        tampered.plan.request.objective = "替换原目标"
        with pytest.raises(ValueError, match="固定输入"): await env.jobs.save_checkpoint(b, tampered, message="invalid")
        tampered = checkpoint.model_copy(deep=True); tampered.next_round = 0
        with pytest.raises(ValueError, match="预算"): await env.jobs.save_checkpoint(b, tampered, message="invalid")
        tampered = checkpoint.model_copy(deep=True); tampered.plan.evidence[0].payload["content"] = {"changed": True}
        with pytest.raises(ValueError, match="依据"): await env.jobs.save_checkpoint(b, tampered, message="invalid")
        final = checkpoint.model_copy(deep=True); final.phase = "finished"
        final.plan.decision = PlanningDecision.model_validate(finish(item({"evidence": [e.model_dump(mode="json") for e in final.plan.evidence]}, "query")))
        final.plan.status = "planned"; final.plan.summary = "存储事务测试固定计划"
        with pytest.raises(ContinuationLeaseLostError): await env.jobs.finish(a, final)
        saved = await env.jobs.finish(b, final)
        assert saved.status == "completed" and saved.plan_id == a.seed.plan.id
        assert await env.store.get(project.id, saved.plan_id) == final.plan
        assert await env.jobs.claim(project.id, task.id, worker_id="terminal-redelivery") is None
        with pytest.raises(ContinuationLeaseLostError): await env.jobs.finish(b, final)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT attempts FROM {env.jobs._table} WHERE id=%s", (task.id,))
            history = cur.fetchone()["attempts"]
            assert [entry["id"] for entry in history] == [a.attempt_id, b.attempt_id]


@pytest.mark.asyncio
@live
async def test_final_plan_and_task_terminal_status_commit_atomically(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="finish-plan", name="最终计划事务"))
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="finish", objective="没有匹配用例"))
        claim = await env.jobs.claim(project.id, task.id, worker_id="fixture")
        checkpoint = claim.checkpoint.model_copy(deep=True); checkpoint.phase = "finished"
        checkpoint.plan.status = "no_match"; checkpoint.plan.summary = "存储事务测试，没有目标"
        checkpoint.plan.decision = PlanningDecision.model_validate(finish())
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {env.jobs._table} ADD CONSTRAINT probe_reject_terminal CHECK(status<>'completed')")
        with pytest.raises(Exception): await env.jobs.finish(claim, checkpoint)
        assert await env.store.get(project.id, claim.seed.plan.id) is None
        assert (await env.jobs.get(project.id, task.id)).status == "running"
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {env.jobs._table} DROP CONSTRAINT probe_reject_terminal")
        saved = await env.jobs.finish(claim, checkpoint)
        assert saved.status == "completed"
        assert (await env.store.get(project.id, saved.plan_id)).status == "no_match"


def test_model_snapshot_never_copies_credentials_or_headers():
    config = SubmissionModel().get_default_model_config()
    config.api_key = "fixture-only-not-a-credential"
    config.oauth_refresh_token = "fixture-only-marker"
    config.extra_headers = {"X-Fixture": "fixture-header-marker"}
    public = planning_model_parameters(config)
    encoded = json.dumps(public)
    assert all(marker not in encoded for marker in (config.api_key, config.oauth_refresh_token, "fixture-header-marker"))
    changed = config.model_copy(update={"max_output_tokens": 128})
    assert planning_model_parameters(changed) != public
    rotated = config.model_copy(update={"api_key": "another-fixture-marker"})
    assert planning_model_parameters(rotated) == public


def delivery(task):
    return QueuedTask(message_id="fixture-delivery", payload=CasePlanningTaskCommand(task_id=task.id, project_id=task.project_id).model_dump())


def expire(env, task):
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {env.jobs._table} SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (task.id,))


@pytest.mark.asyncio
@live
async def test_worker_restarts_after_saved_response_without_repeating_model_or_changing_versions(tmp_path, monkeypatch):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="worker-plan", name="Worker 恢复"))
        target = await active_case(env, project, "query", {"objective": "查询已有订单", "required": "order_id", "last_step": "真实末尾😀"})
        document = tmp_path / "project.md"; document.write_text("# 准备\nPOST /fixtures 返回 body.id。\n", encoding="utf-8")
        doc = await env.docs.upload_document(filename=document.name, content_base64=base64.b64encode(document.read_bytes()).decode(), project_id=project.id)
        env.model.replies = [
            {"action": "query", "summary": "按原文补准备", "queries": [{"kind": "document", "source_id": doc.id}]},
            lambda c: finish(item(c, "query", depends_on=["prepare"], input_bindings={"order_id": {"source_item": "prepare", "output_path_tokens": ["body", "id"]}}),
                {"key": "prepare", "kind": "prepare", "reason": "依据当前项目原件", "evidence_ids": [next(e["id"] for e in c["evidence"] if e["kind"] == "document")],
                    "instructions": {"action": "POST /fixtures", "expected": 201, "output": "body.id"}}),
        ]
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="worker", objective="核对订单", candidate_case_ids=[target.case.id]))
        original = env.jobs.save_checkpoint
        async def crash_after_response(claim, checkpoint, *, message):
            result = await original(claim, checkpoint, message=message)
            if checkpoint.phase == "response": raise asyncio.CancelledError()
            return result
        monkeypatch.setattr(env.jobs, "save_checkpoint", crash_after_response)
        with pytest.raises(asyncio.CancelledError):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="before-crash")(delivery(task))
        first = await env.jobs.get(project.id, task.id)
        assert first.phase == "response" and first.round_count == 1
        assert len(env.model.requests) == 1
        # Advance the active version after the old complete JSON was read.
        from src.schemas.case_management import TestCaseVersionCreateRequest
        new = await env.cases.create_version(target.case.id, TestCaseVersionCreateRequest(content_json={"objective": "新版本不可替换本次依据"},
            execution_notes="new", source_refs=[{"source_type": "manual", "source_id": "new"}], model_key="manual", prompt_version="v2", skill_versions={"manual": "v2"}))
        await env.cases.submit_review(target.case.id); await env.cases.activate(target.case.id)
        assert new.id != target.version.id
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        monkeypatch.setattr(env.jobs, "save_checkpoint", original)
        expire(env, task)
        await PlanningTaskHandler(env.service, PostgresPlanningTaskStore(env.settings), worker_id="after-crash")(delivery(task))
        saved = await env.jobs.get(project.id, task.id)
        plan = await env.store.get(project.id, saved.plan_id)
        assert saved.status == "completed" and saved.attempt_no == 2
        assert saved.deadline_at == first.deadline_at and len(env.model.requests) == 2
        assert plan.status == "planned" and plan.config_snapshot.policy.max_rounds == 5
        assert [entry.key for entry in plan.decision.items] == ["prepare", "query"]
        assert env.model.requests[0].trace_context["trace_id"] == task.id
        assert env.model.requests[0].trace_context["attempt_id"] == plan.rounds[0]["attempt_id"]
        assert env.model.requests[0].trace_context["logical_call_id"] == plan.rounds[0]["logical_call_id"]
        assert env.model.requests[1].trace_context["trace_id"] == task.id
        assert env.model.requests[1].trace_context["attempt_id"] == plan.rounds[1]["attempt_id"]
        assert env.model.requests[1].trace_context["logical_call_id"] == plan.rounds[1]["logical_call_id"]
        assert plan.decision.items[1].case_version_id == target.version.id
        assert plan.evidence[0].payload["content"]["last_step"] == "真实末尾😀"
        assert next(e for e in plan.evidence if e.kind == "document").payload["content"] == document.read_text(encoding="utf-8").rstrip("\n")
        await PlanningTaskHandler(env.service, env.jobs, worker_id="redelivery")(delivery(task))
        assert len(env.model.requests) == 2


@pytest.mark.asyncio
@live
async def test_unknown_invocation_consumes_original_round_and_timeout_never_resets(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="unknown-plan", name="未知调用"))
        target = await active_case(env, project, "target", {"objective": "验证目标"})
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=2))
        entered = asyncio.Event()
        async def interrupted(c):
            entered.set(); await asyncio.Event().wait()
        env.model.replies = [interrupted, lambda c: finish(item(c, "target"))]
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="unknown", objective="验证", candidate_case_ids=[target.case.id]))
        execution = asyncio.create_task(PlanningTaskHandler(env.service, env.jobs, worker_id="first")(delivery(task)))
        await asyncio.wait_for(entered.wait(), 5)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError): await execution
        assert (await env.jobs.get(project.id, task.id)).phase == "invoking"
        expire(env, task)
        await PlanningTaskHandler(env.service, env.jobs, worker_id="second")(delivery(task))
        result = await env.jobs.get(project.id, task.id)
        plan = await env.store.get(project.id, result.plan_id)
        assert result.round_count == 2 and result.status == "completed"
        assert plan.rounds[0]["status"] == "outcome_unknown"
        assert plan.rounds[0]["attempt_id"] != plan.rounds[1]["attempt_id"]
        assert "不能确认是否发送或计费" in plan.warnings[0]
        assert len(env.model.requests) == 2
        exhausted = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="expired", objective="时间额度耗尽"))
        claim = await env.jobs.claim(project.id, exhausted.id, worker_id="expired")
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {env.jobs._table} SET deadline_at=clock_timestamp()-INTERVAL '1 second', lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (exhausted.id,))
        await PlanningTaskHandler(env.service, env.jobs, worker_id="restart")(delivery(exhausted))
        timed = await env.jobs.get(project.id, exhausted.id)
        assert timed.status == "failed" and timed.round_count == 0
        assert "超时" in timed.message and len(env.model.requests) == 2


@pytest.mark.asyncio
@live
async def test_worker_uses_real_redis_outbox_and_terminal_redelivery(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="redis-plan", name="真实 Redis 规划"))
        target = await active_case(env, project, "target", {"objective": "验证完整用例", "custom": [0, False, "😀"]})
        env.model.replies = [lambda c: finish(item(c, "target"))]
        stream = "isolated:planning:" + uuid4().hex
        env.tasks._stream = stream
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="redis", objective="规划目标", candidate_case_ids=[target.case.id]))
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=stream, group="fixture-planning")
        await queue.connect()
        try:
            outbox = PostgresTaskOutbox(env.settings)
            assert await outbox.relay_once(queue) == 1
            worker = RedisTaskWorker(queue, consumer="fixture-redis-planning", block_ms=1,
                handler=PlanningTaskHandler(env.service, env.jobs, worker_id="fixture-redis-planning"))
            assert await worker.run_once() == 1
            saved = await env.jobs.get(project.id, task.id)
            assert saved.status == "completed"
            plan = await env.store.get(project.id, saved.plan_id)
            assert plan.evidence[0].payload["content"]["custom"] == [0, False, "😀"]
            await queue.enqueue(delivery(task).payload)
            assert await worker.run_once() == 1
            assert len(env.model.requests) == 1
            assert (await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="redis", objective="规划目标", candidate_case_ids=[target.case.id]))).id == task.id
        finally:
            await queue._client.delete(stream, stream + ":dlq")
            await queue.close()


@pytest.mark.asyncio
@live
async def test_changed_model_parameters_do_not_invoke_and_unconfirmed_checkpoint_is_recoverable(tmp_path, monkeypatch):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="frozen-model", name="模型快照与未知提交"))
        target = await active_case(env, project, "target", {"objective": "验证目标"})
        payload = CasePlanningTaskRequest(creation_key="changed-model", objective="验证", candidate_case_ids=[target.case.id])
        task = await env.tasks.submit(project.id, payload)
        cfg = env.model.get_model_config("fixture").model_copy(update={"max_output_tokens": 128})
        original_config = env.model.get_model_config
        monkeypatch.setattr(env.model, "get_model_config", lambda _: cfg)
        await PlanningTaskHandler(env.service, env.jobs, worker_id="model-check")(delivery(task))
        failed = await env.jobs.get(project.id, task.id)
        assert failed.status == "failed" and failed.round_count == 0 and "参数已变化" in failed.message
        assert env.model.requests == []
        monkeypatch.setattr(env.model, "get_model_config", original_config)
        task = await env.tasks.submit(project.id, payload.model_copy(update={"creation_key": "unconfirmed-checkpoint"}))
        env.model.replies = [lambda c: finish(item(c, "target"))]
        original_save = env.jobs.save_checkpoint
        async def lose_checkpoint_ack(claim, checkpoint, *, message):
            result = await original_save(claim, checkpoint, message=message)
            if checkpoint.phase == "response": raise RuntimeError("committed checkpoint acknowledgement lost")
            return result
        monkeypatch.setattr(env.jobs, "save_checkpoint", lose_checkpoint_ack)
        from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
        with pytest.raises(PlanningCheckpointUnavailable):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="before-ack-loss")(delivery(task))
        assert (await env.jobs.get(project.id, task.id)).phase == "response"
        assert (await env.jobs.get(project.id, task.id)).status == "running"
        assert len(env.model.requests) == 1
        monkeypatch.setattr(env.jobs, "save_checkpoint", original_save)
        expire(env, task)
        original_finish = env.jobs.finish
        async def lose_final_ack(claim, checkpoint):
            await original_finish(claim, checkpoint)
            raise RuntimeError("final transaction acknowledgement lost")
        monkeypatch.setattr(env.jobs, "finish", lose_final_ack)
        with pytest.raises(RuntimeError, match="acknowledgement"):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="after-ack-loss")(delivery(task))
        saved = await env.jobs.get(project.id, task.id)
        assert saved.status == "completed" and saved.plan_id
        assert len(env.model.requests) == 1
        await PlanningTaskHandler(env.service, env.jobs, worker_id="final-redelivery")(delivery(task))
        assert len(env.model.requests) == 1


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("production_cli", [False, True])
async def test_independent_worker_restart_uses_mysql_model_http_and_persisted_document(tmp_path, production_cli):
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
    from src.registry.models import ModelRegistry
    from src.schemas.settings import ModelConfigUpdateRequest
    from src.schemas.model_config import ModelCapabilitiesOverride

    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="process-plan", name="独立 Worker 进程"))
        target = await active_case(env, project, "target", {"objective": "查询订单", "requires": "order_id", "nested": {"完整步骤": [0, False, "中文😀"]}})
        source = tmp_path / "worker-project.md"; source.write_text("# 订单准备\nPOST /fixtures 返回 body.id；GET /fixtures/{id} 验证订单。\n", encoding="utf-8")
        doc = await env.docs.upload_document(filename=source.name, content_base64=base64.b64encode(source.read_bytes()).decode(), project_id=project.id)
        calls, blocked, release = [], threading.Event(), threading.Event()
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                context = json.loads(body["messages"][-1]["content"])
                calls.append({"round": context["round"], "evidence_ids": [e["id"] for e in context["evidence"]],
                    "warnings": context["warnings"], "model": body["model"], "path": self.path,
                    "stream": body.get("stream"), "validation_error": (context.get("validation_feedback") or {}).get("error"),
                    "query_errors": [r.get("error") for r in context["query_results"] if r.get("error")]})
                if len(calls) == 1:
                    decision = {"action": "query", "summary": "读取实际项目准备原文", "queries": [{"kind": "document", "source_id": doc.id}]}
                else:
                    if len(calls) == 2:
                        blocked.set(); release.wait(20)
                    if not any(e["kind"] == "document" for e in context["evidence"]):
                        decision = {"action": "query", "summary": "原文读取失败需重读", "queries": [{"kind": "document", "source_id": doc.id}]}
                    else:
                        decision = finish(item(context, "target", depends_on=["prepare"]), {"key": "prepare", "kind": "prepare",
                        "reason": "依据实际文档准备", "evidence_ids": [next(e["id"] for e in context["evidence"] if e["kind"] == "document")],
                        "instructions": {"action": "POST /fixtures", "expected": 201, "output": "body.id"}})
                encoded = json.dumps({"id": "fixture-response", "object": "chat.completion", "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(decision)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}).encode()
                try:
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError): pass
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        provider.daemon_threads = True
        serving = threading.Thread(target=provider.serve_forever, daemon=True); serving.start()
        model_table = "up_pm_" + uuid4().hex[:12]
        env.settings.database.llm_model_table = model_table
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE %s", (model_table,)); assert cur.fetchone() is None
        models = MySQLModelConfigStore(env.settings); models.initialize()
        models.upsert(ModelConfigUpdateRequest(model_name="planning-fixture", provider="openai", transport="openai_chat_completions",
            base_url=f"http://127.0.0.1:{provider.server_port}/v1", api_key="local-fixture-marker", is_active=True,
            use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False), max_output_tokens=1024))
        env.service._models = ModelRuntimeService(ModelRegistry(models), env.settings)
        stream = "isolated:planning-process:" + uuid4().hex + ":case_planning"; env.tasks._stream = stream
        env.settings.orchestration.redis_task_outbox_streams = stream
        env.settings.orchestration.redis_task_consumer_group = "fixture-process"
        env.settings.orchestration.redis_task_reclaim_idle_ms = 1000
        env.settings.orchestration.redis_task_block_ms = 100
        group = "fixture-process:case_planning" if production_cli else "fixture-process"
        extra_tables = []
        if production_cli:
            from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
            # The production read runtime initializes parse metadata too. Record
            # ownership before starting it and release children before doc versions.
            for table in PostgresDocumentParseStore(env.settings).tables:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                    assert cur.fetchone()["existing"] is None
                extra_tables.append(table)
            for field in ("postgres_test_case_embedding_table", "postgres_recording_table", "postgres_recording_event_table"):
                table = "up_pc_" + uuid4().hex[:12]
                setattr(env.settings.database, field, table); extra_tables.append(table)
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute("SELECT to_regclass(%s) AS existing", (table,)); assert cur.fetchone()["existing"] is None
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=stream, group=group)
        await queue.connect()
        process = None
        collecting = None
        try:
            task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="process", objective="验证订单", candidate_case_ids=[target.case.id]))
            assert await PostgresTaskOutbox(env.settings).relay_once(queue) == 1
            def start(worker_id):
                if production_cli:
                    settings_fields = env.settings.model_dump(mode="json")
                    child_env = {k: v for k, v in os.environ.items()
                        if k.split("__")[0].lower() not in settings_fields}
                    for field, value in settings_fields.items():
                        if field == "api_auth_token":
                            value = env.settings.api_auth_token.get_secret_value() if env.settings.api_auth_token else ""
                        child_env[field.upper()] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
                    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
                    # Continuously drain pipes even before the controlled HTTP
                    # wait; otherwise Windows pipe capacity can stall logging.
                    return subprocess.Popen([sys.executable, "-X", "utf8", "-m", "src.cli.run_case_planning_worker",
                        "--once", "--worker-id", worker_id], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[1], env=child_env)
                child = subprocess.Popen([sys.executable, "-X", "utf8", "tests/planning_worker_probe.py"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
                # Exact fixture settings travel through stdin, never shell interpolation or log output.
                child.stdin.write((json.dumps({"settings": env.settings.model_dump(mode="json"), "stream": stream,
                    "group": group, "worker_id": worker_id}) + "\n").encode()); child.stdin.flush(); child.stdin.close(); child.stdin = None
                return child
            process = start("before-kill")
            collecting = asyncio.create_task(asyncio.to_thread(process.communicate))
            async with asyncio.timeout(20):
                while not blocked.is_set():
                    if process.poll() is not None:
                        out, err = await collecting; raise AssertionError((out + err).decode(errors="replace"))
                    await asyncio.sleep(.05)
            state = await env.jobs.get(project.id, task.id)
            assert state.phase == "invoking" and state.round_count == 2
            process.kill()
            out, err = await asyncio.wait_for(collecting, timeout=5)
            collecting = None
            assert process.returncode != 0
            release.set()
            expire(env, task)
            process = start("after-kill")
            out, err = await asyncio.to_thread(process.communicate, timeout=25)
            assert process.returncode == 0, (out + err).decode(errors="replace")
            assert b"processed=1" in out, (out + err).decode(errors="replace")
            result = await env.jobs.get(project.id, task.id)
            plan = await env.store.get(project.id, result.plan_id)
            assert result.status == "completed" and result.attempt_no == 2 and result.round_count == 3, json.dumps({"message": result.message, "calls": calls, "rounds": plan.rounds, "stderr": err.decode(errors="replace")}, ensure_ascii=False)
            assert result.deadline_at == state.deadline_at
            assert plan.rounds[1]["status"] == "outcome_unknown"
            assert len(calls) == 3 and [call["round"] for call in calls] == [1, 2, 3]
            assert all(call["path"] == "/v1/chat/completions" for call in calls)
            assert plan.evidence[0].version == target.version.id
            assert next(e for e in plan.evidence if e.kind == "document").payload["content"] == source.read_text(encoding="utf-8").rstrip("\n")
            assert [i.key for i in plan.decision.items] == ["prepare", "target"]
            await queue.enqueue(delivery(task).payload)
            process = start("terminal-replay")
            out, err = await asyncio.to_thread(process.communicate, timeout=15)
            assert process.returncode == 0 and b"processed=1" in out, (out + err).decode(errors="replace")
            assert len(calls) == 3
            evidence_name = "p2-planning-worker-cli.json" if production_cli else "p2-planning-worker-probe.json"
            evidence_dir = Path(os.getenv("QA_PLANNING_TASK_EVIDENCE_DIR") or
                str(tmp_path / "evidence"))
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / evidence_name).write_text(json.dumps({
                "scope": "production CLI and dedicated planning runtime" if production_cli else "isolated process host; production RedisTaskWorker/PlanningTaskHandler; full API lifespan not used",
                "task_id": task.id, "plan_id": result.plan_id, "attempt_count": result.attempt_no, "calls": calls,
                "retained_fixed_case_version": True, "retained_document_bytes": True, "terminal_redelivery_no_model_calls": True,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            if process and process.poll() is None:
                process.kill()
                if collecting is not None:
                    await asyncio.wait_for(collecting, timeout=5)
                else:
                    await asyncio.to_thread(process.communicate, timeout=5)
            release.set(); await asyncio.to_thread(provider.shutdown); provider.server_close(); serving.join(timeout=2)
            await queue._client.delete(stream, stream + ":dlq"); await queue.close()
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in extra_tables: cur.execute(f'DROP TABLE IF EXISTS "{table}"')
            with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE `{model_table}`"); conn.commit()
