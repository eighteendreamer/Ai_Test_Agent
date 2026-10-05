"""Actual originals/processes/stores/HTTP: generation catalog is a read projection."""
from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.api.routes.task_pool import router
from src.application.task_pool_service import TaskPoolService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_task_pool_store import PostgresTaskPoolStore
from src.schemas.case_generation_tasks import GenerationTaskPolicy, GenerationRetryRequest
from src.schemas.project import ProjectCreateRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_generation_api import app_for
from test_generation_tasks import generation_environment, task_request, evidence, expire
from test_generation_retries import budget_failure
from test_incremental_generation_sources import accepted, responder
from test_requirement_reviews import LIVE
from test_task_pool_entries import session


@asynccontextmanager
async def catalog_environment(tmp_path, extension="md"):
    async with generation_environment(tmp_path, extension) as data:
        values, service, store, config = data
        env = values[0]
        fields = ["postgres_session_table", "postgres_message_table", "postgres_event_table",
                  "postgres_snapshot_table", "postgres_approval_table", "postgres_case_planning_task_table"]
        tables = ["up_gc_"+uuid4().hex[:12] for _ in fields]
        for field, table in zip(fields, tables): setattr(env.settings.database, field, table)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        env.sessions = PostgresSessionStore(env.settings)
        await env.sessions.initialize()
        await PostgresPlanningTaskStore(env.settings).initialize()
        app = app_for(service, config)
        app.state.task_pool_service = TaskPoolService(store=env.sessions, entries_store=PostgresTaskPoolStore(env.settings))
        app.include_router(router, prefix="/api/v1")
        try: yield data, app
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in reversed(tables): cur.execute(f'DROP TABLE "{table}"')


