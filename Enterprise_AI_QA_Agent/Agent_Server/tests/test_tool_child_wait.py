"""Original child waits cannot resurrect a cancelled Job or have two owners."""
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.projects.project_store import PostgresProjectStore
from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService
from src.core.config import get_settings
from src.modes.api_testing_mode.campaign_state import ExecutionPolicy
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.registry.tools import ToolRegistry
from src.registry.agents import AgentRegistry
from src.domain.models import SessionRecord
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall
from test_tool_runtime_heartbeat import _tool
from test_api_worker_result_reader import environment
from test_execution_worker_cli import isolated_runtime


@pytest.mark.asyncio
async def test_cancelled_original_cannot_be_parked_or_claimed_again():
    jobs = ToolJobService(InMemoryToolJobStore())
    job = await jobs.create_job(tool=ToolRegistry().get('api-test-runner'), call_id='original',
        session_id='parent', turn_id='turn', trace_id='trace', input_payload={'objective': 'owned'})
    await jobs.mark_running(job)
    await jobs.cancel_job(job.id, 'user cancelled')
    before = (await jobs.get_job(job.id)).model_dump(mode='json')
    assert await jobs.mark_waiting_children(job.id, summary='waiting', metadata={'child_wait': {}}) is None
    assert await jobs.claim_child_wait(job.id) is None
    assert (await jobs.get_job(job.id)).model_dump(mode='json') == before


@pytest.mark.asyncio
async def test_only_one_original_wait_resume_can_claim_the_job():
    jobs = ToolJobService(InMemoryToolJobStore())
    job = await jobs.create_job(tool=ToolRegistry().get('api-test-runner'), call_id='original',
        session_id='parent', turn_id='turn', trace_id='trace', input_payload={'objective': 'owned'})
    await jobs.mark_running(job)
    waiting = await jobs.mark_waiting_children(job.id, summary='waiting', metadata={'child_wait': {'server_approval_granted': True}})
    assert waiting.completed_at is None
    claimed = await asyncio.gather(*(jobs.claim_child_wait(job.id) for _ in range(8)))
    assert sum(item is not None for item in claimed) == 1
    owner = next(item for item in claimed if item is not None)
    assert owner.input_payload == job.input_payload and owner.call_id == 'original'
    assert owner.metadata['child_wait']['server_approval_granted']


@pytest.mark.asyncio
async def test_losing_runtime_claim_preserves_the_other_owner():
    jobs = ToolJobService(InMemoryToolJobStore())
    tool = _tool()
    job = await jobs.create_job(tool=tool, call_id='original', session_id='parent',
        turn_id='turn', trace_id='trace', input_payload={})
    await jobs.mark_running(job)
    await jobs.mark_waiting_children(job.id, summary='waiting', metadata={'child_wait': {}})
    original_claim = jobs.claim_child_wait
    async def lost_claim(identity):
        assert await original_claim(identity)
        return None
    jobs.claim_child_wait = lost_claim
    runtime = ToolRuntimeService(tool_job_service=jobs)
    async def handler(arguments, context):
        pytest.fail('Losing reader cannot enter the handler')
    runtime._handlers[tool.key] = handler
    context = ToolExecutionContext(session_id='parent', turn_id='turn', trace_id='trace',
        user_message='resume', normalized_input='resume', context_bundle={}, tool_job_id=job.id)
    with pytest.raises(RuntimeError, match='already running'):
        await runtime.execute(tool=tool, call=ModelToolCall(id='original', name=tool.key, arguments={}), context=context)
    preserved = await jobs.get_job(job.id)
    assert preserved.status.value == 'running'
    assert preserved.error_message is None and preserved.completed_at is None


@pytest.mark.asyncio
async def test_restored_waiting_child_stays_running_without_new_dispatch():
    jobs, _, task, child, context = await environment()
    child.status = SessionStatus.waiting_approval
    class Sessions:
        async def get_session(self, identity):
            assert identity == child.id
            return child
    class Dispatcher:
        async def dispatch(self, payload, context):
            pytest.fail('Original waiting child must not be redispatched')
    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool([task]), policy=ExecutionPolicy(max_retries=3),
        coordinator_runtime_service=Dispatcher(), session_store=Sessions(), parent_context=context,
        credential_manager=CredentialManager(), tool_job_service=jobs)
    results = await coordinator.run_all()
    assert results[0].status == 'running'
    assert coordinator.waiting_child_session_ids == [child.id]
    assert not coordinator.has_unsettled_workers


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_TOOL_CHILD_WAIT_STORE') != '1', reason='actual isolated PostgreSQL wait/claim races')
async def test_actual_pg_original_wait_claim_and_cancellation(tmp_path):
    directory = Path(os.environ['QA_TOOL_CHILD_WAIT_STORE_DIR'])
    directory.mkdir(parents=True, exist_ok=False)
    async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
        await PostgresProjectStore(settings).initialize()
        store = PostgresSessionStore(settings)
        await store.initialize()
        now = datetime.now(UTC)
        session = SessionRecord(id=str(uuid4()), title='Original wait races', created_at=now, updated_at=now,
            status=SessionStatus.idle, session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive, mode_key='api_testing')
        await store.save_session(session)
        jobs = ToolJobService(PostgresToolJobStore(settings))
        await jobs.initialize()
        tool = ToolRegistry().get('api-test-runner')
        owned = await jobs.create_job(tool=tool, call_id='original', session_id=session.id,
            turn_id='turn', trace_id='trace', input_payload={'objective': 'owned'})
        await jobs.mark_running(owned)
        original = owned.model_dump(mode='json')
        assert await jobs.mark_waiting_children(owned.id, summary='waiting', metadata={'child_wait': {}})
        readers = [ToolJobService(PostgresToolJobStore(settings)) for _ in range(8)]
        claims = await asyncio.gather(*(reader.claim_child_wait(owned.id) for reader in readers))
        assert sum(item is not None for item in claims) == 1
        assert (await jobs.get_job(owned.id)).input_payload == original['input_payload']
        cancelled = await jobs.create_job(tool=tool, call_id='cancelled', session_id=session.id,
            turn_id='turn', trace_id='trace', input_payload={'objective': 'owned'})
        await jobs.mark_running(cancelled)
        await jobs.cancel_job(cancelled.id, 'user cancelled')
        before = (await jobs.get_job(cancelled.id)).model_dump(mode='json')
        assert await jobs.mark_waiting_children(cancelled.id, summary='waiting', metadata={'child_wait': {}}) is None
        assert await jobs.claim_child_wait(cancelled.id) is None
        assert (await jobs.get_job(cancelled.id)).model_dump(mode='json') == before
        (directory / 'postgres-races.json').write_text(json.dumps({'external_ai_calls': 0,
            'full_goal_complete': False, 'parallel_claims': 8, 'successful_claims': 1,
            'cancelled_original_preserved': True, 'original': original,
            'claimed': (await jobs.get_job(owned.id)).model_dump(mode='json'), 'cancelled': before},
            ensure_ascii=False, indent=2), encoding='utf8')


