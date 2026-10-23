"""Boundary regressions using actual new scene and frozen PG planning samples."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.application.runtime.case_action_binding import _digest
from src.application.runtime.case_action_binding import action_input_schema, bind_case_action, ARGUMENT
from src.application.test_runs.scene_expected_value import scene_expected_value
from src.application.test_runs.agent_case_contract import case_result_contract, receipt_prompt_version
from src.schemas.scene_receipt import SceneExecutionCheck, SceneExecutionReceipt, SceneExpectedReference, case_receipt_type
from src.schemas.case_execution import AgentCaseExecutionContext, AgentCaseExecutionReceipt
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.model_config import ModelInvocationResult
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService

ROOT = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03"


def recorded():
    context = AgentCaseExecutionContext.model_validate(json.loads((ROOT/
        "p5-scene-expectation-live-first/frozen-execution.json").read_bytes()))
    scene = next(e for e in context.evidence if e.kind == "scene")
    ref = SceneExpectedReference(evidence_id=scene.id,content_hash=scene.content_hash,
        payload_path_tokens=["content","assertions","0","expected"])
    return context,scene,ref,context.steps[0].item


@pytest.mark.parametrize("index,expected",[(0,2),(1,5),(2,0),(3,False),(4,None)])
def test_actual_authored_typed_expectations(index,expected):
    context,scene,ref,item = recorded()
    ref.payload_path_tokens[2] = str(index)
    value,proof = scene_expected_value(ref,item,context)
    assert value == expected and type(value) is type(expected)
    assert proof["content_hash"] == scene.content_hash
    assert proof["payload_hash"] == scene.payload_hash
    assert proof["verification_status"] == "not_executed"


@pytest.mark.parametrize("fault",["disabled","unbound","foreign","native","projection","version","id","reference-hash","duplicate"])
def test_recorded_source_cannot_be_replaced_or_borrowed(fault):
    context,scene,ref,item = recorded()
    if fault == "disabled": context.scene_receipt_protocol = None
    elif fault == "unbound": item.evidence_ids.remove(scene.id)
    elif fault == "foreign": context.project_id = "other-project"
    elif fault == "native":
        scene.payload["content"]["assertions"][0]["expected"] = 99
        scene.payload_hash = _digest(scene.payload)
    elif fault == "projection": scene.payload["verification_status"] = "passed"
    elif fault == "version": scene.version = "another-version"
    elif fault == "id": scene.payload["evidence_id"] = "another-id";scene.payload_hash = _digest(scene.payload)
    elif fault == "reference-hash": ref.content_hash = "0"*64
    else: context.evidence.append(scene.model_copy(deep=True))
    with pytest.raises(ValueError): scene_expected_value(ref,item,context)


@pytest.mark.parametrize("path",[["source"],["verification_status"],["content","steps","0","action"],
    ["content","assertions","-1","expected"],["content","assertions","999","expected"]])
def test_recorded_actuals_and_missing_fields_are_not_criteria(path):
    context,_,ref,item = recorded();ref.payload_path_tokens = path
    with pytest.raises(ValueError): scene_expected_value(ref,item,context)


def test_recorded_unfilled_step_is_not_a_null_assertion():
    context,scene,ref,item = recorded()
    index = next(i for i,s in enumerate(scene.payload["content"]["steps"]) if s.get("expected") is None)
    ref.payload_path_tokens = ["content","steps",str(index),"expected"]
    with pytest.raises(ValueError,match="尚未填写"): scene_expected_value(ref,item,context)


@pytest.mark.parametrize("sources",[{}, {"expected_path_tokens":[],"expected_source":{"evidence_id":"s",
    "content_hash":"0"*64,"payload_path_tokens":["content","assertions","0","expected"]}}])
def test_new_check_requires_exactly_one_criterion(sources):
    with pytest.raises(ValueError): SceneExecutionCheck.model_validate({"name":"check","operator":"equals",
        "observations":[{"call_id":"actual","output_path_tokens":[]}],**sources})


def test_original_schema_remains_selected_and_does_not_accept_scene_wire():
    original = json.loads((ROOT/"p5-scene-expectation-before/scene-expectation-proposed-receipt.json").read_bytes())
    assert case_receipt_type() is AgentCaseExecutionReceipt
    with pytest.raises(ValueError): AgentCaseExecutionReceipt.model_validate(original)
    updated = deepcopy(original);updated["schema_version"] = 2
    assert SceneExecutionReceipt.model_validate(updated).steps[0].checks[0].expected_source
    assert receipt_prompt_version() == "case-execution-receipt-v8"
    assert receipt_prompt_version("case-scene-receipt-v1") == "case-execution-scene-receipt-v8"
    assert '"SceneExecutionCheck"' in case_result_contract("case-scene-receipt-v1")


def test_prompt_revision_only_clarifies_existing_path_types_and_cardinality():
    clarification = ("For every operator other than semantic, observations must contain exactly one reference "
        "to the actual value being compared. Do not append an action-success record or a second "
        "value to that scalar comparison; report other expectations as separate checks. ")
    prose_guard = ("A prose sentence or business description is not a directly comparable scalar: use one "
        "semantic check for that expectation. Do not reuse a prose expected_path_tokens value for "
        "equals/contains checks against booleans, numbers, HTML, or unrelated substrings, and do "
        "not derive typed expected values from the prose. For example, if the only case expectation "
        "is 'the details panel is expanded and shows the server confirmation', do not compare that "
        "same path to detailsHidden or detailsText with deterministic operators; assess the observed "
        "DOM and response together with semantic. ")
    current = case_result_contract()
    assert current.count(clarification) == 1
    assert current.count(prose_guard) == 1
    path_types = ('All path token arrays contain strings only, including array indexes: index 0 is "0", '
        'never the JSON number 0. This applies to both expected_path_tokens and output_path_tokens. ')
    assert current.count(path_types) == 1
    assert receipt_prompt_version() == "case-execution-receipt-v8"
    assert ('if expected_source is {"expected_count":"1"}, its path is ["expected_count"], '
        'never ["0","expected_count"] unless expected_source actually has a root array.') in current
    assert ('A valid reference has this exact form: {"call_id":"call_abc123",'
        '"output_path_tokens":["result","count"]}. Use that flat form for every observations element '
        'and every outputs value.') in current
    assert "a tool or browser lifecycle failure does not create a second receipt entry" in current


def test_natural_language_expectations_require_semantic_assessment():
    contract = case_result_contract()
    assert "A prose sentence or business description is not a directly comparable scalar" in contract
    assert "do not derive typed expected values from the prose" in contract
    assert "assess the observed DOM and response together with semantic" in contract


def test_actual_scene_wire_is_parsed_only_for_the_frozen_new_protocol():
    value = json.loads((ROOT/"p5-scene-expectation-before/scene-expectation-proposed-receipt.json").read_bytes())
    value["schema_version"] = 2
    response = ModelInvocationResult(text=json.dumps(value,ensure_ascii=False),
        response_summary={"mode":"ok","finish_reason":"stop"})
    with pytest.raises(ValueError): AgentCaseAssessmentService.parse_response(response)
    parsed = AgentCaseAssessmentService.parse_response(response,scene_receipt_protocol="case-scene-receipt-v1")
    assert parsed.steps[0].checks[0].expected_source.evidence_id == value["steps"][0]["checks"][0]["expected_source"]["evidence_id"]


def test_policy_requires_a_scene_source_and_old_context_serialization_omits_flag():
    with pytest.raises(ValueError): CasePlanningPolicy(scene_receipt_protocol="case-scene-receipt-v1")
    context,_,_,_ = recorded();context.scene_receipt_protocol = None
    assert "scene_receipt_protocol" not in context.execution_json()
    assert "scene_receipt_protocol" not in CasePlanningPolicy().model_dump(mode="json")


def test_scene_consumption_wire_selects_the_new_receipt_without_changing_old_wire():
    context,_,ref,_ = recorded()
    context.consumption_protocol = "case-consumption-phase-v1"
    wire = {"schema_version":2,"steps":[{"item_key":context.target_key,"status":"passed","summary":"完成",
        "checks":[{"name":"计数","operator":"equals","expected_source":ref.model_dump(mode="json"),
            "observations":[{"call_id":"actual-count","output_path_tokens":["result","count"]}]}]}]}
    bundle = {**context.runtime_context(),"case_execution_managed":True,"attempt_id":"actual-attempt"}
    schema = action_input_schema({"type":"object","properties":{}},bundle)
    assert "SceneExecutionReceipt" in schema["$defs"]
    binding = bind_case_action(bundle=bundle,arguments={ARGUMENT:{"item_key":context.target_key,
        "operation_key":"close","consumption_closure":{"reason":"实际结果已核对","receipt":wire}}},
        session_id="actual-session",turn_id="actual-turn")
    assert binding.item_key == context.target_key
    context.scene_receipt_protocol = None
    original = action_input_schema({"type":"object","properties":{}},
        {**context.runtime_context(),"case_execution_managed":True})
    assert "AgentCaseExecutionReceipt" in original["$defs"]
    assert "SceneExecutionReceipt" not in original["$defs"]
