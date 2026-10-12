"""Multiple original tool waits must preserve the remaining approval batch."""
from datetime import UTC, datetime
from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from test_agent_case_assessment import business_http
from test_turn_budget_persistence import _ModelRuntime
from test_tool_child_wait import wait_recovery_environment
from test_agent_case_session import ControlledModels, EmptyMCP
from test_execution_worker_cli import isolated_runtime
from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService, WorkerDispatchSpec
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.projects.project_store import PostgresProjectStore
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_child_wait import park_tool_wait
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.domain.models import SessionRecord
from src.application.sessions.session_service import SessionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.core.config import get_settings
from src.graph.builder import build_agent_graph
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.store import InMemorySessionStore
from src.schemas.model_config import ModelInvocationResult
from src.schemas.project import ProjectRecord
from src.schemas.session import ExecutionRequest, RuntimeMode, SessionMode, SessionStatus
from src.schemas.session import ApprovalDecisionRequest, SendMessageRequest, ToolApprovalStatus
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


async def batch_environment():
    now = datetime.now(UTC)
    session = SessionRecord(id='batch-parent', title='Original batch', status=SessionStatus.running,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
        mode_key='api_testing', created_at=now, updated_at=now)
    registry = ToolRegistry()
    jobs = ToolJobService(InMemoryToolJobStore())
    tool_runtime = ToolRuntimeService(tool_job_service=jobs)
    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntime(),
        tool_runtime_service=tool_runtime, tool_registry=registry, runtime_control=RuntimeControlRegistry(),
        tool_job_service=jobs)
    state = runtime._build_initial_state(session, ExecutionRequest(session_id=session.id,
        turn_id='original-turn', user_message='Read both original outputs',
        normalized_input='Read both original outputs', mode_key='api_testing'))
    state.update(model_call_count=1, max_iterations=8)
    records = []
    for call_id in ('one', 'two'):
        tool = registry.get('knowledge-rag')
        job = await jobs.create_job(tool=tool, call_id=call_id, session_id=session.id,
            turn_id=state['turn_id'], trace_id=state['trace_id'], input_payload={'query': call_id})
        await jobs.mark_running(job)
        await jobs.mark_waiting_children(job.id, summary='waiting', metadata={'child_wait': {}})
        records.append(ToolExecutionRecord(call_id=call_id, tool_key=tool.key, tool_name=tool.name,
            status='waiting_children', summary='waiting', input=job.input_payload, job_id=job.id,
            output={'waiting_child_session_ids': ['child-' + call_id]}))
    state['tool_results'] = [record.model_dump(mode='python') for record in records]
    return runtime, tool_runtime, session, state, records


@pytest.mark.asyncio
async def test_wait_checkpoint_keeps_original_approval_context():
    runtime, _, session, state, records = await batch_environment()
    state['pending_approvals'] = [{'id': 'remaining', 'tool_key': 'api-test-runner'}]
    state['pending_turn'] = runtime._build_pending_turn(state, stage='waiting_approval')
    park_tool_wait(state, records[0])
    assert state['pending_turn']['pending_approval_ids'] == ['remaining']
    restored = runtime._state_from_pending_turn(session, state['pending_turn'])
    assert restored['user_message'] == state['user_message']
    assert restored['pending_approvals'] == state['pending_approvals']
    assert restored['tool_results'] == state['tool_results']
    assert restored['model_call_count'] == 1 and restored['max_iterations'] == 8


@pytest.mark.asyncio
async def test_both_original_waits_settle_before_entering_the_model(business_http):
    runtime, tool_runtime, session, state, records = await batch_environment()
    async def read(arguments, context):
        async with httpx.AsyncClient() as client:
            response = await client.get(business_http[0] + '/orders/' + business_http[2])
        return {'status': 'completed', 'summary': 'actual HTTP read', 'body': response.json()}
    tool_runtime._handlers['knowledge-rag'] = read
    park_tool_wait(state, records[0])
    async def model_boundary(session, current, **kwargs):
        assert all(item['status'] == 'completed' for item in current['tool_results'])
        return current
    runtime._execute_state = model_boundary
    settled = await runtime._resume_waiting_tool(session, state)
    assert len(settled['tool_results']) == 2
    assert len(business_http[1]) == 2