def wait_recovery_environment():
    wait = {'job_id': 'original', 'child_session_ids': ['child']}
    parent = SimpleNamespace(id='parent', status=SessionStatus.interrupted, project_id='project',
        metadata={'pending_turn': {'turn_id': 'original-turn', 'waiting_tool': wait}})
    child = SimpleNamespace(id='child', status=SessionStatus.completed, project_id='project',
        metadata={'parent_session_id': 'parent', 'parent_turn_id': 'original-turn'})
    snapshot = SimpleNamespace(id='original-snapshot', graph_state={'pending_turn': {'waiting_tool': wait}})
    store = SimpleNamespace(get_session=AsyncMock(return_value=child),
        get_latest_snapshot=AsyncMock(return_value=snapshot), list_sessions=AsyncMock(return_value=[parent]))
    service = CoordinatorRuntimeService(get_settings(), store, AsyncMock(), AgentRegistry())
    dispatcher = SimpleNamespace(submit_resume=AsyncMock())
    service.set_agent_task_dispatcher(dispatcher)
    return service, parent, child, snapshot, dispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['user_interrupt', 'child_pending', 'foreign_project',
    'foreign_turn', 'checkpoint_changed', 'parent_lease'])
async def test_auto_resume_only_original_settled_child_checkpoint(condition):
    service, parent, child, snapshot, dispatcher = wait_recovery_environment()
    if condition == 'user_interrupt':
        parent.metadata['pending_turn'].pop('waiting_tool')
    elif condition == 'child_pending':
        child.status = SessionStatus.waiting_approval
    elif condition == 'foreign_project':
        child.project_id = 'foreign'
    elif condition == 'foreign_turn':
        child.metadata['parent_turn_id'] = 'foreign'
    elif condition == 'checkpoint_changed':
        snapshot.graph_state = {'pending_turn': {}}
    elif condition == 'parent_lease':
        parent.metadata['turn_lease_token'] = 'active'
    assert not await service._maybe_resume_waiting_tool(parent)
    dispatcher.submit_resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_notification_uses_same_original_snapshot_idempotency_key():
    service, parent, _, _, dispatcher = wait_recovery_environment()
    assert await service._maybe_resume_waiting_tool(parent)
    assert await service._maybe_resume_waiting_tool(parent)
    requests = dispatcher.submit_resume.await_args_list
    assert [item.args[0] for item in requests] == ['parent', 'parent']
    assert [item.args[1].client_request_id for item in requests] == ['child_wait:original-snapshot'] * 2
    assert all(item.args[1].source == 'tool.child_settled' for item in requests)


@pytest.mark.asyncio
async def test_proxy_approval_running_parent_can_resume_original_child_wait():
    service, parent, _, _, dispatcher = wait_recovery_environment()
    parent.status = SessionStatus.running
    parent.metadata.pop('turn_lease_token', None)
    assert await service._maybe_resume_waiting_tool(parent)
    dispatcher.submit_resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_failed_recovery_does_not_block_other_parent_waits(caplog):
    service, parent, _, _, dispatcher = wait_recovery_environment()
    broken = SimpleNamespace(id='broken', status=SessionStatus.interrupted, project_id='project',
        metadata={'pending_turn': {'turn_id': 'original-turn',
            'waiting_tool': {'job_id': 'broken-job', 'child_session_ids': ['child']}}})
    service._store.list_sessions.return_value = [broken, parent]
    service._store.get_session.side_effect = [RuntimeError('injected first read failure'),
        service._store.get_session.return_value]
    await service.resume_waiting_tools()
    dispatcher.submit_resume.assert_awaited_once()
    assert dispatcher.submit_resume.await_args.args[0] == parent.id
    assert 'tool_child_wait_resume_failed' in caplog.text
