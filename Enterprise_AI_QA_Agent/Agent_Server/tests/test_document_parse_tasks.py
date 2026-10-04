from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI

from src.application.documents.parser_config_service import DocumentParserConfigService
from src.application.documents.parsing_service import DocumentParsingService
from src.api.routes.document_parsing import router
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.document_parse_task_handler import DocumentParseTaskHandler
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_parsing import DocumentParseTaskRequest, DocumentParsingSettings
from src.schemas.project import ProjectCreateRequest
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_management import TestCaseGenerateRequest as GenerateRequest
from src.schemas.case_planning import PlanningQuery
from src.application.test_cases.generation_pipeline import ProjectTestCaseContextProvider
from src.application.documents.api_doc_store import ApiDocConflictError
from src.application.security.upload_security_service import UploadSecurityService
from test_case_planning import environment
from test_document_parsers import actual_pdf, actual_docx

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_PARSERS") != "1",
    reason="isolated actual MySQL/PostgreSQL/RustFS")]


@asynccontextmanager
async def parsing_environment(tmp_path):
    async with environment(tmp_path, True) as env:
        env.settings.database.postgres_task_outbox_table = "up_dp_ob_" + uuid4().hex[:12]
        store = PostgresDocumentParseStore(env.settings, lease_seconds=1)
        tables = [*store.tables, env.settings.database.postgres_task_outbox_table]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        outbox = PostgresTaskOutbox(env.settings)
        await outbox.initialize()
        configs = DocumentParserConfigService(config_store=env.configs, project_service=env.projects)
        service = DocumentParsingService(store=store, documents=env.docs, projects=env.projects,
            configs=configs, objects=env.objects, stream="isolated:document-parsing:" + uuid4().hex)
        owned = []
        upload = env.objects.store_uploaded_bytes
        async def tracked(**kwargs):
            result = await upload(**kwargs)
            if kwargs["object_prefix"].startswith("document-parsing/"):
                owned.append(result["uri"])
            return result
        env.objects.store_uploaded_bytes = tracked
        try:
            await service.initialize()
            env.docs.set_parsing_service(service)
            yield env, service, store, configs, outbox
        finally:
            # Also own artifacts written by an independent Worker process.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT checkpoint,failure_artifact FROM {store._tasks}")
                for row in cur.fetchall():
                    for field in ("checkpoint", "failure_artifact"):
                        if row[field]:
                            owned.append(row[field]["storage_uri"])
            for uri in set(owned):
                await env.objects.delete_object_uri(uri)
            # Only explicitly verified isolated tables; release source references
            # before the outer document fixture deletes its own isolated assets.
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in tables:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}"')


async def upload(env, tmp_path, *, encrypted=False):
    project = await env.projects.create(ProjectCreateRequest(project_key="parse-task", name="持久解析验收"))
    path = tmp_path / "actual.pdf"
    actual_pdf(path, encrypted=encrypted)
    document = await env.docs.upload_document(filename=path.name, project_id=project.id, request_id=uuid4().hex,
        content_base64=base64.b64encode(path.read_bytes()).decode())
    return project, document, path


