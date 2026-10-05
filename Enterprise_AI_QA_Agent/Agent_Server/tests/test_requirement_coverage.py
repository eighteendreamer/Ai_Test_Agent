"""Actual documents, generated full drafts and append-only public coverage reviews."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.application.documents.impact_sources import impact_record_hash
from src.application.test_cases.version_content import version_content_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_coverage_store import fixed_coverage
from src.schemas.case_generation_tasks import GenerationRetryRequest, GenerationTaskPolicy
from src.schemas.case_management import TestCaseReviewSubmitRequest as ReviewRequest, TestCaseActivateRequest as ActivateRequest
from src.schemas.project import ProjectCreateRequest
from src.schemas.requirements import RequirementCoverageRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_document_impact_sources import draft
from test_generation_tasks import generation_environment, task_request, evidence
from test_generation_retries import budget_failure
from test_incremental_generation_sources import accepted, responder
from test_requirement_reviews import LIVE


def test_duplicate_versions_and_points_rejected_without_model():
    link = dict(case_version_id=str(uuid4()), content_hash="1"*64, point_keys=["days"])
    for links in [[link, link], [{**link, "point_keys": ["days", "days"]}]]:
        with pytest.raises(ValidationError):
            RequirementCoverageRequest(request_id="original", note="已核对", case_links=links)
    assert RequirementCoverageRequest(request_id="clear", note="撤销关联", case_links=[]).case_links == []


def link_for(version, task_id=None, points=None):
    return dict(case_version_id=version.id, content_hash=version.content_hash,
        point_keys=points or ["retention"], generation_task_id=task_id)


def routes(base, version):
    stem = f"{base}/requirements/{version['requirement_id']}"
    return f"{stem}/versions/{version['id']}/coverage-reviews", f"{stem}/matrix"


async def generate(values, service, store, *, include_new=True):
    env, project, doc, _, case, *_ = values
    review = await accepted(values)
    group = await service.submit(project.id, doc.id, task_request(review, case,
        include_new_cases=include_new, new_case_mode_key="api_testing" if include_new else None))
    _, tasks = await service.read_group(project.id, doc.id, group.id)
    completed = [await service.execute(await store.claim(project.id, task.id, worker_id="coverage-drafts")) for task in tasks]
    assert all(task.status == "completed" for task in completed), [task.model_dump() for task in completed]
    versions = [await env.cases.get_version(task.checkpoint.receipt["versions"][0]["version_id"]) for task in completed]
    return review, group, completed, versions


async def save(name, values, generation, group, sends, **extra):
    await evidence("coverage-"+name, values, generation, group, physical_requests=sends,
        actual_generation_http_calls=len(sends), actual_public_coverage_api=True, **extra)
    if not (directory := os.getenv("QA_GENERATION_TASK_EVIDENCE_DIR")):
        return
    env, project, doc, _, _, req_store, _, snapshot, *_ = values
    dest = Path(directory)/("coverage-"+name)
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {req_store.coverage.table} WHERE project_id=%s ORDER BY created_at,id", (project.id,))
        records = [row["record"] for row in cur.fetchall()]
        cur.execute(f"SELECT record FROM {req_store._versions} ORDER BY version")
        req_versions = [row["record"] for row in cur.fetchall()]
        cur.execute(f"SELECT record FROM {env.cases._store._version_table} ORDER BY case_id,version")
        case_versions = [row["record"] for row in cur.fetchall()]
    for record in records:
        assert fixed_coverage(record).model_dump(mode="json") == record
    for record in case_versions:
        from src.schemas.case_management import TestCaseVersionRecord
        assert version_content_hash(TestCaseVersionRecord.model_validate(record)) == record["content_hash"]
    raw = (await env.objects.read_object_uri(snapshot.storage_uri))["content"]
    assert hashlib.sha256(raw).hexdigest() == snapshot.sha256
    (dest/"impact-result.json").write_bytes(raw)
    (dest/"coverage-db.json").write_text(json.dumps(dict(coverage=records, requirement_versions=req_versions,
        case_versions=case_versions, impact_sha256=snapshot.sha256), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_generated_drafts_reviewed_and_mapped_without_rewriting_requirement(tmp_path, extension):
    async with generation_environment(tmp_path, extension) as (values, generation, store, config):
        env, project, doc, _, case, req_store, requirements, snapshot, result, _, client, base = values
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            review, group, tasks, versions = await generate(values, generation, store)
            v1 = review["requirement_version"]
            route, matrix = routes(base, v1)
            for version in versions:
                await env.cases.submit_review(version.case_id, ReviewRequest(version_id=version.id, request_id="review-coverage"))
            original = (await client.get(matrix)).json()
            assert original["coverage_review"] is None
            request = dict(request_id="map-update", note="已阅读完整草稿，仅确认保留期验证点",
                case_links=[link_for(versions[0], tasks[0].id)])
            reply = await client.post(route, json=request)
            assert reply.status_code == 201, reply.text
            first = reply.json()
            assert first["requirement_version_sha256"] == v1["content_sha256"] and first["revision"] == 1
            assert first["case_links"][0]["version_record_sha256"] == impact_record_hash(versions[0])
            assert first["case_links"][0]["review_status"] == "pending_review"
            assert first["case_links"][0]["active_version_id"] == case.version.id
            assert first["case_links"][0]["generation"]["receipt"] == tasks[0].checkpoint.receipt
            current = (await client.get(matrix)).json()
            assert current["version"] == v1 and current["coverage_review"] == first
            assert [point["design_status"] for point in current["points"]] == ["design_covered", "uncovered"]
            assert all(point["execution_status"] == "not_assessed" for point in current["points"])
            assert current["deployment_status"] == "unknown"
            assert len(versions[0].content_json["steps"]) == 12 and versions[0].content_json["深层扩展"] == case.version.content_json["深层扩展"]
            # Real activation is explicit and does not rewrite the earlier coverage snapshot.
            for version in versions:
                old = (await env.cases.get_case(version.case_id)).active_version_id
                await env.cases.activate(version.case_id, ActivateRequest(version_id=version.id,
                    expected_active_version_id=old, request_id="activate-coverage"))
            request2 = dict(request_id="map-both", expected_coverage_id=first["id"], note="分别核对两个验证点",
                case_links=[link_for(versions[0], tasks[0].id), link_for(versions[1], tasks[1].id, ["boundary"])])
            reply = await client.post(route, json=request2)
            assert reply.status_code == 201, reply.text
            second = reply.json()
            assert all(link["review_status"] == "approved" for link in second["case_links"])
            pinned = (await client.get(matrix, params={"coverage_id": first["id"]})).json()
            assert pinned == current
            assert (await client.get(matrix, params={"coverage_id": "initial"})).json() == original
            assert [p["design_status"] for p in (await client.get(matrix)).json()["points"]] == ["design_covered"]*2
            later = await accepted(values, request_id="changed-requirement", expected_review_id=review["id"],
                requirement_id=v1["requirement_id"], expected_version_id=v1["id"], statement="订单保留十天", case_links=[])
            assert [p["design_status"] for p in (await client.get(matrix)).json()["points"]] == ["needs_review"]*2
            historic = (await client.get(matrix, params={"version_id":v1["id"], "coverage_id": second["id"]})).json()
            assert historic["version"] == v1 and historic["coverage_review"] == second
            assert (await client.get(matrix, params={"coverage_id": second["id"]})).status_code == 404
            clear = await client.post(route, json=dict(request_id="clear", expected_coverage_id=second["id"], note="撤销旧版本关联", case_links=[]))
            assert clear.status_code == 201, clear.text
            assert [p["design_status"] for p in (await client.get(matrix, params={"version_id":v1["id"]})).json()["points"]] == ["uncovered"]*2
            page = (await client.get(route, params={"limit":1})).json()
            assert page["has_more"] and page["items"][0] == clear.json()
            assert (await client.get(route, params={"limit":1,"offset":2})).json()["items"] == [first]
            assert (await requirements.list_versions(project.id, doc.id, v1["requirement_id"]))[0].model_dump(mode="json") == v1
            assert await env.cases.get_version(case.version.id) == case.version
            assert len(sends) == 2
            await save(extension, values, generation, group, sends, first=first, second=second, original_matrix=original,
                first_matrix=current, pinned_matrix=pinned, later_requirement=later, clear=clear.json(), no_coverage_http=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_commit_reply_lost_replayed_without_case_config_model_or_object_reads(tmp_path, monkeypatch):
    async with generation_environment(tmp_path) as (values, generation, store, config):
        env, project, doc, _, case, req_store, _, _, _, _, client, base = values
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            review, group, tasks, versions = await generate(values, generation, store, include_new=False)
            route, matrix = routes(base, review["requirement_version"])
            request = dict(request_id="lost-reply", note="显式确认草稿设计", case_links=[link_for(versions[0],tasks[0].id)])
            write = req_store.coverage._save
            def lost(*args):
                write(*args)
                raise ConnectionError("actual coverage commit reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(req_store.coverage, "_save", lost)
                assert (await client.post(route,json=request)).status_code == 503
            async def forbidden(*args, **kwargs): raise AssertionError("recovery must not read live evidence/config or send")
            def forbidden_sync(*args, **kwargs): raise AssertionError("recovery must not revalidate live case/receipt")
            with monkeypatch.context() as patch:
                patch.setattr(req_store.coverage, "_save", forbidden_sync)
                patch.setattr(env.cases, "get_version", forbidden)
                patch.setattr(generation._generator, "invoke_frozen", forbidden)
                patch.setattr(generation._sources, "capture", forbidden)
                patch.setattr(env.objects, "read_object_uri", forbidden)
                patch.setattr(config, "get", forbidden)
                replies = await asyncio.gather(*(client.post(route,json=request) for _ in range(4)))
                assert all(reply.status_code == 201 for reply in replies)
                saved = replies[0].json()
                assert all(reply.json() == saved for reply in replies)
                assert (await client.get(f"{base}/requirement-coverage-reviews/by-request", params={"request_id":request["request_id"]})).json() == saved
                assert (await client.post(route,json={**request,"note":"changed"})).status_code == 409
                await env.projects.archive(project.id)
                assert (await client.post(route,json=request)).json() == saved
                assert (await client.post(route,json={**request,"request_id":"after-archive"})).status_code == 409
                assert (await client.get(matrix)).json()["coverage_review"] == saved
            assert len(sends) == 1
            await save("lost-reply", values, generation, group, sends, coverage=saved, recovered_without_live_reads=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_first_creation_and_revision_races_project_points_and_removed_scope(tmp_path):
    async with generation_environment(tmp_path) as (values, generation, store, config):
        env, project, doc, _, case, _, _, _, result, _, client, base = values
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            review, group, tasks, versions = await generate(values,generation,store,include_new=False)
            route, matrix = routes(base, review["requirement_version"])
            req = dict(request_id="race-original",note="确认完整草稿",case_links=[link_for(versions[0],tasks[0].id)])
            replies = await asyncio.gather(*(client.post(route,json=req) for _ in range(4)))
            assert all(r.status_code == 201 for r in replies), [r.text for r in replies]
            first = replies[0].json()
            assert all(r.json() == first for r in replies)
            revisions = await asyncio.gather(*(client.post(route,json={**req,"request_id":f"race-{i}","expected_coverage_id":first["id"]}) for i in range(2)))
            assert sorted(r.status_code for r in revisions) == [201,409]
            current = next(r.json() for r in revisions if r.status_code == 201)
            foreign = await env.projects.create(ProjectCreateRequest(project_key="foreign-coverage",name="其他项目"))
            foreign_case = await draft(env,foreign,"foreign",[{"source_type":"manual","source_id":"notes"}])
            rejects = [dict(case_links=[link_for(foreign_case.version)]),
                dict(case_links=[link_for(versions[0],points=["missing"])]),
                dict(case_links=[{**link_for(versions[0]),"content_hash":"0"*64}]),
                dict(case_links=[link_for(versions[0],str(uuid4()))])]
            for i,changes in enumerate(rejects):
                reply=await client.post(route,json={**req,"request_id":f"reject-{i}","expected_coverage_id":current["id"],**changes})
                assert reply.status_code == 409, reply.text
            assert (await client.get(base.replace(project.id,foreign.id)+"/requirement-coverage-reviews/by-request",params={"request_id":req["request_id"]})).status_code == 404
            removed=await accepted(values,request_id="removed",expected_review_id=review["id"],
                requirement_id=review["requirement_version"]["requirement_id"],expected_version_id=review["requirement_version"]["id"],
                change="removed",points=[],case_links=[])
            removed_route,_=routes(base,removed["requirement_version"])
            assert (await client.post(removed_route,json={**req,"request_id":"removed-link"})).status_code == 409
            assert (await client.get(matrix,params={"coverage_id":"bad"})).status_code == 422
            assert len(sends)==1
            await save("race",values,generation,group,sends,first=first,revision=current,removed=removed)


@pytest.mark.asyncio
@LIVE
async def test_actual_receipt_version_and_coverage_tamper_rejected_originals_recover(tmp_path):
    async with generation_environment(tmp_path) as (values,generation,store,config):
        env,project,doc,_,case,req_store,_,_,_,_,client,base=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            review,group,tasks,versions=await generate(values,generation,store,include_new=False)
            route,matrix=routes(base,review["requirement_version"])
            req=dict(request_id="original",note="核对固定范围",case_links=[link_for(versions[0],tasks[0].id)])
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"SELECT checkpoint FROM {store._tasks} WHERE id=%s",(tasks[0].id,)); original=cur.fetchone()["checkpoint"]
            bad=deepcopy(original);bad["receipt"]["versions"][0]["content_hash"]="0"*64
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {store._tasks} SET checkpoint=%s::jsonb WHERE id=%s",(json.dumps(bad),tasks[0].id))
            assert (await client.post(route,json=req)).status_code==409
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {store._tasks} SET checkpoint=%s::jsonb WHERE id=%s",(json.dumps(original),tasks[0].id))
                cur.execute(f"SELECT record FROM {env.cases._store._version_table} WHERE id=%s",(versions[0].id,)); original_version=cur.fetchone()["record"]
            bad=deepcopy(original_version);bad["content_json"]["目标"]="tampered"
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {env.cases._store._version_table} SET record=%s::jsonb WHERE id=%s",(json.dumps(bad),versions[0].id))
            assert (await client.post(route,json=req)).status_code==409
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {env.cases._store._version_table} SET record=%s::jsonb WHERE id=%s",(json.dumps(original_version),versions[0].id))
            reply=await client.post(route,json=req);assert reply.status_code==201,reply.text
            saved=reply.json();bad={**saved,"request":{**saved["request"],"note":"tampered"}}
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {req_store.coverage.table} SET record=%s::jsonb WHERE id=%s",(json.dumps(bad),saved["id"]))
            assert (await client.get(matrix)).status_code==409
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {req_store.coverage.table} SET record=%s::jsonb WHERE id=%s",(json.dumps(saved),saved["id"]))
            assert (await client.get(matrix)).json()["coverage_review"]==saved
            assert len(sends)==1
            await save("tamper",values,generation,group,sends,coverage=saved,actual_isolated_tamper_reverted=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_saved_response_retry_case_and_manual_unavailable_state_can_be_explicitly_linked(tmp_path):
    async with generation_environment(tmp_path) as (values,generation,store,config):
        env,project,doc,_,case,req_store,_,_,_,_,client,base=values
        with provider(responder=responder) as (endpoint,sends,_,_):
            await configure(env,endpoint)
            group,parent=await budget_failure(values,generation,store,config,response=True)
            await config.save(project.id,GenerationTaskPolicy(max_input_bytes=1,max_response_bytes=8000000))
            retry=await generation.retry(project.id,doc.id,parent.id,GenerationRetryRequest(creation_key="coverage-retry"))
            final=await generation.execute(await store.claim(project.id,retry.task.id,worker_id="coverage-retry"))
            assert final.status=="completed" and len(sends)==1
            version=await env.cases.get_version(final.checkpoint.receipt["versions"][0]["version_id"])
            fixed_scope=await generation.read_scope(group)
            v=fixed_scope.requirement.model_dump(mode="json")
            route,matrix=routes(base,v)
            manual=await draft(env,project,"manual-other-mode",[{"source_type":"manual","source_id":"notes"}],mode="ui_testing")
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f"UPDATE {env.cases._store._case_table} SET record=record-'version_reviews' WHERE id=%s",(manual.case.id,))
            req=dict(request_id="retry-manual",note="明确核对重试草稿与人工用例",case_links=[link_for(version,final.id),link_for(manual.version,points=["boundary"])])
            reply=await client.post(route,json=req);assert reply.status_code==201,reply.text
            saved=reply.json()
            assert saved["case_links"][0]["generation"]["retry"]==retry.retry.model_dump(include={"id","sha256"})
            assert saved["case_links"][1]["review_status"]=="unavailable" and saved["case_links"][1]["review"] is None
            assert (await client.get(matrix)).json()["points"][1]["design_status"]=="design_covered"
            assert await store.get(project.id,parent.id)==parent and await store.claim(project.id,parent.id,worker_id="terminal") is None
            assert (await env.cases.get_case(case.case.id)).active_version_id==case.version.id
            await save("retry-manual",values,generation,group,sends,coverage=saved,parent=parent.model_dump(mode="json"),
                retry=retry.retry.model_dump(mode="json"),full_retry_version=version.model_dump(mode="json"),saved_response_reused=True)
