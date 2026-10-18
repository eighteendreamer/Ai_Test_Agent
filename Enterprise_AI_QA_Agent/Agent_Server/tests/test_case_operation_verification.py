from copy import deepcopy

import pytest
import httpx

pytest_plugins = ("test_agent_case_assessment",)

from src.application.runtime.case_operation import (
    VERIFICATION_KEY,
    classify_operation_verification,
    save_operation_verification_observation,
)
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.runtime.tool_job_service import ToolJobService
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.case_execution import CaseOperationVerificationDeclaration
from src.schemas.case_planning import CasePlanningPolicy
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.schemas.tool_runtime import ModelToolCall
from src.registry.tools import ToolRegistry
from test_agent_case_assessment import execution
from test_agent_case_execution import adapter_environment
from test_case_plan_run_creation import LIVE


OPERATION_POLICY = CasePlanningPolicy(
    action_binding_protocol="case-action-binding-v1",
    action_admission_protocol="case-action-admission-v1",
    operation_protocol="case-operation-v1",
)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [LIVE])
async def test_live_formal_run_verifies_unknown_write_with_declared_read(
    tmp_path, business_http, live
):
    """A frozen formal item may verify an unknown write only through its declared read."""
    base_url, requests, actual_id = business_http
    declaration = {
        "declaration_id": "verify-created-order",
        "origin_operation_key": "target.create",
        "read_operation_key": "target.verify",
        "tool_key": "api-test-runner",
        "operation": "execute_task",
        "method": "GET",
        "path_template": f"{base_url}/orders/{{id}}",
        "effect_present_statuses": [200],
        "effect_absent_statuses": [404],
    }
    async with adapter_environment(
        tmp_path,
        business_http,
        live=live,
        planning_policy=OPERATION_POLICY,
        operation_verifications=[declaration],
    ) as env:
        run_env, _, _, _, runs, _, adapter, args, _, services = env
        jobs = run_env[1]
        current = await args()
        context, checkpoint, attempt = await adapter()._load(
            run=current["run"],
            item=current["item"],
            version=current["version"],
            attempt_id=current["attempt_id"],
        )
        await adapter()._save(
            current["item"],
            attempt,
            checkpoint.model_copy(update={"phase": "running"}),
        )
        runtime = services["tool_runtime"]
        tool = ToolRegistry().get("api-test-runner")
        tool_context = ToolExecutionContext(
            session_id=checkpoint.session_id,
            turn_id=checkpoint.turn_id,
            trace_id="formal-verification-trace",
            user_message=context.objective,
            normalized_input=context.objective,
            context_bundle={
                **context.runtime_context(),
                "case_execution_managed": True,
                "attempt_id": checkpoint.execution_attempt_id,
            },
        )

        origin_arguments = {
            ARGUMENT: {"item_key": "target", "operation_key": "target.create"},
            "worker_action": "execute_task",
            "task": {
                "task_id": "unknown-create",
                "method": "POST",
                "path": f"{base_url}/orders",
                "full_url": f"{base_url}/orders",
                "request_body": {"flag": False, "count": 0, "nullable": None},
                "timeout_seconds": 5,
            },
        }
        original_handler = runtime._handlers[tool.key]

        async def lose_return(arguments, execution_context):
            await original_handler(arguments, execution_context)
            raise ConnectionError("response lost after remote POST")

        runtime._handlers[tool.key] = lose_return
        try:
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(
                    tool,
                    ModelToolCall(
                        id="provider-create",
                        name=tool.key,
                        arguments=deepcopy(origin_arguments),
                    ),
                    tool_context,
                )
        finally:
            runtime._handlers[tool.key] = original_handler

        origin = next(job for job in await jobs.list_jobs() if job.call_id == "provider-create")
        assert origin.status.value == "running"
        assert origin.metadata["case_operation"]["decision"] == "dispatched"
        assert requests == [("POST", "/orders")]

        read_url = f"{base_url}/orders/{actual_id}"
        read_arguments = {
            ARGUMENT: {"item_key": "target", "operation_key": "target.verify"},
            "worker_action": "execute_task",
            "task": {
                "task_id": "verify-created-order",
                "method": "GET",
                "path": read_url,
                "full_url": read_url,
                "timeout_seconds": 5,
            },
        }
        read_record = await runtime.execute(
            tool,
            ModelToolCall(
                id="provider-verify",
                name=tool.key,
                arguments=read_arguments,
            ),
            tool_context,
        )
        assert read_record.status == "completed"
        assert requests == [("POST", "/orders"), ("GET", f"/orders/{actual_id}")]

        verification_job = await jobs.get_job(read_record.job_id)
        assert verification_job is not None
        verification = verification_job.metadata[VERIFICATION_KEY]
        assert verification["origin_job_id"] == origin.id
        assert verification["origin_operation_key"] == "target.create"
        assert verification["read_operation_key"] == "target.verify"
        # The frozen receipt keeps this as None until the operation's ordinary
        # output checkpoint is processed; validation rejects a guessed boolean.
        assert verification["action_sent"] is None
        assert verification["observation"] == {
            "version": "case-operation-verification-observation-v1",
            "status": "effect_present",
            "response_status": 200,
            "reason": "response_status_declared_present",
        }
        assert len(verification["sha256"]) == 64
        assert verification_job.output_payload["task_result"]["response_status"] == 200

        # Read the immutable PostgreSQL operation index directly: origin and
        # verification are separate logical operations in the same frozen scope.
        settings = services["environment"].settings
        operation_index = jobs._case_action_admission.operations
        with postgres_connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT record FROM {operation_index.table} "
                "WHERE record->>'origin_job_id' IN (%s, %s) "
                "ORDER BY record->'scope'->>'operation_key'",
                (origin.id, read_record.job_id),
            )
            indexed = [row["record"] for row in cursor.fetchall()]
        assert [entry["scope"]["operation_key"] for entry in indexed] == [
            "target.create", "target.verify"
        ]
        assert [entry["origin_job_id"] for entry in indexed] == [
            origin.id, read_record.job_id
        ]

        # The unknown origin remains unknown; the read is separate evidence and
        # does not rewrite or re-send the original POST.
        origin_after = await jobs.get_job(origin.id)
        assert origin_after.status.value == "running"
        assert origin_after.output_payload == {}
