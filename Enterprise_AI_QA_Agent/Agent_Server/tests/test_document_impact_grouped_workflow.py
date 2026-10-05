"""Frozen group consumption and explicit evidence declarations over real files."""
from __future__ import annotations

import hashlib
import json
import os
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_reasoning_plan import DocumentImpactReasoningPlans
from src.application.documents.impact_reasoning_plan_service import DocumentImpactReasoningPlanService
from src.application.documents.impact_response_protocol import DocumentImpactResponseProtocol, capture_impact_response_protocol, _parse_decision
from src.application.documents.impact_sources import impact_record_hash
from src.application.documents.impact_workflow import capture_impact_workflow_protocol
from src.schemas.document_impact_grouped import DocumentImpactGroupedContext, DocumentImpactGroupedWireDecision
from src.schemas.document_impact_reasoning import DocumentImpactReasoningCheckpoint
from src.schemas.case_management import TestCaseBundle as CaseBundle
from test_document_impact_reasoning_plan import fixed_sample
from test_document_impact_text_anchors import anchored_input
from test_document_impact_workflow import workflow
from test_document_impact_responses import finish
from test_document_impact_responses import read
from src.schemas.document_impact_reasoning import DocumentImpactReasoningPolicy
from src.schemas.document_impact_calls import DocumentImpactInvocationPolicy
from src.application.documents.impact_checkpoint_io import ImpactCallCheckpointUnavailable
from test_document_impact_calls import prepared, provider
from test_document_impact_tasks import task_environment, evidence, expire
from impact_bound_test_support import bound_fixture_response


def grouped_input():
    seed, envelope, manifest, payload, response, record, wire = anchored_input()
    policy_seed, _, _ = fixed_sample()
    seed = seed.model_copy(update={"reasoning_settings": policy_seed.reasoning_settings,
        "response_protocol": capture_impact_response_protocol(version="impact-call-v6"),
        "workflow_protocol": capture_impact_workflow_protocol(version="impact-coordinator-v2")})
    plan = DocumentImpactReasoningPlans.build(seed, envelope, manifest, impact_record_hash(manifest))
    checkpoint = DocumentImpactReasoningCheckpoint(task_id=record.task_id, input_sha256=envelope.input_sha256,
        manifest_sha256=impact_record_hash(manifest), config_sha256=impact_record_hash(seed.reasoning_settings),
        storage_uri="rustfs://fixed/plan", sha256="0"*64, size_bytes=1, created_at=record.created_at)
    context = DocumentImpactGroupedContext(protocol=seed.workflow_protocol, stage="discover_rules",
        reasoning_checkpoint=checkpoint, discovery_task=plan.discovery[0])
    selection = plan.discovery[0].selection
    request = DocumentImpactCallService.build_request(seed, envelope, manifest, selection, coordination_context=context)
    payload = payload.model_copy(update={"selection": selection, "request": request})
    wire = {"protocol_version": "impact-response-v4", "action": "finish", "summary": "共同读取固定变化",
        "unit_outcome": "analyzed", "candidates": [], "evidence_outcomes": [
            {"member_ref": ref, "outcome": "analyzed", "reason": "完整原文或差异已对照"}
            for ref in json.loads(request.messages[-1]["content"])["reasoning_members"]]}
    return seed, envelope, manifest, record, payload, response, wire


def test_real_failed_scope_group_protocol_retains_eight_explicit_declarations():
    seed, envelope, manifest, record, payload, response, wire = grouped_input()
    assessed = DocumentImpactResponseProtocol.assess(seed, envelope, manifest, record, payload,
        response.model_copy(update={"text": json.dumps(wire, ensure_ascii=False)}))
    assert assessed.status == "unit_finished" and assessed.validator_version == "impact-response-validator-v2"
    assert assessed.decision.protocol_version == "impact-canonical-v2"
    assert len(assessed.decision.evidence_outcomes) == 8 and not assessed.decision.candidates
    assert [item.unit for item in assessed.decision.evidence_outcomes] == DocumentImpactCallService.units(manifest)[:8]


def test_actual_default_response_exposes_finish_outcome_contract():
    directory = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p4-default-model-grouped-v6-first"
    actual = json.loads((directory / "logical-response-1.json").read_text(encoding="utf-8"))
    # Actual response had complete member declarations but null task outcome.
    # Do not repair or reinterpret that historical model answer.
    with pytest.raises(ValueError, match="唯一且符合协议"):
        _parse_decision(actual["text"], DocumentImpactGroupedWireDecision)
    protocol = capture_impact_response_protocol(version="impact-call-v6")
    assert "unit_outcome is required" in protocol.instruction
    assert "never null" in protocol.instruction
    assert "Required non-null for finish" in protocol.response_schema["properties"]["unit_outcome"]["description"]


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "foreign", "reorder", "read_final"])
def test_group_cannot_infer_or_replace_member_coverage(mutation):
    seed, envelope, manifest, record, payload, response, wire = grouped_input()
    values = wire["evidence_outcomes"]
    if mutation == "omit": values.pop()
    elif mutation == "duplicate": values[-1] = values[0]
    elif mutation == "foreign": values[-1]["member_ref"] = "D999"
    elif mutation == "reorder": values.reverse()
    else:
        wire.update(action="read", unit_outcome=None, reads={"segment_refs": ["P1"]})
    assessed = DocumentImpactResponseProtocol.assess(seed, envelope, manifest, record, payload,
        response.model_copy(update={"text": json.dumps(wire, ensure_ascii=False)}))
    assert assessed.status == "rejected" and not assessed.decision


