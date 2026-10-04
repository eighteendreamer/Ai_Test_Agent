from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.impact_calls import ImpactCallCheckpointUnavailable
from src.application.documents.impact_response_protocol import parse_impact_decision
from src.application.documents.impact_response_service import DocumentImpactResponseService
from src.application.documents.impact_sources import impact_record_hash
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.document_impact_calls import DocumentImpactCallInput, DocumentImpactCallSelection, DocumentImpactUnit
from src.schemas.document_impact_responses import DocumentImpactDecision
from test_document_impact_calls import call_service, prepared, provider
from test_document_impact_tasks import evidence, expire, task_environment
from test_document_impact_sources import draft


live = pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS, real document files and HTTP")


def response_service(env, tasks, store, calls):
    return DocumentImpactResponseService(calls=calls, store=store, objects=env.objects)


def finish(unit, manifest, views, case):
    citations = []
    for role, view in zip(("old", "new"), views):
        source = next(item for item in manifest.sources if role in item.roles)
        text = view.result.text
        citations.append({"source_id": source.id, "text_sha256": source.text_sha256,
            "start_char": 0, "end_char": len(text), "quote": text})
    case_citations = [{"version_id": case.version.id, "version_record_sha256": impact_record_hash(case.version),
        "root": "content_json", "path": path, "value": value} for path, value in [
            (["目标"], "验证订单保留7天"), (["steps", 11, "操作"], "完整步骤11"),
            (["深层扩展", "布尔"], False), (["深层扩展", "零"], 0), (["深层扩展", "空"], None)]]
    return {"protocol_version": "impact-response-v1", "unit": unit.model_dump(mode="json"),
        "action": "finish", "summary": "保留期限变化候选", "unit_outcome": "analyzed", "candidates": [{
            "key": "retention-change", "kind": "modified", "summary": "订单保留7天调整为9天",
            "reason": "固定新旧原文的期限不同", "document_citations": citations,
            "case_impacts": [{"version_id": case.version.id, "level": "major", "reason": "用例仍预期7天",
                "verification_points": ["保留期限边界"], "citations": case_citations}],
            "applicability_suggestion": "current", "uncertainties": ["部署生效状态未核实"]}],
        "unresolved": ["仅为当前单元候选，尚未评审"]}


def read(body):
    payload = json.loads(body["messages"][-1]["content"])
    return {"protocol_version": "impact-response-v1", "unit": payload["unit"], "action": "read",
        "summary": "需要固定新旧原文", "reads": {"ranges": [{"source_id": source["id"],
            "start_char": 0, "end_char": source["length_chars"]} for source in payload["fixed_source_catalog"]]}}


def simple_finish():
    return {"protocol_version": "impact-response-v1", "unit": {"kind": "case", "id": "fixture"},
        "action": "finish", "summary": "格式变化，无规则候选", "unit_outcome": "analyzed"}


@pytest.mark.parametrize("wrap", ["plain", "fenced", "prose"])
def test_protocol_accepts_existing_json_presentation_without_changing_fields(wrap):
    text = json.dumps(simple_finish(), ensure_ascii=False)
    if wrap == "fenced": text = "说明\n```json\n" + text + "\n```\n完成"
    if wrap == "prose": text = "说明：" + text + "\n完成"
    assert parse_impact_decision(text).model_dump(mode="json")["unit"] == simple_finish()["unit"]


