"""Task grouping retains the real failed scope and persists actual file provenance."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_checkpoint_io import ImpactCallCheckpointUnavailable
from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_reasoning_plan import DocumentImpactReasoningPlans
from src.application.documents.impact_reasoning_plan_service import DocumentImpactReasoningPlanService
from src.application.documents.impact_sources import impact_record_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.document_impact_reasoning import DocumentImpactReasoningPolicy, DocumentImpactReasoningSettings
from src.schemas.document_impact_batches import DocumentImpactBatchPolicy
from test_document_impact_calls import prepared, provider
from test_document_impact_tasks import task_environment, evidence, expire
from test_document_impact_text_anchors import anchored_input
from test_document_impact_sources import draft


def fixed_sample(policy=None):
    seed, envelope, manifest, _, _, _, _ = anchored_input()
    seed = seed.model_copy(update={"reasoning_settings": DocumentImpactReasoningSettings(
        scope_key=seed.project_id, revision=1, payload=policy or DocumentImpactReasoningPolicy())})
    return seed, envelope, manifest


@pytest.mark.parametrize("policy,groups", [(DocumentImpactReasoningPolicy(), 1),
    (DocumentImpactReasoningPolicy(max_group_units=2), 4),
    (DocumentImpactReasoningPolicy(max_group_read_bytes=1), 8)])
def test_actual_failed_full_scope_is_owned_once_without_dropping_evidence(policy, groups):
    seed, envelope, manifest = fixed_sample(policy)
    plan = DocumentImpactReasoningPlans.build(seed, envelope, manifest, impact_record_hash(manifest))
    units = DocumentImpactCallService.units(manifest)
    assert len(plan.discovery) == groups
    assert [unit for task in plan.discovery for unit in task.members] == [unit for unit in units if unit.kind != "case"]
    assert [item.unit for item in plan.cases] == [unit for unit in units if unit.kind == "case"]
    assert len(plan.cases) == 3 and plan.analysis_status == "not_started"
    for task in plan.discovery:
        assert task.id == hashlib.sha256(json.dumps(task.model_dump(mode="json", exclude={"id"}),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert len(task.members) <= policy.max_group_units
        request = DocumentImpactCallService.build_request(seed, envelope, manifest, task.selection)
        payload = json.loads(request.messages[-1]["content"])
        reads = {"primary": payload["primary"], "additional_fixed_reads": payload["additional_fixed_reads"]}
        assert task.read_bytes == len(json.dumps(reads, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        assert bool(task.reason_codes) == (task.read_bytes > policy.max_group_read_bytes)


def test_group_reads_both_complete_original_versions_and_case_sources():
    seed, envelope, manifest = fixed_sample()
    plan = DocumentImpactReasoningPlans.build(seed, envelope, manifest, impact_record_hash(manifest))
    payload = json.loads(DocumentImpactCallService.build_request(seed, envelope, manifest, plan.discovery[0].selection).messages[-1]["content"])
    reads = [payload["primary"], *payload["additional_fixed_reads"]]
    assert {item["text"] for item in reads if "text" in item} >= {envelope.input.old.result.text, envelope.input.new.result.text}
    assert len(reads) == 8 and {item["kind"] for item in reads if "kind" in item} >= {"raw", "normalized", "section"}
    for task in plan.cases:
        assert task.source_segment_ids == [manifest.segments[0].id]
        case = DocumentImpactBatches.read_case(seed, envelope, manifest, task.unit.id)["case"]["version"]
        assert len(case["content_json"]["steps"]) == 8
        assert case["content_json"]["extension"] == {"false": False, "zero": 0, "null": None}
        assert case["execution_notes"]


def test_single_unreadable_unit_is_retained_with_explicit_retrieval_limit():
    seed, envelope, _ = fixed_sample()
    batch = seed.batch_settings.model_copy(update={"payload": seed.batch_settings.payload.model_copy(
        update={"max_retrieval_bytes": 1})})
    seed = seed.model_copy(update={"batch_settings": batch})
    manifest = DocumentImpactBatches.build(seed, envelope)
    plan = DocumentImpactReasoningPlans.build(seed, envelope, manifest, impact_record_hash(manifest))
    assert len(plan.discovery) == 8
    assert all(item.reason_codes == ["retrieval_bytes_limit"] and item.read_bytes is None for item in plan.discovery)
    assert len(plan.cases) == 3


def test_historical_task_without_frozen_policy_does_not_read_current_settings():
    seed, envelope, manifest = fixed_sample()
    seed = seed.model_copy(update={"reasoning_settings": None})
    with pytest.raises(ValueError, match="未冻结推理策略"):
        DocumentImpactReasoningPlans.build(seed, envelope, manifest, impact_record_hash(manifest))


LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires actual file processes/MySQL/PG/RustFS")


def service(env, tasks, store):
    return DocumentImpactReasoningPlanService(tasks=tasks, store=store, objects=env.objects)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_files_plan_freezing_history_and_real_stores(tmp_path, monkeypatch, extension):
    async with task_environment(tmp_path, extension, protocol_version="impact-call-v5") as values:
        env, project, doc, paths, _, _, sources, configs, case, store, tasks = values
        settings = await configs.save_reasoning_settings(project.id, DocumentImpactReasoningPolicy(max_group_units=2))
        with provider() as (endpoint, sends, _, _):
            claim, manifest, _, _ = await prepared(values, endpoint)
            plans = service(env, tasks, store)
            checkpoint, plan = await plans.prepare(claim)
            assert claim.seed.reasoning_settings == settings and plan.config == settings
            assert plan.analysis_status == "not_started" and sends == []
            assert [unit for task in plan.discovery for unit in task.members] == [
                unit for unit in DocumentImpactCallService.units(manifest) if unit.kind != "case"]
            assert plan.cases[0].unit.id == case.version.id
            assert plan.cases[0].source_segment_ids == [manifest.segments[0].id]
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {store.reasoning_plans.table} WHERE task_id=%s", (claim.task.id,))
                assert cur.fetchone()["record"] == checkpoint.model_dump(mode="json")
            await configs.save_reasoning_settings(project.id, DocumentImpactReasoningPolicy(max_group_units=1))
            later = await env.cases.create_version(case.case.id, VersionRequest(**case.version.model_dump(include={
                "content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
            await env.cases.archive(case.case.id)
            async def forbidden(*args, **kwargs):
                raise AssertionError("saved reasoning plan must not query current config/version, invoke or upload")
            restarted = PostgresDocumentImpactStore(env.settings)
            with monkeypatch.context() as patch:
                patch.setattr(configs, "get_reasoning_settings", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(sources, "prepare_fixed", forbidden)
                assert await service(env, tasks, restarted).prepare(claim) == (checkpoint, plan)
            assert plan.cases[0].unit.id != later.id and sends == []
            await store.finish_failed(claim, error_code="fixture_plan_only", message="plan prepared; no model analysis")
            assert await plans.read(project.id, doc.id, claim.task.id) == (checkpoint, plan)
            assert await store.claim(project.id, claim.task.id, worker_id="terminal") is None
            raw = (await env.objects.read_object_uri(checkpoint.storage_uri))["content"]
            assert hashlib.sha256(raw).hexdigest() == checkpoint.sha256
            evidence(f"reasoning/{extension}/verification.json", checkpoint=checkpoint.model_dump(mode="json"),
                plan=plan.model_dump(mode="json"), seed=claim.seed.model_dump(mode="json"),
                actual_http_calls=0, full_model_workflow=False, later_version_excluded=later.id)
            directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target = Path(directory) / "reasoning" / extension
                for path in paths: (target / path.name).write_bytes(path.read_bytes())
                (target / "reasoning-plan.json").write_bytes(raw)


@pytest.mark.asyncio
@LIVE
async def test_lost_plan_reply_and_concurrent_recovery_read_original_object(tmp_path, monkeypatch):
    async with task_environment(tmp_path, protocol_version="impact-call-v5") as values:
        env, project, doc, _, _, _, _, _, _, store, tasks = values
        with provider() as (endpoint, sends, _, _):
            claim, _, _, _ = await prepared(values, endpoint)
            plans = service(env, tasks, store)
            original = store.reasoning_plans.save
            async def lost_reply(*args):
                await original(*args)
                raise ConnectionError("fixture lost reply after actual plan commit")
            with monkeypatch.context() as patch:
                patch.setattr(store.reasoning_plans, "save", lost_reply)
                with pytest.raises(ImpactCallCheckpointUnavailable): await plans.prepare(claim)
            checkpoint, plan = await plans.prepare(claim)
            async def forbidden(*args, **kwargs): raise AssertionError("saved plan must not upload")
            with monkeypatch.context() as patch:
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                assert all(item == (checkpoint, plan) for item in await asyncio.gather(*(plans.prepare(claim) for _ in range(4))))
            assert sends == []
            evidence("reasoning/lost-reply.json", checkpoint=checkpoint.model_dump(mode="json"), actual_http_calls=0,
                recovered_committed_plan=True, unconfirmed_object_retained=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_concurrent_preparation_persists_one_plan(tmp_path):
    async with task_environment(tmp_path, protocol_version="impact-call-v5") as values:
        env, project, doc, _, _, _, _, _, _, store, tasks = values
        with provider() as (endpoint, sends, _, _):
            claim, _, _, _ = await prepared(values, endpoint)
            plans = service(env, tasks, store)
            assert await store.reasoning_plans.get(project.id, doc.id, claim.task.id) is None
            results = await asyncio.gather(*(plans.prepare(claim) for _ in range(4)))
            assert all(item == results[0] for item in results)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {store.reasoning_plans.table} WHERE task_id=%s", (claim.task.id,))
                assert cur.fetchone()["n"] == 1
            assert sends == []
            evidence("reasoning/concurrent-create.json", checkpoint=results[0][0].model_dump(mode="json"),
                concurrent_preparations=4, saved_plans=1, actual_http_calls=0)


@pytest.mark.asyncio
@LIVE
async def test_actual_multi_segment_scope_and_missing_source_are_preserved(tmp_path):
    async with task_environment(tmp_path, protocol_version="impact-call-v5") as values:
        env, project, doc, _, _, _, _, configs, _, store, tasks = values
        missing = await draft(env, project, "human-source", [{"source_type": "manual", "source_id": "human-notes"}])
        await configs.save_batch_settings(project.id, DocumentImpactBatchPolicy(max_segment_chars=16, boundary_context_chars=5))
        await configs.save_reasoning_settings(project.id, DocumentImpactReasoningPolicy(max_group_units=2))
        with provider() as (endpoint, sends, _, _):
            claim, manifest, _, _ = await prepared(values, endpoint)
            checkpoint, plan = await service(env, tasks, store).prepare(claim)
            assert len(plan.discovery) > 1 and len(manifest.segments) > 2
            assert [unit for group in plan.discovery for unit in group.members] == [
                unit for unit in DocumentImpactCallService.units(manifest) if unit.kind != "case"]
            task = next(item for item in plan.cases if item.unit.id == missing.version.id)
            assert task.source_segment_ids == []
            gaps = [item for item in manifest.source_gaps if item.case_version_id == missing.version.id]
            assert len(gaps) == 1 and gaps[0].codes == ["non_document_source"]
            _, envelope = await tasks.read_input(project.id, doc.id, claim.task.id)
            assert len(DocumentImpactBatches.read_case(claim.seed, envelope, manifest, missing.version.id)[
                "case"]["version"]["content_json"]["steps"]) == 12
            assert sends == []
            evidence("reasoning/segments-source-gap.json", checkpoint=checkpoint.model_dump(mode="json"),
                plan=plan.model_dump(mode="json"), gaps=[item.model_dump(mode="json") for item in gaps],
                manifest_segments=len(manifest.segments), actual_http_calls=0)


@pytest.mark.asyncio
@LIVE
async def test_plan_scope_lease_and_original_object_tampering(tmp_path, monkeypatch):
    async with task_environment(tmp_path, protocol_version="impact-call-v5") as values:
        env, project, doc, _, _, _, _, _, _, store, tasks = values
        with provider() as (endpoint, sends, _, _):
            claim, _, _, _ = await prepared(values, endpoint)
            plans = service(env, tasks, store)
            checkpoint, _ = await plans.prepare(claim)
            assert await store.reasoning_plans.get(str(uuid4()), doc.id, claim.task.id) is None
            with pytest.raises(ValueError, match="不能替换"):
                await store.reasoning_plans.save(claim, checkpoint.model_copy(update={"config_sha256": "0" * 64}))
            read = env.objects.read_object_uri
            async def corrupted(uri, **kwargs):
                result = await read(uri, **kwargs)
                return {**result, "content": result["content"] + b" "} if uri == checkpoint.storage_uri else result
            with monkeypatch.context() as patch:
                patch.setattr(env.objects, "read_object_uri", corrupted)
                with pytest.raises(ValueError, match="大小或哈希"): await plans.read(project.id, doc.id, claim.task.id)
            expire(store, claim.task.id)
            restarted = PostgresDocumentImpactStore(env.settings)
            recovered = await restarted.claim(project.id, claim.task.id, worker_id="plan-recovery")
            assert recovered is not None
            with pytest.raises(ContinuationLeaseLostError): await store.reasoning_plans.save(claim, checkpoint)
            assert await service(env, tasks, restarted).prepare(recovered) == await plans.read(project.id, doc.id, claim.task.id)
            assert sends == []
            evidence("reasoning/scope-lease.json", stale_worker_rejected=True, corruption_rejected=True,
                cross_project_read_rejected=True, actual_http_calls=0)
