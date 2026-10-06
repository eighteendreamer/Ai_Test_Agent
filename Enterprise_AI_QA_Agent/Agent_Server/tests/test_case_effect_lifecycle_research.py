"""Actual effect evidence must outlive a single per-step receipt summary."""
from copy import deepcopy

import pytest

from test_agent_case_assessment import receipt
from test_agent_case_execution import regression_business_http
from test_case_operations import LIVE, enable_protocol, operation_runtime, save_operation
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.tool_runtime import ModelToolCall


async def action(runtime, tool, execution, arguments, call_id, item_key, operation_key, method, path):
    wire = deepcopy(arguments)
    wire[ARGUMENT] = {"item_key": item_key, "operation_key": operation_key}
    wire["task"].update(task_id=call_id, method=method, path=path)
    if method != "POST":
        wire["task"].pop("request_body", None)
    return await runtime.execute(tool, ModelToolCall(id=call_id, name=tool.key, arguments=wire), execution)


async def complete(values, records, execution, *, cleanup_call="cleanup", verify_method=False):
    body = receipt()
    cleanup = next(step for step in body["steps"] if step["item_key"] == "cleanup")
    for check in cleanup["checks"]:
        check["observations"][0]["call_id"] = cleanup_call
    cleanup["outputs"]["body"]["call_id"] = cleanup_call
    if verify_method:
        cleanup["checks"].append({"name": "实际操作与固定清理依据一致", "operator": "equals",
            "expected_path_tokens": ["method"], "observations": [{"call_id": cleanup_call,
                "output_path_tokens": ["task_result", "execution_checkpoint", "http_observation_v1", "hops", "0", "method"]}]})
    context = AgentCaseExecutionContext.model_validate(execution.context_bundle["case_execution"])
    item = await values[4].get_item(context.run_item_id)
    outcome = await AgentCaseAssessmentService(values[0][1]).assess(context=context, receipt=body,
        tool_records=records, session_id=execution.session_id, turn_id=execution.turn_id,
        attempt_id=execution.context_bundle["attempt_id"], model_key="controlled-effect-research",
        lease_token=item.lease_token)
    result = await values[4].complete_item(values[0][0].run_item_id, outcome.completion)
    return result


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("clean_all", [False, True])
async def test_actual_two_declared_creations_require_both_objects_to_be_cleaned(
    tmp_path, regression_business_http, clean_all,
):
    business = regression_business_http
    async with operation_runtime(tmp_path, business) as (values, runtime, tool, execution, arguments):
        root = business[0][0]
        records = [await action(runtime, tool, execution, arguments, "seed", "seed", "seed.first", "POST", root + "/orders")]
        records.append(await action(runtime, tool, execution, arguments, "seed-second", "seed", "seed.second", "POST", root + "/orders"))
        first, second = business[2]
        records.append(await action(runtime, tool, execution, arguments, "target", "target", "target.read", "GET", root + "/orders/" + first))
        records.append(await action(runtime, tool, execution, arguments, "cleanup", "cleanup", "cleanup.first", "DELETE", root + "/orders/" + first))
        if clean_all:
            records.append(await action(runtime, tool, execution, arguments, "cleanup-second", "cleanup", "cleanup.second", "DELETE", root + "/orders/" + second))
        await save_operation(tmp_path, "all-two-input" if clean_all else "one-of-two-input", values, business,
            effect_research_baseline="e8e9ed16", before_result_assessment=True)
        result = await complete(values, records, execution, cleanup_call="cleanup-second" if clean_all else "cleanup")
        assert len(business[2]) == 2
        assert set(business[1]) == (set() if clean_all else {second})
        cleanup_status = result.actual["preparations"]["cleanup"]["status"]
        await save_operation(tmp_path, "all-two-objects-cleaned" if clean_all else "one-of-two-cleaned", values, business,
            effect_research_baseline="e8e9ed16", actual_result=result.model_dump(mode="json"),
            deliberate_operations_have_distinct_keys=True, reported_cleanup_status=cleanup_status,
            missing_effect_object_ids=list(business[1]),
            full_effect_lifecycle_implemented=False, all_known_created_objects_actually_removed=not business[1])
        if clean_all:
            assert cleanup_status == "passed"
        else:
            assert cleanup_status != "passed", "Cleanup receipt passed while the second deliberately created object still exists"


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("after_call", [False, True])
async def test_actual_reading_an_order_does_not_prove_its_cleanup(
    tmp_path, regression_business_http, after_call,
):
    business = regression_business_http
    async with operation_runtime(tmp_path, business) as (values, runtime, tool, execution, arguments):
        root = business[0][0]
        records = [await action(runtime, tool, execution, arguments, "seed", "seed", "seed.first", "POST", root + "/orders")]
        created_id = business[2][0]
        records.append(await action(runtime, tool, execution, arguments, "target", "target", "target.read", "GET", root + "/orders/" + created_id))
        if after_call:
            records.append(await action(runtime, tool, execution, arguments, "cleanup", "cleanup", "cleanup.read", "GET", root + "/orders/" + created_id))
        await save_operation(tmp_path, "after-read-input" if after_call else "other-read-input", values, business,
            effect_research_baseline="e8e9ed16", before_result_assessment=True)
        result = await complete(values, records, execution, cleanup_call="cleanup" if after_call else "target")
        assert set(business[1]) == {created_id}
        assert all(method != "DELETE" for method, _ in business[0][1])
        cleanup_status = result.actual["preparations"]["cleanup"]["status"]
        await save_operation(tmp_path, "after-phase-read" if after_call else "other-step-read", values, business,
            effect_research_baseline="e8e9ed16", actual_result=result.model_dump(mode="json"),
            reported_cleanup_status=cleanup_status, no_actual_cleanup_HTTP=True,
            read_was_owned_by_after_step=after_call, missing_effect_object_ids=list(business[1]),
            full_effect_lifecycle_implemented=False)
        assert cleanup_status != "passed", "A successful read was accepted as cleanup evidence while the object still exists"