@pytest.mark.asyncio
async def test_foreign_second_checkpoint_is_rejected_before_any_original_http(business_http):
    runtime, tool_runtime, session, state, records = await batch_environment()
    calls = []
    async def read(arguments, context):
        calls.append(arguments)
        pytest.fail('No original handler may enter after a frozen batch conflict')
    tool_runtime._handlers['knowledge-rag'] = read
    park_tool_wait(state, records[0])
    state['pending_turn']['waiting_tools'][1]['input'] = {'query': 'foreign'}
    with pytest.raises(ValueError, match='frozen input changed'):
        await runtime._resume_waiting_tool(session, state)
    assert calls == [] and business_http[1] == []


@pytest.mark.asyncio
async def test_historical_single_wait_is_still_read_from_its_original_job(business_http):
    runtime, tool_runtime, session, state, records = await batch_environment()
    state['tool_results'] = state['tool_results'][:1]
    async def read(arguments, context):
        async with httpx.AsyncClient() as client:
            response = await client.get(business_http[0] + '/orders/' + business_http[2])
        return {'status': 'completed', 'summary': 'actual HTTP read', 'body': response.json()}
    tool_runtime._handlers['knowledge-rag'] = read
    park_tool_wait(state, records[0])
    state['pending_turn'].pop('waiting_tools')
    async def boundary(session, current, **kwargs):
        return current
    runtime._execute_state = boundary
    settled = await runtime._resume_waiting_tool(session, state)
    assert settled['tool_results'][0]['status'] == 'completed'
    assert len(business_http[1]) == 1


@pytest.mark.asyncio
async def test_secondary_wait_snapshot_change_does_not_use_the_first_marker_only():
    service, parent, _, snapshot, dispatcher = wait_recovery_environment()
    wait = parent.metadata['pending_turn']['waiting_tool']
    parent.metadata['pending_turn']['waiting_tools'] = [wait, {'job_id': 'second', 'child_session_ids': ['child']}]
    snapshot.graph_state['pending_turn']['waiting_tools'] = [wait]
    assert not await service._maybe_resume_waiting_tool(parent)
    dispatcher.submit_resume.assert_not_awaited()


class BatchModel(_ModelRuntime):
    def __init__(self, business):
        self.business = business
        self.requests = []

    @asynccontextmanager
    async def stream_handler(self, handler):
        yield

    async def invoke(self, key, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            calls = [ModelToolCall(id=identity, name='knowledge-rag', arguments={'query': identity})
                for identity in ('one', 'two')]
            for identity in ('write-one', 'write-two'):
                calls.append(ModelToolCall(id=identity, name='api-test-runner', arguments={
                    'worker_action': 'execute_task', 'task': {'task_id': identity, 'method': 'POST',
                        'path': '/orders', 'full_url': self.business[0] + '/orders', 'timeout_seconds': 5}}))
            return ModelInvocationResult(text='Read both dependencies, then perform the approved writes.',
                tool_calls=calls, response_summary={'mode': 'ok', 'finish_reason': 'tool_calls'})
        # Inspect the actual final graph/model boundary, not only the response.
        replies = [item for item in request.messages if item.get('role') == 'tool']
        assert [item['tool_call_id'] for item in replies] == ['one', 'two', 'write-one', 'write-two']
        assert all(json.loads(item['content'])['status'] in {'completed', 'denied'} for item in replies)
        return ModelInvocationResult(text='The original batch has settled.',
            response_summary={'mode': 'ok', 'finish_reason': 'stop'})


@asynccontextmanager
async def batch_stores(tmp_path, live):
    if live:
        async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key='original-batch',
                name='Original batch acceptance', created_at=now, updated_at=now))
            store = PostgresSessionStore(settings)
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await store.initialize()
            await jobs.initialize()
            yield settings, store, jobs, project.id
    else:
        yield get_settings(), InMemorySessionStore(), ToolJobService(InMemoryToolJobStore()), str(uuid4())