async def catalog_evidence(name,values,service,group,**extra):
    await evidence(name,values,service,group,actual_public_catalog_api=True,**extra)
    child=extra.get("child_task")
    reference=(child.get("checkpoint") or {}).get("response") if child else None
    if reference and (directory:=os.getenv("QA_GENERATION_TASK_EVIDENCE_DIR")):
        raw=(await values[0].objects.read_object_uri(reference["storage_uri"]))["content"]
        assert hashlib.sha256(raw).hexdigest()==reference["sha256"] and len(raw)==reference["size_bytes"]
        (Path(directory)/name/(reference["sha256"]+".json")).write_bytes(raw)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_real_generation_catalog_states_filters_paging_origins_and_archived_read(tmp_path, extension):
    async with catalog_environment(tmp_path, extension) as ((values, service, store, config), app):
        env, project, doc, _, case, *_ = values
        review = await accepted(values)
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            normal = await service.submit(project.id, doc.id, task_request(review, case, include_new_cases=True, new_case_mode_key="api_testing"))
            _, modules = await service.read_group(project.id, doc.id, normal.id)
            completed = await service.execute(await store.claim(project.id, modules[0].id, worker_id="pool-normal"))
            assert completed.status == "completed" and len(sends) == 1
            version = await env.cases.get_version(completed.checkpoint.receipt["versions"][0]["version_id"])
            assert len(version.content_json["steps"]) == 12
            await config.save(project.id, GenerationTaskPolicy(max_input_bytes=1, transport_policy="single_attempt", call_timeout_seconds=15))
            failure_group = await service.submit(project.id, doc.id, task_request(review, case).model_copy(update={"creation_key":"pool-failure"}))
            _, failure_tasks = await service.read_group(project.id, doc.id, failure_group.id)
            failed = await service.execute(await store.claim(project.id, failure_tasks[0].id, worker_id="pool-failure"))
            assert failed.status == "failed" and len(sends) == 1
            await config.save(project.id, GenerationTaskPolicy(transport_policy="single_attempt", call_timeout_seconds=15))
            child = await service.retry(project.id, doc.id, failed.id, GenerationRetryRequest(creation_key="literal_%_'请求"))
            claim = await store.claim(project.id, child.task.id, worker_id="pool-child")
            other = await env.projects.create(ProjectCreateRequest(project_key="pool-foreign", name="另一项目"))
            for index in range(25):
                await session(env, project, "generation-session-"+str(index), updated_at=claim.task.updated_at+timedelta(seconds=1))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://actual-api") as client:
                async def page(**params):
                    result = await client.get("/api/v1/task-pool/entries", params=params)
                    assert result.status_code == 200, result.text
                    return result.json()
                first = await page(project_id=project.id, limit=24)
                second = await page(project_id=project.id, limit=24, offset=24)
                assert first["has_more"] and not second["has_more"]
                combined = first["items"]+second["items"]
                catalog = [item for item in combined if item["kind"] == "case_generation"]
                assert len(combined) == 31 and len(catalog) == 4  # 25 sessions + comparison + impact + 4 modules
                by_id = {item["task"]["id"]: item for item in catalog}
                assert set(by_id) == {completed.id, modules[1].id, failed.id, child.task.id}
                assert {item["task"]["status"] for item in catalog} == {"queued", "running", "completed", "failed"}
                summary = by_id[child.task.id]["task"]
                assert summary["retry"] == {"id":child.retry.id,"parent_task_id":failed.id,"creation_key":child.retry.creation_key,"strategy":"frozen_request"}
                assert summary["creation_key"] != child.retry.creation_key
                assert by_id[completed.id]["task"]["result_kind"] == "update"
                assert by_id[modules[1].id]["task"]["result_kind"] is None
                for state, ids in {"active":{modules[1].id,child.task.id},"completed":{completed.id},"failed":{failed.id}}.items():
                    assert {item["task"]["id"] for item in (await page(kind="case_generation",state=state))["items"]} == ids
                for query in (child.task.id, child.retry.creation_key, failure_group.id, failed.id, doc.title):
                    assert child.task.id in {item["task"]["id"] for item in (await page(kind="case_generation", query=query))["items"]}
                assert not (await page(project_id=other.id, kind="case_generation"))["items"]
                assert not (await page(query="%' OR 1=1 --"))["items"]
                public = json.dumps(catalog)
                for private in ("seed","checkpoint","config","storage_uri","lease_token","worker_id","budget","response"):
                    assert f'"{private}"' not in public
                assert normal.scope.storage_uri not in public
                final = await service.execute(claim)
                assert final.status == "completed" and len(sends) == 2
                tied=(await page(project_id=project.id,kind="session",limit=100))["items"]
                identities=[entry["session"]["id"] for entry in tied]
                assert identities==sorted(identities)
                fixed = await page(project_id=project.id, kind="case_generation")
                rebuilt = TaskPoolService(store=env.sessions, entries_store=PostgresTaskPoolStore(env.settings))
                assert (await rebuilt.list_entries_page(project_id=project.id,kind="case_generation",limit=24)).model_dump(mode="json") == fixed
                await env.projects.archive(project.id)
                assert await page(project_id=project.id,kind="case_generation") == fixed
                assert await store.get(project.id,failed.id) == failed
                assert await store.claim(project.id,failed.id,worker_id="terminal-read") is None
                assert await store.claim(project.id,final.id,worker_id="terminal-read") is None
                assert len(sends) == 2
                await catalog_evidence("catalog-"+extension,values,service,normal,public_catalog=fixed,mixed_first_page=first,
                    mixed_second_page=second,retry=child.retry.model_dump(mode="json"),child_task=final.model_dump(mode="json"),
                    physical_requests=sends,draft=version.model_dump(mode="json"),catalog_reads_no_send=True)
                await catalog_evidence("catalog-"+extension+"-retry",values,service,failure_group,public_catalog=fixed,
                    retry=child.retry.model_dump(mode="json"),child_task=final.model_dump(mode="json"),physical_requests=sends,catalog_reads_no_send=True)
                await env.docs.update_document(doc.id,project_id=other.id,title="另一项目私有文档标题")
                rebound=await page(project_id=project.id,kind="case_generation")
                assert {entry["document_title"] for entry in rebound["items"]}=={"用例增量生成"}
                assert not (await page(project_id=project.id,query="另一项目私有文档标题"))["items"]


