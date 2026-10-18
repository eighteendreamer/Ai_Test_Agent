import pytest
import httpx

pytest_plugins = ("test_agent_case_assessment",)

from src.application.runtime.case_operation import (
    VERIFICATION_KEY,
    classify_operation_verification,
    save_operation_verification_observation,
)
from src.application.runtime.tool_job_service import ToolJobService
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.case_execution import CaseOperationVerificationDeclaration
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.schemas.tool_runtime import ModelToolCall
from src.registry.tools import ToolRegistry
from test_agent_case_assessment import execution
from test_case_plan_run_creation import LIVE


def _receipt(*, present=None, absent=None):
    return {
        "version": "case-operation-verification-v1",
        "declaration_id": "check-order",
        "origin_job_id": "origin-job",
        "origin_operation_id": "origin-operation",
        "origin_operation_key": "order.create",
        "read_job_id": "read-job",
        "read_operation_id": "read-operation",
        "read_operation_key": "order.read",
        "action_sent": True,
        "decision": "dispatched",
        "effect_present_statuses": list(present or []),
        "effect_absent_statuses": list(absent or []),
        "observation": None,
    }


@pytest.mark.parametrize(
    ("status_code", "present", "absent", "expected", "reason"),
    [
        (201, [201], [], "effect_present", "response_status_declared_present"),
        (404, [], [404], "effect_absent", "response_status_declared_absent"),
        (204, [201], [404], "inconclusive", "response_status_not_declared"),
    ],
)
def test_operation_verification_classifies_only_declared_http_status(
    status_code, present, absent, expected, reason
):
    result = classify_operation_verification(
        _receipt(present=present, absent=absent),
        {"task_result": {"response_status": status_code}},
        job_status="completed",
    )

    assert result == {
        "version": "case-operation-verification-observation-v1",
        "status": expected,
        "response_status": status_code,
        "reason": reason,
    }


def test_operation_verification_failure_or_missing_response_is_inconclusive():
    receipt = _receipt(present=[201], absent=[404])

    assert classify_operation_verification(
        receipt, {"task_result": {"response_status": 201}}, job_status="failed"
    )["status"] == "inconclusive"
    assert classify_operation_verification(
        receipt, {"error": "timeout"}, job_status="completed"
    )["reason"] == "read_response_missing"


@pytest.mark.parametrize(
    "payload",
    [
        {"effect_present_statuses": [200, 200]},
        {"effect_present_statuses": [200], "effect_absent_statuses": [200]},
        {"effect_present_statuses": [99]},
        {"effect_absent_statuses": [600]},
    ],
)
def test_operation_verification_declaration_rejects_ambiguous_statuses(payload):
    with pytest.raises(ValueError):
        CaseOperationVerificationDeclaration.model_validate(
            {
                "declaration_id": "check-order",
                "origin_operation_key": "order.create",
                "read_operation_key": "order.read",
                "tool_key": "http",
                "operation": "execute_task",
                "method": "GET",
                "path_template": "/orders/{id}",
                **payload,
            }
        )


@pytest.mark.asyncio
async def test_observe_output_persists_output_and_verification_observation_together():
    store = InMemoryToolJobStore()
    jobs = ToolJobService(store)
    tool = ToolDescriptor(
        key="http-client",
        name="HTTP client",
        description="test HTTP client",
        category="test",
    )
    job = await jobs.create_job(
        tool=tool,
        call_id="read-call",
        session_id="session",
        turn_id="turn",
        trace_id="trace",
        input_payload={"task": {"method": "GET"}},
        metadata={VERIFICATION_KEY: _receipt(present=[201], absent=[404])},
    )
    await jobs.mark_running(job)

    saved = await jobs.observe_output(
        job.id,
        status="completed",
        output_payload={"task_result": {"response_status": 201, "response_body": {"id": "1"}}},
    )
    persisted = await jobs.get_job_detail(job.id)

    assert persisted is not None
    assert saved.output_payload == persisted.output_payload
    verification = persisted.metadata[VERIFICATION_KEY]
    assert verification["observation"]["status"] == "effect_present"
    assert verification["observation"]["response_status"] == 201
    assert len(verification["sha256"]) == 64
    assert persisted.metadata["tool_output_observation"]["output_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_live_http_and_postgres_job_persist_verification_observation(
    tmp_path, business_http, live
):
    async with execution(tmp_path, business_http, live=live, perform_tools=False) as env:
        context, jobs, _, session_id, turn_id, attempt_id, *_ = env
        async with httpx.AsyncClient() as client:
            created = await client.post(business_http[0] + "/orders")
        assert created.status_code == 201
        order_id = created.json()["order.id"]
        url = business_http[0] + "/orders/" + order_id
        tool = ToolRegistry().get("api-test-runner")
        call = ModelToolCall(
            id="verification-read",
            name=tool.key,
            arguments={
                "worker_action": "execute_task",
                "task": {
                    "task_id": "verification-read",
                    "method": "GET",
                    "path": url,
                    "full_url": url,
                    "timeout_seconds": 5,
                },
            },
        )
        job = await jobs.create_job(
            tool=tool,
            call_id=call.id,
            session_id=session_id,
            turn_id=turn_id,
            trace_id="verification-trace",
            input_payload=call.arguments,
            metadata={VERIFICATION_KEY: _receipt(present=[200], absent=[404])},
        )
        runtime = ToolRuntimeService(tool_job_service=jobs)
        execution_context = ToolExecutionContext(
            session_id=session_id,
            turn_id=turn_id,
            trace_id="verification-trace",
            user_message="verify the created order",
            normalized_input="verify the created order",
            context_bundle={**context.runtime_context(), "attempt_id": attempt_id},
            tool_job_id=job.id,
            tool_key=tool.key,
            call_id=call.id,
        )

        record = await runtime.execute(tool, call, execution_context)
        persisted = await jobs.get_job_detail(job.id)

        assert record.status == "completed"
        assert [method for method, _ in business_http[1]] == ["POST", "GET"]
        assert persisted is not None
        assert persisted.metadata[VERIFICATION_KEY]["observation"]["status"] == "effect_present"
        assert persisted.metadata[VERIFICATION_KEY]["observation"]["response_status"] == 200
        assert persisted.output_payload["task_result"]["response_status"] == 200