def batch_services(store, jobs, model, business, children):
    tools, skills = ToolRegistry(), SkillRegistry()
    control = RuntimeControlRegistry()
    tool_runtime = ToolRuntimeService(tool_job_service=jobs, session_store=store)
    async def wait_or_read(arguments, context):
        child = await store.get_session(children[arguments['query']])
        if child.status != SessionStatus.completed:
            return {'status': 'waiting_children', 'summary': 'Original child still waiting',
                'waiting_child_session_ids': [child.id]}
        async with httpx.AsyncClient() as client:
            response = await client.get(business[0] + '/orders/' + business[2])
        assert response.status_code == 200
        return {'status': 'completed', 'summary': 'Actual original read', 'body': response.json()}
    # This producer is controlled; the ordinary graph, Job persistence, real API
    # writer, approval, snapshot, resume and model boundary are production code.
    tool_runtime._handlers['knowledge-rag'] = wait_or_read
    graph = build_agent_graph(agent_registry=AgentRegistry(), tool_registry=tools,
        model_registry=ControlledModels(), skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
        mcp_runtime_service=EmptyMCP(), memory_runtime_service=None, test_case_embedding_service=None,
        permission_service=PermissionService(), prompt_assembly_service=PromptAssemblyService(),
        model_runtime_service=model, tool_runtime_service=tool_runtime, runtime_control=control,
        tool_job_service=jobs)
    runtime = RuntimeService(graph=graph, model_runtime_service=model, tool_runtime_service=tool_runtime,
        tool_registry=tools, runtime_control=control, tool_job_service=jobs, permission_service=PermissionService())
    sessions = SessionService(store, InputOrchestratorService(ModeRegistry()), runtime, ModeRegistry())
    return sessions, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, True])