@pytest.mark.parametrize("fault", ["duplicate", "nan", "multiple", "broken", "broken_fence", "array", "no_json"])
def test_ambiguous_or_broken_json_is_not_rescued_from_nested_objects(fault):
    text = json.dumps(simple_finish())
    if fault == "duplicate": text = text.replace('"action": "finish"', '"action": "read", "action": "finish"')
    if fault == "nan": text = text.replace('"summary": "', '"unresolved": [NaN], "summary": "', 1)
    if fault == "multiple": text = "说明" + text + text
    if fault == "broken": text = '{"outer":' + text
    if fault == "broken_fence": text = '```json\n{"outer":' + text + '\n```'
    if fault == "array": text = '[' + text + ']'
    if fault == "no_json": text = "只有说明文字"
    with pytest.raises(ValueError, match="唯一且符合协议"):
        parse_impact_decision(text)


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_actual_files_http_complete_candidates_immutable_checks_and_recovery(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension) as values:
        env, project, doc, paths, views, _, sources, configs, case, store, tasks = values
        await draft(env, project, "manual-gap", [{"source_type": "manual", "source_id": "human"}])
        holder = {}
        def responder(body):
            payload = json.loads(body["messages"][-1]["content"])
            assert payload["analysis_protocol"]["version"] == "impact-call-v2"
            assert "DocumentImpactRuleCandidate" in payload["analysis_protocol"]["response_schema"]["$defs"]
            assert {item["version_id"] for item in payload["fixed_case_catalog"]}=={item.version_id for item in holder["manifest"].cases}
            assert {item["id"] for item in payload["fixed_difference_catalog"]}=={f"{item.kind}:{item.index}" for item in holder["manifest"].differences}
            assert len(payload["primary"]["case"]["version"]["content_json"]["steps"]) == 12
            return "```json\n" + json.dumps(finish(holder["unit"], holder["manifest"], views, case), ensure_ascii=False) + "\n```"
        with provider(responder=responder) as (endpoint, sends, _, _):
            claim, manifest, selection, calls = await prepared(values, endpoint, adapter=adapter)
            holder.update(unit=selection.unit, manifest=manifest)
            def changed_schema(*args, **kwargs):
                raise AssertionError("new calls in a submitted task must use its fixed schema snapshot")
            with monkeypatch.context() as patch:
                patch.setattr(DocumentImpactDecision,"model_json_schema",changed_schema)
                invocation = await calls.prepare(claim, call_key="typed-response", selection=selection)
            _, request = await calls.read_request(project.id,doc.id,claim.task.id,invocation.id)
            assert json.loads(request.request.messages[-1]["content"])["analysis_protocol"]==claim.seed.response_protocol.model_dump(mode="json")
            original_response = await calls.invoke(claim, invocation.id)
            checking = response_service(env, tasks, store, calls)
            checkpoint, result = await checking.assess(claim, invocation.id)
            assert result.status == "unit_finished" and not result.issues
            assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
            assert result.decision.candidates[0].applicability_suggestion == "current"  # suggestion is not deployment proof
            assert result.decision.candidates[0].case_impacts[0].citations[-1].value is None
            assert result.basis_limitations and len(sends) == 1
            assert (await store.calls.get(project.id,doc.id,claim.task.id,call_id=invocation.id)) == original_response
            assert (await tasks.get(project.id,doc.id,claim.task.id)).status == "running"
            raw=(await env.objects.read_object_uri(checkpoint.result.storage_uri))["content"]
            assert checkpoint.result.sha256 == hashlib.sha256(raw).hexdigest()
            await env.cases.create_version(case.case.id, VersionRequest(**case.version.model_dump(include={
                "content_json","execution_notes","source_refs","model_key","prompt_version","skill_versions"})))
            current=await env.docs.get_document(doc.id)
            await env.docs.upload_version(doc.id,ApiDocVersionUploadRequest(project_id=project.id,request_id="restore-old",
                expected_parent_version_id=current.current_version_id,filename=paths[0].name,
                content_base64=base64.b64encode(paths[0].read_bytes()).decode()))
            env.impact_skill_file.unlink()
            expire(store,claim.task.id)
            restarted=PostgresDocumentImpactStore(env.settings)
            recovered=await restarted.claim(project.id,claim.task.id,worker_id="response-check-recovery")
            second_tasks=DocumentImpactTaskService(store=restarted,sources=sources,documents=env.docs,
                projects=env.projects,objects=env.objects,stream=None)
            second_calls=call_service(env,second_tasks,restarted)
            recovered_service=response_service(env,second_tasks,restarted,second_calls)
            async def forbidden(*args,**kwargs):raise AssertionError("saved response check must only read fixed stored objects")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime,"invoke",forbidden)
                patch.setattr(env.objects,"store_uploaded_bytes",forbidden)
                patch.setattr(configs,"get_settings",forbidden)
                patch.setattr(sources,"prepare_fixed",forbidden)
                patch.setattr("src.application.documents.impact_response_service.DocumentImpactResponseProtocol.assess",forbidden)
                assert await recovered_service.assess(recovered,invocation.id)==(checkpoint,result)
            assert len(sends)==1 and recovered.task.deadline_at==claim.task.deadline_at
            assert await restarted.responses.get(str(uuid4()),doc.id,claim.task.id,invocation.id) is None
            with pytest.raises(ContinuationLeaseLostError):await store.responses.save(claim,checkpoint)
            with pytest.raises(ValueError,match="不能覆盖"):
                await restarted.responses.save(recovered,checkpoint.model_copy(update={"result":checkpoint.result.model_copy(update={"sha256":"0"*64})}))
            await restarted.finish_failed(recovered,error_code="fixture_scope_not_complete",message="review candidates only")
            assert await recovered_service.read(project.id,doc.id,claim.task.id,invocation.id)==(checkpoint,result)
            assert await restarted.claim(project.id,claim.task.id,worker_id="terminal") is None
            with pytest.raises(ContinuationLeaseLostError):await restarted.responses.save(recovered,checkpoint)
            evidence(f"responses/{extension}-{adapter}/verification.json",checkpoint=checkpoint.model_dump(mode="json"),
                assessment=result.model_dump(mode="json"),actual_http_calls=len(sends),recovered_without_parse_or_send=True,
                default_model_judgement=False,independent_process_restart=False)
            directory=os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target=Path(directory)/"responses"/f"{extension}-{adapter}"
                for path in paths:(target/path.name).write_bytes(path.read_bytes())
                (target/"assessment.json").write_bytes(raw)


