from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation
from sqlalchemy.exc import IntegrityError

from src.application.documents.difference_config_service import CONFIG_KEY, DocumentDifferenceConfigService
from src.application.documents.difference_service import DocumentDifferenceService
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_difference_task_handler import DocumentDifferenceTaskHandler
from src.runtime.postgres_document_difference_store import PostgresDocumentDifferenceStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.document_differences import DocumentDifferenceCommand, DocumentDifferenceSettings, DocumentDifferenceTaskRequest
from src.schemas.document_parsing import DocumentParseSnapshotView, DocumentParseTaskRequest
from src.schemas.project import ProjectCreateRequest
from test_document_differences import verify_spans, write_pair
from test_document_parse_tasks import parsing_environment

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_DIFFERENCE_TASKS") != "1",
    reason="isolated actual MySQL/PostgreSQL/RustFS durable document comparison")]


@asynccontextmanager
async def comparison_environment(tmp_path):
    async with parsing_environment(tmp_path) as (env, parsing, parse_store, _, outbox):
        parse_store.lease_seconds = 30
        store = PostgresDocumentDifferenceStore(env.settings, lease_seconds=30)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in store.tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        configs = DocumentDifferenceConfigService(config_store=env.configs, project_service=env.projects)
        service = DocumentDifferenceService(store=store, documents=env.docs, projects=env.projects,
            configs=configs, objects=env.objects, parsing=parsing, stream="isolated:comparison:" + uuid4().hex)
        owned, upload = [], env.objects.store_uploaded_bytes
        async def tracked(**kwargs):
            result = await upload(**kwargs)
            if kwargs["object_prefix"].startswith("document-differences/"):
                owned.append(result["uri"])
            return result
        env.objects.store_uploaded_bytes = tracked
        try:
            await service.initialize()
            yield env, service, store, configs, parsing, parse_store
        finally:
            # Own committed/checkpoint artifacts even if an assertion loses its response.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT checkpoint,failure_artifact FROM {store._tasks}")
                for row in cur.fetchall():
                    for field in ("checkpoint", "failure_artifact"):
                        if row[field]:
                            owned.append(row[field]["storage_uri"])
            for uri in set(owned):
                await env.objects.delete_object_uri(uri)
            # These tables were explicitly verified absent in this isolated fixture.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in store.tables:
                    cur.execute(f'DROP TABLE "{table}"')