@pytest.mark.asyncio
@LIVE
async def test_saved_response_retry_catalog_reads_actual_relation_without_model_or_current_config(tmp_path, monkeypatch):
    async with catalog_environment(tmp_path) as ((values,service,store,config), app):
        env,project,doc,*_ = values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent = await budget_failure(values,service,store,config,response=True)
            await config.save(project.id,GenerationTaskPolicy(max_response_bytes=8000000))
            child = await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="saved-pool"))
            final = await service.execute(await store.claim(project.id,child.task.id,worker_id="saved-pool"))
            assert final.status == "completed" and len(sends) == 1
            async def forbidden(*args,**kwargs): raise AssertionError("catalog cannot invoke/read objects/config")
            with monkeypatch.context() as patch:
                patch.setattr(service._generator,"invoke_frozen",forbidden)
                patch.setattr(service._objects,"read_object_uri",forbidden)
                patch.setattr(config,"get",forbidden)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://actual-api") as client:
                    result = await client.get("/api/v1/task-pool/entries",params={"kind":"case_generation","query":child.task.id})
                    assert result.status_code == 200, result.text
                    summary = result.json()["items"][0]["task"]
                    assert summary["retry"]["strategy"] == "saved_response" and summary["retry"]["parent_task_id"] == parent.id
                    assert summary["result_kind"] == "update"
            assert len(sends) == 1 and await store.get(project.id,parent.id) == parent
            await catalog_evidence("catalog-saved-response",values,service,group,public_catalog=result.json(),
                physical_requests=sends,retry=child.retry.model_dump(mode="json"),child_task=final.model_dump(mode="json"),catalog_reads_no_send=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_inflight_unknown_remains_failed_in_catalog_and_never_resends(tmp_path):
    async with catalog_environment(tmp_path) as ((values,service,store,config),app):
        env,project,doc,_,case,*_ = values
        review=await accepted(values)
        with provider(responder=responder,gated=True) as (endpoint,sends,arrived,release):
            await configure(env,endpoint)
            group=await service.submit(project.id,doc.id,task_request(review,case))
            _,tasks=await service.read_group(project.id,doc.id,group.id)
            execution=asyncio.create_task(service.execute(await store.claim(project.id,tasks[0].id,worker_id="pool-inflight")))
            try:
                assert await asyncio.to_thread(arrived.wait,8)
                execution.cancel()
                with pytest.raises(asyncio.CancelledError): await execution
            finally: release.set()
            expire(store,tasks[0].id)
            final=await service.execute(await store.claim(project.id,tasks[0].id,worker_id="pool-unknown"))
            assert final.error_code=="call_outcome_unknown" and len(sends)==1
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://actual-api") as client:
                response=await client.get("/api/v1/task-pool/entries",params={"kind":"case_generation","state":"failed"})
                assert response.status_code==200,response.text
                entry=response.json()["items"][0]
                assert entry["task"]["id"]==final.id and entry["task"]["error_code"]=="call_outcome_unknown"
                assert entry["task"]["retry"] is None and entry["task"]["result_kind"] is None
                rejected=await client.post(f"/api/v1/projects/{project.id}/documents/{doc.id}/case-generation-tasks/{final.id}/retries",json={"creation_key":"pool-unknown-retry"})
                assert rejected.status_code==409 and "未知" in rejected.text
                assert len(sends)==1 and await store.get(project.id,final.id)==final
                await catalog_evidence("catalog-unknown",values,service,group,public_catalog=response.json(),
                    physical_requests=sends,actual_inflight_cancel=True,explicit_retry_rejected=True,catalog_reads_no_send=True)