async def test_actual_fixed_original_to_durable_snapshot_and_terminal_replay(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, outbox):
        project, doc, path = await upload(env, tmp_path)
        unparsed = await env.docs.read_document_content(doc.id)
        assert unparsed["read_error"] and unparsed["content"] == "" and "%PDF" not in unparsed["content"]
        request = DocumentParseTaskRequest(creation_key="actual-first", version_id=doc.current_version_id)
        submitted = await service.submit(project.id, doc.id, request)
        changed = DocumentParsingSettings()
        changed.options.pdf_extraction_mode = "layout"
        await configs.save_settings(project.id, changed)
        claim = await store.claim(project.id, submitted.id, worker_id="actual-worker")
        assert claim.seed.options.pdf_extraction_mode == "plain"
        completed = await service.execute(claim)
        assert completed.status == "completed" and completed.snapshot_id
        snapshot, result = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
        assert result.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert "Order retention: 7 days." in result.text
        assert snapshot.seed.version_id == doc.current_version_id
        assert snapshot.seed.config.scope_key == "global" and result.options.pdf_extraction_mode == "plain"
        parsed_content = await env.docs.read_document_content(doc.id)
        assert parsed_content["parse_snapshot"]["id"] == snapshot.id and parsed_content["content"] == result.text
        context = await ProjectTestCaseContextProvider(api_docs_service=env.docs, knowledge_graph_service=None,
            session_store=None).collect(project=project, request=GenerateRequest(objective="根据订单文档生成用例", mode_key="api_testing", api_doc_ids=[doc.id],
                include_knowledge_graph=False, include_history=False))
        assert context["source_refs"][0]["metadata"]["parse_snapshot"]["id"] == snapshot.id
        evidence, _ = await env.sources.query(project.id, PlanningQuery(kind="document", source_id=doc.id), 10)
        assert evidence[0].payload["parse_snapshot"]["id"] == snapshot.id
        with pytest.raises(ApiDocConflictError, match="持久业务引用"):
            await env.docs.delete_document(doc.id)
        assert (await env.objects.read_object_uri(doc.original_asset.storage_uri))["content"] == path.read_bytes()
        assert await store.claim(project.id, submitted.id, worker_id="another-worker") is None
        restarted = DocumentParsingService(store=PostgresDocumentParseStore(env.settings), documents=env.docs,
            projects=env.projects, configs=configs, objects=env.objects, stream=None)
        assert (await restarted.submit(project.id, doc.id, request)).id == submitted.id
        assert (await restarted.read_snapshot(project.id, doc.id, snapshot.id))[1] == result
        next_task = await service.submit(project.id, doc.id, request.model_copy(update={"creation_key": "actual-second"}))
        second = await service.execute(await store.claim(project.id, next_task.id, worker_id="actual-worker"))
        next_snapshot, next_result = await service.read_snapshot(project.id, doc.id, second.snapshot_id)
        assert next_snapshot.id != snapshot.id and next_result.options.pdf_extraction_mode == "layout"
        assert (await service.read_snapshot(project.id, doc.id, snapshot.id))[1].options.pdf_extraction_mode == "plain"
        path.write_bytes(path.read_bytes().replace(b"7 days", b"9 days"))
        version2 = await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id,
            request_id="new-original", expected_parent_version_id=doc.current_version_id, filename=path.name,
            content_base64=base64.b64encode(path.read_bytes()).decode()))
        assert (await env.docs.read_document_content(doc.id))["read_error"]
        historical = await env.docs.read_version_content(doc.id, doc.current_version_id)
        assert "7 days" in historical["content"] and "9 days" not in historical["content"]
        assert historical["parse_snapshot"]["seed"]["version_id"] == doc.current_version_id
        third_task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(creation_key="new-version", version_id=version2.version.id))
        await service.execute(await store.claim(project.id, third_task.id, worker_id="actual-worker"))
        assert "9 days" in (await env.docs.read_document_content(doc.id))["content"]
        assert "7 days" in (await service.read_snapshot(project.id, doc.id, snapshot.id))[1].text


async def test_actual_parallel_submission_is_one_task_and_one_outbox(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, _, _):
        project, doc, _ = await upload(env, tmp_path)
        request = DocumentParseTaskRequest(creation_key="parallel", version_id=doc.current_version_id)
        tasks = await asyncio.gather(*(service.submit(project.id, doc.id, request) for _ in range(4)))
        assert len({item.id for item in tasks}) == 1
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {env.settings.database.postgres_task_outbox_table}")
            rows = cur.fetchall()
        assert len(rows) == 1 and rows[0]["payload"]["task_id"] == tasks[0].id
        wrong = request.model_copy(update={"version_id": str(uuid4())})
        with pytest.raises(ValueError, match="同一解析请求身份"):
            await service.submit(project.id, doc.id, wrong)
        other = await env.projects.create(ProjectCreateRequest(project_key="other-parse", name="其他项目"))
        with pytest.raises(KeyError, match="当前项目"):
            await service.submit(other.id, doc.id, request)
        assert await store.get(other.id, tasks[0].id) is None


