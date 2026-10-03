"""Actual graph/HTTP/PG checks for durable Agent admission and recovery."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI

from test_agent_case_assessment import business_http, execution
from test_agent_case_session import (
    ControlledAgent, ControlledModels, EmptyMCP, session_stores,
    AgentRegistry, ToolRegistry, SkillRegistry, RuntimeControlRegistry,
    ToolRuntimeService, build_agent_graph, SkillRuntimeService, PermissionService,
    PromptAssemblyService, RuntimeService, SessionService, InputOrchestratorService, ModeRegistry,
)
from test_case_plan_run_creation import LIVE
from src.application.test_runs.agent_case_execution import (
    AgentCaseExecutionAdapter, AgentCaseApprovalWait, AgentCaseRecoveryRequired, CHECKPOINT_KEY,
)
from src.application.test_runs.run_service import TestRunService as RunService
from src.schemas.run_management import RunItemApprovalWaitRequest, RunItemCheckpointRequest, RunItemLeaseRequest
from src.schemas.session import ApprovalDecisionRequest, ToolApprovalStatus
from src.api.routes.run_management import router
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.schemas.run_management import RunItemExecuteRequest, RunItemApprovalDecisionRequest, RunItemApprovalPending, RunClaimRequest
from src.schemas.model_config import ModelInvocationResult
from src.application.orchestration.redis_execution_dispatcher import RedisExecutionDispatcher
from src.infrastructure.redis_task_queue import QueuedTask
from src.schemas.run_dispatch import RunDispatchTask
from tests.test_live_resource_leases import manager as resource_manager
from src.core.request_context import bind_request_context, get_request_context
from src.application.test_runs.agent_regression_service import AgentRegressionService
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.run_management import RegressionRunCreateRequest
from src.api.routes.case_plans import router as plan_router
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker


@pytest.fixture
def regression_business_http():
    requests, orders, created = [], {}, []
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            with lock:
                order_id = "regression-" + uuid4().hex
                # The first target genuinely fails its frozen expected count=0.
                body = {"order.id": order_id, "flag": False, "count": 1 if not created else 0,
                        "nullable": None, "message": "订单处于待支付状态，尚未进入出库流程"}
                created.append(order_id)
                orders[order_id] = body
                requests.append(("POST", self.path))
            self.reply(201, body)

        def do_GET(self):
            with lock:
                body = orders.get(self.path.removeprefix("/orders/"))
                requests.append(("GET", self.path))
            self.reply(200 if body else 404, body or {})

        def do_DELETE(self):
            with lock:
                body = orders.pop(self.path.removeprefix("/orders/"), None)
                requests.append(("DELETE", self.path))
            self.reply(200 if body else 404, body or {})

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"http://127.0.0.1:{server.server_port}", requests, None), orders, created
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PUBLIC_PLAN_WORKER") != "1", reason="requires actual isolated Redis/MySQL/PG/RustFS")
async def test_public_plan_launch_outbox_redis_worker_agent_approvals_and_terminal_replay(tmp_path, regression_business_http):
    business, orders, created = regression_business_http
    async with adapter_environment(tmp_path, business, live=True, start_item=False) as values:
        env, _, reader, _, runs, _, adapter, _, _, services = values
        base, creator = services["environment"], services["creator"]
        table = "up_launch_worker_" + uuid4().hex[:12]
        with postgres_connect(base.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None
        base.settings.database.postgres_task_outbox_table = table
        queue = RedisTaskQueue(base.settings.database.redis_url,
            stream="qa:test:public-launch:" + uuid4().hex, group="launch-workers")
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        creator._modes, creator._outbox_table, creator._stream = ModeRegistry(), table, queue.stream
        app = FastAPI()
        app.state.case_plan_run_service = creator
        app.state.test_run_service, app.state.test_run_execution_service = runs, service
        app.include_router(plan_router, prefix="/api/v1")
        app.include_router(router, prefix="/api/v1")
        try:
            await queue.connect()
            outbox = PostgresTaskOutbox(base.settings)
            await outbox.initialize()
            dispatcher = RedisExecutionDispatcher(queue=queue, test_run_service=runs, execution_service=service, outbox=outbox)
            async def handle(task):
                await dispatcher.handle(task, worker_id="actual-launch-worker")
            worker = RedisTaskWorker(queue, consumer="actual-launch-worker", handler=handle, block_ms=0)
            plan = env[6].run.plan_snapshot.plan
            path = f"/api/v1/projects/{plan.project_id}/case-plans/{plan.id}/runs"
            payload = {"creation_key": "actual-worker-launch", "mode_key": "api_testing"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://launch") as client:
                response = await client.post(path, json=payload)
                assert response.status_code == 201, response.text
                detail = response.json()
                run_id, item_id = detail["run"]["id"], detail["items"][0]["id"]
                assert detail["run"]["status"] == "queued" and not business[1]
                assert await outbox.relay_once(queue) == 1
                assert await queue._client.xlen(queue.stream) == 1
                assert await worker.run_once() == 1
                assert not await queue.pending()
                waiting = await runs.get(run_id)
                assert waiting.items[0].status == "waiting_approval" and not business[1]
                for _ in range(3):
                    approval_id = (await runs.get(run_id)).items[0].approval_id
                    response = await client.post(f"/api/v1/run-items/{item_id}/approval",
                        json={"approval_id": approval_id, "decision": "approved"})
                    assert response.status_code == 200, response.text
                completed = await runs.get(run_id)
                assert completed.run.status == "completed" and completed.results[0].status == "failed"
                assert completed.results[0].actual["case_output"]["body"]["count"] == 1
                assert not orders and len(created) == 1
                assert business[1] == [("POST", "/orders"), ("GET", "/orders/" + created[0]), ("DELETE", "/orders/" + created[0])]
                with postgres_connect(base.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT payload, published_at FROM {table}")
                    saved = cur.fetchall()
                assert len(saved) == 1 and saved[0]["published_at"] is not None
                # At-least-once transport redelivery must not reclaim a terminal item.
                await queue.enqueue(saved[0]["payload"])
                assert await worker.run_once() == 1
                assert not await queue.pending() and await runs.get(run_id) == completed
                assert len(business[1]) == 3
                replay = await client.post(path, json=payload)
                assert replay.status_code == 201 and replay.json()["run"]["id"] == run_id
                assert replay.json()["run"]["status"] == "completed"
                assert len(business[1]) == 3
        finally:
            if queue._client is not None:
                await queue._client.delete(queue.stream, queue.stream + ":dead_letter")
                await queue.close()
            with postgres_connect(base.settings) as conn, conn.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS "{table}"')


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_agent_regression_http_run_recreates_data_and_preserves_actual_parent_failure(tmp_path, regression_business_http, live, monkeypatch):
    business, orders, created = regression_business_http
    async with adapter_environment(tmp_path, business, live, start_item=False) as values:
        env, _, reader, _, runs, agent, adapter, _, _, services = values
        base = services["environment"]
        runs.set_agent_regression_service(AgentRegressionService(plan_store=base.store,
            plan_run_service=services["creator"], planning_service=base.service, run_store=env[7]))
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service, app.state.test_run_service = service, runs
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            async def execute_item(item_id, lease_token):
                prefix = "/api/v1/run-items/" + item_id
                response = await client.post(prefix + "/execute", json={"lease_token": lease_token})
                assert response.status_code == 200, response.text
                data = response.json()
                for _ in range(3):
                    assert data["approval_id"]
                    response = await client.post(prefix + "/approval", json={"approval_id": data["approval_id"], "decision": "approved"})
                    assert response.status_code == 200, response.text
                    data = response.json()
                return data

            failed = await execute_item(env[0].run_item_id, env[6].items[0].lease_token)
            assert failed["status"] == "failed"
            assert failed["actual"]["case_output"]["body"]["count"] == 1
            original = await runs.get(env[0].run_id)
            assert not orders and len(created) == 1
            if live:
                insert = env[7]._insert_run_and_items
                tables = [base.settings.database.postgres_test_suite_table, base.settings.database.postgres_test_run_table,
                    base.settings.database.postgres_test_run_item_table, services["creator"]._outbox_table]

                def counts():
                    with postgres_connect(base.settings) as conn, conn.cursor() as cur:
                        values = []
                        for table in tables:
                            cur.execute(f"SELECT count(*) AS n FROM {table}")
                            values.append(cur.fetchone()["n"])
                    return values

                before = counts()
                def interrupted(cur, run, items):
                    insert(cur, run, items)
                    raise RuntimeError("controlled regression crash after real inserts")
                monkeypatch.setattr(env[7], "_insert_run_and_items", interrupted)
                with pytest.raises(RuntimeError, match="controlled regression crash"):
                    await runs.create_regression(env[0].run_id, RegressionRunCreateRequest(result_ids=[failed["id"]]))
                assert counts() == before
                assert await runs.get(env[0].run_id) == original
                monkeypatch.setattr(env[7], "_insert_run_and_items", insert)
            response = await client.post("/api/v1/runs/" + env[0].run_id + "/regression", json={"result_ids": [failed["id"]]})
            assert response.status_code == 201, response.text
            new = response.json()
            assert new["run"]["execution_strategy"] == "agent"
            assert new["run"]["parent_run_id"] == env[0].run_id
            claim = (await runs.claim(new["run"]["id"], RunClaimRequest(worker_id="real-regression", limit=1))).claims[0]
            passed = await execute_item(claim.item.id, claim.lease_token)
            assert passed["status"] == "passed"
            assert passed["regression_source_result_id"] == failed["id"]
            assert passed["actual"]["case_output"]["body"]["count"] == 0
            assert len(created) == 2 and created[0] != created[1] and not orders
            assert business[1] == [("POST", "/orders"), ("GET", "/orders/" + created[0]), ("DELETE", "/orders/" + created[0]),
                                   ("POST", "/orders"), ("GET", "/orders/" + created[1]), ("DELETE", "/orders/" + created[1])]
            assert await runs.get(env[0].run_id) == original
            assert (await runs.get(new["run"]["id"])).run.status == "completed"
            assert not (await runs.claim(new["run"]["id"], RunClaimRequest(worker_id="terminal", limit=1))).claims
            assert len(agent.requests) == 8


@asynccontextmanager
async def adapter_environment(tmp_path, business_http, live=False, agent=None, start_item=True):
    async with AsyncExitStack() as resources:
        services = {}
        env = await resources.enter_async_context(execution(tmp_path, business_http, live,
            perform_tools=False, with_business_instructions=True, services=services, start_item=start_item))
        base = services['environment']
        writer, reader = await resources.enter_async_context(session_stores(env[1], live))
        services['creator']._sessions = reader
        tools, skills, control = ToolRegistry(), SkillRegistry(), RuntimeControlRegistry()
        agent = agent or ControlledAgent()
        runtime_tools = ToolRuntimeService(tool_job_service=env[1], session_store=writer)
        services['tool_runtime'] = runtime_tools
        graph = build_agent_graph(agent_registry=AgentRegistry(), tool_registry=tools,
            model_registry=ControlledModels(), skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
            mcp_runtime_service=EmptyMCP(), memory_runtime_service=None, test_case_embedding_service=None,
            permission_service=PermissionService(), prompt_assembly_service=PromptAssemblyService(),
            model_runtime_service=agent, tool_runtime_service=runtime_tools, runtime_control=control,
            tool_job_service=env[1])
        runtime = RuntimeService(graph=graph, model_runtime_service=agent, tool_runtime_service=runtime_tools,
            tool_registry=tools, runtime_control=control, tool_job_service=env[1], permission_service=PermissionService())
        sessions = SessionService(writer, InputOrchestratorService(ModeRegistry()), runtime, ModeRegistry(),
            project_service=base.projects, turn_execution_lease_seconds=15)
        clock = [env[8]]
        runs = RunService(store=env[7], project_service=base.projects, suite_service=None,
            test_case_service=base.cases, session_store=writer, tool_job_service=env[1], clock=lambda: clock[0])

        def adapter():
            return AgentCaseExecutionAdapter(run_service=runs, session_service=sessions,
                session_store=reader, tool_job_service=env[1])

        async def args():
            current = await runs.get_item(env[0].run_item_id)
            return {'case': await base.cases.get_case(current.case_id),
                'version': await base.cases.get_version(current.case_version_id),
                'item': current, 'run': await runs.get_record(current.run_id),
                'attempt_id': (await runs.get_latest_attempt(current.id)).id}

        try:
            yield env, writer, reader, sessions, runs, agent, adapter, args, clock, services
        finally:
            await sessions.close()


async def approval_continuation(runs, reader, adapter, args, pending):
    values = await args()
    approval = next(a for a in await reader.list_approvals(pending.session_id) if a.id == pending.approval_id)
    await runs.mark_waiting_approval(values['item'].id, RunItemApprovalWaitRequest(
        lease_token=values['item'].lease_token, approval_id=pending.approval_id,
        tool_job_id=pending.tool_job_id, reason=pending.summary,
        approval_scope_hash=approval.metadata['approval_scope_hash']))
    resumed = await runs.resume_waiting_approval(values['item'].id, pending.approval_id)
    await runs.start_item(resumed.id, RunItemLeaseRequest(lease_token=resumed.lease_token))
    return await adapter().resume_approval(**await args(), approval_id=pending.approval_id,
        payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_durable_admission_owned_approvals_actual_outputs_and_terminal_result(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, writer, reader, sessions, runs, agent, adapter, args, _, _ = values
        pending = await adapter().execute(**await args())
        assert isinstance(pending, AgentCaseApprovalWait) and not business_http[1]
        first_attempt = await runs.get_latest_attempt(env[0].run_item_id)
        assert first_attempt.checkpoint_key == CHECKPOINT_KEY
        assert first_attempt.checkpoint_payload['phase'] == 'snapshot'
        assert first_attempt.checkpoint_payload['execution_attempt_id'] == first_attempt.id
        first_approval = next(entry for entry in await reader.list_approvals(pending.session_id)
            if entry.id == pending.approval_id)
        assert first_approval.metadata['run_id'] == env[0].run_id
        assert first_approval.metadata['run_item_id'] == env[0].run_item_id
        assert first_approval.metadata['attempt_id'] == first_attempt.id
        with pytest.raises(ValueError, match='test run item approval endpoint'):
            await sessions.resolve_approval(pending.session_id, pending.approval_id,
                ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
        for count in range(3):
            previous = pending
            pending = await approval_continuation(runs, reader, adapter, args, pending)
            assert len(business_http[1]) == count + 1
            replay = await adapter().resume_approval(**await args(), approval_id=previous.approval_id,
                payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
            assert len(business_http[1]) == count + 1
            if count < 2:
                assert isinstance(pending, AgentCaseApprovalWait)
                assert replay.approval_id == pending.approval_id
        outcome = pending
        assert outcome.completion.status == 'passed'
        actual = outcome.completion.actual
        assert actual['case_output']['body']['order.id'] == business_http[2]
        assert actual['case_output']['body']['flag'] is False
        assert actual['preparations']['seed']['status'] == actual['preparations']['cleanup']['status'] == 'passed'
        assert actual['execution_identity']['execution_attempt_id'] == first_attempt.id
        result = await runs.complete_item(env[0].run_item_id, outcome.completion)
        assert (await runs.get_result(result.id)).actual == actual
        assert len(agent.requests) == 4
        assert not await env[7].claim_items(run_id=env[0].run_id, worker_id='terminal',
            limit=1, lease_seconds=60, now=env[8])


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_saved_terminal_snapshot_survives_lost_completion_and_new_attempt_without_http_replay(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, _, reader, _, runs, agent, adapter, args, clock, _ = values
        pending = await adapter().execute(**await args())
        for _ in range(3):
            pending = await approval_continuation(runs, reader, adapter, args, pending)
        original = await runs.get_latest_attempt(env[0].run_item_id)
        clock[0] += timedelta(seconds=91)
        assert (await runs.recover_expired(env[0].run_id)).recovered_count == 1
        claim = (await env[7].claim_items(run_id=env[0].run_id, worker_id='recovered',
            limit=1, lease_seconds=60, now=clock[0]))[0]
        await runs.start_item(claim[0].id, RunItemLeaseRequest(lease_token=claim[1].lease_token))
        recovered = await adapter().execute(**await args())
        assert len(business_http[1]) == 3 and len(agent.requests) == 4
        identity = recovered.completion.actual['execution_identity']
        assert identity['execution_attempt_id'] == original.id
        assert identity['result_attempt_id'] == claim[1].id
        result = await runs.complete_item(claim[0].id, recovered.completion)
        assert result.attempt_id == claim[1].id
        assert (await runs.get_result(result.id)).actual['execution_identity'] == identity
        jobs = [await env[1].get_job(record.job_id) for record in recovered.tool_records]
        assert all(job.metadata['attempt_id'] == original.id for job in jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_crash_after_intent_before_any_snapshot_requires_reconciliation(tmp_path, business_http, live, monkeypatch):
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, _, _, sessions, runs, agent, adapter, args, clock, _ = values

        async def crash(*args, **kwargs):
            saved = await runs.get_latest_attempt(env[0].run_item_id)
            assert saved.checkpoint_payload['phase'] == 'running'
            raise RuntimeError('controlled crash after durable execution intent')

        monkeypatch.setattr(sessions, 'execute_case_turn', crash)
        with pytest.raises(RuntimeError, match='controlled crash'):
            await adapter().execute(**await args())
        clock[0] += timedelta(seconds=61)
        await runs.recover_expired(env[0].run_id)
        claim = (await env[7].claim_items(run_id=env[0].run_id, worker_id='recovered',
            limit=1, lease_seconds=60, now=clock[0]))[0]
        await runs.start_item(claim[0].id, RunItemLeaseRequest(lease_token=claim[1].lease_token))
        with pytest.raises(AgentCaseRecoveryRequired, match='不能重复操作'):
            await adapter().execute(**await args())
        assert not business_http[1] and not agent.requests


class CancelAfterCreated(ControlledAgent):
    def __init__(self):
        super().__init__()
        self.interrupted = False
        self.after_created = asyncio.Event()

    async def invoke(self, key, request):
        if not self.interrupted and any(message.get('role') == 'tool' for message in request.messages):
            self.interrupted = True
            self.after_created.set()
            await asyncio.Event().wait()
        return await super().invoke(key, request)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_http_completed_but_approval_snapshot_stale_replays_tool_evidence_without_reposting(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, CancelAfterCreated()) as values:
        env, writer, reader, sessions, runs, agent, adapter, args, clock, _ = values
        pending = await adapter().execute(**await args())
        task = asyncio.create_task(approval_continuation(runs, reader, adapter, args, pending))
        try:
            await asyncio.wait_for(agent.after_created.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert business_http[1] == [('POST', '/orders')]
        job = await env[1].get_job(pending.tool_job_id)
        assert job.status.value == 'completed'
        # The cancelled coroutine is terminal. Observe the actual preserved
        # lease until the real reaper removes it; do not assume clock alignment.
        async def wait_for_recovery():
            while True:
                await sessions.recover_expired_turn_executions()
                if not (await reader.get_session(pending.session_id)).metadata.get('turn_lease_token'):
                    return
                await asyncio.sleep(.5)
        await asyncio.wait_for(wait_for_recovery(), 22)
        clock[0] += timedelta(seconds=91)
        await runs.recover_expired(env[0].run_id)
        claim = (await env[7].claim_items(run_id=env[0].run_id, worker_id='recovered',
            limit=1, lease_seconds=60, now=clock[0]))[0]
        await runs.start_item(claim[0].id, RunItemLeaseRequest(lease_token=claim[1].lease_token))
        # A recovered Worker has no new user decision. Consume the saved one
        # and replay the completed POST evidence through ordinary execution.
        resumed = await adapter().execute(**await args())
        assert isinstance(resumed, AgentCaseApprovalWait)
        assert resumed.approval_id != pending.approval_id
        assert business_http[1] == [('POST', '/orders')]
        assert (await env[1].get_job(pending.tool_job_id)).status.value == 'completed'


@pytest.mark.asyncio
async def test_stale_lease_foreign_checkpoint_or_context_cannot_admit_tools(tmp_path, business_http):
    async with adapter_environment(tmp_path, business_http) as values:
        env, _, _, _, runs, agent, adapter, args, _, _ = values
        fields = await args()
        fields['item'] = fields['item'].model_copy(update={'lease_token': 'stale'})
        with pytest.raises(ValueError, match='租约'):
            await adapter().execute(**fields)
        await adapter().execute(**await args())
        checkpoint = (await runs.get_latest_attempt(env[0].run_item_id)).checkpoint_payload.copy()
        checkpoint['execution_attempt_id'] = 'foreign-attempt'
        await runs.save_checkpoint(env[0].run_item_id, RunItemCheckpointRequest(
            lease_token=env[6].items[0].lease_token, checkpoint_key=CHECKPOINT_KEY, checkpoint_payload=checkpoint))
        with pytest.raises(ValueError, match='恢复链'):
            await adapter().execute(**await args())
        assert not business_http[1] and len(agent.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_existing_dispatch_handler_claims_agent_item_and_suspends_without_reclaiming(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, start_item=False) as values:
        env, _, reader, _, runs, agent, adapter, _, clock, _ = values
        clock[0] += timedelta(seconds=61)
        await runs.recover_expired(env[0].run_id)
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        command = RunDispatchTask(task_id='controlled-dispatch', request_id='controlled-request',
            trace_id='controlled-trace', project_id=env[0].project_id, run_id=env[0].run_id)
        dispatcher = RedisExecutionDispatcher(queue=None, test_run_service=runs, execution_service=service)
        task = QueuedTask('controlled-message', command.model_dump(mode='json'))
        await dispatcher.handle(task, worker_id='controlled-worker')
        item = await runs.get_item(env[0].run_item_id)
        assert item.status == 'waiting_approval' and item.approval_id and item.tool_job_id
        await dispatcher.handle(task, worker_id='replayed-delivery')
        assert (await runs.get_item(item.id)).attempt_no == item.attempt_no
        assert len(agent.requests) == 1 and not business_http[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_existing_run_http_routes_use_agent_strategy_and_return_each_approval_and_result(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, start_item=False) as values:
        env, _, reader, sessions, runs, agent, adapter, args, _, _ = values

        class ForbiddenLegacy:
            async def execute(self, **kwargs):
                raise AssertionError('Agent case must not enter legacy parameter compiler')

        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=ForbiddenLegacy(), session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service, app.state.test_run_service = service, runs
        app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://agent') as client:
            prefix = '/api/v1/run-items/' + env[0].run_item_id
            response = await client.post(prefix + '/execute', json={'lease_token': env[6].items[0].lease_token})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['session_id'] and not business_http[1]
            for count in range(3):
                replay = await client.post(prefix + '/execute', json={'lease_token': env[6].items[0].lease_token})
                assert replay.status_code == 200, replay.text
                assert replay.json()['approval_id'] == data['approval_id']
                response = await client.post(prefix + '/approval', json={'approval_id': data['approval_id'], 'decision': 'approved'})
                assert response.status_code == 200, response.text
                data = response.json()
                assert len(business_http[1]) == count + 1
                if count < 2:
                    assert data['approval_id'] and data['session_id']
            assert data['status'] == 'passed'
            assert data['actual']['case_output']['body']['order.id'] == business_http[2]
            assert (await runs.get_result(data['id'])).actual == data['actual']
            replay = await client.post(prefix + '/execute', json={'lease_token': env[6].items[0].lease_token})
            assert replay.status_code == 200 and replay.json()['id'] == data['id']
            assert len(agent.requests) == 4 and len(business_http[1]) == 3


class StopsAfterDenial(ControlledAgent):
    async def invoke(self, key, request):
        if any(message.get('role') == 'tool' for message in request.messages):
            from test_agent_case_assessment import receipt
            self.requests.append(request)
            result = receipt()
            for entry in result['steps']:
                entry.update(status='skipped' if entry['item_key'] == 'cleanup' else 'blocked',
                    summary='准备操作被拒绝，目标未执行', checks=[], outputs={})
            return ModelInvocationResult(text=json.dumps(result, ensure_ascii=False),
                response_summary={'mode': 'ok', 'finish_reason': 'stop'})
        return await super().invoke(key, request)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_denied_owned_approval_saves_blocked_result_without_business_calls(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live, StopsAfterDenial(), start_item=False) as values:
        env, _, reader, _, runs, agent, adapter, _, _, _ = values
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        pending = await service.execute_item(env[0].run_item_id,
            RunItemExecuteRequest(lease_token=env[6].items[0].lease_token))
        payload = RunItemApprovalDecisionRequest(approval_id=pending.approval_id, decision=ToolApprovalStatus.denied)
        result = await service.resolve_item_approval(env[0].run_item_id, payload)
        assert result.status == 'blocked'
        assert result.actual['case_status'] == 'blocked'
        assert (await runs.get_result(result.id)).actual == result.actual
        assert (await env[1].get_job(pending.tool_job_id)).status.value == 'denied'
        assert (await service.resolve_item_approval(env[0].run_item_id, payload)).id == result.id
        assert not business_http[1] and len(agent.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_unknown_tool_outcome_never_reexecutes_or_overwrites_job_evidence(tmp_path, business_http, live):
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, _, _, _, _, agent, adapter, args, _, _ = values
        pending = await adapter().execute(**await args())
        job = await env[1].get_job(pending.tool_job_id)
        await env[1].mark_running(job)
        with pytest.raises(AgentCaseRecoveryRequired, match='结果未知'):
            await adapter().resume_approval(**await args(), approval_id=pending.approval_id,
                payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
        assert (await env[1].get_job(job.id)).status.value == 'running'
        assert not business_http[1] and len(agent.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_item_session_preserves_formal_parent_model_and_server_environment(tmp_path, business_http, live):
    from src.schemas.session import CreateSessionRequest
    from src.schemas.case_planning import CasePlanRunRequest
    async with adapter_environment(tmp_path, business_http, live) as values:
        env, _, reader, sessions, runs, _, adapter, _, _, services = values
        parent = await sessions.create_internal_session(CreateSessionRequest(project_id=env[0].project_id,
            mode_key='api_testing', preferred_model='controlled-case-agent', metadata={'environment': 'testing'}))
        detail = await services['creator'].create(env[0].project_id, env[0].plan_id,
            CasePlanRunRequest(creation_key='with-parent', mode_key='api_testing', session_id=parent.id))
        claim = (await env[7].claim_items(run_id=detail.run.id, worker_id='parent-case',
            limit=1, lease_seconds=60, now=env[8]))[0]
        await runs.start_item(claim[0].id, RunItemLeaseRequest(lease_token=claim[1].lease_token))
        base = services['environment']
        pending = await adapter().execute(case=await base.cases.get_case(claim[0].case_id),
            version=await base.cases.get_version(claim[0].case_version_id), run=await runs.get_record(detail.run.id),
            item=await runs.get_item(claim[0].id), attempt_id=claim[1].id)
        stored = await reader.get_session(pending.session_id)
        assert stored.id != parent.id and stored.project_id == parent.project_id
        assert stored.preferred_model == parent.preferred_model
        assert stored.session_mode == parent.session_mode and stored.runtime_mode == parent.runtime_mode
        assert stored.metadata['environment'] == 'testing'
        assert stored.metadata['parent_session_id'] == parent.id
        assert not business_http[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [LIVE])
async def test_agent_admission_does_not_reserve_resources_from_legacy_mode_fields(resource_manager, live):
    from tests.test_case_execution_service import _records
    _, run, item, _, version = _records()
    run = run.model_copy(update={'execution_strategy': 'agent'})
    version = version.model_copy(update={'test_data': {'runner_arguments': {
        'account_id': 'legacy-account', 'environment_id': 'legacy-environment', 'runner_backend': 'docker'}}})
    manager, _ = resource_manager
    await manager.configure_quota(scope='global', identifier='all', limit=4)
    service = ExecutionService(run_service=object(), test_case_service=object(), adapter=None)
    service.set_resource_lease_manager(manager)
    leases = await service._claim_execution_resources(run=run, item=item, version=version, attempt_id='actual-attempt')
    try:
        active = await manager.list_active()
        assert [row['resource_type'] for row in active] == ['agent']
        assert active[0]['attempt_id'] == 'actual-attempt'
    finally:
        await service._release_execution_resources(leases)
    assert not await manager.list_active() and not await manager.usage_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [LIVE])
async def test_actual_tool_resource_leases_use_frozen_execution_identity(tmp_path, business_http, resource_manager, live):
    async with adapter_environment(tmp_path, business_http, live, start_item=False) as values:
        env, _, reader, _, runs, _, adapter, _, _, services = values
        manager, _ = resource_manager
        await manager.configure_quota(scope='global', identifier='all', limit=2)
        runtime_tools = services['tool_runtime']
        original = runtime_tools._handlers['api-test-runner']
        observed = []

        async def with_resource(arguments, context):
            ambient = get_request_context()
            lease = await manager.acquire(resource_type='environment', resource_id='actual-tool-environment',
                worker_id='actual-tool', lease_seconds=30,
                **{key: getattr(ambient, key, None) for key in (
                    'project_id', 'session_id', 'run_id', 'run_item_id', 'attempt_id')})
            try:
                observed.append((await manager.get(resource_type=lease.resource_type, resource_id=lease.resource_id),
                    context.job_metadata(), context.session_id, context.turn_id))
                return await original(arguments, context)
            finally:
                await manager.retire(lease)

        runtime_tools._handlers['api-test-runner'] = with_resource
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_resource_lease_manager(manager)
        service.set_agent_execution_adapter(adapter())
        # An API/worker request may carry a parent or stale correlation scope;
        # the actual tool resources must use the server-owned frozen identity.
        with bind_request_context(project_id='unrelated-project', session_id='unrelated-session',
            turn_id='unrelated-turn', run_id='unrelated-run', run_item_id='unrelated-item', attempt_id='unrelated-attempt'):
            pending = await service.execute_item(env[0].run_item_id,
                RunItemExecuteRequest(lease_token=env[6].items[0].lease_token))
            for _ in range(3):
                pending = await service.resolve_item_approval(env[0].run_item_id,
                    RunItemApprovalDecisionRequest(approval_id=pending.approval_id, decision=ToolApprovalStatus.approved))
        assert pending.status == 'passed' and len(observed) == 3
        for actual, expected, session_id, turn_id in observed:
            identity_keys = ('project_id', 'run_id', 'run_item_id', 'attempt_id')
            assert {key: actual[key] for key in identity_keys} == {key: expected[key] for key in identity_keys}
            assert actual['session_id'] == session_id and actual['turn_id'] == turn_id
        assert len(business_http[1]) == 3
        assert not await manager.list_active() and not await manager.usage_snapshot()


async def expire_preserved_session_lease(sessions, reader, session_id):
    async def wait():
        while True:
            await sessions.recover_expired_turn_executions()
            if not (await reader.get_session(session_id)).metadata.get('turn_lease_token'):
                return
            await asyncio.sleep(.5)
    await asyncio.wait_for(wait(), 22)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
@pytest.mark.parametrize('decision', [ToolApprovalStatus.approved, ToolApprovalStatus.denied])
async def test_saved_approval_decision_recovers_without_another_user_decision(
    tmp_path, business_http, live, decision, monkeypatch):
    agent = StopsAfterDenial() if decision == ToolApprovalStatus.denied else ControlledAgent()
    async with adapter_environment(tmp_path, business_http, live, agent, start_item=False) as values:
        env, writer, reader, sessions, runs, _, adapter, _, clock, _ = values
        service = ExecutionService(run_service=runs, test_case_service=runs._cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        service.set_agent_execution_adapter(adapter())
        pending = await service.execute_item(env[0].run_item_id,
            RunItemExecuteRequest(lease_token=env[6].items[0].lease_token))
        reached = asyncio.Event()
        append = sessions._append_approval_resolved_event

        async def stop_after_decision(session_id, approval):
            await append(session_id, approval)
            reached.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(sessions, '_append_approval_resolved_event', stop_after_decision)
        note = '实际保存的审批决定，恢复时保持'
        task = asyncio.create_task(service.resolve_item_approval(env[0].run_item_id,
            RunItemApprovalDecisionRequest(approval_id=pending.approval_id, decision=decision, reason=note)))
        try:
            await asyncio.wait_for(reached.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            monkeypatch.setattr(sessions, '_append_approval_resolved_event', append)
        saved = next(entry for entry in await reader.list_approvals(pending.session_id) if entry.id == pending.approval_id)
        assert saved.status == decision and saved.decision_note == note
        assert not business_http[1]
        await expire_preserved_session_lease(sessions, reader, pending.session_id)
        clock[0] += timedelta(seconds=91)
        await runs.recover_expired(env[0].run_id)
        claim = (await runs.claim(env[0].run_id,
            RunClaimRequest(worker_id='decision-recovery', limit=1))).claims[0]
        resumed = await service.execute_item(claim.item.id, RunItemExecuteRequest(lease_token=claim.lease_token))
        if decision == ToolApprovalStatus.approved:
            assert isinstance(resumed, RunItemApprovalPending) and resumed.approval_id != pending.approval_id
            assert business_http[1] == [('POST', '/orders')]
        else:
            assert resumed.status == 'blocked' and not business_http[1]
        saved = next(entry for entry in await reader.list_approvals(pending.session_id) if entry.id == pending.approval_id)
        assert saved.status == decision and saved.decision_note == note