@pytest.mark.asyncio
@live
async def test_actual_read_then_continuation_uses_full_history_and_requested_fixed_ranges(tmp_path):
    async with task_environment(tmp_path) as values:
        env,project,doc,_,views,_,_,_,case,store,tasks=values
        holder={}
        def responder(body):
            payload=json.loads(body["messages"][-1]["content"])
            if not payload["additional_fixed_reads"]:return json.dumps(read(body),ensure_ascii=False)
            return json.dumps(finish(holder["unit"],holder["manifest"],views,case),ensure_ascii=False)
        with provider(responder=responder) as (endpoint,sends,_,_):
            claim,manifest,selection,calls=await prepared(values,endpoint)
            selection=DocumentImpactCallSelection(unit=selection.unit)
            holder.update(unit=selection.unit,manifest=manifest)
            first=await calls.prepare(claim,call_key="need-originals",selection=selection)
            await calls.invoke(claim,first.id)
            checking=response_service(env,tasks,store,calls)
            _,request_read=await checking.assess(claim,first.id)
            assert request_read.status=="read_requested" and len(sends)==1
            followup=await calls.prepare(claim,call_key="with-fixed-originals",selection=DocumentImpactCallSelection(
                unit=selection.unit,previous_call_id=first.id,**request_read.decision.reads.model_dump()))
            await calls.invoke(claim,followup.id)
            _,finished=await checking.assess(claim,followup.id)
            assert finished.status=="unit_finished" and len(sends)==2
            assert len(sends[-1]["messages"])==4  # system + original user + full assistant + new user
            evidence("responses/read-continuation.json",read=request_read.model_dump(mode="json"),
                finished=finished.model_dump(mode="json"),actual_http_calls=len(sends),auto_coordinator=False)


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("fault,code",[("quote","invalid_text_citation"),("path","invalid_case_citation"),
    ("foreign_case","invalid_case_citation"),("unit","unit_mismatch"),("unread_text","unread_text_citation"),
    ("unread_case","unread_case"),("normalized","unread_text_citation"),("duplicate","invalid_protocol"),
    ("multiple","invalid_protocol"),("unknown_read","invalid_fixed_read")])
async def test_real_response_rejection_does_not_change_call_or_resend(tmp_path,fault,code):
    async with task_environment(tmp_path) as values:
        env,project,doc,_,views,_,_,_,case,store,tasks=values
        holder={}
        def responder(body):
            data=finish(holder["unit"],holder["manifest"],views,case)
            if fault=="quote":data["candidates"][0]["document_citations"][0]["quote"]="fabricated"
            if fault=="path":data["candidates"][0]["case_impacts"][0]["citations"][0]["path"]=["steps",-1]
            if fault=="foreign_case":
                impact=data["candidates"][0]["case_impacts"][0];impact["version_id"]=str(uuid4())
                for cite in impact["citations"]:cite["version_id"]=impact["version_id"]
            if fault=="unit":data["unit"]={"kind":"case","id":str(uuid4())}
            if fault=="unknown_read":
                data=read(body);data["reads"]["ranges"][0]["source_id"]="foreign-project-source"
            text=json.dumps(data,ensure_ascii=False)
            if fault=="duplicate":text=text.replace('"action": "finish"','"action": "read", "action": "finish"')
            if fault=="multiple":text="说明"+text+text
            return text
        with provider(responder=responder) as (endpoint,sends,_,_):
            claim,manifest,selection,calls=await prepared(values,endpoint)
            if fault=="unread_text":selection=DocumentImpactCallSelection(unit=selection.unit)
            if fault=="unread_case":selection=DocumentImpactCallSelection(unit=DocumentImpactUnit(kind="segment",id=manifest.segments[0].id),segment_ids=[item.id for item in manifest.segments])
            if fault=="normalized":selection=DocumentImpactCallSelection(unit=DocumentImpactUnit(kind="difference",id="normalized:0"),case_version_ids=[case.version.id])
            holder.update(unit=selection.unit,manifest=manifest)
            invocation=await calls.prepare(claim,call_key=fault,selection=selection)
            response=await calls.invoke(claim,invocation.id)
            checking=response_service(env,tasks,store,calls)
            _,rejected=await checking.assess(claim,invocation.id)
            assert rejected.status=="rejected" and code in [issue.code for issue in rejected.issues]
            assert (await store.calls.get(project.id,doc.id,claim.task.id,call_id=invocation.id))==response
            assert await checking.assess(claim,invocation.id)==await checking.read(project.id,doc.id,claim.task.id,invocation.id)
            assert len(sends)==1
            evidence(f"responses/rejected-{fault}.json",assessment=rejected.model_dump(mode="json"),actual_http_calls=1)