async def test_actual_saved_result_recovers_without_reparsing_and_fences_old_attempt(tmp_path, monkeypatch):
    async with parsing_environment(tmp_path) as (env, service, store, _, _):
        project, doc, _ = await upload(env, tmp_path)
        task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(creation_key="recovery", version_id=doc.current_version_id))
        original_claim = await store.claim(project.id, task.id, worker_id="interrupted-worker")
        finish = store.finish
        async def interrupted(*args, **kwargs):
            raise asyncio.CancelledError()
        monkeypatch.setattr(store, "finish", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await service.execute(original_claim)
        monkeypatch.setattr(store, "finish", finish)
        checkpoint_task = await service.get(project.id, doc.id, task.id)
        assert checkpoint_task.status == "running" and checkpoint_task.phase == "result_saved"
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._tasks} SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (task.id,))
        current = await store.claim(project.id, task.id, worker_id="recovery-worker")
        assert current.snapshot is not None and current.task.attempt_no == 2
        assert current.task.deadline_at == original_claim.task.deadline_at
        assert current.remaining_seconds < original_claim.remaining_seconds
        with pytest.raises(ContinuationLeaseLostError):
            await store.finish(original_claim)
        async def forbidden(**kwargs):
            raise AssertionError("恢复已保存结果时不应再次解析")
        monkeypatch.setattr(service._parser, "parse", forbidden)
        completed = await service.execute(current)
        assert completed.snapshot_id == current.snapshot.id
        assert (await service.read_snapshot(project.id, doc.id, completed.snapshot_id))[1].text


async def test_actual_parser_failure_retains_trace_and_cannot_be_reclaimed(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, _, _):
        project, doc, _ = await upload(env, tmp_path, encrypted=True)
        task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(creation_key="failure", version_id=doc.current_version_id))
        failed = await service.execute(await store.claim(project.id, task.id, worker_id="failure-worker"))
        assert failed.status == "failed" and failed.error_code == "document_parser_failed" and failed.snapshot_id is None
        assert failed.failure_artifact
        raw = (await env.objects.read_object_uri(failed.failure_artifact.storage_uri))["content"]
        assert hashlib.sha256(raw).hexdigest() == failed.failure_artifact.sha256
        trace = json.loads(raw)
        assert "加密 PDF" in trace["error"] and trace["exit_code"] == 2
        assert "stdout" in trace and "stderr" in trace
        assert await store.claim(project.id, task.id, worker_id="retry-worker") is None


async def test_http_mysql_policy_outbox_redis_worker_and_actual_snapshot(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, outbox):
        project, doc, _ = await upload(env, tmp_path)
        app = FastAPI()
        app.state.document_parsing_service = service
        app.state.document_parser_config_service = configs
        app.include_router(router, prefix="/api/v1")
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=service._stream, group="parse-acceptance")
        await queue.connect()
        worker = RedisTaskWorker(queue, consumer="actual-parse-worker", block_ms=10,
            handler=DocumentParseTaskHandler(service, store, worker_id="actual-parse-worker"))
        prefix = f"/projects/{project.id}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1") as client:
            try:
                settings = (await client.get(prefix + "/document-parsing-settings")).json()
                settings["payload"]["options"]["pdf_extraction_mode"] = "layout"
                saved = await client.put(prefix + "/document-parsing-settings", json=settings["payload"])
                saved.raise_for_status()
                assert saved.json()["scope_key"] == project.id
                request = {"creation_key": "http-worker", "version_id": doc.current_version_id}
                path = prefix + f"/documents/{doc.id}/parse-tasks"
                response = await client.post(path, json=request)
                assert response.status_code == 202
                task_id = response.json()["id"]
                assert (await client.get(path + "/by-request", params={"creation_key": "http-worker"})).json()["id"] == task_id
                assert await outbox.relay_once(queue, limit=1) == 1
                assert await worker.run_once() == 1
                result = (await client.get(path + f"/{task_id}")).json()
                assert result["status"] == "completed"
                snapshot_response = await client.get(prefix + f"/documents/{doc.id}/parse-snapshots/{result['snapshot_id']}")
                snapshot_response.raise_for_status()
                body = snapshot_response.json()
                assert body["result"]["options"]["pdf_extraction_mode"] == "layout"
                assert "7 days" in body["result"]["text"]
                await queue.enqueue({"schema_version": 1, "task_type": "document_parsing", "project_id": project.id, "task_id": task_id})
                assert await worker.run_once() == 1
                assert (await client.get(path + f"/{task_id}")).json()["attempt_no"] == 1
                assert (await client.post(path, json={**request, "version_id": "invalid"})).status_code == 422
            finally:
                worker.stop()
                await queue._client.delete(queue.stream)
                await queue.close()


