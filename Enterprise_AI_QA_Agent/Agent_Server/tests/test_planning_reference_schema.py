"""Replay the default Step cleanup response rejected on 2026-10-19."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from src.application.test_cases.planning_effect_tasks import DECISIONS_V2, effect_task_schema
from src.application.test_cases.planning_evidence_references import bind_evidence_references
from src.schemas.case_planning_references import REFERENCE_PROTOCOL_V2, ReferencedEvidenceDecisionV2
from test_planning_evidence_references import actual, reference_wire


def captured_cleanup():
    return json.loads((Path(__file__).parent / "fixtures" /
        "planning_cleanup_reference_with_excerpt.json").read_text(encoding="utf-8"))


def text_reference(receipt):
    return receipt["bindings"][0]["effect_declarations"][0]["checks"][1]["expected_source"]


def test_actual_rejected_cleanup_is_also_invalid_in_delivered_schema():
    receipt = captured_cleanup()
    assert text_reference(receipt)["excerpt"] is not None
    with pytest.raises(ValueError):
        DECISIONS_V2["cleanup"].model_validate(receipt)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(receipt, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))


@pytest.mark.parametrize("field,value", [("literal", None), ("encoding", None),
    ("payload_path_tokens", ["content"])])
def test_text_reference_schema_and_parser_reject_the_same_invalid_shapes(field, value):
    receipt = captured_cleanup()
    source = text_reference(receipt)
    source["excerpt"] = None
    source[field] = value
    with pytest.raises(ValueError):
        DECISIONS_V2["cleanup"].model_validate(receipt)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(receipt, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))


@pytest.mark.parametrize("kind", ["text_alias", "document_value", "document_text"])
def test_valid_existing_reference_shapes_keep_the_same_serialized_values(kind):
    receipt = captured_cleanup()
    source = text_reference(receipt)
    source["excerpt"] = None
    if kind != "text_alias":
        source.update(reference="E2", payload_path_tokens=["content"])
        if kind == "document_value":
            source.update(literal=None, encoding=None)
        else:
            source["excerpt"] = "JSON键order.id是完整键（含点号）。"
    jsonschema.validate(receipt, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))
    original = deepcopy(source)
    parsed = DECISIONS_V2["cleanup"].model_validate(receipt)
    assert text_reference(parsed.model_dump(mode="json")) == original


def test_v2_references_still_reject_tool_contracts_as_business_expectations():
    plan, canonical, catalog = actual()
    wire = reference_wire(canonical, catalog, REFERENCE_PROTOCOL_V2)
    tool_ref = next(ref for ref, entry in catalog["evidence"].items() if entry["kind"] == "tool")
    wire["bindings"][0]["effect_declarations"][0]["checks"][0]["expected_source"] = {
        "reference": tool_ref, "payload_path_tokens": []}
    parsed = ReferencedEvidenceDecisionV2.model_validate(wire)
    with pytest.raises(ValueError, match="文档预期短引用不能指向用例或工具"):
        bind_evidence_references(parsed, catalog, plan.evidence)
