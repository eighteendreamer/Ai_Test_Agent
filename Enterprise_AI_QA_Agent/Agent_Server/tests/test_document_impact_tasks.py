from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy.exc import IntegrityError

from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.application.documents.impact_analysis_context import DocumentImpactAnalysisContexts, IMPACT_SKILL_KEY
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.document_impact import DocumentImpactPolicy, DocumentImpactTaskRequest
from src.schemas.project import ProjectCreateRequest
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.model_config import ModelCapabilitiesOverride
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from test_document_difference_tasks import comparison_environment
from test_document_impact_sources import draft, setup, source


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS and document processes")]


@asynccontextmanager
async def task_environment(tmp_path, extension="md"):
    async with comparison_environment(tmp_path) as (env, diff, diff_store, configs, parsing, parse_store):
        project, doc, paths, views, diff_id, sources, impact_configs = await setup(
            env, diff, diff_store, configs, parsing, parse_store, tmp_path, extension)
        case = await draft(env, project, "fixed-retention", [source(doc, views[0])])
        await env.cases.submit_review(case.case.id)
        await env.cases.activate(case.case.id)
        store = PostgresDocumentImpactStore(env.settings, lease_seconds=30)
        model_table = "up_im_" + uuid4().hex[:12]
        env.settings.database.llm_model_table = model_table
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE %s", (model_table,))
            assert cur.fetchone() is None
        models = MySQLModelConfigStore(env.settings)
        await asyncio.to_thread(models.initialize)
        await asyncio.to_thread(models.upsert, ModelConfigUpdateRequest(model_name="impact-fixture", provider="openai",
            transport="openai_chat_completions", base_url="http://127.0.0.1:9/v1", api_key="local-fixture-marker",
            is_active=True, use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
            context_window=128000, max_output_tokens=1024))
        skills_root = tmp_path / "analysis-skills"
        skill_file = skills_root / IMPACT_SKILL_KEY / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_bytes((Path(__file__).resolve().parents[1] / "src" / "SKILLS" / IMPACT_SKILL_KEY / "SKILL.md").read_bytes())
        runtime = ModelRuntimeService(ModelRegistry(models), env.settings)
        skill_runtime = SkillRuntimeService(SkillRegistry(skills_root))
        contexts = DocumentImpactAnalysisContexts(models=runtime, skills=skill_runtime, configs=impact_configs)
        env.impact_models, env.impact_runtime, env.impact_skill_file = models, runtime, skill_file
        env.impact_contexts, env.impact_skills = contexts, skill_runtime
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in store.tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        service = DocumentImpactTaskService(store=store, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream="isolated:impact:" + uuid4().hex,
            analysis_contexts=contexts)
        owned, original_upload = [], env.objects.store_uploaded_bytes
        async def tracked(**kwargs):
            saved = await original_upload(**kwargs)
            if kwargs["object_prefix"].startswith("document-impacts/"):
                owned.append(saved["uri"])
            return saved
        env.objects.store_uploaded_bytes = tracked
        try:
            await service.initialize()
            yield env, project, doc, paths, views, diff_id, sources, impact_configs, case, store, service
        finally:
            for uri in set(owned):
                await env.objects.delete_object_uri(uri)
            # Only the task table verified absent above is owned by this test.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute(f'DROP TABLE "{table}"')
            with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE `{model_table}`")
                conn.commit()


def expire(store, task_id):
    with postgres_connect(store._settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {store._tasks} SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
            (task_id,))