@pytest.mark.parametrize('second_decision', ['approved', 'denied'])
async def test_actual_batch_approval_restore_and_real_http(tmp_path, business_http, live, second_decision):
    if live and os.getenv('RUN_LIVE_TOOL_WAIT_BATCH') != '1':
        pytest.skip('Actual isolated PG mixed wait and approval batch')
    output = Path(os.environ['QA_TOOL_WAIT_BATCH_DIR']) / ('postgres' if live else 'memory') / second_decision
    output.mkdir(parents=True, exist_ok=False)
    async with batch_stores(tmp_path, live) as (settings, store, jobs, project_id):
        now = datetime.now(UTC)
        parent = SessionRecord(id=str(uuid4()), title='Wait and approvals', status=SessionStatus.idle,
            session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key='api_testing',
            created_at=now, updated_at=now, project_id=project_id)
        await store.save_session(parent)
        children = {identity: str(uuid4()) for identity in ('one', 'two')}
        for identity in children.values():
            await store.save_session(SessionRecord(id=identity, title='Controlled child', status=SessionStatus.running,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                mode_key='api_testing', created_at=now, updated_at=now, project_id=project_id,
                metadata={'parent_session_id': parent.id}))
        model = BatchModel(business_http)
        sessions, runtime = batch_services(store, jobs, model, business_http, children)
        try:
            response = await sessions.send_message(parent.id, SendMessageRequest(content='Test the provided API batch',
                agent_key='api-testing-agent', model_key='controlled-case-agent'))
            original = await store.get_latest_snapshot(parent.id)
            assert original.stage == 'waiting_approval'
            assert len(original.graph_state['pending_turn']['waiting_tools']) == 2
            assert len(model.requests) == 1 and business_http[1] == []
            turn = original.graph_state['turn_id']
            for identity in children.values():
                child = await store.get_session(identity)
                child.metadata['parent_turn_id'] = turn
                await store.save_session(child)
            approvals = await store.list_approvals(parent.id)
            assert len(approvals) == 2
            ordered = sorted(approvals, key=lambda item: item.metadata['call_id'])
            checkpoints = [original.model_dump(mode='json')]
            for index, approval in enumerate(ordered):
                decision = 'approved' if index == 0 else second_decision
                await sessions.resolve_approval(parent.id, approval.id,
                    ApprovalDecisionRequest(decision=ToolApprovalStatus(decision)))
                async def continued():
                    while True:
                        snapshot = await store.get_latest_snapshot(parent.id)
                        ids = snapshot.graph_state.get('pending_turn', {}).get('pending_approval_ids', [])
                        persisted_parent = await store.get_session(parent.id)
                        expected_status = SessionStatus.waiting_approval if index == 0 else SessionStatus.interrupted
                        continuation_task = sessions._approval_resume_tasks.get(approval.id)
                        if (snapshot.id != checkpoints[-1]['id'] and approval.id not in ids
                            and persisted_parent.status == expected_status
                            and (continuation_task is None or continuation_task.done())):
                            return snapshot
                        await asyncio.sleep(.02)
                snapshot = await asyncio.wait_for(continued(), 20)
                checkpoints.append(snapshot.model_dump(mode='json'))
                assert snapshot.stage == ('waiting_approval' if index == 0 else 'interrupted')
                assert len(model.requests) == 1
            assert [method for method, _ in business_http[1]] == ['POST'] * (2 if second_decision == 'approved' else 1)
        finally:
            await sessions.close()

        persisted = await store.get_session(parent.id)
        (output / 'after-initial-service-close.json').write_text(json.dumps({
            'parent_status': persisted.status.value, 'metadata': persisted.metadata,
            'snapshot': (await store.get_latest_snapshot(parent.id)).model_dump(mode='json')},
            ensure_ascii=False, indent=2, default=str), encoding='utf8')
        if live:
            store = PostgresSessionStore(settings)
            jobs = ToolJobService(PostgresToolJobStore(settings))
        sessions, runtime = batch_services(store, jobs, model, business_http, children)
        runtime._max_iterations = 99  # Must not donate a fresh budget to the old turn.
        coordinator = CoordinatorRuntimeService(settings, store, sessions, AgentRegistry())
        try:
            first = await store.get_session(children['one'])
            first.status = SessionStatus.completed
            await store.save_session(first)
            assert not await coordinator._maybe_resume_waiting_tool(await store.get_session(parent.id))
            assert len(model.requests) == 1
            second = await store.get_session(children['two'])
            second.status = SessionStatus.completed
            await store.save_session(second)
            ready_parent = await store.get_session(parent.id)
            (output / 'before-original-resume.json').write_text(json.dumps({
                'parent_status': ready_parent.status.value, 'parent_metadata': ready_parent.metadata,
                'children': [{'id': child.id, 'status': child.status.value, 'project_id': child.project_id,
                    'metadata': child.metadata} for child in [await store.get_session(identity)
                        for identity in children.values()]]}, ensure_ascii=False, indent=2, default=str), encoding='utf8')
            assert await coordinator._maybe_resume_waiting_tool(ready_parent)
            final = await store.get_latest_snapshot(parent.id)
            assert final.stage == 'completed'
            assert final.graph_state['turn_id'] == turn
            assert final.graph_state['model_call_count'] == 2 and final.graph_state['max_iterations'] == 8
            records = final.graph_state['tool_results']
            assert [item['call_id'] for item in records] == ['one', 'two', 'write-one', 'write-two']
            assert [item['status'] for item in records] == ['completed'] * 3 + [
                'completed' if second_decision == 'approved' else 'denied']
            for item in records[:2]:
                assert item['output']['body']['flag'] is False and item['output']['body']['count'] == 0
            original_jobs = await jobs.list_jobs(session_id=parent.id)
            assert len(original_jobs) == 4
            assert {item.call_id: item.status.value for item in original_jobs} == {
                'one': 'completed', 'two': 'completed', 'write-one': 'completed',
                'write-two': 'completed' if second_decision == 'approved' else 'denied'}
            before = len(model.requests), list(business_http[1])
            assert not await coordinator._maybe_resume_waiting_tool(await store.get_session(parent.id))
            assert (len(model.requests), business_http[1]) == before
            assert [method for method, _ in business_http[1]] == ['POST'] * (
                2 if second_decision == 'approved' else 1) + ['GET', 'GET']
            (output / 'facts.json').write_text(json.dumps({'external_ai_calls': 0, 'full_goal_complete': False,
                'real_postgres': live, 'independent_agent_process': False, 'controlled_wait_producer': True,
                'second_approval': second_decision, 'events': business_http[1], 'job_count': 4,
                'model_calls_before_settled': 1, 'model_calls_final': 2, 'max_iterations': 8,
                'extra_model_calls': 0, 'extra_http': 0, 'all_children_required': True,
                'final_tool_ids': [item['call_id'] for item in records],
                'final_statuses': [item['status'] for item in records],
                'job_statuses': {item.call_id: item.status.value for item in original_jobs}},
                ensure_ascii=False, indent=2), encoding='utf8')
            (output / 'snapshots.json').write_text(json.dumps(checkpoints + [final.model_dump(mode='json')],
                ensure_ascii=False, indent=2, default=str), encoding='utf8')
            (output / 'jobs.json').write_text(json.dumps([item.model_dump(mode='json')
                for item in await jobs.list_jobs(session_id=parent.id)], ensure_ascii=False, indent=2), encoding='utf8')
        finally:
            await sessions.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, True])
