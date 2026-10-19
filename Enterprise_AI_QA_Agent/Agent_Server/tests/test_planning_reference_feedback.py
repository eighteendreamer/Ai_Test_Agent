"""Replay live6 citations without editing the failed plan or relaxing evidence."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from src.application.runtime.case_effect_relations import compile_relations
from src.application.test_cases.planning_effect_tasks import (
    DECISIONS_V2, accept_task_decision, effect_task_prompts, effect_task_schema,
)
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog
from src.schemas.case_planning import PlanningEvidence
from src.schemas.case_planning_effect_tasks import EffectTaskCheckpoint
from src.schemas.case_planning_references import REFERENCE_PROTOCOL, REFERENCE_PROTOCOL_V2
from src.schemas.case_planning_stages import PlanningStageCheckpoint


SAMPLE = Path(__file__).parent / "fixtures/planning_cleanup_ambiguous_text.json"


def captured():
    return json.loads(SAMPLE.read_text(encoding="utf8"))


def fine_citation(value):
    """Explicit new candidate using an already-supported E text citation."""
    result = deepcopy(value)
    declaration = result["bindings"][0]["effect_declarations"][0]
    declaration["checks"][0]["expected_source"].update(
        reference="E2", payload_path_tokens=["content"],
        excerpt="DELETE同一路径返回200及刚删除的订单JSON。")
    return result


def accept(sample, value):
    evidence = [PlanningEvidence.model_validate(e) for e in sample["evidence"]]
    return accept_task_decision(
        PlanningStageCheckpoint.model_validate(sample["stages"]),
        EffectTaskCheckpoint.model_validate(sample["completed_tasks"]),
        DECISIONS_V2["cleanup"].model_validate(value),
        evidence_reference_catalog(evidence), evidence, REFERENCE_PROTOCOL_V2)


def test_actual_ambiguity_reports_the_exact_declaration_field_and_count(caplog):
    sample = captured()
    wire = sample["ambiguous_cleanup"]
    jsonschema.validate(wire, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))
    with pytest.raises(ValueError) as failure:
        accept(sample, wire)
    message = str(failure.value)
    assert "orders-delete-cleanup" in message
    assert "checks[0]" in message
    assert "literal_occurrences=2" in message
    assert "excerpt_occurrences=1" in message
    # Diagnostics carry identity/path/count, not original source text.
    assert "DELETE同一路径" not in message
    log = next(r for r in caplog.records if r.message == "case_effect_text_reference_rejected")
    assert log.excerpt_occurrences == 1 and log.literal_occurrences == 2
    assert log.payload_path_tokens == ["content"]
    assert "DELETE同一路径" not in str(log.__dict__)


def test_actual_t_excerpt_remains_invalid_in_both_schema_and_parser():
    wire = captured()["invalid_t_excerpt_cleanup"]
    with pytest.raises(ValueError):
        DECISIONS_V2["cleanup"].model_validate(wire)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))


def test_explicit_e_excerpt_resolves_the_same_original_value_and_location():
    sample = captured()
    original = deepcopy(sample)
    value = fine_citation(sample["ambiguous_cleanup"])
    jsonschema.validate(value, effect_task_schema("cleanup", REFERENCE_PROTOCOL_V2))
    state, decision = accept(sample, value)
    assert len(state.completed) == 3
    evidence = [PlanningEvidence.model_validate(e) for e in sample["evidence"]]
    item = decision.items[0]
    content = next(e.payload["content"] for e in evidence if e.kind == "case")
    definition = next(d for d in compile_relations(item, content, evidence,
        "case-effect-document-expected-v1") if d["role"] == "cleanup")
    check = definition["checks"][0]
    assert type(check["expected"]) is int and check["expected"] == 200
    proof = check["expected_source_proof"]
    source = next(e.payload["content"] for e in evidence if e.kind == "document")
    start = source.index("DELETE同一路径返回") + len("DELETE同一路径返回")
    assert proof["char_start"] == start and proof["char_end"] == start + 3
    assert proof["line"] == 4
    assert state.completed[:2] == EffectTaskCheckpoint.model_validate(sample["completed_tasks"]).completed
    assert sample == original


@pytest.mark.parametrize("mutation", ["wrong-excerpt", "ambiguous-excerpt", "wrong-literal"])
def test_fine_citation_cannot_invent_or_ignore_original_text(mutation):
    sample = captured()
    value = fine_citation(sample["ambiguous_cleanup"])
    ref = value["bindings"][0]["effect_declarations"][0]["checks"][0]["expected_source"]
    if mutation == "wrong-excerpt": ref["excerpt"] = "DELETE返回204"
    if mutation == "ambiguous-excerpt": ref["excerpt"] = "200"
    if mutation == "wrong-literal": ref["literal"] = "204"
    with pytest.raises(ValueError):
        accept(sample, value)


@pytest.mark.parametrize("protocol", [REFERENCE_PROTOCOL, REFERENCE_PROTOCOL_V2])
def test_each_task_keeps_the_existing_reference_fallback_guidance(protocol):
    for prompt in effect_task_prompts(protocol).values():
        assert "literal必须唯一存在" in prompt
        assert "重复原文有歧义时用E+唯一excerpt/literal/encoding" in prompt
        assert "T已提供确切原文，不复制ID/hash/excerpt" in prompt