def evidence(name, **values):
    directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
    if directory:
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_submission_freezes_scope_without_download_and_prepares_after_mutation(tmp_path, monkeypatch, extension):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, diff_id, sources, configs, case, store, service = values
        request = DocumentImpactTaskRequest(creation_key="fixed-three-party", difference_snapshot_id=diff_id)
        async def forbidden(*args, **kwargs):
            raise AssertionError("metadata submission/recovery must not download or read mutable configuration")
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", forbidden)
            task = await service.submit(project.id, doc.id, request)
        seed = await store.get_seed(project.id, doc.id, task.id)
        assert [item.version_id for item in seed.cases] == [case.version.id]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed FROM {store._tasks} WHERE id=%s", (task.id,))
            seed_json = cur.fetchone()["seed"]
        for reference in seed_json["cases"]:
            assert set(reference) == {"case", "version_id", "content_hash", "version_record_sha256"}
            assert "content_json" not in reference["case"] and "version" not in reference
        assert seed_json["cases"][0]["content_hash"] == case.version.content_hash
        assert "text" not in seed_json and "cases" in seed_json
        await configs.save_settings(project.id, DocumentImpactPolicy(max_case_versions=1, max_context_chars=1))
        later = await env.cases.create_version(case.case.id, VersionRequest(**case.version.model_dump(include={
            "content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
        await env.cases.archive(case.case.id)
        await draft(env, project, "new-case", [{"source_type": "manual", "source_id": "later"}])
        current = await env.docs.get_document(doc.id)
        await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id, request_id="v3-restored",
            expected_parent_version_id=current.current_version_id, filename=paths[0].name,
            content_base64=base64.b64encode(paths[0].read_bytes()).decode()))
        claim = await store.claim(project.id, task.id, worker_id="actual-input-worker")
        with monkeypatch.context() as patch:
            patch.setattr(configs, "get_settings", forbidden)
            patch.setattr(env.cases, "list_project_current_versions", forbidden)
            prepared = await service.prepare_input(claim)
        assert prepared.status == "running" and prepared.phase == "input_saved" and prepared.snapshot_id is None
        checkpoint, envelope = await service.read_input(project.id, doc.id, task.id)
        assert envelope.input.config == seed.config
        assert [bundle.version.id for bundle in envelope.input.cases] == [case.version.id]
        assert later.id not in [bundle.version.id for bundle in envelope.input.cases]
        assert envelope.input.cases[0].case == seed.cases[0].case
        assert len(envelope.input.cases[0].version.content_json["steps"]) == 12
        assert envelope.input.originals[0].parse.snapshot.id == views[0].snapshot.id
        assert envelope.input.old.snapshot == views[0].snapshot and envelope.input.new.snapshot == views[1].snapshot
        assert [(item.old.text, item.new.text) for item in envelope.input.difference.raw.changes[0].characters] == [("7", "9")]
        assert (await service.get_by_request(project.id, doc.id, request.creation_key)).id == task.id
        page = await service.list_tasks(project.id, doc.id, version_id=views[1].snapshot.seed.version_id, limit=1)
        assert page.items == [prepared] and not page.has_more
        assert not (await service.list_tasks(project.id, doc.id, version_id=views[0].snapshot.seed.version_id)).items
        raw = (await env.objects.read_object_uri(checkpoint.storage_uri))["content"]
        assert hashlib.sha256(raw).hexdigest() == checkpoint.sha256
        directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
        if directory:
            base = Path(directory) / extension
            base.mkdir(parents=True, exist_ok=True)
            for path in paths:
                (base / path.name).write_bytes(path.read_bytes())
            (base / "input.json").write_bytes(raw)
        evidence(extension + "/result.json", task=prepared.model_dump(mode="json"), seed=seed.model_dump(mode="json"),
            source_case_version=case.version.id, excluded_later_version=later.id, model_calls=0)


async def test_concurrent_submission_and_replay_precede_changed_configuration(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, configs, case, store, service = values
        request = DocumentImpactTaskRequest(creation_key="one-submission", difference_snapshot_id=diff_id)
        tasks = await asyncio.gather(*(service.submit(project.id, doc.id, request) for _ in range(8)))
        assert len({task.id for task in tasks}) == 1
        task_id = tasks[0].id
        async def forbidden(*args, **kwargs):
            raise AssertionError("known submission replay must not capture new inputs")
        service._stream = None
        with monkeypatch.context() as patch:
            patch.setattr(sources, "capture", forbidden)
            assert (await service.submit(project.id, doc.id, request)).id == task_id
        changed = request.model_copy(update={"case_version_ids": [case.version.id]})
        with pytest.raises(ValueError, match="请求身份"):
            await service.submit(project.id, doc.id, changed)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {store._tasks}")
            assert cur.fetchone()["n"] == 1
            cur.execute(f"SELECT COUNT(*) AS n FROM {env.settings.database.postgres_task_outbox_table} WHERE payload->>'task_type'='document_impact'")
            assert cur.fetchone()["n"] == 1
        with pytest.raises(ValueError, match="尚未保存"):
            await service.read_input(project.id, doc.id, task_id)
        evidence("concurrent.json", task_id=task_id, concurrent_requests=8, tasks=1, outbox=1)


async def test_saved_checkpoint_unknown_reply_new_store_and_stale_worker(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, _, _, store, service = values
        request = DocumentImpactTaskRequest(creation_key="lost-input-reply", difference_snapshot_id=diff_id)
        task = await service.submit(project.id, doc.id, request)
        first = await store.claim(project.id, task.id, worker_id="old-worker")
        assert await store.claim(project.id, task.id, worker_id="overlap-worker") is None
        save = store.save_input_checkpoint
        async def lost_reply(*args, **kwargs):
            await save(*args, **kwargs)
            raise ConnectionError("actual PG checkpoint committed, response lost")
        with monkeypatch.context() as patch:
            patch.setattr(store, "save_input_checkpoint", lost_reply)
            with pytest.raises(ConnectionError):
                await service.prepare_input(first)
        saved = await service.get(project.id, doc.id, task.id)
        assert saved.input_checkpoint is not None and saved.status == "running"
        expire(store, task.id)
        restarted = PostgresDocumentImpactStore(env.settings, lease_seconds=30)
        recovered = await restarted.claim(project.id, task.id, worker_id="new-worker")
        assert recovered.seed == first.seed and recovered.task.phase == "input_saved"
        assert recovered.task.deadline_at == first.task.deadline_at and recovered.attempt_id != first.attempt_id
        second_service = DocumentImpactTaskService(store=restarted, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream=None)
        async def forbidden(*args, **kwargs):
            raise AssertionError("saved inputs must be read without repeating source preparation")
        with monkeypatch.context() as patch:
            patch.setattr(sources, "prepare_fixed", forbidden)
            assert (await second_service.prepare_input(recovered)).input_checkpoint == saved.input_checkpoint
        with pytest.raises(ContinuationLeaseLostError):
            await store.save_input_checkpoint(first, saved.input_checkpoint)
        with pytest.raises(ValueError, match="不能改写"):
            await restarted.save_input_checkpoint(recovered, saved.input_checkpoint.model_copy(update={"input_sha256": "0" * 64}))
        failed = await restarted.finish_failed(recovered, error_code="test_analysis_not_started", message="isolated end-state check")
        assert failed.input_checkpoint == saved.input_checkpoint
        assert failed.status == "failed" and await restarted.claim(project.id, task.id, worker_id="terminal") is None
        evidence("recovery.json", task=failed.model_dump(mode="json"), first_attempt=first.attempt_id,
            new_attempt=recovered.attempt_id, old_deadline_preserved=True, preparation_replayed=False, model_calls=0)


async def test_outbox_failure_rolls_back_submission_and_fixed_version_tampering_is_rejected(tmp_path):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, _, case, store, service = values
        request = DocumentImpactTaskRequest(creation_key="outbox-rollback", difference_snapshot_id=diff_id)
        table = env.settings.database.postgres_task_outbox_table
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT reject_impact CHECK(payload->>'task_type'<>'document_impact')")
        try:
            with pytest.raises((CheckViolation, IntegrityError)):
                await service.submit(project.id, doc.id, request)
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT reject_impact")
        assert await store.get_by_request(project.id, doc.id, request.creation_key) is None
        seed = await sources.capture(project.id, doc.id, diff_id, request)
        version_table = env.settings.database.postgres_test_case_version_table
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {version_table} SET record=jsonb_set(record,'{{execution_notes}}','\"tampered after capture\"'::jsonb) WHERE id=%s", (case.version.id,))
        with pytest.raises(ValueError, match="捕获依据"):
            await store.submit(seed, creation_key="tampered", request_hash=store.request_hash(request), stream=service._stream)
        assert await store.get_by_request(project.id, doc.id, "tampered") is None


async def test_corruption_scope_and_prepare_failure_do_not_become_analysis_success(tmp_path, monkeypatch):
    async with task_environment(tmp_path) as values:
        env, project, doc, _, _, diff_id, sources, _, case, store, service = values
        request = DocumentImpactTaskRequest(creation_key="corrupt-checkpoint", difference_snapshot_id=diff_id)
        task = await service.submit(project.id, doc.id, request)
        other = await env.projects.create(ProjectCreateRequest(project_key="other-impact", name="其他项目"))
        with pytest.raises(KeyError):
            await service.get(other.id, doc.id, task.id)
        claim = await store.claim(project.id, task.id, worker_id="corruption-worker")
        prepared = await service.prepare_input(claim)
        original = env.objects.read_object_uri
        async def corrupt(uri):
            result = await original(uri)
            if uri == prepared.input_checkpoint.storage_uri:
                result["content"] += b"corruption"
            return result
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", corrupt)
            with pytest.raises(ValueError, match="哈希"):
                await service.read_input(project.id, doc.id, task.id)
        assert (await service.get(project.id, doc.id, task.id)).status == "running"
        failing = await service.submit(project.id, doc.id, request.model_copy(update={"creation_key": "invalid-fixed-input"}))
        failure_claim = await store.claim(project.id, failing.id, worker_id="invalid-input-worker")
        async def reject(*args, **kwargs):
            raise ValueError("actual fixed-input contract rejection")
        with monkeypatch.context() as patch:
            patch.setattr(sources, "prepare_fixed", reject)
            failed = await service.prepare_input(failure_claim)
        assert failed.status == "failed" and failed.error_code == "document_impact_input_invalid"
        assert failed.input_checkpoint is None and failed.snapshot_id is None
        assert await store.claim(project.id, failed.id, worker_id="terminal-replay") is None
