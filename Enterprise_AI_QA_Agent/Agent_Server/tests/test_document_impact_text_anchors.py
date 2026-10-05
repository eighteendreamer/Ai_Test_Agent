"""v5 citation responsibility with real historical inputs and actual live workflows."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_response_protocol import DocumentImpactResponseProtocol, capture_impact_response_protocol
from src.application.documents.impact_text_evidence import fixed_impact_text_evidence
from src.schemas.document_impact import DocumentImpactInputEnvelope, DocumentImpactSourceSeed
from src.schemas.document_impact_batches import DocumentImpactBatchManifest
from src.schemas.document_impact_calls import DocumentImpactCallInput, DocumentImpactCallSelection, DocumentImpactUnit
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext
from test_document_impact_bound_responses import bound_input, assess_sample
from test_document_impact_citation_basis import EVIDENCE, saved_object
from test_document_impact_tasks import evidence, task_environment
from test_document_impact_calls import prepared, provider
from test_document_impact_workflow import responder, workflow
from impact_bound_test_support import bound_fixture_response


def anchored_input():
    values = bound_input()
    canonical = assess_sample(values).decision.model_dump(mode="json")
    seed, envelope, manifest, payload, response, record, _ = values
    seed = seed.model_copy(update={"response_protocol": capture_impact_response_protocol(version="impact-call-v5")})
    request = DocumentImpactCallService.build_request(seed, envelope, manifest, payload.selection)
    payload = payload.model_copy(update={"request": request})
    wire = json.loads(bound_fixture_response({"messages": request.messages}, canonical))
    return seed, envelope, manifest, payload, response, record, wire


def assessment(values):
    seed, envelope, manifest, payload, response, record, wire = values
    return DocumentImpactResponseProtocol.assess(seed, envelope, manifest, record, payload,
        response.model_copy(update={"text": json.dumps(wire, ensure_ascii=False)}))


def test_positions_and_quotes_are_bound_to_actual_evidence_without_model_fields():
    values = anchored_input()
    seed, envelope, manifest, payload, _, _, wire = values
    assert seed.response_protocol.version == "impact-call-v5" and wire["protocol_version"] == "impact-response-v3"
    assert isinstance(wire["candidates"][0]["document_citations"][0], str)
    result = assessment(values)
    assert result.status == "unit_finished" and result.runtime_versions["identity_binding"] == "impact-text-bindings-v1"
    citation = result.decision.candidates[0].document_citations[0]
    assert citation.quote == envelope.input.old.result.text
    assert citation.start_char == 0 and citation.end_char == len(citation.quote)
    assert citation.text_sha256 == manifest.sources[0].text_sha256
    assert [item.value for item in result.decision.candidates[0].case_impacts[0].citations[:3]] == [False, 0, None]
    assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
    directory = json.loads(payload.request.messages[-1]["content"])["fixed_text_evidence"]
    assert all(citation["quote"] for citation in directory.values())


@pytest.mark.parametrize("mutation", ["unknown", "source", "position", "quote", "forged_fields", "case_value", "body_and_catalog"])
def test_unknown_or_tampered_anchors_do_not_bypass_existing_facts(mutation):
    values = anchored_input()
    seed, _, manifest, payload, _, _, wire = values
    body = json.loads(payload.request.messages[-1]["content"])
    ref = wire["candidates"][0]["document_citations"][0]
    expected = "invalid_fixed_reference"
    if mutation == "unknown": wire["candidates"][0]["document_citations"] = ["T999"]
    if mutation == "source": body["fixed_text_evidence"][ref]["source_ref"] = "S2"
    if mutation == "position": body["fixed_text_evidence"][ref]["start_char"] = 1
    if mutation == "quote": body["fixed_text_evidence"][ref]["quote"] += "伪造"
    if mutation == "forged_fields":
        wire["candidates"][0]["document_citations"] = [{"source_ref": "S1", "start_char": 0, "end_char": 1, "quote": "x"}]
        expected = "invalid_protocol"
    if mutation == "case_value":
        wire["candidates"][0]["case_impacts"][0]["citations"][0]["value"] = 0
        expected = "invalid_case_citation"
    if mutation == "body_and_catalog":
        read = body["additional_fixed_reads"][0]
        read["text"] = read["text"][:-1] + "X"
        body["fixed_text_evidence"] = fixed_impact_text_evidence(manifest, [body])
        expected = "invalid_text_citation"
    payload.request.messages[-1]["content"] = json.dumps(body, ensure_ascii=False)
    result = assessment(values)
    assert result.status == "rejected" and expected in [issue.code for issue in result.issues]


def test_normalized_differences_and_case_catalogs_are_not_text_anchors():
    seed, envelope, manifest, payload, _, _, _ = anchored_input()
    item = next(item for item in manifest.differences if item.kind == "normalized")
    selection = DocumentImpactCallSelection(unit=DocumentImpactUnit(kind="difference", id=f"normalized:{item.index}"))
    request = DocumentImpactCallService.build_request(seed, envelope, manifest, selection)
    assert json.loads(request.messages[-1]["content"])["fixed_text_evidence"] == {}


def test_additional_reads_append_stable_references_and_repeated_delivery_reuses_them():
    seed, envelope, manifest, payload, response, _, _ = anchored_input()
    unit = DocumentImpactUnit(kind="segment", id=manifest.segments[0].id)
    first = DocumentImpactCallService.build_request(seed, envelope, manifest, DocumentImpactCallSelection(unit=unit))
    old_payload = payload.model_copy(update={"selection": DocumentImpactCallSelection(unit=unit), "request": first})
    selection = DocumentImpactCallSelection(unit=unit, segment_ids=[manifest.segments[-1].id], previous_call_id="saved-read")
    second = DocumentImpactCallService.build_request(seed, envelope, manifest, selection, previous=(old_payload, response))
    old = json.loads(first.messages[-1]["content"])["fixed_text_evidence"]
    new = json.loads(second.messages[-1]["content"])["fixed_text_evidence"]
    assert len(new) > len(old) and all(new[key] == value for key, value in old.items())
    body = json.loads(first.messages[-1]["content"])
    assert fixed_impact_text_evidence(manifest, [body, body]) == old
    assert first.messages == second.messages[:len(first.messages)]


def test_actual_saved_v4_request_and_protocol_hash_are_unchanged():
    directory = EVIDENCE / "p4-default-model-bound-v4"
    seed = DocumentImpactSourceSeed.model_validate_json((directory / "seed.json").read_bytes())
    envelope = DocumentImpactInputEnvelope.model_validate_json(saved_object(directory, "input.json"))
    manifest = DocumentImpactBatchManifest.model_validate_json((directory / "manifest.json").read_bytes())
    payload = DocumentImpactCallInput.model_validate_json((directory / "logical-request-1.json").read_bytes())
    context = DocumentImpactCoordinationContext.model_validate(json.loads(payload.request.messages[-1]["content"])["coordination_context"])
    assert seed.response_protocol == capture_impact_response_protocol(version="impact-call-v4")
    assert DocumentImpactCallService.build_request(seed, envelope, manifest, payload.selection, coordination_context=context) == payload.request


def test_partial_overlapping_reads_only_anchor_delivered_block_slices():
    seed, envelope, manifest, _, _, _, _ = anchored_input()
    source = manifest.sources[0]
    text = envelope.input.old.result.text
    first = DocumentImpactBatches.read_range(seed, envelope, manifest, source.id, 2, 18)
    second = DocumentImpactBatches.read_range(seed, envelope, manifest, source.id, 10, 25)
    assert first["blocks"] and second["blocks"]
    payload = {"primary": first, "additional_fixed_reads": [second]}
    anchors = fixed_impact_text_evidence(manifest, [payload])
    assert anchors and all(item["source_ref"] == "S1" for item in anchors.values())
    for item in anchors.values():
        left, right = item["start_char"], item["end_char"]
        assert (2 <= left < right <= 18) or (10 <= left < right <= 25)
        assert item["quote"] == text[left:right]
    assert not any(item["start_char"] == 0 or item["end_char"] == len(text) for item in anchors.values())


def test_equal_text_in_distinct_document_versions_keeps_distinct_sources():
    seed, envelope, manifest, _, _, _, _ = anchored_input()
    old = DocumentImpactBatches.read_range(seed, envelope, manifest, manifest.sources[0].id, 0, 1)
    new = DocumentImpactBatches.read_range(seed, envelope, manifest, manifest.sources[1].id, 0, 1)
    assert old["text"] == new["text"]
    anchors = fixed_impact_text_evidence(manifest, [{"primary": old, "additional_fixed_reads": [new]}])
    assert len(anchors) == 2
    assert anchors["T1"]["quote"] == anchors["T2"]["quote"]
    assert anchors["T1"]["source_ref"] == "S1" and anchors["T2"]["source_ref"] == "S2"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_TASKS") != "1", reason="requires actual HTTP/files/MySQL/PG/RustFS")
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
@pytest.mark.parametrize("adapter", [False, True])
async def test_v5_actual_full_workflow_and_fixed_history(tmp_path, monkeypatch, extension, adapter):
    async with task_environment(tmp_path, extension, protocol_version="impact-call-v5") as values:
        env, project, doc, paths, views, _, _, _, case, store, tasks = values
        holder = {}
        with provider(responder=responder(holder, views, case)) as (endpoint, sends, _, _):
            claim, manifest, _, calls = await prepared(values, endpoint, adapter=adapter)
            holder.update(first=calls.units(manifest)[0], manifest=manifest)
            runner = workflow(env, tasks, store)
            saved = await runner.execute(claim)
            snapshot, result = await runner.read_result(project.id, doc.id, saved.id)
            assert saved.status == "completed" and all(item.status == "analyzed" for item in result.units)
            assert len(result.rules) == 1 and len(result.units[0].calls) == 2
            assert result.units[-1].candidates[0].document_citations == result.rules[0].candidate.document_citations
            assert result.units[-1].candidates[0].case_impacts[0].citations[-1].value is None
            records = await store.calls.list(project.id, doc.id, saved.id)
            for record in records:
                _, response = await calls.read_response(project.id, doc.id, saved.id, record.id)
                wire = json.loads(response.text)
                assert wire["protocol_version"] == "impact-response-v3"
                if wire.get("candidates") and "document_citations" in wire["candidates"][0]:
                    assert all(isinstance(item, str) for item in wire["candidates"][0]["document_citations"])
            sent = len(sends)
            async def forbidden(*args, **kwargs):
                raise AssertionError("v5 terminal history must not dispatch/rebuild/upload")
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime, "invoke", forbidden)
                patch.setattr(env.objects, "store_uploaded_bytes", forbidden)
                assert await runner.read_result(project.id, doc.id, saved.id) == (snapshot, result)
            assert len(sends) == sent and await store.claim(project.id, saved.id, worker_id="terminal") is None
            raw = (await env.objects.read_object_uri(snapshot.storage_uri))["content"]
            assert hashlib.sha256(raw).hexdigest() == snapshot.sha256
            evidence(f"anchors/{extension}-{adapter}/verification.json", snapshot=snapshot.model_dump(mode="json"),
                actual_http_calls=sent, model_identity_binding="impact-text-bindings-v1", default_model_judgement=False)
            directory = os.getenv("QA_IMPACT_TASK_EVIDENCE_DIR")
            if directory:
                target = Path(directory) / "anchors" / f"{extension}-{adapter}"
                for path in paths: (target / path.name).write_bytes(path.read_bytes())
                (target / "result.json").write_bytes(raw)
