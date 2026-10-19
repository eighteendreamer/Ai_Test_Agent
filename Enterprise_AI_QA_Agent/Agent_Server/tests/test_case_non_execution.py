"""Replay the live5 pre-HTTP failure without confusing it with unknown effects."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_agent_case_execution import regression_business_http
from test_case_effect_evidence import original_effect_runtime
from test_case_operations import LIVE
from src.application.runtime.case_action_binding import ARGUMENT, _digest
from src.application.runtime.case_effect_evidence import capture_returned_evidence
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.case_cleanup_coverage import cleanup_coverage
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.case_planning import PlanningDecisionV3, PreparationScopeRecord
from src.schemas.tool_job import ToolOutputObservation


SAMPLE = Path(__file__).parent / "fixtures/api_task_missing_path.json"


def failed_sample():
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    # Fixture deduplicates identical definitions; restore each exact original
    # envelope and its original digest, without rewriting historical evidence.
    for job in sample["original_jobs"]:
        intent = job["metadata"]["case_effect_evidence_intent"]
        intent["definitions"] = deepcopy(sample["shared_definitions"])
        intent["binding"] = deepcopy(job["metadata"]["case_action_binding"])
        assert intent["sha256"] == _digest({key: value for key, value in intent.items() if key != "sha256"})
    return sample


def execution_context():
    return ToolExecutionContext(session_id="session", turn_id="turn", trace_id="trace",
        user_message="test", normalized_input="test", context_bundle={})


@pytest.mark.asyncio
async def test_captured_missing_path_records_server_non_execution_before_http(regression_business_http):
    sample = failed_sample()
    ctx = execution_context()
    result = await ApiTestingModeRuntime(api_docs_service=None).handle(sample["input"], ctx)
    for key in ("error", "validation_errors", "http_request_sent"):
        assert result[key] == sample["output"][key]
    assert not regression_business_http[0][1]
    proof = getattr(ctx, "non_execution", None)
    assert proof is not None, "Known pre-HTTP failure has no server-owned execution proof"
    assert proof.reason == "invalid_task_payload" and proof.stage == "input_validation"


@pytest.mark.parametrize("proved", [False, True])
def test_original_failure_is_excluded_only_with_server_observation(proved):
    saved = failed_sample()["original_jobs"]
    job = SimpleNamespace(**next(j for j in saved if j["output_payload"].get("error") == "invalid_task_payload"))
    observation = deepcopy(job.metadata["tool_output_observation"])
    # Public output alone is deliberately insufficient, even when it says false.
    job.output_payload["non_execution"] = {"stage": "input_validation", "reason": "invalid_task_payload"}
    observation["output_hash"] = _digest(job.output_payload)
    if proved:
        observation["non_execution"] = {"stage": "input_validation", "reason": "invalid_task_payload"}
    event = capture_returned_evidence(job, job.output_payload, observation)
    produced = next(e for e in event["declarations"] if e["role"] == "produced")
    if proved:
        assert produced["resolution"] == "not_executed" and not produced["objects"]
        assert not produced["checks_passed"]
    else:
        assert produced["resolution"] == "captured" and not produced["checks_passed"]


def test_old_observation_roundtrips_and_non_execution_cannot_claim_success():
    raw = failed_sample()["original_jobs"][0]["metadata"]["tool_output_observation"]
    assert ToolOutputObservation.model_validate(raw).model_dump(mode="json") == raw
    with pytest.raises(ValueError, match="failed native return"):
        ToolOutputObservation.model_validate({**raw, "status": "completed",
            "non_execution": {"stage": "input_validation", "reason": "invalid_task_payload"}})


@pytest.mark.parametrize("state", ["legacy", "not-executed", "lost-return", "identity-projected"])
def test_real_v2_scope_keeps_unknown_and_requires_matching_business_identity(state):
    sample = failed_sample()
    ctx = sample["coverage_context"]
    decision = PlanningDecisionV3.model_validate(ctx.pop("decision"))
    ctx["preparation_scopes"] = {key: PreparationScopeRecord.model_validate(value)
        for key, value in ctx["preparation_scopes"].items()}
    context = SimpleNamespace(**ctx, steps=[SimpleNamespace(item=item) for item in decision.items],
        consumption_protocol=None, effect_coverage_protocol="case-effect-coverage-v2", effect_coverage_max_bytes=1024000)
    rows = []
    for raw in sample["original_jobs"]:
        job = SimpleNamespace(**deepcopy(raw))
        invalid = job.output_payload.get("error") == "invalid_task_payload"
        observation = deepcopy(job.metadata["tool_output_observation"])
        if invalid and state != "legacy":
            observation["non_execution"] = {"stage": "input_validation", "reason": "invalid_task_payload"}
        if state == "identity-projected":
            # Diagnostic new declaration, never rewrite the stored live5 plan.
            intent = job.metadata["case_effect_evidence_intent"]
            for entry in intent["definitions"]:
                entry["object_path_tokens"] = ["task_result", "response_body", "order.id"]
            intent["sha256"] = _digest({key: value for key, value in intent.items() if key != "sha256"})
        event = None if invalid and state == "lost-return" else capture_returned_evidence(job, job.output_payload, observation)
        rows.append({"origin_job_id": job.id, "binding": job.metadata["case_action_binding"],
            "returned_evidence": event, "evidence_state": "captured" if event else "awaiting_reliable_return"})
    coverage = next(iter(cleanup_coverage(context, rows)["cleanups"].values()))
    if state == "identity-projected":
        assert coverage["status"] == "passed" and coverage["covered_object_count"] == 1
    else:
        assert coverage["status"] == "blocked"
        assert bool(coverage["unknown"]) == (state != "not-executed")
        assert coverage["covered_object_count"] == 0  # Root outputs are unequal.
    if state == "not-executed":
        assert any(r.get("reason") == "not_executed" for r in coverage["excluded_operations"])


@pytest.mark.asyncio
async def test_model_or_public_payload_cannot_supply_non_execution(regression_business_http):
    (url, effects, _), orders, created = regression_business_http
    ctx = execution_context()
    runtime = ApiTestingModeRuntime(api_docs_service=None)
    spoof = {"stage": "input_validation", "reason": "invalid_task_payload"}
    result = await runtime.handle({"worker_action": "execute_task", "non_execution": spoof,
        "task": {"task_id": "actual-create", "method": "POST", "path": url + "/orders", "non_execution": spoof}}, ctx)
    assert result["ok"] and len(effects) == 1 and len(created) == 1
    assert getattr(ctx, "non_execution", None) is None
    await runtime.handle({"worker_action": "execute_task", "task": {
        "task_id": "actual-cleanup", "method": "DELETE", "path": url + "/orders/" + created[0]}}, ctx)
    assert not orders


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("fault", ["none", "ack-loss", "delivery-loss"])
async def test_actual_original_failure_then_create_cleanup_and_pg_recovery(tmp_path, regression_business_http, monkeypatch, fault):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business, coverage=True, select_operations=True) as values:
        services, runtime, tool, execution, context, store, action, read, save = values
        jobs = services[0][1]
        observe = jobs._store.observe_job_output
        if fault == "ack-loss":
            async def lost_ack(*args, **kwargs):
                await observe(*args, **kwargs)
                raise ConnectionError("native failed return committed; acknowledgement lost")
            monkeypatch.setattr(jobs._store, "observe_job_output", lost_ack)
        if fault == "delivery-loss":
            from test_tool_operation_observation import FailedDelivery
            runtime._artifact_storage_service = FailedDelivery()
        args = {**failed_sample()["input"], ARGUMENT: {"item_key": "seed", "operation_key": "invalid-seed"}}
        invalid = await runtime.execute(tool, ModelToolCall(id="invalid-seed", name=tool.key, arguments=args), execution)
        assert invalid.status == "failed" and not business[0][1]
        monkeypatch.setattr(jobs._store, "observe_job_output", observe)
        runtime._artifact_storage_service = None
        inventory = await services[0][1].list_case_effect_evidence(
            project_id=context.project_id, run_id=context.run_id, plan_id=context.plan_id)
        assert inventory[0]["returned_evidence"]["resolution"] == "not_executed"
        assert cleanup_coverage(context, inventory)["cleanups"]["cleanup"]["status"] == "blocked"
        await action("create", "seed", "create", "POST", business[0][0] + "/orders")
        identity = business[2][0]
        await action("query", "target", "query", "GET", business[0][0] + "/orders/" + identity)
        await action("cleanup", "cleanup", "cleanup", "DELETE", business[0][0] + "/orders/" + identity)
        assert not business[1]
        replay = await runtime.execute(tool, ModelToolCall(id="invalid-replay", name=tool.key, arguments=args), execution)
        assert replay.status == "failed" and len(business[0][1]) == 3
        # Reconstruct the reader instead of relying on the in-process object.
        restored = type(store)(store._settings)
        rows = await restored.list_run_evidence(context.project_id, context.run_id, context.plan_id)
        projected = [{"origin_job_id": r["origin_job"]["id"],
            "binding": r["origin_job"]["metadata"]["case_action_binding"],
            "returned_evidence": r["returned_evidence"], "evidence_state": r["evidence_state"]} for r in rows]
        coverage = cleanup_coverage(context, projected)["cleanups"]["cleanup"]
        assert coverage["status"] == "passed" and coverage["covered_object_count"] == 1
        assert any(r.get("reason") == "not_executed" for r in coverage["excluded_operations"])
        await save("non-execution-recovery-" + fault, actual_non_execution_recovery=True, actual_coverage=coverage)
