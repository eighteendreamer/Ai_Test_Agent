"""Exercise durable replanning against actual configuration, documents and Runs."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_plan_run_creation import planned
from test_case_planning import active_case, finish, item
from test_case_planning_tasks import SubmissionModel, delivery, expire
from src.api.routes.run_management import router as run_router
from src.api.routes.case_planning_tasks import router as task_router
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_runs.revision_planning_service import RunRevisionPlanningService
from src.application.test_runs.plan_mapping import json_digest
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.task_deferred import TaskDeferred
from src.schemas.case_planning import CasePlanRunRequest, CasePlanningPolicy
from src.schemas.case_planning_task import CasePlanningTaskRecord
from src.schemas.run_revision_planning import RunRevisionPlanningRequest


live = pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_REVISION_PLANNING") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS")


def evidence(name, value):
    output = os.getenv("QA_REVISION_BRIDGE_OUTPUT_DIR")
    if output:
        root = Path(output)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


@asynccontextmanager
async def revision_environment(tmp_path):
    async with planned(tmp_path, True) as (env, store, suites, creator, plan, now, outbox):
        detail = await creator.create(plan.project_id, plan.id,
            CasePlanRunRequest(creation_key="revision-bridge-original", mode_key="api_testing"))
        original_suite = await suites.get(detail.run.suite_id)
        claims = await store.claim_items(run_id=detail.run.id, worker_id="original-executor",
            limit=2, lease_seconds=600, now=now)
        assert {entry.plan_item_key for entry, _ in claims} == {"create", "independent"}
        create, attempt = next(claim for claim in claims if claim[0].plan_item_key == "create")
        await store.start_item(create.id, attempt.lease_token, now)
        await store.save_checkpoint(create.id, attempt.lease_token, "before-replanning", {"progress": "saved"}, 1, now)
        original = await store.get_run(detail.run.id)
        document_file = tmp_path / "revision-requirements.md"
        document_file.write_text("# 执行中新发现的必要前置\n"
            "左侧查询在原创建动作后，还需执行项目用例 item-4 的准备检查。\n"
            "右侧查询仍使用原创建结果；独立目标保持不变。已领取项不改写。\n", encoding="utf-8")
        document = await env.docs.upload_document(filename=document_file.name, project_id=plan.project_id,
            content_base64=base64.b64encode(document_file.read_bytes()).decode())
        table = "up_rpt_" + uuid4().hex[:12]
        env.settings.database.postgres_case_planning_task_table = table
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None
        env.jobs = PostgresPlanningTaskStore(env.settings, lease_seconds=30)
        tasks = CasePlanningTaskService(store=env.jobs, planning_service=env.service,
            project_service=env.projects, dispatch_stream="isolated:case_planning")
        await tasks.initialize()
        env.service._models = SubmissionModel()
        revisions = RunRevisionPlanningService(tasks=env.jobs, planner=env.service, runs=store,
            projects=env.projects, revision_service=creator, dispatch_stream="isolated:case_planning")
        app = FastAPI()
        app.state.run_revision_planning_service, app.state.case_planning_task_service = revisions, tasks
        app.include_router(run_router, prefix="/api/v1")
        app.include_router(task_router, prefix="/api/v1")
        env.original, env.plan, env.run_store, env.suites, env.creator = original, plan, store, suites, creator
        env.original_suite, env.document, env.revisions = original_suite, document, revisions
        env.path = f"/projects/{plan.project_id}/runs/{detail.run.id}/revision-planning-tasks"
        fields = ("postgres_test_case_version_table", "postgres_test_case_table", "postgres_api_doc_table",
            "postgres_project_table", "postgres_case_plan_table", "postgres_test_case_result_table",
            "postgres_test_run_attempt_table", "postgres_test_run_item_table", "postgres_test_run_table",
            "postgres_test_suite_item_table", "postgres_test_suite_table")
        evidence("allocations-" + tmp_path.name, {"postgres": [table, outbox, store._plan_revision_table,
            *env.docs._catalog_store.history_tables, *[getattr(env.settings.database, field) for field in fields]],
            "task_table": table, "mysql": env.settings.database.application_config_table,
            "document_uri": document.storage_uri})
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://revision/api/v1") as client:
                env.revision_client = client
                yield env
        finally:
            output = os.getenv("QA_REVISION_BRIDGE_OUTPUT_DIR")
            if output:
                root = Path(output) / tmp_path.name
                root.mkdir(parents=True, exist_ok=True)
                for file in tmp_path.iterdir():
                    if file.suffix in {".json", ".md"}:
                        shutil.copyfile(file, root / file.name)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'DROP TABLE "{table}"')


async def submit(env, key="revision-task"):
    payload = {"creation_key": key, "expected_revision": 0, "reason": "执行发现左侧目标缺少必要准备"}
    response = await env.revision_client.post(env.path, json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["run_revision"]["status"] == "pending"
    return CasePlanningTaskRecord.model_validate(response.json()), payload


def replies(env):
    def decide(context):
        source = context["constraints"]["run_revision"]
        assert source["run_id"] == env.original.run.id
        assert source["previous_plan"] == env.plan.decision.model_dump(mode="json")
        assert context["constraints"]["required_case_versions"] == {
            node.case_id: node.case_version_id for node in env.plan.decision.items if node.kind == "target"}
        assert not any("lease_token" in entry for entry in source["items"])
        document = next(entry for entry in context["evidence"] if entry["kind"] == "document")
        assert "item-4" in document["payload"]["content"]
        dependency = item(context, "item-4", key="extra", kind="dependency")
        dependency["evidence_ids"].append(document["id"])
        return finish(item(context, "item-0", key="create", kind="dependency"), dependency,
            item(context, "item-1", key="left", depends_on=["create", "extra"]),
            item(context, "item-2", key="right", depends_on=["create"]),
            item(context, "item-3", key="independent"))
    return [{"action": "query", "summary": "查询新缺口依据及当前项目的补充用例",
        "queries": [{"kind": "document", "source_id": env.document.id}, {"kind": "cases", "query": "item-4"}]}, decide]


def handler(env, **kwargs):
    return PlanningTaskHandler(env.service, env.jobs, worker_id="revision-worker",
        revision_finalizer=env.revisions, **kwargs)


@pytest.mark.asyncio
@live
async def test_submission_replays_fixed_source_without_model_or_current_configuration(tmp_path, monkeypatch):
    async with revision_environment(tmp_path) as env:
        task, payload = await submit(env)
        assert env.service._models.requests == []
        changed = await env.revision_client.post(env.path, json={**payload, "reason": "另一个缺口"})
        assert changed.status_code == 409
        assert (await env.revision_client.post(env.path, json={**payload, "creation_key": "inject", "plan_id": "model-controlled"})).status_code == 422
        assert (await env.revision_client.post(env.path.replace(env.plan.project_id, str(uuid4())), json=payload)).status_code == 404
        assert (await env.revision_client.post(env.path, json={**payload, "creation_key": "stale", "expected_revision": 1})).status_code == 409
        claim = await env.jobs.claim(task.project_id, task.id, worker_id="inspect-fixed-source")
        source = claim.seed.run_revision
        assert source.source_plan_id == env.plan.id and len(source.required_case_versions) == 3
        assert len(source.read_case_versions) == 4
        assert claim.seed.plan.request.objective == env.plan.request.objective
        assert claim.seed.plan.request.candidate_case_ids == []
        assert "DO-NOT-INJECT-TEMPLATE" not in claim.seed.model_dump_json()
        await env.projects.archive(task.project_id)
        env.revisions._stream = None
        async def unavailable(*args, **kwargs): raise AssertionError("a replay must use saved submission")
        monkeypatch.setattr(env.service, "prepare_task", unavailable)
        replay = await env.revision_client.post(env.path, json=payload)
        assert replay.status_code == 202 and replay.json()["id"] == task.id
        assert await env.run_store.get_run(env.original.run.id) == env.original
        evidence(tmp_path.name, {"initial": task.model_dump(mode="json"), "replay": replay.json(),
            "source": source.model_dump(mode="json")})


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("boundary", ["before_apply", "after_apply"])
async def test_terminal_redelivery_recovers_candidate_and_committed_revision(tmp_path, monkeypatch, boundary):
    async with revision_environment(tmp_path) as env:
        task, payload = await submit(env)
        env.service._models.replies = replies(env)
        original_finalize, original_revise = env.revisions.finalize, env.creator.revise
        if boundary == "before_apply":
            async def interrupted(*args): raise asyncio.CancelledError()
            monkeypatch.setattr(env.revisions, "finalize", interrupted)
        else:
            async def lose_ack(*args):
                await original_revise(*args)
                raise asyncio.CancelledError()
            monkeypatch.setattr(env.creator, "revise", lose_ack)
        with pytest.raises(asyncio.CancelledError):
            await handler(env)(delivery(task))
        pending = await env.jobs.get(task.project_id, task.id)
        assert pending.status == "completed" and pending.run_revision.status == "pending"
        assert len(env.service._models.requests) == 2
        history = await env.creator.list_revisions(task.project_id, env.original.run.id)
        assert len(history) == (1 if boundary == "after_apply" else 0)
        if boundary == "after_apply":
            await env.projects.archive(task.project_id)
        monkeypatch.setattr(env.revisions, "finalize", original_finalize)
        monkeypatch.setattr(env.creator, "revise", original_revise)
        env.jobs = PostgresPlanningTaskStore(env.settings, lease_seconds=30)
        env.revisions._tasks = env.jobs
        await handler(env)(delivery(task))
        saved = await env.jobs.get(task.project_id, task.id)
        assert saved.run_revision.status == "applied" and saved.run_revision.revision == 1
        assert saved.attempt_no == 1 and len(env.service._models.requests) == 2
        revised = await env.run_store.get_run(env.original.run.id)
        assert revised.run.plan_revision == 1 and len(revised.items) == 5
        assert revised.attempts == env.original.attempts
        for original in env.original.items:
            if original.attempt_no:
                current = next(entry for entry in revised.items if entry.id == original.id)
                differences = {key for key, value in current.model_dump().items()
                    if original.model_dump()[key] != value}
                assert differences.issubset({"position", "updated_at"}), differences
        for attempt in revised.attempts:
            fixed = await env.run_store.get_execution_detail(env.original.run.id, attempt.run_item_id, attempt.id)
            assert fixed.run.plan_snapshot == env.original.run.plan_snapshot
        assert await env.suites.get(env.original.run.suite_id) == env.original_suite
        await handler(env)(delivery(task))
        assert len(await env.creator.list_revisions(task.project_id, env.original.run.id)) == 1
        queried = await env.revision_client.get(f"/projects/{task.project_id}/case-planning-tasks/{task.id}")
        assert queried.json()["run_revision"]["status"] == "applied"
        evidence(tmp_path.name, {"pending": pending.model_dump(mode="json"), "recovered": queried.json(),
            "history": [record.model_dump(mode="json") for record in await env.creator.list_revisions(task.project_id, env.original.run.id)],
            "model_calls": len(env.service._models.requests), "attempts_preserved": revised.attempts == env.original.attempts})


@pytest.mark.asyncio
@live
async def test_response_checkpoint_resume_and_concurrent_terminal_finalization(tmp_path, monkeypatch):
    async with revision_environment(tmp_path) as env:
        task, _ = await submit(env)
        env.service._models.replies = replies(env)
        save = env.jobs.save_checkpoint
        async def interrupted(claim, checkpoint, *, message):
            value = await save(claim, checkpoint, message=message)
            if checkpoint.phase == "response": raise asyncio.CancelledError()
            return value
        monkeypatch.setattr(env.jobs, "save_checkpoint", interrupted)
        with pytest.raises(asyncio.CancelledError): await handler(env)(delivery(task))
        assert (await env.jobs.get(task.project_id, task.id)).phase == "response"
        expire(env, task)
        monkeypatch.setattr(env.jobs, "save_checkpoint", save)
        entered, release = asyncio.Event(), asyncio.Event()
        revise = env.creator.revise
        async def delayed(*args):
            entered.set()
            await release.wait()
            return await revise(*args)
        monkeypatch.setattr(env.creator, "revise", delayed)
        first = asyncio.create_task(handler(env)(delivery(task)))
        try:
            await asyncio.wait_for(entered.wait(), 20)
            with pytest.raises(TaskDeferred): await handler(env)(delivery(task))
        finally:
            release.set()
            await first
        saved = await env.jobs.get(task.project_id, task.id)
        assert saved.run_revision.status == "applied" and saved.attempt_no == 2
        assert len(env.service._models.requests) == 2
        assert len(await env.creator.list_revisions(task.project_id, env.original.run.id)) == 1
        evidence(tmp_path.name, {"task": saved.model_dump(mode="json"), "model_calls": len(env.service._models.requests)})


@pytest.mark.asyncio
@live
async def test_competing_revision_records_rejection_and_keeps_candidate(tmp_path):
    async with revision_environment(tmp_path) as env:
        first, _ = await submit(env, "first")
        second, _ = await submit(env, "second")
        env.service._models.replies = [*replies(env), *replies(env)]
        await handler(env)(delivery(first))
        before = await env.run_store.get_run(env.original.run.id)
        await handler(env)(delivery(second))
        rejected = await env.jobs.get(second.project_id, second.id)
        assert rejected.status == "completed" and rejected.run_revision.status == "rejected"
        assert "修订版本已变化" in rejected.run_revision.message
        assert (await env.service.get(second.project_id, rejected.plan_id)).status == "planned"
        assert await env.run_store.get_run(env.original.run.id) == before
        await handler(env)(delivery(second))
        assert len(env.service._models.requests) == 4
        assert len(await env.creator.list_revisions(second.project_id, env.original.run.id)) == 1
        evidence(tmp_path.name, {"rejected": rejected.model_dump(mode="json"), "model_calls": 4})


@pytest.mark.asyncio
@live
async def test_model_cannot_drop_original_targets_to_make_replanning_pass(tmp_path):
    async with revision_environment(tmp_path) as env:
        task, _ = await submit(env)
        query, correct = replies(env)
        def dropped(context):
            return finish(item(context, "item-1", key="left"))
        def corrected(context):
            assert "运行修订目标" in context["validation_feedback"]["error"]
            return correct(context)
        env.service._models.replies = [query, dropped, corrected]
        await handler(env)(delivery(task))
        saved = await env.jobs.get(task.project_id, task.id)
        assert saved.run_revision.status == "applied" and saved.round_count == 3
        plan = await env.service.get(task.project_id, saved.plan_id)
        assert {node.case_id: node.case_version_id for node in plan.decision.items if node.kind == "target"} == {
            node.case_id: node.case_version_id for node in env.plan.decision.items if node.kind == "target"}
        evidence(tmp_path.name, {"task": saved.model_dump(mode="json"), "model_calls": len(env.service._models.requests)})


@pytest.mark.asyncio
@live
async def test_failed_candidate_is_retained_without_reporting_revision_success(tmp_path):
    async with revision_environment(tmp_path) as env:
        await env.service.save_policy(env.plan.project_id, CasePlanningPolicy(max_rounds=1))
        task, _ = await submit(env)
        env.service._models.replies = [finish()]
        await handler(env)(delivery(task))
        saved = await env.jobs.get(task.project_id, task.id)
        assert saved.status == "failed" and saved.run_revision.status == "rejected"
        assert (await env.service.get(task.project_id, saved.plan_id)).status == "planning_failed"
        assert await env.run_store.get_run(env.original.run.id) == env.original
        await handler(env)(delivery(task))
        assert len(env.service._models.requests) == 1
        evidence(tmp_path.name, {"task": saved.model_dump(mode="json"), "model_calls": 1})


@pytest.mark.asyncio
async def test_fixed_targets_are_read_completely_beyond_recall_page_size(tmp_path):
    async with planned(tmp_path) as (env, _, _, _, original, _, _):
        project = await env.projects.get(original.project_id)
        bundles = [await active_case(env, project, f"large-{i}", {"objective": f"固定目标 {i}"}) for i in range(51)]
        versions = {bundle.case.id: bundle.version.id for bundle in bundles}
        record = original.model_copy(deep=True, update={"evidence": []})
        record.config_snapshot.policy.max_candidates = 1
        await env.service._recall(record, versions)
        assert {entry.source_id: entry.version for entry in record.evidence} == versions
        assert len(record.evidence) == 51
