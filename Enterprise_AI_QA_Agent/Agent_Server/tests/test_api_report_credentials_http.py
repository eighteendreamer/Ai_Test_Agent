"""Reports must redact actual credential values before truncating evidence."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_api_cookie_session_http import campaign_tasks, cookie_target
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, ApiTestingState, AssertionSpec
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.runtime.store import InMemorySessionStore


def save_facts(tmp_path, name, facts):
    output = Path(os.getenv("QA_API_REPORT_CREDENTIAL_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    (output/f"{name}.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf8")


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_actual_runtime_cookie_reports_and_artifacts_are_public_copies(tmp_path, worker, failed):
    with cookie_target(True) as (base, events, values):
        store = InMemorySessionStore()
        launcher = ControlledWorkerLauncher(store)
        runtime = ApiTestingModeRuntime(api_docs_service=None, session_store=store,
            coordinator_runtime_service=launcher if worker else None)
        tasks = campaign_tasks(base)[:3]
        if failed:
            tasks[-1].assertions = [AssertionSpec(kind="status_code", expected=201)]
            tasks[-1].name = "Profile with " + values["refresh"]
        state = ApiTestingState(campaign=ApiTestCampaign(campaign_id="cookie-public-report", tasks=tasks))
        context = SimpleNamespace(session_id="parent", turn_id="turn", context_bundle={})
        await runtime._execute_campaign(state, context)
        original = [task.model_dump(mode="json") for task in state.campaign.tasks]
        raw_response_present = state.campaign.tasks[-1].response_body["opaque_echo"] == values["refresh"]
        raw_available = all(value not in json.dumps(state.report.model_dump(mode="json")) for value in values.values())
        markdown = runtime._report_builder.build_markdown(campaign=state.campaign, report=state.report)
        artifacts_safe = all(value not in json.dumps(state.artifacts) for value in values.values())
        markdown_safe = all(value not in markdown for value in values.values())
        evaluation_safe = all(value not in json.dumps({"verification": state.verification_result,
            "evaluation": state.evaluation_result, "errors": state.errors, "notes": state.notes})
            for value in values.values())
        # No network calls are needed to build a report or rebuild its formats.
        count = len(events)
        runtime._report_builder.build(campaign=state.campaign)
        facts = {"worker": worker, "failed_assertion": failed, "events": events,
            "report_safe": raw_available, "artifacts_safe": artifacts_safe, "markdown_safe": markdown_safe,
            "final_evaluation_safe": evaluation_safe,
            "raw_execution_response_preserved": raw_response_present,
            "execution_objects_unchanged": original == [task.model_dump(mode="json") for task in state.campaign.tasks],
            "report_extra_http": len(events)-count, "passed_tasks": state.report.completed,
            "failed_tasks": state.report.failed, "artifact_types": [a["type"] for a in state.artifacts],
            "external_ai_calls": 0}
        save_facts(tmp_path, f"runtime-{'worker' if worker else 'direct'}-{'failed' if failed else 'passed'}", facts)
        assert raw_available and artifacts_safe and markdown_safe and evaluation_safe, "Known Cookie value reached a public report format."
        assert raw_response_present and facts["execution_objects_unchanged"]
        assert state.report.completed == (2 if failed else 3) and state.report.failed == int(failed)
        assert len(events) == 4 and facts["report_extra_http"] == 0


@pytest.mark.parametrize("limit", [500, 2000, 4000])
def test_failed_report_redacts_known_values_before_preview_limits(tmp_path, limit):
    from uuid import uuid4
    runtime = ApiTestingModeRuntime(api_docs_service=None)
    secret = uuid4().hex
    prefix = json.dumps({"token": "ordinary word", "opaque": ""})[:-2]
    padding = limit-len(prefix)-16
    body = {"token": "ordinary word", "opaque": "x"*padding+secret+"tail"}
    old_preview = json.dumps(body)[:limit]
    assert secret[:16] in old_preview and secret not in old_preview
    credential = runtime._credential_manager.create_from_user_input(extracted={"token": secret})
    task = ApiTestTask(task_id="failed-preview", name="observe "+secret, method="GET", path="/profile?key="+secret,
        auth_ref=credential.credential_session_id, status="failed", response_status=200,
        request_query={"opaque": secret}, response_body=body,
        response_headers={"X-Opaque": secret}, last_error="value "+secret,
        check_results=[{"name": "exact", "passed": False, "actual": secret, "expected": "not "+secret}])
    original = task.model_dump(mode="json")
    campaign = ApiTestCampaign(campaign_id="preview", objective="check "+secret, tasks=[task])
    builder = runtime._report_builder
    report = builder.build(campaign=campaign)
    markdown = builder.build_markdown(campaign=campaign, report=report)
    artifacts = builder.build_artifacts(campaign=campaign, report=report, markdown_report=markdown)
    all_text = json.dumps(report.model_dump(mode="json"))+markdown+"".join(a["content"].decode() for a in artifacts)
    safe = secret not in all_text and secret[:12] not in all_text
    facts = {"public_formats_safe_including_partial_preview": safe,
        "original_execution_unchanged": original == task.model_dump(mode="json"),
        "failed_tasks": report.failed, "passed_tasks": report.completed, "external_ai_calls": 0}
    save_facts(tmp_path, f"failed-preview-{limit}", facts)
    assert safe, "Known credential or its truncated prefix reached a report."
    assert facts["original_execution_unchanged"] and report.failed == 1 and report.completed == 0
    assert "ordinary word" in json.dumps(report.tasks[0]["response"]["body"])


def test_shared_output_policy_redacts_literal_substrings_without_global_context():
    from uuid import uuid4
    from copy import deepcopy
    from src.application.security.output_safety_policy import OutputSafetyPolicy
    secret = "字面值."+uuid4().hex+"[+]"
    payload = {"opaque": "prefix:"+secret+":suffix", "nested": [secret, (secret, False, 0, None)],
               "token": "ordinary word", "password": {"type": "string", "description": "password field", "example": secret}}
    original = deepcopy(payload)
    policy = OutputSafetyPolicy(known_sensitive_values=[secret, secret[:8]])
    output, _, paths = policy.sanitize_tool_output(payload)
    safe = secret not in json.dumps(output) and secret[:8] not in json.dumps(output)
    assert safe and output["opaque"] == "prefix:[REDACTED]:suffix"
    assert output["nested"][1][1:] == (False, 0, None)
    assert output["token"] == "ordinary word" and output["password"]["type"] == "string"
    assert output["password"]["example"] == "[REDACTED]" and paths
    assert payload == original
    unchanged_without_context = OutputSafetyPolicy().sanitize_for_audit({"opaque": secret}) == {"opaque": secret}
    assert unchanged_without_context