async def inputs(env, parsing, parse_store, tmp_path, extension="md"):
    project = await env.projects.create(ProjectCreateRequest(project_key="comparison", name="持久文档比较验收"))
    paths = write_pair(tmp_path, extension)
    doc = await env.docs.upload_document(filename=paths[0].name, project_id=project.id, request_id=uuid4().hex,
        content_base64=base64.b64encode(paths[0].read_bytes()).decode())
    views = []
    for number in (1, 2):
        if number == 1:
            version = doc.current_version_id
        else:
            result = await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id,
                request_id="original-v2", expected_parent_version_id=doc.current_version_id, filename=paths[1].name,
                content_base64=base64.b64encode(paths[1].read_bytes()).decode()))
            version = result.version.id
        task = await parsing.submit(project.id, doc.id, DocumentParseTaskRequest(creation_key=f"parse-v{number}", version_id=version))
        completed = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="actual-parser"))
        assert completed.status == "completed"
        snapshot, result = await parsing.read_snapshot(project.id, doc.id, completed.snapshot_id)
        views.append(DocumentParseSnapshotView(snapshot=snapshot, result=result))
    request = DocumentDifferenceTaskRequest(creation_key="compare-v1-v2", old_snapshot_id=views[0].snapshot.id, new_snapshot_id=views[1].snapshot.id)
    return project, doc, paths, views, request


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_durable_result_freezes_mysql_settings_and_exact_snapshots(tmp_path, monkeypatch, extension):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, paths, views, request = await inputs(env, parsing, parse_store, tmp_path, extension)
        settings = DocumentDifferenceSettings()
        settings.policy.timeout_seconds, settings.policy.max_lines = 70, 1000
        config = await configs.save_settings(project.id, settings)
        assert MySQLApplicationConfigStore(env.settings).get(CONFIG_KEY, scope_key=project.id).payload["policy"]["timeout_seconds"] == 70
        async def forbidden(*args, **kwargs):
            raise AssertionError("提交/重放/读取历史不应读取当前策略或下载原解析正文")
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", forbidden)
            task = await service.submit(project.id, doc.id, request)
        settings.policy.timeout_seconds, settings.policy.max_lines = 80, 2000
        await configs.save_settings(project.id, settings)
        claim = await store.claim(project.id, task.id, worker_id="actual-comparison")
        assert claim.task.phase == "comparing"
        assert claim.seed.config == config and claim.seed.config.payload.policy.max_lines == 1000
        assert claim.seed.old_snapshot == views[0].snapshot and claim.seed.new_snapshot == views[1].snapshot
        completed = await service.execute(claim)
        assert completed.status == "completed"
        with monkeypatch.context() as patch:
            patch.setattr(configs, "get_settings", forbidden)
            assert (await service.submit(project.id, doc.id, request)).id == task.id
            snapshot, result = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
        assert snapshot.seed.config == config and result.policy.max_lines == 1000
        assert 0 < snapshot.effective_policy.timeout_seconds <= 70
        assert [(c.old.text, c.new.text) for c in result.raw.changes[0].characters] == [("7", "9")]
        verify_spans(result, *views)
        assert await store.claim(project.id, task.id, worker_id="terminal-replay") is None
        assert (await service.get_by_request(project.id, doc.id, request.creation_key)).id == task.id
        assert (await service.list_tasks(project.id, doc.id, version_id=views[1].snapshot.seed.version_id, limit=1)).items == [completed]
        assert (await service.list_snapshots(project.id, doc.id, version_id=views[0].snapshot.seed.version_id)).items == []
        assert (await service.list_snapshots(project.id, doc.id)).items == [snapshot]
        for view in views:
            assert await parsing.read_snapshot(project.id, doc.id, view.snapshot.id) == (view.snapshot, view.result)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {env.settings.database.postgres_task_outbox_table} WHERE payload->>'task_type'='document_difference'")
            commands = cur.fetchall()
            assert len(commands) == 1 and commands[0]["payload"]["task_id"] == task.id
            cur.execute(f"SELECT seed,old_snapshot_id,new_snapshot_id FROM {store._tasks} WHERE id=%s", (task.id,))
            row = cur.fetchone()
            assert str(row["old_snapshot_id"]) == views[0].snapshot.id and str(row["new_snapshot_id"]) == views[1].snapshot.id
            assert row["seed"]["config"]["payload"]["policy"]["timeout_seconds"] == 70
            assert "text" not in row["seed"]["old_snapshot"]
        await env.projects.archive(project.id)
        assert (await service.read_snapshot(project.id, doc.id, snapshot.id))[1] == result
        assert (await service.submit(project.id, doc.id, request)).id == task.id
        with pytest.raises(ValueError, match="archived"):
            await service.submit(project.id, doc.id, request.model_copy(update={"creation_key": "archived-new"}))
        with pytest.raises(ValueError, match="archived"):
            await configs.save_settings(project.id, settings)
        output = os.getenv("QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR")
        if output:
            directory = Path(output) / extension
            directory.mkdir(parents=True, exist_ok=True)
            for path in paths:
                (directory / path.name).write_bytes(path.read_bytes())
            for number, view in enumerate(views, 1):
                (directory / f"parse-v{number}.json").write_text(view.model_dump_json(indent=2), encoding="utf-8")
            (directory / "task.json").write_text(completed.model_dump_json(indent=2), encoding="utf-8")
            (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
            (directory / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")


async def test_actual_concurrent_request_is_atomic_and_source_fk_is_durable(tmp_path):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, views, request = await inputs(env, parsing, parse_store, tmp_path)
        tasks = await asyncio.gather(*(service.submit(project.id, doc.id, request) for _ in range(4)))
        assert len({task.id for task in tasks}) == 1
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS total FROM {store._tasks}")
            assert cur.fetchone()["total"] == 1
            cur.execute(f"SELECT count(*) AS total FROM {env.settings.database.postgres_task_outbox_table} WHERE payload->>'task_type'='document_difference'")
            assert cur.fetchone()["total"] == 1
        with pytest.raises(ValueError, match="同一比较请求身份"):
            await service.submit(project.id, doc.id, request.model_copy(update={"new_snapshot_id": views[0].snapshot.id}))
        # Exact owned source row; verified FK prevents losing either comparison input.
        with pytest.raises(IntegrityError) as failure:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {parse_store._snapshots} WHERE id=%s", (views[0].snapshot.id,))
        assert isinstance(failure.value.orig, ForeignKeyViolation)
        assert (await parsing.get_snapshot(project.id, doc.id, views[0].snapshot.id)) == views[0].snapshot


async def test_actual_checkpoint_recovery_fences_old_attempt_without_recomparing(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, _, views, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        first = await store.claim(project.id, task.id, worker_id="first-comparison")
        async def interrupted(*args, **kwargs):
            raise asyncio.CancelledError()
        with monkeypatch.context() as patch:
            patch.setattr(store, "finish", interrupted)
            with pytest.raises(asyncio.CancelledError):
                await service.execute(first)
        current = await service.get(project.id, doc.id, task.id)
        assert current.phase == "result_saved" and current.status == "running"
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._tasks} SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (task.id,))
        second = await store.claim(project.id, task.id, worker_id="recovered-comparison")
        assert second.snapshot and second.task.attempt_no == 2 and second.task.deadline_at == first.task.deadline_at
        with pytest.raises(ContinuationLeaseLostError):
            await store.finish(first)
        with pytest.raises(ValueError, match="不能改写"):
            await store.save_snapshot_checkpoint(second, second.snapshot.model_copy(update={"id": str(uuid4())}))
        async def forbidden(*args, **kwargs):
            raise AssertionError("已保存结果的恢复不得重新读取源件、比较或加载配置")
        with monkeypatch.context() as patch:
            patch.setattr(service._comparator, "compare", forbidden)
            patch.setattr(parsing, "read_snapshot", forbidden)
            patch.setattr(configs, "get_settings", forbidden)
            completed = await service.execute(second)
        assert completed.snapshot_id == second.snapshot.id and completed.status == "completed"
        assert (await service.read_snapshot(project.id, doc.id, completed.snapshot_id))[0] == second.snapshot
        assert await store.claim(project.id, task.id, worker_id="terminal") is None


async def test_actual_process_failure_trace_is_saved_and_readable(tmp_path):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        settings = DocumentDifferenceSettings()
        settings.policy.max_cpu_seconds = 0.000001
        await configs.save_settings(project.id, settings)
        task = await service.submit(project.id, doc.id, request)
        failed = await service.execute(await store.claim(project.id, task.id, worker_id="limited-comparison"))
        assert failed.status == "failed" and failed.error_code == "document_difference_process_failed" and failed.snapshot_id is None
        trace = await service.read_failure(project.id, doc.id, task.id)
        assert trace.exit_code is not None and "CPU" in trace.error
        actual = (await env.objects.read_object_uri(trace.artifact.storage_uri))["content"]
        assert json.loads(actual) == {"exit_code": trace.exit_code, "error": trace.error, "stdout": trace.stdout, "stderr": trace.stderr}
        assert await store.claim(project.id, task.id, worker_id="terminal") is None
        output = os.getenv("QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR")
        if output:
            directory = Path(output) / "failure"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "task.json").write_text(failed.model_dump_json(indent=2), encoding="utf-8")
            (directory / "trace.json").write_bytes(actual)