def grouped_responder(holder, views, case, *, omit=False, insufficient=False):
    def respond(body):
        payload = json.loads(body["messages"][-1]["content"])
        context = payload["coordination_context"]
        value = {"unit": payload["unit"], "action": "finish", "summary": "固定组/用例判断",
            "unit_outcome": "analyzed", "candidates": [], "evidence_outcomes": []}
        if context["stage"] == "discover_rules":
            members = context["discovery_task"]["members"]
            value["evidence_outcomes"] = [{"unit": unit, "outcome": "evidence_insufficient" if insufficient and index==0 else "analyzed",
                "reason": f"已完整对照第{index+1}项，仍需评审"} for index, unit in enumerate(members)]
            if omit: value["evidence_outcomes"].pop()
            proposal = finish(holder["first"], holder["manifest"], views, case)["candidates"][0]
            proposal["case_impacts"] = []
            supplied = payload["fixed_text_evidence"].values()
            if any(not any(item["quote"] == citation["quote"] for item in supplied)
                for citation in proposal["document_citations"]):
                return bound_fixture_response(body, read(body))
            value["candidates"] = [proposal]
        else:
            current = CaseBundle.model_validate(payload["primary"]["case"])
            assert len(current.version.content_json["steps"]) == 12
            assert current.version.content_json == case.version.content_json
            assert current.version.content_json["深层扩展"] == {"布尔": False, "零": 0, "空": None, "未知字段": "不能丢失"}
            assert current.version.execution_notes
            assert payload["additional_fixed_reads"]  # Original authoring source actually supplied.
            holder.setdefault("case_payloads", []).append(payload)
            impacts = finish(holder["first"], holder["manifest"], views, current)["candidates"][0]["case_impacts"]
            value["candidates"] = [{**rule["candidate"], "key": rule["id"], "case_impacts": impacts}
                for rule in context["rules"]]
        return bound_fixture_response(body, value)
    return respond


LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1", reason="requires actual files/stores/HTTP")


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_actual_group_discovery_complete_cases_and_terminal_restore(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension, protocol_version="impact-call-v6") as values:
        env, project, doc, paths, views, _, _, configs, case, store, tasks = values
        holder = {}
        with provider(responder=grouped_responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint, adapter=adapter)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, store)
            saved = await runner.execute(claim)
            assert saved.status == "completed"
            snapshot, result = await runner.read_result(project.id, doc.id, saved.id)
            assert result.implementation_version == "impact-coordinator-v2"
            assert len(result.discovery_tasks) == 1 and len(sends) == 2
            assert [item.unit for item in result.units] == calls.units(manifest)
            assert all(item.status == "analyzed" for item in result.units)
            assert all(not item.candidates and item.reason for item in result.units if item.unit.kind != "case")
            assert len(result.rules) == 1 and len(result.rules[0].origins) == 1
            assert result.rules[0].origins[0].checkpoint.validator_version == "impact-response-validator-v2"
            assert result.units[-1].candidates[0].case_impacts[0].citations[-1].value is None
            assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
            plan_check, plan = await DocumentImpactReasoningPlanService(tasks=tasks, store=store, objects=env.objects).read(
                project.id, doc.id, saved.id)
            assert result.reasoning_checkpoint == plan_check and plan.analysis_status == "not_started"
            async def forbidden(*args, **kwargs): raise AssertionError("terminal replay cannot invoke/build/upload/config")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                patch.setattr(configs, "get_reasoning_settings", forbidden)
                assert await runner.read_result(project.id, doc.id, saved.id) == (snapshot, result)
            assert await store.claim(project.id, saved.id, worker_id="terminal") is None
            with pytest.raises(KeyError): await runner.read_result(str(uuid4()), doc.id, saved.id)
            evidence(f"grouped/{extension}-{adapter}/verification.json", snapshot=snapshot.model_dump(mode="json"),
                result=result.model_dump(mode="json"), plan=plan.model_dump(mode="json"), actual_http_calls=len(sends),
                physical_requests=sends, default_model_judgement=False)
            if directory := os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR"):
                target = Path(directory)/"grouped"/f"{extension}-{adapter}"
                for path in paths: (target/path.name).write_bytes(path.read_bytes())
                (target/"result.json").write_bytes((await env.objects.read_object_uri(snapshot.storage_uri))["content"])


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("scenario", ["omit", "insufficient"])
async def test_actual_group_missing_member_and_individual_insufficiency_are_visible(tmp_path, scenario):
    async with task_environment(tmp_path, protocol_version="impact-call-v6") as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=grouped_responder(holder, views, case, omit=scenario=="omit",
            insufficient=scenario=="insufficient")) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            saved = await workflow(env, tasks, store).execute(claim)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert result.status == "partial"
            if scenario == "omit":
                assert result.discovery_tasks[0].status == "rejected" and not result.rules
                assert all(item.status == "rejected" for item in result.units if item.unit.kind != "case")
            else:
                assert result.units[0].status == "evidence_insufficient"
                assert all(item.status == "analyzed" for item in result.units[1:])
            evidence(f"grouped/{scenario}.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends))


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("scenario", ["multi", "oversize", "provider_failure", "lost_result"])
async def test_actual_group_budget_unknown_and_durable_result_recovery(tmp_path, monkeypatch, scenario):
    async with task_environment(tmp_path, protocol_version="impact-call-v6") as values:
        env, project, doc, _, views, _, _, configs, case, store, tasks = values
        policy = DocumentImpactReasoningPolicy(max_group_units=2) if scenario == "multi" else (
            DocumentImpactReasoningPolicy(max_group_read_bytes=1) if scenario == "oversize" else DocumentImpactReasoningPolicy())
        await configs.save_reasoning_settings(project.id, policy)
        holder = {}
        with provider(mode="http_failure" if scenario == "provider_failure" else "ok",
            responder=grouped_responder(holder, views, case)) as (endpoint, sends, _, _):
            invocation = DocumentImpactInvocationPolicy(max_logical_calls=1) if scenario == "provider_failure" else None
            claim, manifest, _, calls = await prepared(values, endpoint, policy=invocation)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, store)
            if scenario == "lost_result":
                original = store.save_snapshot_checkpoint
                async def lost_reply(*args, **kwargs):
                    await original(*args, **kwargs)
                    raise OSError("fixture actual result PG commit reply lost")
                with monkeypatch.context() as patch:
                    patch.setattr(store, "save_snapshot_checkpoint", lost_reply)
                    with pytest.raises(ImpactCallCheckpointUnavailable): await runner.execute(claim)
                count = len(sends)
                async def forbidden(*args, **kwargs): raise AssertionError("saved result recovery cannot invoke/upload")
                with monkeypatch.context() as patch:
                    patch.setattr(env.impact_runtime, "invoke", forbidden)
                    patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                    saved = await runner.execute(claim)
                assert len(sends) == count == 2
            else:
                saved = await runner.execute(claim)
            _, result = await runner.read_result(project.id, doc.id, saved.id)
            if scenario == "multi":
                assert len(result.discovery_tasks) == 2 and len(sends) == 4
                assert len(result.rules) == 1 and len(result.rules[0].origins) == 2
                assert all(item.status == "analyzed" for item in result.units)
            elif scenario == "oversize":
                assert len(result.discovery_tasks) == 4 and len(sends) == 1
                assert all(item.status == "unprocessed" and item.reason_codes==["group_read_bytes_limit"]
                    for item in result.units if item.unit.kind != "case")
            elif scenario == "provider_failure":
                assert len(sends) == 3  # One saved logical call; actual SDK attempts are retained.
                assert result.discovery_tasks[0].status == "ineligible" and not result.rules
                assert result.units[-1].status == "unprocessed" and result.status == "partial"
            evidence(f"grouped/{scenario}.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends),
                physical_requests=sends, default_model_judgement=False)


@pytest.mark.asyncio
@LIVE
async def test_actual_cancelled_group_http_is_unknown_and_never_automatically_resent(tmp_path):
    async with task_environment(tmp_path, protocol_version="impact-call-v6") as values:
        env, project, doc, _, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(gated=True, responder=grouped_responder(holder, views, case)) as (endpoint, sends, arrived, release):
            claim, manifest, _, calls = await prepared(values, endpoint)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            running = asyncio.create_task(workflow(env, tasks, store).execute(claim))
            assert await asyncio.to_thread(arrived.wait, 10)
            running.cancel()
            with pytest.raises(asyncio.CancelledError): await running
            old = (await store.calls.list(project.id, doc.id, claim.task.id))[0]
            assert old.phase == "invoking" and len(sends) == 1
            release.set()
            expire(store, claim.task.id)
            recovered = await store.claim(project.id, claim.task.id, worker_id="group-unknown-recovery")
            saved = await workflow(env, tasks, store).execute(recovered)
            _, result = await workflow(env, tasks, store).read_result(project.id, doc.id, saved.id)
            assert len(sends) == 2 and result.status == "partial" and not result.rules
            assert all(item.status == "unknown" and item.calls == [old.id] for item in result.units if item.unit.kind != "case")
            assert result.units[-1].status == "analyzed"
            evidence("grouped/unknown-http.json", result=result.model_dump(mode="json"), actual_http_calls=len(sends),
                original_request_not_resent=True, independent_process_restart=False, physical_requests=sends)
