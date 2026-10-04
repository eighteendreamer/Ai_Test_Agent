"""Replay the actual failed default-model inputs without another model send."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_response_protocol import capture_impact_response_protocol
from src.schemas.document_impact import DocumentImpactInputEnvelope, DocumentImpactSourceSeed
from src.schemas.document_impact_batches import DocumentImpactBatchManifest, DocumentImpactCaseCitation
from src.schemas.document_impact_calls import DocumentImpactCallInput


EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "upgrade_2026-10-03"


def saved_object(directory, name):
    references = json.loads((directory / "object-references.json").read_text(encoding="utf-8"))
    reference = next(item for item in references if item["name"] == name)
    raw = (directory / "objects" / (reference["sha256"] + ".object")).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"] and len(raw) == reference["size_bytes"]
    return raw


@pytest.mark.parametrize("sample", ["p4-default-model-first", "p4-default-model-transport"])
def test_actual_v2_requests_replay_unchanged_and_v3_provides_required_citation_hashes(sample):
    directory = EVIDENCE / sample
    seed = DocumentImpactSourceSeed.model_validate_json((directory / "seed.json").read_bytes())
    manifest = DocumentImpactBatchManifest.model_validate_json((directory / "manifest.json").read_bytes())
    envelope = DocumentImpactInputEnvelope.model_validate_json(saved_object(directory, "input.json"))
    original = DocumentImpactCallInput.model_validate_json(saved_object(directory, "request.json"))
    assert seed.response_protocol.version == "impact-call-v2"
    rebuilt = DocumentImpactCallService.build_request(seed, envelope, manifest, original.selection)
    assert rebuilt == original.request
    original_payload = json.loads(original.request.messages[0]["content"])
    assert all(set(item) == {"case_id", "version_id"} for item in original_payload["fixed_case_catalog"])
    assert all(case.version_record_sha256 not in original.request.messages[0]["content"] for case in manifest.cases)

    new_seed = seed.model_copy(update={"response_protocol": capture_impact_response_protocol(version="impact-call-v3")})
    upgraded = DocumentImpactCallService.build_request(new_seed, envelope, manifest, original.selection)
    payload = json.loads(upgraded.messages[0]["content"])
    assert payload["analysis_protocol"]["version"] == "impact-call-v3"
    assert payload["fixed_case_catalog"] == [case.model_dump(mode="json") for case in manifest.cases]
    assert new_seed.response_protocol.response_schema == seed.response_protocol.response_schema
    assert upgraded.system_prompt == original.request.system_prompt
    assert payload["primary"] == original_payload["primary"]
    assert payload["additional_fixed_reads"] == original_payload["additional_fixed_reads"]
    assert envelope.input_sha256 == original.input_sha256
    assert len(envelope.input.cases) == 3
    assert all(len(case.version.content_json["steps"]) == 8 for case in envelope.input.cases)

    for bundle, catalog in zip(envelope.input.cases, payload["fixed_case_catalog"]):
        assert catalog["version_id"] == bundle.version.id
        for field, value in [("zero", 0), ("null", None), ("false", False)]:
            citation = DocumentImpactCaseCitation(version_id=catalog["version_id"],
                version_record_sha256=catalog["version_record_sha256"], path=["extension", field], value=value)
            DocumentImpactBatches.validate_case_citation(new_seed, envelope, manifest, citation)
        wrong = citation.model_copy(update={"version_record_sha256": catalog["content_hash"]})
        with pytest.raises(ValueError):
            DocumentImpactBatches.validate_case_citation(new_seed, envelope, manifest, wrong)


def test_actual_sdk_retry_preserves_complete_fixed_messages_and_output_budget():
    directory = EVIDENCE / "p4-default-model-transport"
    fixed = DocumentImpactCallInput.model_validate_json(saved_object(directory, "request.json"))
    requests = [(directory / f"http-request-{number}.json").read_bytes() for number in (1, 2)]
    assert requests[0] == requests[1]
    payload = json.loads(requests[0])
    assert payload["messages"] == [{"role": "system", "content": fixed.request.system_prompt}, *fixed.request.messages]
    assert payload.get("max_tokens", payload.get("max_completion_tokens")) == fixed.budget.output_reserved_tokens == 8192
    physical = json.loads((directory / "physical-http.json").read_text(encoding="utf-8"))
    assert len(physical) == 2 and physical[0]["error_type"] == "ReadTimeout"
    call = json.loads((directory / "call.json").read_text(encoding="utf-8"))
    assert call["phase"] == "outcome_unknown" and call["response"] is None
