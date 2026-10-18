import pytest

from src.application.runtime.case_operation import (
    VERIFICATION_KEY,
    classify_operation_verification,
    save_operation_verification_observation,
)
from src.application.runtime.tool_job_service import ToolJobService
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.case_execution import CaseOperationVerificationDeclaration


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
