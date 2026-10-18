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


def test_captured_incomplete_correction_remains_error_and_prose_is_not_silently_repaired():
    sample = json.loads(FIXTURE.read_bytes())["incomplete_correction"]
    assert sample["result_status"] == "error"
    assert "length" in sample["error_message"]
    correction = ModelInvocationResult.model_validate(sample["correction_response"])
    with pytest.raises(ValueError, match="length"):
        AgentCaseAssessmentService.parse_response(correction)
    graph = sample["original_response"]
    response = ModelInvocationResult(text=graph["model_response_text"], response_summary=graph["model_response_summary"])
    with pytest.raises(ValueError, match="JSON"):
        AgentCaseAssessmentService.parse_response(response)
    # Diagnostic only: the isolated JSON is schema-valid. Production must keep
    # rejecting the unframed original and the incomplete correction response.
    raw = response.text[response.text.index('{"schema_version"'):]
    receipt = AgentCaseAssessmentService.parse_response(response.model_copy(update={"text": raw}))
    assert receipt.steps[0].status == "failed"
