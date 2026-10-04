"""Replay actual failed inputs and exercise v4 over real files/HTTP/stores."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_identity_bindings import ImpactFixedReferenceError, bind_impact_decision
from src.application.documents.impact_response_protocol import (
    DocumentImpactResponseProtocol, capture_impact_response_protocol, parse_bound_impact_decision,
)
from src.schemas.document_impact import DocumentImpactInputEnvelope, DocumentImpactSourceSeed
from src.schemas.document_impact_batches import DocumentImpactBatchManifest
from src.schemas.document_impact_calls import DocumentImpactCallInput, DocumentImpactCallRecord, DocumentImpactCallResponse, DocumentImpactCallSelection, DocumentImpactUnit
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext
from test_document_impact_citation_basis import EVIDENCE, saved_object
from test_document_impact_tasks import evidence, task_environment
from test_document_impact_calls import prepared, provider
from test_document_impact_workflow import responder, workflow


def actual_sample():
    directory = EVIDENCE / "p4-default-model-automatic-first"
    seed = DocumentImpactSourceSeed.model_validate_json((directory / "seed.json").read_bytes())
    envelope = DocumentImpactInputEnvelope.model_validate_json(saved_object(directory, "input.json"))
    manifest = DocumentImpactBatchManifest.model_validate_json((directory / "manifest.json").read_bytes())
    request = DocumentImpactCallInput.model_validate_json((directory / "logical-request-1.json").read_bytes())
    response = DocumentImpactCallResponse.model_validate_json((directory / "logical-response-1.json").read_bytes())
    record = next(DocumentImpactCallRecord.model_validate(item) for item in json.loads((directory / "calls.json").read_bytes())
        if item["id"] == response.call_id)
    return seed, envelope, manifest, request, response, record


def bound_input(*, complete=True, unit_kind="case"):
    seed, envelope, manifest, previous, response, record = actual_sample()
    seed = seed.model_copy(update={"response_protocol": capture_impact_response_protocol(version="impact-call-v4")})
    unit = DocumentImpactUnit(kind="case", id=manifest.cases[0].version_id) if unit_kind == "case" else previous.selection.unit
    selection = DocumentImpactCallSelection(unit=unit, segment_ids=[item.id for item in manifest.segments] if complete else [])
    request = DocumentImpactCallService.build_request(seed, envelope, manifest, selection)
    payload = previous.model_copy(update={"selection": selection, "request": request})
    text = envelope.input.old.result.text
    raw = {"protocol_version": "impact-response-v2", "action": "finish", "summary": "固定原文和完整用例的候选",
        "unit_outcome": "analyzed", "candidates": [{"key": "retention", "kind": "modified",
            "summary": "保留期变化候选", "reason": "需要评审", "document_citations": [
                {"source_ref": "S1", "start_char": 0, "end_char": len(text), "quote": text}],
            "case_impacts": [{"case_ref": "C1", "level": "major", "reason": "验证点需要复核", "citations": [
                {"path": ["extension", "false"], "value": False},
                {"path": ["extension", "zero"], "value": 0},
                {"path": ["extension", "null"], "value": None},
                {"path": ["steps", 7, "expected"], "value": envelope.input.cases[0].version.content_json["steps"][7]["expected"]},
                {"root": "execution_notes", "path": [], "value": envelope.input.cases[0].version.execution_notes}]}]}]}
    return seed, envelope, manifest, payload, response, record, raw


def assess_sample(values):
    seed, envelope, manifest, payload, response, record, raw = values
    return DocumentImpactResponseProtocol.assess(seed, envelope, manifest, record, payload,
        response.model_copy(update={"text": json.dumps(raw, ensure_ascii=False)}))


def test_actual_wrong_unit_is_rejected_unchanged_and_new_read_binds_saved_unit():
    seed, envelope, manifest, request, response, record = actual_sample()
    assessment = DocumentImpactResponseProtocol.assess(seed, envelope, manifest, record, request, response)
    assert [issue.code for issue in assessment.issues] == ["unit_mismatch"]
    old_payload = json.loads(request.request.messages[-1]["content"])
    context = DocumentImpactCoordinationContext.model_validate(old_payload["coordination_context"])
    assert DocumentImpactCallService.build_request(seed, envelope, manifest, request.selection,
        coordination_context=context) == request.request
    new_seed = seed.model_copy(update={"response_protocol": capture_impact_response_protocol(version="impact-call-v4")})
    new_request = DocumentImpactCallService.build_request(new_seed, envelope, manifest, request.selection,
        coordination_context=context)
    payload = request.model_copy(update={"request": new_request})
    refs = json.loads(new_request.messages[-1]["content"])["fixed_references"]
    wire = {"protocol_version": "impact-response-v2", "action": "read", "summary": "补读新原文和差异",
        "reads": {"ranges": [{"source_ref": "S2", "start_char": 0, "end_char": manifest.sources[1].length_chars}],
            "difference_refs": list(refs["differences"])}}
    decision = bind_impact_decision(parse_bound_impact_decision(json.dumps(wire)), new_seed, manifest, payload)
    assert decision.unit == request.selection.unit
    assert decision.unit.id != assessment.decision.unit.id
    assert decision.reads.ranges[0].source_id == manifest.sources[1].id
    assert decision.reads.difference_ids == list(old_payload["fixed_difference_catalog"][i]["id"] for i in range(len(manifest.differences)))
    assert new_seed.response_protocol.version == "impact-call-v4"
    assert "unit" not in new_seed.response_protocol.response_schema["properties"]


def test_exact_bindings_preserve_complete_case_values_and_original_hashes():
    values = bound_input()
    assessment = assess_sample(values)
    assert assessment.status == "unit_finished" and not assessment.issues
    assert assessment.runtime_versions["identity_binding"] == "impact-bindings-v1"
    citations = assessment.decision.candidates[0].case_impacts[0].citations
    assert [citation.value for citation in citations[:3]] == [False, 0, None]
    assert all(citation.version_record_sha256 == values[2].cases[0].version_record_sha256 for citation in citations)
    assert assessment.decision.candidates[0].document_citations[0].text_sha256 == values[2].sources[0].text_sha256
    assert len(values[1].input.cases[0].version.content_json["steps"]) == 8
    assert assessment.semantic_status == "pending_review" and assessment.deployment_status == "unknown"


@pytest.mark.parametrize("kind", ["text", "offset", "path", "false_is_zero", "source_ref", "case_ref", "unread_text", "unread_case", "catalog"])
def test_incorrect_or_unread_evidence_is_still_rejected(kind):
    values = bound_input(complete=kind != "unread_text", unit_kind="segment" if kind == "unread_case" else "case")
    seed, envelope, manifest, payload, response, record, raw = values
    candidate = raw["candidates"][0]
    expected = "invalid_text_citation"
    if kind == "text": candidate["document_citations"][0]["quote"] += "伪造"
    if kind == "offset": candidate["document_citations"][0]["start_char"] = 1
    if kind == "path":
        candidate["case_impacts"][0]["citations"][0]["path"] = ["extension.false"]
        expected = "invalid_case_citation"
    if kind == "false_is_zero":
        candidate["case_impacts"][0]["citations"][0]["value"] = 0
        expected = "invalid_case_citation"
    if kind == "source_ref":
        candidate["document_citations"][0]["source_ref"] = "S99"
        expected = "invalid_fixed_reference"
    if kind == "case_ref":
        candidate["case_impacts"][0]["case_ref"] = "C99"
        expected = "invalid_fixed_reference"
    if kind == "unread_text": expected = "unread_text_citation"
    if kind == "unread_case": expected = "unread_case"
    if kind == "catalog":
        decoded = json.loads(payload.request.messages[-1]["content"])
        decoded["fixed_references"]["cases"]["C1"]["version_record_sha256"] = "0" * 64
        payload.request.messages[-1]["content"] = json.dumps(decoded)
        expected = "invalid_fixed_reference"
    assessment = assess_sample(values)
    assert assessment.status == "rejected" and expected in [issue.code for issue in assessment.issues]


@pytest.mark.parametrize("reads", [{"segment_refs": ["P99"]}, {"case_refs": ["C99"]},
    {"difference_refs": ["D99"]}, {"ranges": [{"source_ref": "S99", "start_char": 0, "end_char": 1}]}])
def test_unknown_read_references_never_select_another_identity(reads):
    seed, _, manifest, payload, _, _, _ = bound_input()
    raw = {"protocol_version": "impact-response-v2", "action": "read", "summary": "缺失依据", "reads": reads}
    with pytest.raises(ImpactFixedReferenceError):
        bind_impact_decision(parse_bound_impact_decision(json.dumps(raw)), seed, manifest, payload)


@pytest.mark.parametrize("field", ["unit", "version_id", "version_record_sha256"])
def test_model_cannot_override_protected_identities(field):
    values = bound_input()
    raw = values[-1]
    if field == "unit": raw["unit"] = {"kind": "case", "id": "another-project-version"}
    else: raw["candidates"][0]["case_impacts"][0]["citations"][0][field] = "another-project-version"
    assert assess_sample(values).issues[0].code == "invalid_protocol"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1", reason="requires real files/HTTP/MySQL/PG/RustFS")
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_v4_actual_automatic_read_binding_case_stage_and_history(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension, protocol_version="impact-call-v4") as values:
        env, project, doc, paths, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint, adapter=adapter)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            assert claim.seed.response_protocol.version == "impact-call-v4"
            saved = await workflow(env, tasks, store).execute(claim)
            runner = workflow(env, tasks, store)
            snapshot, result = await runner.read_result(project.id, doc.id, saved.id)
            assert saved.status == "completed" and all(item.status == "analyzed" for item in result.units)
            assert len(result.rules) == 1 and len(result.units[0].calls) == 2
            assert len(sends) == len(result.units) + 1
            assert result.units[-1].candidates[0].key == result.rules[0].id
            assert result.units[-1].candidates[0].document_citations == result.rules[0].candidate.document_citations
            assert result.units[-1].candidates[0].case_impacts[0].citations[-1].value is None
            for call_id in result.units[-1].calls:
                _, response = await calls.read_response(project.id, doc.id, saved.id, call_id)
                wire = json.loads(response.text)
                assert wire["protocol_version"] == "impact-response-v2" and "unit" not in wire
                assert wire["candidates"][0]["rule_ref"] == "R1"
                assert "version_id" not in wire["candidates"][0]["case_impacts"][0]
            sent = len(sends)
            async def forbidden(*args, **kwargs):
                raise AssertionError("v4 terminal history must not dispatch/rebuild/upload")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                patch.setattr(tasks._sources, "prepare_fixed", forbidden)
                assert await runner.read_result(project.id, doc.id, saved.id) == (snapshot, result)
            assert len(sends) == sent and await store.claim(project.id, saved.id, worker_id="terminal") is None
            raw = (await env.objects.read_object_uri(snapshot.storage_uri))["content"]
            assert hashlib.sha256(raw).hexdigest() == snapshot.sha256
            evidence(f"bound/{extension}-{adapter}/verification.json", snapshot=snapshot.model_dump(mode="json"),
                result=result.model_dump(mode="json"), actual_http_calls=len(sends), identity_binding="impact-bindings-v1",
                default_model_judgement=False, independent_worker_process=False)
            directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target = Path(directory) / "bound" / f"{extension}-{adapter}"
                for path in paths: (target/path.name).write_bytes(path.read_bytes())
                (target/"result.json").write_bytes(raw)
