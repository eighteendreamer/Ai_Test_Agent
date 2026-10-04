from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_case_planning import environment, SequenceModel, finish, item
from test_case_plan_run_creation import planned
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_embedding_job_store import PostgresEmbeddingJobStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import (
    TestCaseActivateRequest as Activate,
    TestCaseDraftCreateRequest as Draft,
    TestCaseReviewSubmitRequest as Submit,
    TestCaseVersionCreateRequest as Version,
)
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.project import ProjectCreateRequest


LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_VERSION_REVIEWS") != "1",
    reason="requires isolated MySQL/PostgreSQL/RustFS"))
FIELDS = {"content_json", "execution_notes", "generation_metadata", "source_refs", "model_key", "prompt_version", "skill_versions"}


async def create(env):
    source = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-impact-input-final/md/input.json"
    captured = next(entry for entry in json.loads(source.read_text(encoding="utf-8"))["input"]["cases"]
        if entry["case"]["case_key"] == "retention")
    payload = {key: captured["version"][key] for key in FIELDS}
    payload["source_refs"] = [{"source_type": "manual", "source_id": "captured-retention"}]
    project = await env.projects.create(ProjectCreateRequest(project_key="retention", name="真实保留期限样本"))
    bundle = await env.cases.create_draft(project_id=project.id, payload=Draft(case_key="retention",
        title=captured["case"]["title"], mode_key="api_testing", case_type="json", **payload))
    await env.cases.submit_review(bundle.case.id)
    await env.cases.activate(bundle.case.id)
    return project, bundle


