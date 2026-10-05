"""Actual fixed files, MySQL/PG/RustFS/Redis and per-module HTTP/draft receipts."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.test_cases.generation_task_config import GenerationTaskConfig
from src.application.test_cases.generation_task_service import GenerationTaskService, GenerationCheckpointUnavailable
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.generation_task_handler import GenerationTaskHandler
from src.runtime.postgres_generation_task_store import PostgresGenerationTaskStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_generation_tasks import GenerationTaskPolicy, GenerationTaskRequest
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_incremental_generation_sources import accepted, components, responder, request_for
from test_requirement_reviews import LIVE, environment


@asynccontextmanager
async def generation_environment(tmp_path, extension="md"):
    async with environment(tmp_path, extension) as values:
        env, project, doc, paths, case, _, requirements, *_ = values
        sources, generator, templates, skills, skill_file = components(env, requirements, tmp_path)
        await templates.initialize()
        store = PostgresGenerationTaskStore(env.settings, env.cases._store)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in store.tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        config = GenerationTaskConfig(configs=env.configs, projects=env.projects)
        service = GenerationTaskService(store=store, sources=sources, config=config, generator=generator,
            cases=env.cases, projects=env.projects, documents=env.docs, objects=env.objects, stream="isolated:generation:"+uuid4().hex)
        owned, upload = [], env.objects.store_uploaded_bytes
        async def tracked(**kwargs):
            saved = await upload(**kwargs)
            if kwargs["object_prefix"].startswith("case-generation/"): owned.append(saved["uri"])
            return saved
        env.objects.store_uploaded_bytes = tracked
        try:
            await service.initialize()
            await config.save(project.id, GenerationTaskPolicy(transport_policy="single_attempt", call_timeout_seconds=15))
            yield values, service, store, config
        finally:
            env.objects.store_uploaded_bytes = upload
            for uri in set(owned): await env.objects.delete_object_uri(uri)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables: cur.execute(f'DROP TABLE "{table}"')


def task_request(review, case, **changes):
    return GenerationTaskRequest(**request_for(review, case, **changes).model_dump(), creation_key="incremental-original")


async def evidence(name, values, service, group, **extra):
    if not (directory := os.getenv("QA_GENERATION_TASK_EVIDENCE_DIR")): return
    env, project, doc, paths, *_ = values
    destination = Path(directory)/name
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths: (destination/path.name).write_bytes(path.read_bytes())
    fixed_group, tasks = await service.read_group(project.id, doc.id, group.id)
    objects = [fixed_group.scope, *[task.checkpoint.response for task in tasks if task.checkpoint and task.checkpoint.response]]
    for reference in objects:
        raw = (await env.objects.read_object_uri(reference.storage_uri))["content"]
        assert hashlib.sha256(raw).hexdigest() == reference.sha256 and len(raw) == reference.size_bytes
        (destination/(reference.sha256+".json")).write_bytes(raw)
    (destination/"verification.json").write_text(json.dumps({"group": fixed_group.model_dump(mode="json"),
        "tasks": [task.model_dump(mode="json") for task in tasks], "actual_leased_module": True,
        "actual_redis_worker": False, "independent_worker_process": False,
        "production_api_or_cli_connected": False, "controlled_model": True, **extra}, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_redis_worker_updates_and_creates_drafts_with_atomic_receipts(tmp_path, extension):
    async with generation_environment(tmp_path, extension) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            request = task_request(review, case, include_new_cases=True, new_case_mode_key="api_testing")
            group = await service.submit(project.id, doc.id, request)
            assert await service.submit(project.id, doc.id, request) == group
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            assert len(tasks) == 2 and not sends
            queue = RedisTaskQueue(env.settings.database.redis_url, stream=service._stream, group="generation-acceptance")
            await queue.connect()
            try:
                outbox = PostgresTaskOutbox(env.settings)
                assert await outbox.relay_once(queue, limit=10) == 2
                worker = RedisTaskWorker(queue, consumer="generation-real-worker", handler=GenerationTaskHandler(service, store, worker_id="generation-real-worker"), block_ms=1)
                assert await worker.run_once() == 1
                assert await worker.run_once() == 1
                group2, completed = await service.read_group(project.id, doc.id, group.id)
                assert group2 == group and all(task.status == "completed" for task in completed), [task.model_dump() for task in completed]
                assert len(sends) == 2
                update = completed[0].checkpoint.receipt["versions"][0]
                created = completed[1].checkpoint.receipt["versions"][0]
                assert update["case_id"] == case.case.id and update["version"] == 2
                current = await env.cases.get_case(case.case.id)
                assert current.active_version_id == case.version.id and current.version_reviews[update["version_id"]].status == "draft"
                draft = await env.cases.get_version(update["version_id"])
                new = await env.cases.get_case(created["case_id"])
                assert new.lifecycle_status == "draft" and new.active_version_id is None
                assert len(draft.content_json["steps"]) == 12 and draft.content_json["深层扩展"] == case.version.content_json["深层扩展"]
                assert draft.generation_metadata["incremental_generation"]["requirement_version_id"] == review["requirement_version"]["id"]
                assert await env.cases.get_version(case.version.id) == case.version
                for task in tasks:
                    await queue.enqueue({"task_type": "case_generation", "project_id": project.id, "task_id": task.id})
                    assert await worker.run_once() == 1
                    assert await store.claim(project.id, task.id, worker_id="terminal") is None
                assert len(sends) == 2 and (await env.cases.get_case(case.case.id)).latest_version == 2
                await evidence(extension, values, service, group, physical_requests=sends, actual_generation_http_calls=2,
                    draft_update=draft.model_dump(mode="json"), draft_new=new.model_dump(mode="json"),
                    actual_redis_worker=True, redis_terminal_redelivery_no_send=True)
            finally:
                await queue._client.delete(queue.stream)
                await queue.close()


@pytest.mark.asyncio
@LIVE
async def test_actual_group_and_draft_commit_reply_loss_recover_without_new_send(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            request = task_request(review, case)
            submit = store._submit_group
            def lost_submit(*args, **kwargs):
                submit(*args, **kwargs)
                raise ConnectionError("actual committed group reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store, "_submit_group", lost_submit)
                with pytest.raises(GenerationCheckpointUnavailable): await service.submit(project.id, doc.id, request)
            async def forbidden(*args, **kwargs): raise AssertionError("replay must not rebuild scope/config or send")
            with monkeypatch.context() as patch:
                patch.setattr(service._sources, "capture", forbidden)
                patch.setattr(config, "get", forbidden)
                group = await service.submit(project.id, doc.id, request)
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            claim = await store.claim(project.id, tasks[0].id, worker_id="lost-commit")
            commit = store._commit_module
            def lost_commit(*args, **kwargs):
                commit(*args, **kwargs)
                raise ConnectionError("actual committed draft receipt reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store, "_commit_module", lost_commit)
                with pytest.raises(GenerationCheckpointUnavailable): await service.execute(claim)
            saved = await store.get(project.id, tasks[0].id)
            assert saved.status == "completed" and (await env.cases.get_case(case.case.id)).latest_version == 2
            with monkeypatch.context() as patch:
                patch.setattr(service._generator, "invoke_frozen", forbidden)
                patch.setattr(service._objects, "store_uploaded_bytes", forbidden)
                assert await service.execute(claim) == saved
                assert await store.claim(project.id, saved.id, worker_id="recovery") is None
            assert len(sends) == 1
            await evidence("lost-replies", values, service, group, physical_requests=sends,
                actual_generation_http_calls=1, actual_group_and_draft_commit_reply_lost=True)


def expire(store, task_id):
    with postgres_connect(store._settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {store._tasks} SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (task_id,))


@pytest.mark.asyncio
@LIVE
async def test_actual_response_commit_loss_and_new_lease_use_saved_response(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            first = await store.claim(project.id, tasks[0].id, worker_id="first")
            save_response = store._save_response
            def lost_response(*args, **kwargs):
                save_response(*args, **kwargs)
                raise ConnectionError("actual committed response reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store, "_save_response", lost_response)
                with pytest.raises(GenerationCheckpointUnavailable): await service.execute(first)
            assert (await store.get(project.id, first.task.id)).checkpoint.phase == "response_saved"
            await config.save(project.id, GenerationTaskPolicy(max_input_bytes=1))
            expire(store, first.task.id)
            recovered = await store.claim(project.id, first.task.id, worker_id="restarted")
            async def forbidden(*args, **kwargs): raise AssertionError("saved response recovery cannot invoke/current-read")
            with monkeypatch.context() as patch:
                patch.setattr(service._generator, "invoke_frozen", forbidden)
                patch.setattr(service._sources, "capture", forbidden)
                patch.setattr(config, "get", forbidden)
                final = await service.execute(recovered)
            assert final.status == "completed" and len(sends) == 1
            assert (await env.cases.get_case(case.case.id)).latest_version == 2
            await evidence("response-recovery", values, service, group, physical_requests=sends,
                actual_generation_http_calls=1, actual_response_commit_reply_lost=True, new_lease_attempt=recovered.attempt_id)


@pytest.mark.asyncio
@LIVE
async def test_actual_inflight_cancel_unknown_not_resent_and_parent_conflict_keeps_response(tmp_path):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder, gated=True) as (endpoint, sends, arrived, release):
            await configure(env, endpoint)
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            claim = await store.claim(project.id, tasks[0].id, worker_id="cancelled")
            execution = asyncio.create_task(service.execute(claim))
            assert await asyncio.to_thread(arrived.wait, 8)
            execution.cancel()
            with pytest.raises(asyncio.CancelledError): await execution
            release.set()
            expire(store, claim.task.id)
            final = await service.execute(await store.claim(project.id, claim.task.id, worker_id="unknown-recovery"))
            assert final.status == "failed" and final.error_code == "call_outcome_unknown" and len(sends) == 1
            assert (await env.cases.get_case(case.case.id)).latest_version == 1
            await evidence("unknown", values, service, group, physical_requests=sends, actual_generation_http_calls=1,
                actual_inflight_cancel=True, unknown_not_resent=True)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            request = task_request(review, case).model_copy(update={"creation_key": "conflict-new-task"})
            group2 = await service.submit(project.id, doc.id, request)
            await env.cases.create_version(case.case.id, VersionRequest(**case.version.model_dump(include={
                "content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
            _, tasks = await service.read_group(project.id, doc.id, group2.id)
            final = await service.execute(await store.claim(project.id, tasks[0].id, worker_id="conflict"))
            assert final.status == "failed" and final.error_code == "draft_commit_rejected"
            assert final.checkpoint.response is not None and len(sends) == 1
            assert (await env.cases.get_case(case.case.id)).latest_version == 2
            await evidence("parent-conflict", values, service, group2, physical_requests=sends,
                actual_generation_http_calls=1, parent_conflict_no_extra_draft=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_concurrent_group_and_claim_and_independent_failure(tmp_path):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        def mixed_response(body):
            context = json.loads(body["messages"][-1]["content"])["evidence_context"]["incremental_generation"]
            return "invalid generation JSON" if context["kind"] == "update" else responder(body)
        with provider(responder=mixed_response) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            request = task_request(review, case, include_new_cases=True, new_case_mode_key="api_testing")
            groups = await asyncio.gather(*(service.submit(project.id, doc.id, request) for _ in range(4)))
            assert all(group == groups[0] for group in groups)
            group = groups[0]
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            assert len(tasks) == 2
            claims = await asyncio.gather(*(store.claim(project.id, tasks[0].id, worker_id=f"race-{i}") for i in range(4)))
            owned = [claim for claim in claims if claim is not None]
            assert len(owned) == 1
            failed = await service.execute(owned[0])
            success = await service.execute(await store.claim(project.id, tasks[1].id, worker_id="independent"))
            assert failed.status == "failed" and failed.error_code == "response_validation_rejected" and failed.checkpoint.response
            assert success.status == "completed" and success.checkpoint.receipt["versions"]
            assert (await env.cases.get_case(case.case.id)).latest_version == 1 and len(sends) == 2
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS count FROM {store._groups}")
                assert cur.fetchone()["count"] == 1
                cur.execute(f"SELECT COUNT(*) AS count FROM {store._outbox} WHERE event_key LIKE %s", ("case-generation:%",))
                assert cur.fetchone()["count"] == 2
            await evidence("concurrency-independent", values, service, group, physical_requests=sends,
                actual_generation_http_calls=2, concurrent_submissions=4, concurrent_claims=4,
                exactly_one_group_and_two_outbox_events=True, failed_module_did_not_block_other=True)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("expiry", ["lease", "deadline"])
async def test_actual_case_lock_wait_expires_budget_and_rolls_back_draft(tmp_path, expiry):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            if expiry == "deadline":
                await config.save(project.id, GenerationTaskPolicy(timeout_seconds=1,
                    transport_policy="single_attempt", call_timeout_seconds=15))
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            store.lease_seconds = 1 if expiry == "lease" else 30
            claim = await store.claim(project.id, tasks[0].id, worker_id="lock-wait")
            execution = None
            try:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id FROM {env.cases._store._case_table} WHERE id=%s FOR UPDATE", (case.case.id,))
                    execution = asyncio.create_task(service.execute(claim))
                    # The actual case row lock outlives the chosen budget after
                    # the original HTTP response has been durably checkpointed.
                    for _ in range(100):
                        task = await store.get(project.id, claim.task.id)
                        if task.checkpoint and task.checkpoint.phase == "response_saved": break
                        await asyncio.sleep(0.01)
                    assert task.checkpoint and task.checkpoint.phase == "response_saved"
                    await asyncio.sleep(1.1)
                if expiry == "lease":
                    with pytest.raises(ContinuationLeaseLostError): await execution
                else:
                    expired = await execution
                    await evidence("deadline-observed", values, service, group, physical_requests=sends,
                        actual_generation_http_calls=len(sends), case_after_wait=(await env.cases.get_case(case.case.id)).model_dump(mode="json"))
                    assert expired.status == "failed" and expired.error_code == "draft_commit_rejected"
                    assert expired.checkpoint.phase == "response_saved" and expired.checkpoint.receipt is None
                    assert await store.claim(project.id, claim.task.id, worker_id="deadline-terminal") is None
            finally:
                if execution and not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
            assert (await env.cases.get_case(case.case.id)).latest_version == 1
            assert await env.cases.get_version(case.version.id) == case.version
            if expiry == "lease":
                store.lease_seconds = 30
                recovered = await store.claim(project.id, claim.task.id, worker_id="lock-recovered")
                final = await service.execute(recovered)
                assert final.status == "completed" and (await env.cases.get_case(case.case.id)).latest_version == 2
            assert len(sends) == 1
            await evidence(expiry+"-rollback", values, service, group, physical_requests=sends,
                actual_generation_http_calls=1, actual_case_row_lock_expired_budget=expiry,
                draft_and_receipt_rolled_back_together=True, saved_response_recovery_no_send=expiry == "lease")


@pytest.mark.asyncio
@LIVE
async def test_actual_removed_candidate_and_input_budget_do_not_send_or_disable(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        removed = await accepted(values, change="removed", points=[], case_links=[])
        async def forbidden(*args, **kwargs): raise AssertionError("retirement must not invoke")
        group = await service.submit(project.id, doc.id, task_request(removed, case))
        _, tasks = await service.read_group(project.id, doc.id, group.id)
        with monkeypatch.context() as patch:
            patch.setattr(service._generator, "invoke_frozen", forbidden)
            final = await service.execute(await store.claim(project.id, tasks[0].id, worker_id="retirement"))
        assert final.status == "completed" and final.checkpoint.receipt["kind"] == "retirement_candidate"
        assert not final.checkpoint.receipt["versions"]
        current = await env.cases.get_case(case.case.id)
        assert current.lifecycle_status == "active" and current.active_version_id == case.version.id and current.latest_version == 1
        await env.projects.archive(project.id)
        with monkeypatch.context() as patch:
            patch.setattr(service._sources, "capture", forbidden)
            patch.setattr(config, "get", forbidden)
            assert await service.submit(project.id, doc.id, task_request(removed, case)) == group
            assert await store.claim(project.id, final.id, worker_id="terminal") is None
        await evidence("retirement", values, service, group, actual_generation_http_calls=0,
            active_case_not_disabled=True, archived_original_request_replayed=True)
    async with generation_environment(tmp_path/"budget") as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            await config.save(project.id, GenerationTaskPolicy(max_input_bytes=1))
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            final = await service.execute(await store.claim(project.id, tasks[0].id, worker_id="budget"))
            assert final.status == "failed" and final.error_code == "input_budget_or_configuration_rejected"
            assert not sends and final.checkpoint is None and (await env.cases.get_case(case.case.id)).latest_version == 1
            await evidence("budget", values, service, group, actual_generation_http_calls=0, full_input_not_truncated=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_duplicate_key_rolls_back_all_new_drafts_and_retains_response(tmp_path):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        def conflicting(body):
            content = json.loads(responder(body))
            context = json.loads(body["messages"][-1]["content"])["evidence_context"]["incremental_generation"]
            if context["kind"] == "create":
                first = content["cases"][0]
                first["case_key"] = "atomic-new-before-conflict"
                second = deepcopy(first)
                second["case_key"] = case.case.case_key
                content["cases"].append(second)
            return json.dumps(content, ensure_ascii=False)
        with provider(responder=conflicting) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            group = await service.submit(project.id, doc.id, task_request(review, case,
                include_new_cases=True, new_case_mode_key="api_testing", max_new_cases=2))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            failed = await service.execute(await store.claim(project.id, tasks[1].id, worker_id="duplicate-key"))
            assert failed.status == "failed" and failed.error_code == "draft_commit_rejected"
            assert failed.checkpoint.phase == "response_saved" and failed.checkpoint.receipt is None
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS count FROM {env.cases._store._case_table} WHERE project_id=%s", (project.id,))
                assert cur.fetchone()["count"] == 1
                cur.execute(f"SELECT COUNT(*) AS count FROM {env.cases._store._version_table}")
                assert cur.fetchone()["count"] == 1
            assert await env.cases.get_version(case.version.id) == case.version
            assert await store.claim(project.id, failed.id, worker_id="duplicate-terminal") is None
            independent = await service.execute(await store.claim(project.id, tasks[0].id, worker_id="update-independent"))
            assert independent.status == "completed" and len(sends) == 2
            assert (await env.cases.get_case(case.case.id)).active_version_id == case.version.id
            await evidence("duplicate-rollback", values, service, group, physical_requests=sends,
                actual_generation_http_calls=2, first_new_draft_rolled_back_on_second_conflict=True,
                original_response_retained=True, independent_update_completed=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_claim_seed_cannot_be_replaced_and_current_model_guard_precedes_intent(tmp_path):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            claim = await store.claim(project.id, tasks[0].id, worker_id="seed-owner")
            altered = claim.model_copy(update={"seed": claim.seed.model_copy(update={"module_id": str(uuid4())})})
            with pytest.raises(ValueError, match="领取不能替换"):
                await service.execute(altered)
            current = await store.get(project.id, tasks[0].id)
            assert current.status == "running" and current.checkpoint is None and not sends
            await configure(env, endpoint, max_output_tokens=4321)
            final = await service.execute(claim)
            assert final.status == "failed" and final.error_code == "input_budget_or_configuration_rejected"
            assert final.checkpoint is None and not sends and (await env.cases.get_case(case.case.id)).latest_version == 1
            await evidence("seed-config-guard", values, service, group, actual_generation_http_calls=0,
                altered_seed_rejected_without_changing_owned_task=True, model_change_rejected_before_intent=True)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("object_kind", ["scope", "response"])
async def test_actual_rustfs_corruption_refuses_drafts_and_is_restored(tmp_path, monkeypatch, object_kind):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            group = await service.submit(project.id, doc.id, task_request(review, case))
            _, tasks = await service.read_group(project.id, doc.id, group.id)
            claim = await store.claim(project.id, tasks[0].id, worker_id="object-owner")
            reference = group.scope
            if object_kind == "response":
                save_response = store._save_response
                def lose_reply(*args, **kwargs):
                    save_response(*args, **kwargs)
                    raise ConnectionError("actual committed response reply lost before corruption")
                with monkeypatch.context() as patch:
                    patch.setattr(store, "_save_response", lose_reply)
                    with pytest.raises(GenerationCheckpointUnavailable): await service.execute(claim)
                reference = (await store.get(project.id, claim.task.id)).checkpoint.response
                expire(store, claim.task.id)
                claim = await store.claim(project.id, claim.task.id, worker_id="object-recovery")
            raw = (await env.objects.read_object_uri(reference.storage_uri))["content"]
            bucket, object_name = reference.storage_uri.removeprefix("rustfs://").split("/", 1)
            assert object_name.startswith("case-generation/") and project.id in object_name
            async def overwrite(content):
                await env.objects.store_uploaded_bytes(content=content, filename="owned-generation-fault.json",
                    object_prefix="case-generation/owned-fault", object_name=object_name, bucket_name=bucket,
                    content_type="application/json")
            try:
                await overwrite(raw+b"corruption")
                assert (await env.objects.read_object_uri(reference.storage_uri))["content"] == raw+b"corruption"
                final = await service.execute(claim)
                assert final.status == "failed" and final.checkpoint.receipt is None if final.checkpoint else final.status == "failed"
                assert final.error_code == ("input_validation_failed" if object_kind == "scope" else "response_validation_rejected")
            finally:
                await overwrite(raw)
            assert (await env.objects.read_object_uri(reference.storage_uri))["content"] == raw
            assert (await env.cases.get_case(case.case.id)).latest_version == 1 and len(sends) == (object_kind == "response")
            await evidence(object_kind+"-corruption", values, service, group, physical_requests=sends,
                actual_generation_http_calls=len(sends), actual_object_corruption_detected_and_restored=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_disabled_dispatch_keeps_original_group_readable_without_new_scope(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, service, store, config):
        env, project, doc, paths, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            request = task_request(review, case)
            group = await service.submit(project.id, doc.id, request)
            service._stream = None
            async def forbidden(*args, **kwargs): raise AssertionError("disabled/replayed submission must not capture or read current config")
            with monkeypatch.context() as patch:
                patch.setattr(service._sources, "capture", forbidden)
                patch.setattr(config, "get", forbidden)
                assert await service.submit(project.id, doc.id, request) == group
                assert (await service.read_group(project.id, doc.id, group.id))[0] == group
                with pytest.raises(RuntimeError, match="派发流尚未启用"):
                    await service.submit(project.id, doc.id, request.model_copy(update={"creation_key": "disabled-new"}))
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS count FROM {store._groups}")
                assert cur.fetchone()["count"] == 1
            assert not sends
            await evidence("disabled-dispatch", values, service, group, actual_generation_http_calls=0,
                original_group_readable_and_replayed=True, new_submission_rejected_before_scope=True)