@pytest.mark.asyncio
@LIVE
async def test_actual_existing_network_evidence_can_disprove_a_cleanup_read(tmp_path, regression_business_http):
    business = regression_business_http
    async with operation_runtime(tmp_path, business) as (values, runtime, tool, execution, arguments):
        root = business[0][0]
        records = [await action(runtime, tool, execution, arguments, "seed", "seed", "seed.first", "POST", root + "/orders")]
        created_id = business[2][0]
        for call_id, item_key in [("target", "target"), ("cleanup", "cleanup")]:
            records.append(await action(runtime, tool, execution, arguments, call_id, item_key,
                item_key + ".read", "GET", root + "/orders/" + created_id))
        result = await complete(values, records, execution, verify_method=True)
        assert result.status == "failed" and result.actual["preparations"]["cleanup"]["status"] == "failed"
        assert set(business[1]) == {created_id}
        verification = next(v for v in result.actual["verification_results"] if v["metadata"]["plan_item_key"] == "cleanup")
        check = verification["metadata"]["checks"][-1]
        assert (check["expected"], check["actual"], check["status"]) == ("DELETE", "GET", "failed")
        await save_operation(tmp_path, "existing-method-check", values, business,
            effect_research_baseline="e8e9ed16", actual_result=result.model_dump(mode="json"),
            existing_literal_output_check_verified=True, no_new_method_specific_backend_guard=True,
            method_check_does_not_prove_all_objects_removed=True, full_effect_lifecycle_implemented=False)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("lose_return", [False, True])
async def test_actual_cleanup_return_and_unknown_delivery_have_different_evidence(
    tmp_path, regression_business_http, lose_return,
):
    business = regression_business_http
    async with operation_runtime(tmp_path, business) as (values, runtime, tool, execution, arguments):
        root = business[0][0]
        await action(runtime, tool, execution, arguments, "seed", "seed", "seed.first", "POST", root + "/orders")
        created_id = business[2][0]
        handler = runtime._handlers[tool.key]
        if lose_return:
            async def lost(raw, context):
                await handler(raw, context)
                raise ConnectionError("Actual removal returned; response lost before durable observation")
            runtime._handlers[tool.key] = lost
        try:
            if lose_return:
                with pytest.raises(CaseActionClaimUnavailable):
                    await action(runtime, tool, execution, arguments, "cleanup", "cleanup", "cleanup.remove", "DELETE", root + "/orders/" + created_id)
            else:
                await action(runtime, tool, execution, arguments, "cleanup", "cleanup", "cleanup.remove", "DELETE", root + "/orders/" + created_id)
        finally:
            runtime._handlers[tool.key] = handler
        assert not business[1]
        origin = next(j for j in await values[0][1].list_jobs() if j.call_id == "cleanup")
        # Preserve task_id and every business input; only provider identity changes.
        replay = await runtime.execute(tool, ModelToolCall(id="cleanup-recovery", name=tool.key,
            arguments=deepcopy(origin.input_payload)), execution)
        assert len(business[0][1]) == 2
        if lose_return:
            assert origin.status.value == "running" and not origin.output_payload
            assert replay.status == "denied" and replay.output["error"] == "case_operation_outcome_unknown"
        else:
            assert origin.status.value == "completed" and replay.status == "completed"
        assert await values[0][1].get_job(origin.id) == origin
        await save_operation(tmp_path, "cleanup-return-unknown" if lose_return else "cleanup-return-known", values, business,
            effect_research_baseline="e8e9ed16", cleanup_original_job=origin.model_dump(mode="json"),
            original_remove_actually_completed=True, durable_remove_return_known=not lose_return,
            replay_record=replay.model_dump(mode="json"), no_new_HTTP_on_recovery=True,
            full_effect_lifecycle_implemented=False)