async def append(env, bundle):
    return await env.cases.create_version(bundle.case.id, Version(**bundle.version.model_dump(include=FIELDS)))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_captured_v1_remains_candidate_and_full_planning_source_with_v2_draft(tmp_path, live):
    async with environment(tmp_path, live=live) as env:
        project, bundle = await create(env)
        original = bundle.version.model_dump(mode="json")
        v2 = await append(env, bundle)
        current = await env.cases.get_case(bundle.case.id)
        assert current.lifecycle_status == "active" and current.active_version_id == bundle.version.id
        assert current.version_reviews[v2.id].status == "draft"
        env.service._models = SequenceModel(lambda context: finish(item(context, "retention")))
        plan = await env.service.create(project.id, CasePlanningRequest(objective="验证订单保留期限"))
        assert plan.status == "planned", plan.summary
        assert plan.decision.items[0].case_version_id == bundle.version.id
        evidence = next(entry for entry in plan.evidence if entry.kind == "case")
        assert evidence.payload["content"] == original["content_json"]
        assert len(evidence.payload["content"]["steps"]) == 12
        assert (await env.cases.get_version(bundle.version.id)).model_dump(mode="json") == original


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_reviewed_v2_not_newer_v3_and_replay_never_rolls_back_active_pointer(tmp_path, live):
    async with environment(tmp_path, live=live) as env:
        _, bundle = await create(env)
        v2 = await append(env, bundle)
        await env.cases.submit_review(bundle.case.id, Submit(version_id=v2.id, request_id="submit-v2"))
        v3 = await append(env, bundle)
        with pytest.raises(ValueError, match="未评审草稿"):
            await env.cases.activate(bundle.case.id, Activate(version_id=v3.id))
        op2 = Activate(version_id=v2.id, request_id="enable-v2", expected_active_version_id=bundle.version.id)
        enabled = await env.cases.activate(bundle.case.id, op2)
        assert enabled.active_version_id == v2.id and enabled.latest_version == 3
        await env.cases.submit_review(bundle.case.id, Submit(version_id=v3.id, request_id="submit-v3"))
        await env.cases.activate(bundle.case.id, Activate(version_id=v3.id, request_id="enable-v3"))
        replay = await env.cases.activate(bundle.case.id, op2)
        assert replay.active_version_id == v3.id
        assert len([e for e in replay.version_reviews[v2.id].events if e.request_id == "enable-v2"]) == 1
        for changed in (Activate(version_id=v2.id, request_id="enable-v2", expected_active_version_id=v3.id),
            Activate(version_id=v3.id, request_id="enable-v2", expected_active_version_id=bundle.version.id)):
            with pytest.raises(ValueError, match="不能改变参数"):
                await env.cases.activate(bundle.case.id, changed)
        with pytest.raises(ValueError, match="已变化"):
            await env.cases.activate(bundle.case.id, Activate(version_id=v2.id, expected_active_version_id=None))
        await env.cases.archive(bundle.case.id)
        assert (await env.cases.activate(bundle.case.id, op2)).lifecycle_status == "archived"
        with pytest.raises(ValueError, match="归档"):
            await env.cases.activate(bundle.case.id, Activate(version_id=v2.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_default_activation_and_concurrent_append_review_keep_exact_versions(tmp_path, live):
    async with environment(tmp_path, live=live) as env:
        _, bundle = await create(env)
        v2 = await append(env, bundle)
        await env.cases.submit_review(bundle.case.id, Submit(version_id=v2.id))
        v3 = await append(env, bundle)
        assert (await env.cases.activate(bundle.case.id)).active_version_id == v2.id
        await env.cases.submit_review(bundle.case.id, Submit(version_id=v2.id))
        await env.cases.submit_review(bundle.case.id, Submit(version_id=v3.id))
        with pytest.raises(ValueError, match="多个版本"):
            await env.cases.activate(bundle.case.id)
        stale = await env.cases.get_case(bundle.case.id)
        results = await asyncio.gather(*[append(env, bundle) for _ in range(5)],
            env.cases.submit_review(bundle.case.id, Submit(version_id=v3.id, request_id="concurrent-submit")),
            env.cases.activate(bundle.case.id, Activate(version_id=v3.id, request_id="concurrent-enable")))
        current = await env.cases.get_case(bundle.case.id)
        assert current.latest_version == 8 and len(current.version_reviews) == 8
        assert current.active_version_id == v3.id
        assert {v.version for v in results[:5]} == set(range(4, 9))
        await env.cases._store.replace_case(stale.model_copy(update={"lifecycle_status": "archived"}),
            expected_statuses={"active"})
        current = await env.cases.get_case(bundle.case.id)
        assert current.latest_version == 8 and current.active_version_id == v3.id
        with pytest.raises(ValueError, match="archived"):
            await append(env, bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_legacy_preview_preserves_version_content_and_disabled_archived_state(tmp_path, live):
    async with environment(tmp_path, live=live) as env:
        project, bundle = await create(env)
        await append(env, bundle)
        versions = [v.model_dump(mode="json") for v in await env.cases.list_versions(bundle.case.id)]
        store = env.cases._store
        for status in ("disabled", "archived", "draft"):
            case = (await env.cases.get_case(bundle.case.id)).model_copy(update={"lifecycle_status": status, "version_reviews": {}})
            if live:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"UPDATE {env.settings.database.postgres_test_case_table} SET lifecycle_status=%s, record=%s::jsonb WHERE id=%s",
                        (status, case.model_dump_json(), case.id))
            else:
                store._cases[case.id] = case
            preview = await env.cases.migrate_legacy_reviews(project.id)
            assert preview[0]["after_status"] == ("active" if status == "draft" else status)
            assert (await env.cases.get_case(case.id)).version_reviews == {}
            await env.cases.migrate_legacy_reviews(project.id, dry_run=False)
            updated = await env.cases.get_case(case.id)
            assert all(review.origin == "legacy_state" for review in updated.version_reviews.values())
            assert all(review.events[0].action == "legacy_inferred" for review in updated.version_reviews.values())
            assert await env.cases.migrate_legacy_reviews(project.id, dry_run=False) == []
            assert [v.model_dump(mode="json") for v in await env.cases.list_versions(case.id)] == versions


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_new_drafts_preserve_frozen_plan_and_actual_run_creation(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, creator, plan, now, table):
        for entry in plan.decision.items:
            if entry.kind == "prepare":
                continue
            version = await env.cases.get_version(entry.case_version_id)
            await env.cases.create_version(entry.case_id, Version(**version.model_dump(include=FIELDS)))
        detail = await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="after-drafts", mode_key="api_testing"))
        assert {i.case_version_id for i in detail.items} == {i.case_version_id for i in plan.decision.items if i.kind != "prepare"}
        snapshot = detail.run.plan_snapshot
        assert snapshot.plan == plan
        assert (await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="after-drafts", mode_key="api_testing"))).run.id == detail.run.id


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_VERSION_REVIEWS") != "1", reason="requires real PG transactional Outbox")
async def test_real_pg_atomic_outbox_failure_replay_and_reactivation_new_job(tmp_path):
    async with environment(tmp_path, live=True) as env:
        _, bundle = await create(env)
        suffix = uuid4().hex[:12]
        jobs, outbox = "up_vr_jobs_"+suffix, "up_vr_outbox_"+suffix
        env.settings.database.postgres_embedding_job_table = jobs
        env.settings.database.postgres_task_outbox_table = outbox
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for name in (jobs, outbox):
                cur.execute("SELECT to_regclass(%s) AS existing", (name,))
                assert cur.fetchone()["existing"] is None
        try:
            await PostgresEmbeddingJobStore(env.settings).initialize()
            await PostgresTaskOutbox(env.settings).initialize()
            env.cases.set_embedding_task_outbox(embedding_job_table=jobs, outbox_table=outbox, stream="isolated:version-reviews")
            v2 = await append(env, bundle)
            await env.cases.submit_review(bundle.case.id, Submit(version_id=v2.id))
            before = await env.cases.get_case(bundle.case.id)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"ALTER TABLE {outbox} ADD CONSTRAINT reject_activation CHECK (stream <> 'isolated:version-reviews')")
            with pytest.raises(Exception, match="reject_activation"):
                await env.cases.activate(bundle.case.id, Activate(version_id=v2.id, request_id="atomic-v2"))
            assert await env.cases.get_case(bundle.case.id) == before
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS n FROM {jobs}")
                assert cur.fetchone()["n"] == 0
                cur.execute(f"ALTER TABLE {outbox} DROP CONSTRAINT reject_activation")
            op = Activate(version_id=v2.id, request_id="atomic-v2")
            await env.cases.activate(bundle.case.id, op)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {jobs} SET status='completed', completed_at=NOW() RETURNING *")
                terminal = cur.fetchone()
            await env.cases.activate(bundle.case.id, Activate(version_id=bundle.version.id, request_id="return-v1"))
            await env.cases.activate(bundle.case.id, Activate(version_id=v2.id, request_id="return-v2"))
            await env.cases.activate(bundle.case.id, op)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {jobs} WHERE task_id=%s", (terminal["task_id"],))
                assert cur.fetchone() == terminal
                cur.execute(f"SELECT payload FROM {outbox}")
                payloads = [r["payload"] for r in cur.fetchall()]
                assert len(payloads) == 3 and len({p["task_id"] for p in payloads}) == 3
                assert sum(p["case_version_id"] == v2.id for p in payloads) == 2
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for name in (jobs, outbox):
                    cur.execute(f'DROP TABLE IF EXISTS "{name}"')
