"""Actual failed modules -> fresh leased tasks, preserving fixed evidence and sends."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_generation import generation_hash
from src.schemas.case_generation_tasks import GenerationGroup, GenerationRetryRequest, GenerationTaskPolicy
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.project import ProjectCreateRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_generation_api import app_for
from test_generation_tasks import generation_environment, task_request, evidence, expire
from test_incremental_generation_sources import accepted, responder
from test_requirement_reviews import LIVE


def test_saved_legacy_group_hash_and_scope_are_not_rewritten_by_retry_schema():
    path = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-generation-product-browser-delivery/generation-product.json"
    original = json.loads(path.read_text(encoding="utf-8"))["normal"]["group"]
    assert GenerationGroup.model_validate(original).model_dump(mode="json") == original


async def save_retry(name, values, service, store, group, retry, sends, **extra):
    name = "retry-acceptance-"+name
    await evidence(name, values, service, group, physical_requests=sends,
        actual_generation_http_calls=len(sends), actual_public_retry_api=extra.pop("actual_public_retry_api",False), **extra)
    if not (directory := os.getenv("QA_GENERATION_TASK_EVIDENCE_DIR")): return
    destination = Path(directory)/name
    env, project, doc, *_ = values
    view = await service.get_retry_by_request(project.id, doc.id, retry.retry.parent_task_id, retry.retry.creation_key)
    for reference in [group.scope, view.task.checkpoint.response if view.task.checkpoint else None, view.retry.response]:
        if reference:
            raw = (await env.objects.read_object_uri(reference.storage_uri))["content"]
            assert len(raw)==reference.size_bytes and hashlib.sha256(raw).hexdigest()==reference.sha256
            (destination/(reference.sha256+".json")).write_bytes(raw)
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {store._retries} WHERE id=%s",(view.retry.id,))
        pg_retry = cur.fetchone()["record"]
        cur.execute(f"SELECT seed,checkpoint,status FROM {store._tasks} WHERE id=%s",(view.task.id,))
        pg_task = dict(cur.fetchone())
        cur.execute(f"SELECT event_key,payload FROM {store._outbox} WHERE event_key=%s",("case-generation:"+view.task.id,))
        outbox = dict(cur.fetchone())
    assert pg_retry==view.retry.model_dump(mode="json") and outbox["payload"]["task_id"]==view.task.id
    assert pg_task["seed"]["retry"]=={"id":view.retry.id,"sha256":view.retry.sha256}
    (destination/"retry.json").write_text(json.dumps({"view":view.model_dump(mode="json"),
        "pg_retry":pg_retry,"pg_task":pg_task,"outbox":outbox},ensure_ascii=False,indent=2),encoding="utf-8")


async def budget_failure(values, service, store, config, *, response=False):
    env, project, doc, _, case, *_ = values
    review = await accepted(values)
    await config.save(project.id, GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15,
        **({"max_response_bytes":1} if response else {"max_input_bytes":1})))
    group = await service.submit(project.id,doc.id,task_request(review,case))
    _, tasks = await service.read_group(project.id,doc.id,group.id)
    parent = await service.execute(await store.claim(project.id,tasks[0].id,worker_id="actual-parent-failure"))
    assert parent.status=="failed" and parent.error_code==("response_validation_rejected" if response else "input_budget_or_configuration_rejected")
    return group,parent


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension",["md","docx","pdf"])
async def test_actual_public_retry_new_policy_uses_exact_old_full_request_and_keeps_parent(tmp_path,monkeypatch,extension):
    async with generation_environment(tmp_path,extension) as (values,service,store,config):
        env, project, doc, paths, case, *_ = values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent = await budget_failure(values,service,store,config)
            scope = await service.read_scope(group)
            assert not sends and parent.checkpoint is None
            policy = await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15,max_input_bytes=300000))
            base=f"/api/v1/projects/{project.id}/documents/{doc.id}/case-generation-tasks/{parent.id}/retries"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service,config)),base_url="http://actual-api") as client:
                reply=await client.post(base,json={"creation_key":"retry-original"})
                assert reply.status_code==202,reply.text
                view=await service.get_retry_by_request(project.id,doc.id,parent.id,"retry-original")
                assert view.retry.config==policy and view.retry.strategy=="frozen_request" and view.task.id!=parent.id
                assert view.retry.parent_task_sha256==generation_hash(parent.model_dump(mode="json"))
                async def forbidden(*args,**kwargs): raise AssertionError("execution must not recapture scope/config")
                claim=await store.claim(project.id,view.task.id,worker_id="explicit-retry")
                with monkeypatch.context() as patch:
                    patch.setattr(service._sources,"capture",forbidden)
                    patch.setattr(config,"get",forbidden)
                    final=await service.execute(claim)
                assert final.status=="completed" and len(sends)==1
                request=scope.modules[0].generation.invocation
                assert sends[0]["messages"]==[{"role":"system","content":request.system_prompt},*request.messages]
                version=await env.cases.get_version(final.checkpoint.receipt["versions"][0]["version_id"])
                assert len(version.content_json["steps"])==12 and version.content_json["深层扩展"]==case.version.content_json["深层扩展"]
                assert version.generation_metadata["incremental_generation"]["retry"]==claim.seed.retry.model_dump(mode="json")
                assert (await env.cases.get_case(case.case.id)).active_version_id==case.version.id
                assert await store.get(project.id,parent.id)==parent and await store.claim(project.id,parent.id,worker_id="old-terminal") is None
                assert await store.claim(project.id,final.id,worker_id="new-terminal") is None
                assert (await service.read_group(project.id,doc.id,group.id))==(group,[parent])
                assert await service.read_scope(group)==scope
                replay=await client.post(base,json={"creation_key":"retry-original"})
                assert replay.status_code==202 and replay.json()["task"]["status"]=="completed"
                assert (await client.get(base,params={"limit":1})).json()["items"][0]==replay.json()
                assert (await client.post(base,json={"creation_key":"illegal","checkpoint":{}})).status_code==422
                await save_retry("retry-"+extension,values,service,store,group,view,sends,
                    immutable_parent_kept=True,fixed_scope_kept=True,draft=version.model_dump(mode="json"),actual_public_retry_api=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_saved_response_retry_never_resends_and_old_failure_remains(tmp_path,monkeypatch):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,_,case,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config,response=True)
            assert len(sends)==1 and parent.checkpoint.response
            await config.save(project.id,GenerationTaskPolicy(max_input_bytes=1,max_response_bytes=8000000))
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="response-retry"))
            assert view.retry.strategy=="saved_response" and view.retry.response==parent.checkpoint.response
            async def forbidden(*args,**kwargs): raise AssertionError("stored response retry cannot call/capture current sources")
            with monkeypatch.context() as patch:
                patch.setattr(service._generator,"invoke_frozen",forbidden)
                patch.setattr(service._sources,"capture",forbidden)
                patch.setattr(config,"get",forbidden)
                final=await service.execute(await store.claim(project.id,view.task.id,worker_id="response-retry"))
            assert final.status=="completed" and final.checkpoint.response==parent.checkpoint.response and len(sends)==1
            assert await store.get(project.id,parent.id)==parent
            assert (await env.cases.get_case(case.case.id)).latest_version==2
            await save_retry("saved-response",values,service,store,group,view,sends,response_reused_without_new_http=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_inflight_unknown_and_nonfailed_modules_cannot_start_retry(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,_,case,*_=values
        review=await accepted(values)
        with provider(responder=responder,gated=True) as (endpoint,sends,arrived,release):
            await configure(env,endpoint)
            group=await service.submit(project.id,doc.id,task_request(review,case))
            _,tasks=await service.read_group(project.id,doc.id,group.id)
            with pytest.raises(ValueError,match="明确失败"): await service.retry(project.id,doc.id,tasks[0].id,GenerationRetryRequest(creation_key="queued"))
            execution=asyncio.create_task(service.execute(await store.claim(project.id,tasks[0].id,worker_id="inflight")))
            assert await asyncio.to_thread(arrived.wait,8)
            execution.cancel()
            with pytest.raises(asyncio.CancelledError): await execution
            release.set(); expire(store,tasks[0].id)
            parent=await service.execute(await store.claim(project.id,tasks[0].id,worker_id="unknown"))
            assert parent.error_code=="call_outcome_unknown" and len(sends)==1
            base=f"/api/v1/projects/{project.id}/documents/{doc.id}/case-generation-tasks/{parent.id}/retries"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service,config)),base_url="http://actual-api") as client:
                rejected=await client.post(base,json={"creation_key":"unknown-retry"})
                assert rejected.status_code==409 and "未知" in rejected.text
                assert (await client.get(base)).json()["items"]==[]
            assert await store.get(project.id,parent.id)==parent and len(sends)==1
            await evidence("unknown-retry",values,service,group,physical_requests=sends,actual_generation_http_calls=1,
                actual_inflight_cancel=True,explicit_new_retry_rejected=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_same_request_race_lost_commit_reply_and_recovery_without_config(tmp_path,monkeypatch):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config)
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15))
            request=GenerationRetryRequest(creation_key="four-way")
            views=await asyncio.gather(*[service.retry(project.id,doc.id,parent.id,request) for _ in range(4)])
            assert len({v.task.id for v in views})==1
            view=views[0]
            submit=store._submit_retry
            def lose_reply(*args,**kwargs):
                submit(*args,**kwargs)
                raise ConnectionError("actual committed retry/task/Outbox reply lost")
            lost=GenerationRetryRequest(creation_key="lost-commit")
            with monkeypatch.context() as patch:
                patch.setattr(store,"_submit_retry",lose_reply)
                base=f"/api/v1/projects/{project.id}/documents/{doc.id}/case-generation-tasks/{parent.id}/retries"
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service,config)),base_url="http://actual-api") as client:
                    reply=await client.post(base,json=lost.model_dump())
                    assert reply.status_code==503
            async def forbidden(*args,**kwargs): raise AssertionError("same request recovery must not read current policy/sources")
            service._stream=None
            with monkeypatch.context() as patch:
                patch.setattr(config,"get",forbidden)
                patch.setattr(service._sources,"capture",forbidden)
                recovered=await service.retry(project.id,doc.id,parent.id,lost)
                assert recovered==await service.get_retry_by_request(project.id,doc.id,parent.id,lost.creation_key)
                with pytest.raises(RuntimeError,match="派发"): await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="disabled-new"))
            page=await service.list_retries(project.id,doc.id,parent.id,limit=1)
            assert page.has_more and len((await service.list_retries(project.id,doc.id,parent.id,limit=1,offset=1)).items)==1
            claims=await asyncio.gather(*[store.claim(project.id,view.task.id,worker_id=f"race-{i}") for i in range(4)])
            assert sum(c is not None for c in claims)==1
            assert (await service.execute(next(c for c in claims if c))).status=="completed" and len(sends)==1
            assert await store.get(project.id,parent.id)==parent
            await save_retry("race-lost-reply",values,service,store,group,view,sends,
                actual_four_way_submit=True,actual_four_way_claim=True,actual_retry_commit_reply_lost=True,
                recovered_retry=recovered.model_dump(mode="json"),actual_public_retry_api=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_parent_version_conflict_reuses_response_and_preserves_both_failures(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,_,case,*_=values
        review=await accepted(values)
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group=await service.submit(project.id,doc.id,task_request(review,case))
            later=await env.cases.create_version(case.case.id,VersionRequest(**case.version.model_dump(include={
                "content_json","execution_notes","source_refs","model_key","prompt_version","skill_versions"})))
            _,tasks=await service.read_group(project.id,doc.id,group.id)
            parent=await service.execute(await store.claim(project.id,tasks[0].id,worker_id="conflict"))
            assert parent.error_code=="draft_commit_rejected" and parent.checkpoint.response and len(sends)==1
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="conflict-retry"))
            final=await service.execute(await store.claim(project.id,view.task.id,worker_id="unchanged-parent"))
            assert final.status=="failed" and final.error_code=="draft_commit_rejected" and len(sends)==1
            assert (await env.cases.get_case(case.case.id)).latest_version==2 and await env.cases.get_version(later.id)==later
            assert await store.get(project.id,parent.id)==parent
            await save_retry("parent-conflict",values,service,store,group,view,sends,expected_parent_not_rebased=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_retry_project_isolation_archive_and_fixed_sdk_options(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config)
            await config.save(project.id,GenerationTaskPolicy())
            with pytest.raises(ValueError,match="SDK"): await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="changed-sdk"))
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15))
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="original"))
            other=await env.projects.create(ProjectCreateRequest(project_key="retry-foreign",name="其他项目"))
            base=f"/api/v1/projects/{project.id}/documents/{doc.id}/case-generation-tasks/{parent.id}/retries"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(service,config)),base_url="http://actual-api") as client:
                foreign=base.replace(project.id,other.id)
                assert (await client.post(foreign,json={"creation_key":"foreign"})).status_code==404
                assert (await client.get(foreign)).status_code==404
                await env.projects.archive(project.id)
                assert (await client.get(base+"/by-request",params={"creation_key":"original"})).json()==view.model_dump(mode="json")
                assert (await client.post(base,json={"creation_key":"original"})).json()==view.model_dump(mode="json")
                assert (await client.post(base,json={"creation_key":"archive-new"})).status_code==409
            assert not sends
            await save_retry("archive-isolation",values,service,store,group,view,sends,
                original_archived_readable=True,foreign_rejected=True,fixed_sdk_not_replaced=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_retry_request_cannot_change_parent_and_new_model_cannot_replace_fixed_request(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,_,case,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config)
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15))
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="fixed-parent"))
            original=(await service.read_scope(group)).request
            await config.save(project.id,GenerationTaskPolicy(transport_policy="single_attempt",call_timeout_seconds=15,max_input_bytes=1))
            second=await service.submit(project.id,doc.id,task_request({"requirement_version":{
                "requirement_id":original.requirement_id,"id":original.requirement_version_id}},case).model_copy(update={"creation_key":"second-parent"}))
            _,tasks=await service.read_group(project.id,doc.id,second.id)
            other=await service.execute(await store.claim(project.id,tasks[0].id,worker_id="second-failure"))
            assert other.status=="failed"
            with pytest.raises(ValueError,match="不能替换原失败"): await service.retry(project.id,doc.id,other.id,GenerationRetryRequest(creation_key="fixed-parent"))
            await configure(env,endpoint,max_output_tokens=4321)
            final=await service.execute(await store.claim(project.id,view.task.id,worker_id="fixed-model"))
            assert final.status=="failed" and final.error_code=="input_budget_or_configuration_rejected" and final.checkpoint is None
            assert not sends and await store.get(project.id,parent.id)==parent
            await save_retry("parent-and-model-guard",values,service,store,group,view,sends,
                same_request_parent_change_rejected=True,current_model_change_not_substituted=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_copied_response_reference_cannot_be_substituted_and_restores_without_send(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config,response=True)
            await config.save(project.id,GenerationTaskPolicy())
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="copy-guard"))
            claim=await store.claim(project.id,view.task.id,worker_id="copy-owner")
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"SELECT checkpoint FROM {store._tasks} WHERE id=%s",(view.task.id,))
                checkpoint=cur.fetchone()["checkpoint"]
                replaced={**checkpoint,"response":group.scope.model_dump(mode="json")}
                cur.execute(f"UPDATE {store._tasks} SET checkpoint=%s::jsonb WHERE id=%s",(json.dumps(replaced),view.task.id))
            try:
                with pytest.raises(ValueError,match="不能替换复用的原响应"): await service.execute(claim)
            finally:
                with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                    cur.execute(f"UPDATE {store._tasks} SET checkpoint=%s::jsonb WHERE id=%s",(json.dumps(checkpoint),view.task.id))
            assert (await store.get(project.id,view.task.id)).status=="running" and len(sends)==1
            assert (await service.execute(claim)).status=="completed" and len(sends)==1
            assert await store.get(project.id,parent.id)==parent
            await save_retry("response-copy-guard",values,service,store,group,view,sends,
                actual_pg_child_response_substitution_rejected_and_restored=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_original_response_corruption_blocks_retry_submission_and_is_restored(tmp_path):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,*_=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,service,store,config,response=True)
            reference=parent.checkpoint.response
            raw=(await env.objects.read_object_uri(reference.storage_uri))["content"]
            bucket,object_name=reference.storage_uri.removeprefix("rustfs://").split("/",1)
            assert object_name.startswith("case-generation/") and project.id in object_name
            async def overwrite(content):
                await env.objects.store_uploaded_bytes(content=content,filename="owned-retry-fault.json",
                    object_prefix="case-generation/owned-retry-fault",object_name=object_name,bucket_name=bucket,content_type="application/json")
            try:
                await overwrite(raw+b"corruption")
                with pytest.raises(ValueError,match="大小或SHA"): await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="corrupted-response"))
                assert not (await service.list_retries(project.id,doc.id,parent.id)).items
            finally: await overwrite(raw)
            await config.save(project.id,GenerationTaskPolicy())
            view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="restored-response"))
            assert (await service.execute(await store.claim(project.id,view.task.id,worker_id="restored"))).status=="completed" and len(sends)==1
            await save_retry("response-object-guard",values,service,store,group,view,sends,
                actual_rustfs_original_response_corruption_rejected_and_restored=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_retirement_deadline_failure_retry_only_saves_candidate_without_generation(tmp_path,monkeypatch):
    async with generation_environment(tmp_path) as (values,service,store,config):
        env,project,doc,_,case,*_=values
        removed=await accepted(values,change="removed",points=[],case_links=[])
        await config.save(project.id,GenerationTaskPolicy(timeout_seconds=.001))
        group=await service.submit(project.id,doc.id,task_request(removed,case))
        _,tasks=await service.read_group(project.id,doc.id,group.id)
        claim=await store.claim(project.id,tasks[0].id,worker_id="retirement-expired")
        await asyncio.sleep(.02)
        parent=await service.execute(claim)
        assert parent.status=="failed" and parent.checkpoint is None
        await config.save(project.id,GenerationTaskPolicy())
        view=await service.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="candidate-retry"))
        async def forbidden(*args,**kwargs): raise AssertionError("retirement cannot invoke or read authoring")
        with monkeypatch.context() as patch:
            patch.setattr(service._generator,"invoke_frozen",forbidden)
            patch.setattr(service._sources,"capture",forbidden)
            final=await service.execute(await store.claim(project.id,view.task.id,worker_id="retirement-new"))
        assert final.status=="completed" and final.checkpoint.receipt["kind"]=="retirement_candidate" and not final.checkpoint.receipt["versions"]
        assert (await env.cases.get_case(case.case.id)).active_version_id==case.version.id and await store.get(project.id,parent.id)==parent
        await save_retry("retirement-retry",values,service,store,group,view,[],active_case_not_disabled=True)