async def test_deadline_covers_input_reads_and_does_not_reset_on_recovery(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, configs, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        settings = DocumentDifferenceSettings()
        settings.policy.timeout_seconds = 0.05
        await configs.save_settings(project.id, settings)
        task = await service.submit(project.id, doc.id, request)
        original = parsing.read_snapshot
        async def stalled(*args, **kwargs):
            await asyncio.sleep(0.2)
            return await original(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(parsing, "read_snapshot", stalled)
            failed = await service.execute(await store.claim(project.id, task.id, worker_id="stalled-input"))
        assert failed.status == "failed" and failed.error_code == "document_difference_deadline" and failed.failure_artifact is None
        assert await store.claim(project.id, task.id, worker_id="terminal") is None


async def test_actual_project_isolation_seed_check_and_result_integrity(tmp_path):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, views, request = await inputs(env, parsing, parse_store, tmp_path)
        other = await env.projects.create(ProjectCreateRequest(project_key="other-compare", name="其他比较项目"))
        with pytest.raises(KeyError, match="当前项目"):
            await service.submit(other.id, doc.id, request)
        task = await service.submit(project.id, doc.id, request)
        claim = await store.claim(project.id, task.id, worker_id="actual-comparison")
        forged = claim.seed.model_copy(deep=True)
        forged.old_snapshot.size_bytes += 1
        with pytest.raises(ValueError, match="固定解析快照"):
            await store.submit(forged, creation_key="forged-source", request_hash=store.request_hash(request), stream=service._stream)
        completed = await service.execute(claim)
        snapshot, result = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
        with pytest.raises(KeyError, match="当前项目"):
            await service.read_snapshot(other.id, doc.id, snapshot.id)
        # Corrupt only this isolated owned metadata row; persisted body remains unchanged.
        modified = snapshot.model_copy(deep=True)
        modified.summary.raw_changes += 1
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._snapshots} SET record=%s::jsonb WHERE id=%s", (modified.model_dump_json(), snapshot.id))
        try:
            with pytest.raises(ValueError, match="固定输入或执行策略"):
                await service.read_snapshot(project.id, doc.id, snapshot.id)
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {store._snapshots} SET record=%s::jsonb WHERE id=%s", (snapshot.model_dump_json(), snapshot.id))
        assert (await service.read_snapshot(project.id, doc.id, snapshot.id))[1] == result
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS total FROM {store._tasks}")
            assert cur.fetchone()["total"] == 1


async def test_actual_outbox_rejection_rolls_back_task_and_input_references(tmp_path):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, views, request = await inputs(env, parsing, parse_store, tmp_path)
        table = env.settings.database.postgres_task_outbox_table
        # Same actual-constraint failure technique as test_case_plan_run_creation.
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT difference_injected_failure CHECK (payload->>'task_type' <> 'document_difference')")
        try:
            with pytest.raises(IntegrityError) as failure:
                await service.submit(project.id, doc.id, request)
            assert isinstance(failure.value.orig, CheckViolation)
            assert await store.get_by_request(project.id, doc.id, request.creation_key) is None
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS total FROM {store._tasks}")
                assert cur.fetchone()["total"] == 0
                cur.execute(f"SELECT count(*) AS total FROM {table} WHERE payload->>'task_type'='document_difference'")
                assert cur.fetchone()["total"] == 0
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT difference_injected_failure")
        assert (await service.submit(project.id, doc.id, request)).status == "queued"
        assert (await parsing.get_snapshot(project.id, doc.id, views[0].snapshot.id)) == views[0].snapshot


async def test_transport_corruption_of_fixed_input_cannot_publish_a_result(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, views, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        original = env.objects.read_object_uri
        async def corrupted(uri):
            record = await original(uri)
            if uri == views[0].snapshot.storage_uri:
                return {**record, "content": record["content"] + b" "}
            return record
        async def forbidden(**kwargs):
            raise AssertionError("固定输入哈希不符时不能启动比较")
        with monkeypatch.context() as patch:
            patch.setattr(env.objects, "read_object_uri", corrupted)
            patch.setattr(service._comparator, "compare", forbidden)
            failed = await service.execute(await store.claim(project.id, task.id, worker_id="corrupt-transport"))
        assert failed.status == "failed" and failed.error_code == "document_difference_input_invalid"
        assert "大小或哈希不一致" in failed.message
        assert failed.snapshot_id is None and not (await service.list_snapshots(project.id, doc.id)).items
        assert (await parsing.read_snapshot(project.id, doc.id, views[0].snapshot.id)) == (views[0].snapshot, views[0].result)


@pytest.mark.parametrize("field", ["implementation_version", "python_version"])
async def test_queued_prior_engine_identity_is_readable_and_finishes_without_reexecution(tmp_path, monkeypatch, field):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        # A controlled queued-record upgrade fixture, not a claim that an old engine was run.
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed FROM {store._tasks} WHERE id=%s", (task.id,))
            seed = cur.fetchone()["seed"]
            seed[field] = "retired-fixture-version"
            cur.execute(f"UPDATE {store._tasks} SET seed=%s::jsonb WHERE id=%s", (json.dumps(seed), task.id))
        async def forbidden(*args, **kwargs):
            raise AssertionError("执行引擎不符时不能读取正文或计算")
        with monkeypatch.context() as patch:
            patch.setattr(parsing, "read_snapshot", forbidden)
            patch.setattr(service._comparator, "compare", forbidden)
            claim = await store.claim(project.id, task.id, worker_id="upgraded-worker")
            assert getattr(claim.seed, field) == "retired-fixture-version"
            failed = await service.execute(claim)
        assert failed.status == "failed" and failed.error_code == "document_difference_engine_changed"
        assert failed.snapshot_id is None and await store.claim(project.id, task.id, worker_id="terminal") is None


async def test_real_outbox_redis_worker_heartbeats_and_terminal_redelivery(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        # Cross an entire real lease interval, while allowing database/Windows
        # scheduling jitter. Without heartbeats this still expires before save.
        store.lease_seconds = 3
        calls, renewals = [], []
        compare, heartbeat = service._comparator.compare, store.heartbeat
        async def observed_compare(**kwargs):
            calls.append(1)
            await asyncio.sleep(3.2)
            return await compare(**kwargs)
        async def observed_heartbeat(claim):
            await heartbeat(claim)
            renewals.append(claim.attempt_id)
        monkeypatch.setattr(service._comparator, "compare", observed_compare)
        monkeypatch.setattr(store, "heartbeat", observed_heartbeat)
        group = "actual-comparison-" + uuid4().hex
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=service._stream, group=group)
        await queue.connect()
        worker = RedisTaskWorker.from_settings(queue, settings=env.settings, consumer="actual-comparison-worker",
            handler=DocumentDifferenceTaskHandler(service, store, worker_id="actual-comparison-worker"))
        try:
            assert await PostgresTaskOutbox(env.settings).relay_once(queue, limit=1) == 1
            assert await worker.run_once() == 1
            completed = await service.get(project.id, doc.id, task.id)
            assert completed.status == "completed" and completed.attempt_no == 1
            assert len(calls) == 1 and len(renewals) >= 2
            await queue.enqueue(DocumentDifferenceCommand(project_id=project.id, task_id=task.id).model_dump(mode="json"))
            assert await worker.run_once() == 1
            assert (await service.get(project.id, doc.id, task.id)) == completed
            assert len(calls) == 1
            snapshot, result = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
            assert result.raw.changes and snapshot.task_id == task.id
            output = os.getenv("QA_DOCUMENT_DIFFERENCE_TASK_EVIDENCE_DIR")
            if output:
                directory = Path(output) / "redis-worker"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "task.json").write_text(completed.model_dump_json(indent=2), encoding="utf-8")
                (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
                (directory / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
                (directory / "summary.json").write_text(json.dumps({"relay_published": 1, "worker_processed": 1,
                    "terminal_redelivery_processed": 1, "comparison_calls": len(calls), "heartbeat_renewals": len(renewals),
                    "attempt_no": completed.attempt_no}, indent=2), encoding="utf-8")
        finally:
            worker.stop()
            # This stream/group is unique and explicitly owned by the fixture.
            await queue._client.delete(queue.stream)
            await queue.close()


async def test_worker_lost_heartbeat_cancels_execution_and_preserves_unfinished_task(tmp_path, monkeypatch):
    from src.infrastructure.redis_task_queue import QueuedTask
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        store.lease_seconds = 0.3
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def blocked(**kwargs):
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        async def lost(claim):
            await entered.wait()
            raise ContinuationLeaseLostError("actual injected heartbeat ownership loss")
        monkeypatch.setattr(service._comparator, "compare", blocked)
        monkeypatch.setattr(store, "heartbeat", lost)
        command = DocumentDifferenceCommand(project_id=project.id, task_id=task.id)
        queued = QueuedTask(message_id="owned-fixture", payload=command.model_dump(mode="json"), retry_count=0)
        with pytest.raises(ContinuationLeaseLostError):
            await DocumentDifferenceTaskHandler(service, store, worker_id="lost-worker")(queued)
        assert cancelled.is_set()
        unfinished = await service.get(project.id, doc.id, task.id)
        assert unfinished.status == "running" and unfinished.snapshot_id is None
        assert not (await service.list_snapshots(project.id, doc.id)).items


async def test_document_rebinding_before_publish_cannot_release_original_project_result(tmp_path, monkeypatch):
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        other = await env.projects.create(ProjectCreateRequest(project_key="rebound-comparison", name="新归属项目"))
        task = await service.submit(project.id, doc.id, request)
        claim = await store.claim(project.id, task.id, worker_id="binding-worker")
        # Change only this isolated document between calculation and checkpoint.
        compare = service._comparator.compare
        async def rebound(**kwargs):
            result = await compare(**kwargs)
            await env.docs.update_document(doc.id, project_id=other.id)
            return result
        monkeypatch.setattr(service._comparator, "compare", rebound)
        with pytest.raises(ValueError, match="项目归属已变化"):
            await service.execute(claim)
        assert not (await store.list_snapshots(project.id, doc.id)).items
        failed = await service.execute(claim)
        assert failed.status == "failed" and failed.error_code == "document_binding_changed"
        # Restore fixture binding for its existing source cleanup; history was never rewritten.
        await env.docs.update_document(doc.id, project_id=project.id)
        assert await store.claim(project.id, task.id, worker_id="terminal") is None


async def test_actual_atomic_claim_deferral_and_typed_delivery(tmp_path):
    from pydantic import ValidationError
    from src.infrastructure.redis_task_queue import QueuedTask
    async with comparison_environment(tmp_path) as (env, service, store, _, parsing, parse_store):
        project, doc, _, _, request = await inputs(env, parsing, parse_store, tmp_path)
        task = await service.submit(project.id, doc.id, request)
        command = DocumentDifferenceCommand(project_id=project.id, task_id=task.id)
        handler = DocumentDifferenceTaskHandler(service, store, worker_id="delivery-worker")
        payload = command.model_dump(mode="json")
        with pytest.raises(ValidationError):
            await handler(QueuedTask(message_id="wrong-type", payload={**payload, "task_type": "document_parsing"}, retry_count=0))
        assert (await store.get(project.id, task.id)).attempt_no == 0
        claims = await asyncio.gather(*(store.claim(project.id, task.id, worker_id=f"atomic-{i}") for i in range(4)))
        active = [claim for claim in claims if claim is not None]
        assert len(active) == 1
        with pytest.raises(TaskDeferred):
            await handler(QueuedTask(message_id="duplicate-owned", payload=payload, retry_count=0))
        assert (await store.get(project.id, task.id)).attempt_no == 1
        completed = await service.execute(active[0])
        assert completed.status == "completed"
        await handler(QueuedTask(message_id="terminal-duplicate", payload=payload, retry_count=0))
        assert (await store.get(project.id, task.id)) == completed