@pytest.mark.asyncio
@live
async def test_concurrent_check_and_actual_pg_reply_loss_recover_original_object_without_send(tmp_path,monkeypatch):
    async with task_environment(tmp_path) as values:
        env,project,doc,_,views,_,sources,_,case,store,tasks=values
        holder={}
        with provider(responder=lambda _:json.dumps(finish(holder["unit"],holder["manifest"],views,case),ensure_ascii=False)) as (endpoint,sends,_,_):
            claim,manifest,selection,calls=await prepared(values,endpoint)
            holder.update(unit=selection.unit,manifest=manifest)
            invocation=await calls.prepare(claim,call_key="check-race",selection=selection)
            await calls.invoke(claim,invocation.id)
            checking=response_service(env,tasks,store,calls)
            checkpoints=await asyncio.gather(*[checking.assess(claim,invocation.id) for _ in range(5)])
            assert all(item==checkpoints[0] for item in checkpoints) and len(sends)==1
            followup=await calls.prepare(claim,call_key="lost-check-reply",selection=selection.model_copy(update={"previous_call_id":invocation.id}))
            await calls.invoke(claim,followup.id)
            save=store.responses.save
            async def lost_reply(*args,**kwargs):
                await save(*args,**kwargs)
                raise ConnectionError("actual response check PG commit succeeded, reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(store.responses,"save",lost_reply)
                with pytest.raises(ImpactCallCheckpointUnavailable):await checking.assess(claim,followup.id)
            saved=await store.responses.get(project.id,doc.id,claim.task.id,followup.id)
            assert saved is not None and len(sends)==2
            expire(store,claim.task.id)
            restarted=PostgresDocumentImpactStore(env.settings)
            recovered=await restarted.claim(project.id,claim.task.id,worker_id="check-reply-recovery")
            second_tasks=DocumentImpactTaskService(store=restarted,sources=sources,documents=env.docs,projects=env.projects,objects=env.objects,stream=None)
            checking=response_service(env,second_tasks,restarted,call_service(env,second_tasks,restarted))
            checkpoint,result=await checking.assess(recovered,followup.id)
            assert checkpoint==saved and result.status=="unit_finished" and len(sends)==2
            read_object=env.objects.read_object_uri
            async def corrupted(uri,**kwargs):
                read=await read_object(uri,**kwargs)
                return {**read,"content":read["content"]+b" "} if uri==checkpoint.result.storage_uri else read
            with monkeypatch.context() as patch:
                patch.setattr(env.objects,"read_object_uri",corrupted)
                with pytest.raises(ValueError,match="对象大小或哈希"):
                    await checking.read(project.id,doc.id,claim.task.id,followup.id)
            assert len(sends)==2
            evidence("responses/check-race-recovery.json",checkpoint=checkpoint.model_dump(mode="json"),
                concurrent_coroutines=5,actual_http_calls=len(sends),lost_committed_reply_recovered=True,corrupt_object_rejected=True)


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("mode",["length","empty","tool"])
async def test_partial_saved_http_response_stays_ineligible_without_candidate_parsing(tmp_path,mode):
    async with task_environment(tmp_path) as values:
        env,_,_,_,_,_,_,_,_,store,tasks=values
        with provider(mode=mode) as (endpoint,sends,_,_):
            claim,_,selection,calls=await prepared(values,endpoint)
            invocation=await calls.prepare(claim,call_key=mode,selection=selection)
            await calls.invoke(claim,invocation.id)
            _,result=await response_service(env,tasks,store,calls).assess(claim,invocation.id)
            assert result.status=="ineligible" and result.decision is None and len(sends)==1
            evidence(f"responses/ineligible-{mode}.json",assessment=result.model_dump(mode="json"),actual_http_calls=1)


@pytest.mark.parametrize("extension",["md","docx","pdf"])
@pytest.mark.parametrize("adapter",[False,True])
def test_committed_old_call_inputs_keep_hash_and_original_protocol(extension,adapter):
    path=Path(__file__).resolve().parents[2]/"docs"/"upgrade_2026-10-03"/"p4-calls-verified"/"calls"/f"{extension}-{adapter}"/"verification.json"
    old=json.loads(path.read_bytes())
    request=DocumentImpactCallInput.model_validate(old["request"])
    assert impact_record_hash(request)==old["saved"]["request"]["sha256"]
    assert json.loads(request.request.messages[-1]["content"])["analysis_protocol"]["version"]=="impact-call-v1"


@pytest.mark.asyncio
@live
@pytest.mark.parametrize("outcome",["analyzed","evidence_insufficient"])
async def test_finish_without_rule_candidates_preserves_declared_unit_outcome_not_global_coverage(tmp_path,outcome):
    async with task_environment(tmp_path) as values:
        env,project,doc,_,_,_,_,_,_,store,tasks=values
        def responder(body):
            payload=json.loads(body["messages"][-1]["content"])
            value=simple_finish();value.update(unit=payload["unit"],unit_outcome=outcome)
            if outcome=="evidence_insufficient":value.update(summary="无法确定业务影响",unresolved=["缺少部署证据及需求匹配确认"])
            return json.dumps(value,ensure_ascii=False)
        with provider(responder=responder) as (endpoint,sends,_,_):
            claim,_,selection,calls=await prepared(values,endpoint)
            invocation=await calls.prepare(claim,call_key=outcome,selection=selection)
            await calls.invoke(claim,invocation.id)
            _,result=await response_service(env,tasks,store,calls).assess(claim,invocation.id)
            assert result.status=="unit_finished" and result.decision.unit_outcome==outcome
            assert not result.decision.candidates and result.semantic_status=="pending_review"
            assert (await tasks.get(project.id,doc.id,claim.task.id)).status=="running" and len(sends)==1
            evidence(f"responses/outcome-{outcome}.json",assessment=result.model_dump(mode="json"),actual_http_calls=1,global_result_published=False)


@pytest.mark.asyncio
@live
async def test_unknown_check_object_write_keeps_original_response_and_retains_object_without_resend(tmp_path,monkeypatch):
    async with task_environment(tmp_path) as values:
        env,project,doc,_,views,_,_,_,case,store,tasks=values
        holder={}
        with provider(responder=lambda _:json.dumps(finish(holder["unit"],holder["manifest"],views,case),ensure_ascii=False)) as (endpoint,sends,_,_):
            claim,manifest,selection,calls=await prepared(values,endpoint)
            holder.update(unit=selection.unit,manifest=manifest)
            invocation=await calls.prepare(claim,call_key="unknown-check-object",selection=selection)
            original_response=await calls.invoke(claim,invocation.id)
            checking=response_service(env,tasks,store,calls)
            upload=env.objects.store_uploaded_bytes;retained=[]
            async def unknown_write(**kwargs):
                saved=await upload(**kwargs)
                assert kwargs["filename"]=="response-check.json"
                retained.append(saved["uri"])
                raise ConnectionError("actual object saved, reply lost")
            with monkeypatch.context() as patch:
                patch.setattr(env.objects,"store_uploaded_bytes",unknown_write)
                with pytest.raises(ImpactCallCheckpointUnavailable):await checking.assess(claim,invocation.id)
            assert await store.responses.get(project.id,doc.id,claim.task.id,invocation.id) is None
            assert (await store.calls.get(project.id,doc.id,claim.task.id,call_id=invocation.id))==original_response
            assert len(sends)==1 and retained
            checkpoint,result=await checking.assess(claim,invocation.id)
            assert result.status=="unit_finished" and len(sends)==1 and checkpoint.result.storage_uri!=retained[0]
            assert (await env.objects.read_object_uri(retained[0]))["content"]  # unknown writes remain for reconciliation
            assert not await store.responses.object_is_referenced(retained[0])
            evidence("responses/unknown-check-object.json",checkpoint=checkpoint.model_dump(mode="json"),
                retained_unconfirmed_object=retained[0],actual_http_calls=1,original_response_preserved=True)