async def test_independent_worker_cli_reads_fixed_version_and_writes_actual_snapshot(tmp_path):
    from test_execution_worker_cli import child_environment
    async with parsing_environment(tmp_path) as (env, service, store, _, outbox):
        project, doc, _ = await upload(env, tmp_path)
        env.settings.orchestration.redis_task_outbox_streams = service._stream
        task = await service.submit(project.id, doc.id, DocumentParseTaskRequest(creation_key="cli-actual", version_id=doc.current_version_id))
        group = f"{env.settings.orchestration.redis_task_consumer_group}:document_parsing"
        queue = RedisTaskQueue(env.settings.database.redis_url, stream=service._stream, group=group)
        await queue.connect()
        try:
            assert await outbox.relay_once(queue, limit=1) == 1
            output = tmp_path / "worker.stdout"
            error = tmp_path / "worker.stderr"
            with output.open("wb") as stdout, error.open("wb") as stderr:
                process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "src.cli.run_document_parse_worker",
                    "--once", "--worker-id", "actual-cli-worker", "--stream", service._stream],
                    cwd=Path(__file__).resolve().parents[1], env=child_environment(env.settings), stdout=stdout, stderr=stderr)
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        await asyncio.to_thread(process.wait)
            stdout_text, stderr_text = output.read_text(encoding="utf-8"), error.read_text(encoding="utf-8")
            assert process.returncode == 0, stdout_text + stderr_text
            assert "document_parse_processed=1" in stdout_text
            assert "document_parse_task_finished" in stderr_text
            assert task.id in stderr_text and "document_parse_worker_runtime_started" in stderr_text
            completed = await service.get(project.id, doc.id, task.id)
            assert completed.status == "completed" and completed.attempt_no == 1
            snapshot, result = await service.read_snapshot(project.id, doc.id, completed.snapshot_id)
            assert snapshot.seed.version_id == doc.current_version_id and "7 days" in result.text
            evidence = os.getenv("QA_DOCUMENT_PARSE_TASK_EVIDENCE_DIR")
            if evidence:
                directory = Path(evidence)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "worker.stdout").write_text(stdout_text, encoding="utf-8")
                (directory / "worker.stderr").write_text(stderr_text, encoding="utf-8")
                (directory / "task.json").write_text(completed.model_dump_json(indent=2), encoding="utf-8")
                (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
                (directory / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        finally:
            await queue._client.delete(queue.stream)
            await queue.close()


@pytest.mark.parametrize("extension", ["md", "docx"])
async def test_actual_uploaded_requirement_document_parses_with_source_locations(tmp_path, extension):
    async with parsing_environment(tmp_path) as (env, service, store, _, _):
        project = await env.projects.create(ProjectCreateRequest(project_key="actual-requirement", name="需求原件验收"))
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        path = tmp_path / f"requirements.{extension}"
        if extension == "docx":
            actual_docx(path)
        else:
            path.write_bytes("# 订单查询\r\n有效期：7 天。\r\n".encode("utf-8-sig"))
        document = await env.docs.upload_document(filename=path.name, project_id=project.id, request_id=uuid4().hex,
            content_base64=base64.b64encode(path.read_bytes()).decode())
        assert (await env.objects.read_object_uri(document.original_asset.storage_uri))["content"] == path.read_bytes()
        task = await service.submit(project.id, document.id, DocumentParseTaskRequest(creation_key="actual-source", version_id=document.current_version_id))
        completed = await service.execute(await store.claim(project.id, task.id, worker_id="actual-requirement-worker"))
        result = await env.docs.read_document_content(document.id)
        assert not result["read_error"] and "7 天" in result["content"]
        assert result["parse_snapshot"]["id"] == completed.snapshot_id
        assert result["parse_structure"]["blocks"][0]["source"]["kind"] == ("line" if extension == "md" else "docx")
        if extension == "docx":
            assert result["parse_structure"]["tables"][0]["rows"][1][1]["text"] == "7 天"
        else:
            assert result["content"] == path.read_bytes().decode("utf-8-sig")
