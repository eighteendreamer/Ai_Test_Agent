"""Real HTTP effects must survive interruptions in subsequent artifact delivery."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import os

import pytest

from test_agent_case_assessment import business_http, execution
from test_agent_case_execution import adapter_environment, approval_continuation, regression_business_http
from test_case_plan_run_creation import LIVE
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.tool_job import ToolJobStatus
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait
from src.registry.tools import ToolRegistry
from src.schemas.run_management import RunItemLeaseRequest
from src.schemas.tool_runtime import ModelToolCall


class FailedDelivery:
    async def store_output_artifacts(self, output, **kwargs):
        raise RuntimeError("controlled artifact delivery failure after actual HTTP response")


class PausedDelivery:
    def __init__(self):
        self.entered = asyncio.Event()

    async def store_output_artifacts(self, output, **kwargs):
        self.entered.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_preparation_response_is_not_replaced_by_artifact_delivery_error(tmp_path, business_http, live):
    async with execution(tmp_path, business_http, live, perform_tools=False) as env:
        context, jobs, _, session, turn, attempt, *_ = env
        runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=FailedDelivery())
        tool = ToolRegistry().get("api-test-runner")
        url = business_http[0] + "/orders"
        record = await runtime.execute(tool, ModelToolCall(id="seed", name=tool.key, arguments={
            "worker_action": "execute_task", "task": {"task_id": "seed", "method": "POST",
                "full_url": url, "path": url, "timeout_seconds": 5}}), ToolExecutionContext(
                    session_id=session, turn_id=turn, trace_id=turn, user_message=context.objective,
                    normalized_input=context.objective, context_bundle={**context.runtime_context(), "attempt_id": attempt}))
        assert business_http[1] == [("POST", "/orders")]
        job = await jobs.get_job(record.job_id)
        assert job.output_payload["task_result"]["response_status"] == 201
        assert job.output_payload["task_result"]["response_body"]["order.id"] == business_http[2]
        assert record.status == job.status.value == "partial"
        assert job.output_payload["output_delivery"]["status"] == "interrupted"
        assert "artifact delivery failure" in job.output_payload["output_delivery"]["reason"]
        replay = await runtime.record_from_job(job)
        assert replay.output["task_result"] == record.output["task_result"]
        assert len(business_http[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_response_checkpoint_precedes_artifacts_and_approval_recovery_does_not_repost(tmp_path, regression_business_http, live):
    business_http, orders, created = regression_business_http
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, _, reader, sessions, runs, agent, adapter, args, clock, services = values
        gate = PausedDelivery()
        tools = services["tool_runtime"]
        tools._artifact_storage_service = gate
        pending = await adapter().execute(**await args())
        task = asyncio.create_task(approval_continuation(runs, reader, adapter, args, pending))
        try:
            await asyncio.wait_for(gate.entered.wait(), 5)
            assert business_http[1] == [("POST", "/orders")]
            actual_id = created[0]
            assert actual_id in orders
            saved = await env[1].get_job(pending.tool_job_id)
            assert saved.output_payload["task_result"]["response_body"]["order.id"] == actual_id
            assert saved.metadata["tool_output_observation"]["status"] == "completed"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        tools._artifact_storage_service = None

        async def wait_for_real_session_reaper():
            while True:
                await sessions.recover_expired_turn_executions()
                if not (await reader.get_session(pending.session_id)).metadata.get("turn_lease_token"):
                    return
                await asyncio.sleep(.5)

        await asyncio.wait_for(wait_for_real_session_reaper(), 22)
        clock[0] += timedelta(seconds=91)
        await runs.recover_expired(env[0].run_id)
        claim = (await env[7].claim_items(run_id=env[0].run_id, worker_id="output-recovery",
            limit=1, lease_seconds=60, now=clock[0]))[0]
        await runs.start_item(claim[0].id, RunItemLeaseRequest(lease_token=claim[1].lease_token))
        resumed = await adapter().execute(**await args())
        assert isinstance(resumed, AgentCaseApprovalWait) and resumed.approval_id != pending.approval_id
        recovered = await env[1].get_job(pending.tool_job_id)
        assert recovered.status.value == "partial"
        assert recovered.output_payload["task_result"]["response_body"]["order.id"] == actual_id
        assert recovered.output_payload["output_delivery"]["status"] == "interrupted"
        assert business_http[1] == [("POST", "/orders")]
        for _ in range(2):
            resumed = await approval_continuation(runs, reader, adapter, args, resumed)
        # This real target's count=1 contradicts the frozen count=0. Preserving
        # the preparation response must not turn the target into a false pass.
        assert resumed.completion.status == "failed"
        assert resumed.completion.actual["case_output"]["body"]["count"] == 1
        assert [method for method, _ in business_http[1]] == ["POST", "GET", "DELETE"]
        assert len(created) == 1 and not orders
        assert len(agent.requests) == 4
        if live:
            target = Path(os.getenv("QA_TEST_GATE_OUTPUT_DIR", str(tmp_path))) / "p2-operation-output-recovery.json"
            target.write_text(json.dumps({"scope": "real HTTP/PG/RustFS and production Session graph; controlled model; artifact interruption",
                "job_id": recovered.id, "observation": recovered.metadata["tool_output_observation"],
                "result": resumed.completion.status, "requests": business_http[1], "repeated_post": False,
                "delivery_status": recovered.output_payload["output_delivery"]["status"]}, ensure_ascii=False, indent=2), encoding="utf-8")


async def paused_actual_post(env, business_http):
    context, jobs, _, session, turn, attempt, *_ = env
    gate = PausedDelivery()
    runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=gate)
    tool = ToolRegistry().get("api-test-runner")
    url = business_http[0] + "/orders"
    task = asyncio.create_task(runtime.execute(tool, ModelToolCall(id="seed", name=tool.key, arguments={
        "worker_action": "execute_task", "task": {"task_id": "seed", "method": "POST",
            "full_url": url, "path": url, "timeout_seconds": 5}}), ToolExecutionContext(
                session_id=session, turn_id=turn, trace_id=turn, user_message=context.objective,
                normalized_input=context.objective, context_bundle={**context.runtime_context(), "attempt_id": attempt})))
    try:
        await asyncio.wait_for(gate.entered.wait(), 5)
        jobs_saved = await jobs.list_jobs(session_id=session)
        assert len(jobs_saved) == 1
        return jobs_saved[0].model_copy(deep=True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_committed_output_survives_lost_ack_and_new_service_read(tmp_path, business_http, live, monkeypatch):
    services = {}
    async with execution(tmp_path, business_http, live, perform_tools=False, services=services) as env:
        jobs = env[1]
        original = jobs._store.observe_job_output

        async def lose_ack(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("controlled lost acknowledgement after output commit")

        monkeypatch.setattr(jobs._store, "observe_job_output", lose_ack)
        saved = await paused_actual_post(env, business_http)
        fresh = ToolJobService(PostgresToolJobStore(services["environment"].settings) if live else jobs._store)
        reread = await fresh.get_job(saved.id)
        assert reread.output_payload == saved.output_payload
        recovered = await fresh.recover_observed_output(saved.id, reason="actual delivery interruption")
        assert recovered.status == ToolJobStatus.partial
        assert recovered.output_payload["task_result"]["response_body"]["order.id"] == business_http[2]
        assert len(business_http[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("change", ["output", "observation", "cancelled"])
async def test_changed_checkpoint_or_terminal_cannot_be_overwritten(tmp_path, business_http, live, change):
    async with execution(tmp_path, business_http, live, perform_tools=False) as env:
        jobs = env[1]
        saved = await paused_actual_post(env, business_http)
        changed = saved.model_copy(deep=True)
        if change == "output":
            changed.output_payload["task_result"]["response_body"]["order.id"] = "changed-after-observation"
        elif change == "observation":
            changed.metadata["tool_output_observation"]["output_hash"] = "0" * 64
        else:
            changed.status = ToolJobStatus.cancelled
        await jobs._store.save_job(changed)
        assert await jobs._store.settle_job_output(saved.id, expected_output=saved.output_payload,
            expected_observation=saved.metadata["tool_output_observation"], output_payload=saved.output_payload,
            status=ToolJobStatus.completed, summary="stale completion", error_message=None, artifact_count=0) is None
        if change == "cancelled":
            assert await jobs.heartbeat(saved.id) is None
            assert (await jobs.recover_observed_output(saved.id, reason="interrupted")).status == ToolJobStatus.cancelled
        else:
            with pytest.raises(ValueError, match="no longer matches"):
                await jobs.recover_observed_output(saved.id, reason="interrupted")
        current = await jobs.get_job(saved.id)
        assert current.output_payload == changed.output_payload
        assert current.metadata == changed.metadata
        assert current.status == changed.status
        assert len(business_http[1]) == 1
