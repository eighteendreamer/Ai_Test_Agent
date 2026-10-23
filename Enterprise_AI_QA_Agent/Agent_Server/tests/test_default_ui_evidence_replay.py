"""Replay actual default UI evidence without repeating provider or browser work."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_default_ui_scene_live import verify_observation_page
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.schemas.model_config import ModelInvocationResult


FIXTURE = Path(__file__).parent / "fixtures/default_ui_evidence.json"


def captured(directory, scenario):
    sample = next(sample for sample in json.loads(FIXTURE.read_bytes())["samples"]
        if sample["directory"] == directory and sample["scenario"] == scenario)
    detail, jobs, events = sample["detail"], sample["jobs"], sample["events"]
    effects = [event for event in events if event["method"] == "POST"]
    assert len(effects) == 2 and effects[1]["count"] == 1
    return detail, jobs, effects[1]["page_id"], effects[0]["page_id"]


@pytest.mark.parametrize("directory,scenario", [
    ("p5-default-ui-scene-live-current", "positive"),
    ("p5-default-ui-scene-live-current", "negative"),
    ("p5-default-ui-scene-live-current2", "positive"),
    ("p5-default-ui-scene-live-current2", "negative"),
    ("p5-default-ui-scene-live-current3", "positive"),
])
def test_captured_formal_observations_resolve_to_execution_page(directory, scenario):
    detail, jobs, page_id, recording_page = captured(directory, scenario)
    assert detail["results"][0]["status"] == ("passed" if scenario == "positive" else "failed")
    verify_observation_page(detail, jobs, page_id, recording_page)


@pytest.mark.parametrize("fault", ["recording-page", "foreign-run", "missing-job-reference", "missing-path"])
def test_captured_negative_observation_rejects_wrong_evidence(fault):
    detail, jobs, page_id, recording_page = captured("p5-default-ui-scene-live-current", "negative")
    detail, jobs = deepcopy(detail), deepcopy(jobs)
    result = detail["results"][0]
    observation = result["actual"]["receipt"]["steps"][0]["checks"][-1]["observations"][0]
    job = next(job for job in jobs if job["call_id"] == observation["call_id"])
    if fault == "recording-page":
        job["output_payload"]["result"]["page_id"] = recording_page
    elif fault == "foreign-run":
        job["metadata"]["run_id"] = "other-run"
    elif fault == "missing-job-reference":
        result["evidence_refs"] = [ref for ref in result["evidence_refs"] if ref["evidence_id"] != job["id"]]
    else:
        observation["output_path_tokens"] = ["output", "result", "count"]
    with pytest.raises((AssertionError, ValueError)):
        verify_observation_page(detail, jobs, page_id, recording_page)


def test_captured_incomplete_correction_stays_error_and_unique_embedded_receipt_parses():
    sample = json.loads(FIXTURE.read_bytes())["incomplete_correction"]
    assert sample["result_status"] == "error"
    assert "length" in sample["error_message"]
    correction = ModelInvocationResult.model_validate(sample["correction_response"])
    with pytest.raises(ValueError, match="length"):
        AgentCaseAssessmentService.parse_response(correction)
    graph = sample["original_response"]
    response = ModelInvocationResult(text=graph["model_response_text"], response_summary=graph["model_response_summary"])
    # The parser contract accepts one complete schema-valid receipt after brief
    # prose, while the provider's truncated correction remains an error.
    embedded = AgentCaseAssessmentService.parse_response(response)
    assert embedded.steps[0].status == "failed"
    raw = response.text[response.text.index('{"schema_version"'):]
    receipt = AgentCaseAssessmentService.parse_response(response.model_copy(update={"text": raw}))
    assert receipt.steps[0].status == "failed"


def test_captured_equivalent_output_forms_keep_the_persisted_receipt():
    # Reduced from the real Agnes correction at
    # p2-default-ui-agnes-post-checkpoint-current-20261022-01/positive/correction-response-public.json.
    count_ref = {"call_id": "call_4030011b742746b4bd954bd2", "output_path_tokens": ["result", "count"]}
    page_ref = {"call_id": "call_4030011b742746b4bd954bd2", "output_path_tokens": ["result", "pageId"]}
    captured = {
        "schema_version": 1,
        "steps": [{
            "item_key": "count-one-target",
            "status": "passed",
            "summary": "目标页点击一次并读取实际计数和页面身份。",
            "outputs": {"count": count_ref, "pageId": page_ref},
            "output_structure": {"kind": "object", "fields": {
                "count": {"kind": "reference", "source": count_ref},
                "pageId": {"kind": "reference", "source": page_ref},
            }},
        }],
    }
    response = ModelInvocationResult(text=json.dumps(captured, ensure_ascii=False),
        response_summary={"mode": "ok", "finish_reason": "stop"})

    parsed = AgentCaseAssessmentService.parse_response(response)

    assert {name: reference.model_dump(mode="json")
        for name, reference in parsed.steps[0].outputs.items()} == {"count": count_ref, "pageId": page_ref}
    assert parsed.steps[0].output_structure is None

    conflicting = deepcopy(captured)
    conflicting["steps"][0]["output_structure"]["fields"]["count"]["source"] = {
        "call_id": count_ref["call_id"], "output_path_tokens": ["result", "different-count"]}
    with pytest.raises(ValueError, match="输出"):
        AgentCaseAssessmentService.parse_response(ModelInvocationResult(
            text=json.dumps(conflicting, ensure_ascii=False),
            response_summary={"mode": "ok", "finish_reason": "stop"}))