async def test_child_notification_cannot_overwrite_a_new_parent_wait(tmp_path, live):
    if live and os.getenv('RUN_LIVE_TOOL_WAIT_BATCH') != '1':
        pytest.skip('Actual PG stale notification read')
    output = Path(os.environ['QA_TOOL_WAIT_BATCH_DIR']) / 'notification-race' / ('postgres' if live else 'memory')
    output.mkdir(parents=True, exist_ok=False)
    async with batch_stores(tmp_path, live) as (settings, store, _, project_id):
        now = datetime.now(UTC)
        parent = SessionRecord(id=str(uuid4()), title='Notification race', status=SessionStatus.running,
            session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key='api_testing',
            created_at=now, updated_at=now, project_id=project_id, metadata={'pending_turn': {'old': True}})
        child = SessionRecord(id=str(uuid4()), title='Child notification', status=SessionStatus.waiting_approval,
            session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background, mode_key='api_testing',
            created_at=now, updated_at=now, project_id=project_id)
        await store.save_session(parent)
        await store.save_session(child)
        original_get = store.get_session
        checkpoint = {'turn_id': 'original-turn', 'waiting_tool': {'job_id': 'original', 'child_session_ids': [child.id]}}
        reads = 0
        async def race_read(identity, *args, **kwargs):
            nonlocal reads
            found = await original_get(identity, *args, **kwargs)
            if identity == parent.id:
                reads += 1
                if reads == 2:
                    # The ordinary parent writes its checkpoint after the
                    # notifier's read and before its final metadata projection.
                    stale = deepcopy(found)
                    current = deepcopy(found)
                    current.status = SessionStatus.interrupted
                    current.metadata['pending_turn'] = checkpoint
                    await store.save_session(current)
                    return stale
            return found
        store.get_session = race_read
        service = CoordinatorRuntimeService(settings, store, None, AgentRegistry())
        await service._project_worker_notification(parent_session_id=parent.id, parent_turn_id='original-turn',
            child_session_id=child.id, worker=WorkerDispatchSpec(task_id='owned', description='Own child',
                prompt='Owned work', agent_key='api-executor-worker'), content='Waiting for existing approval',
            status='waiting_approval')
        actual = await original_get(parent.id)
        facts = {'real_postgres': live, 'external_ai_calls': 0, 'full_goal_complete': False,
            'status': actual.status.value, 'pending_turn': actual.metadata.get('pending_turn'),
            'expected_pending_turn': checkpoint, 'worker_dispatches': actual.metadata.get('worker_dispatches'),
            'worker_failure_guard': actual.metadata.get('worker_failure_guard')}
        (output / 'facts.json').write_text(json.dumps(facts, indent=2), encoding='utf8')
        assert actual.status == SessionStatus.interrupted
        assert actual.metadata['pending_turn'] == checkpoint
