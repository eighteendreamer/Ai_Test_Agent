"""Real Agent Worker CLI; only the local model HTTP response is controlled."""
import asyncio
import json
import os
import re
import shutil
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_api_worker_auth_refresh_live import auth_target
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.modes.api_testing_mode.campaign_state import ApiTestingState, ApiTestCampaign, EndpointCandidate, ExecutionPolicy
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.contracts import PHASE_CAMPAIGN_READY, STATE_METADATA_KEY
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.precondition_resolver import PreconditionAnalysis
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_session_store import PostgresSessionStore
from src.schemas.model_config import ModelCapabilitiesOverride, model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest


@pytest.mark.asyncio
async def test_api_dispatch_mode_and_original_approval_task_owner():
    from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService, WorkerDispatchSpec
    from src.core.config import get_settings
    from src.modes.api_testing_mode.campaign_state import ApiTestTask
    from src.modes.api_testing_mode.credential_manager import CredentialManager
    from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
    from src.modes.api_testing_mode.task_pool import ApiTaskPool
    from src.registry.agents import AgentRegistry
    from src.runtime.store import InMemorySessionStore

    owner = SimpleNamespace(turn_id='original-turn', operation='approval_resume', status='running')

    class Pending:
        async def list_session(self, session_id, *, pending_only):
            assert session_id == 'parent' and pending_only
            return [owner]

    runtime = CoordinatorRuntimeService(store=InMemorySessionStore(), session_service=None,
        agent_registry=AgentRegistry(), settings=get_settings())
    runtime._agent_task_store = Pending()
    coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(tasks=[]), policy=ExecutionPolicy(),
        coordinator_runtime_service=runtime, session_store=InMemorySessionStore(),
        parent_context={}, credential_manager=CredentialManager())
    spec = WorkerDispatchSpec(**coordinator._build_worker_spec(
        ApiTestTask(task_id='task', method='GET', path='/profile')))
    assert runtime._resolve_worker_mode_key(spec) == 'api_testing'
    assert await runtime._resolve_parent_task('parent', 'original-turn') is owner
    assert await runtime._resolve_parent_task('parent', 'different-turn') is None
    owner.status = 'completed'
    assert await runtime._resolve_parent_task('parent', 'original-turn') is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_API_AGENT_WORKER') != '1', reason='actual PostgreSQL UUID project rows')
async def test_project_uuid_submission_and_resume_preserve_foreign_project_rejection(tmp_path):
    from src.application.projects.project_store import PostgresProjectStore
    from src.domain.models import SessionRecord
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore, AgentSubmissionConflict
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.schemas.agent_task import AgentTaskCommand
    from src.schemas.project import ProjectRecord
    from src.schemas.session import (ChatMessage, MessageRole, ResumeSessionRequest, SendMessageRequest,
        SessionSnapshot, SessionStatus, SessionMode, RuntimeMode)

    output = Path(os.environ['QA_API_AGENT_WORKER_DIR']) / 'identity'
    output.mkdir(parents=True, exist_ok=False)
    async with isolated_runtime(tmp_path) as (settings, _, _, _, _):
        projects, sessions = PostgresProjectStore(settings), PostgresSessionStore(settings)
        await projects.initialize()
        await sessions.initialize()
        await PostgresTaskOutbox(settings).initialize()
        tasks = PostgresAgentTaskStore(settings, sessions)
        await tasks.initialize()
        now = datetime.now(UTC)
        project = await projects.create(ProjectRecord(id=str(uuid4()), project_key='uuid-regression',
            name='真实 UUID 身份回归', created_at=now, updated_at=now))

        async def session(project_id, status='idle'):
            record = SessionRecord(id=str(uuid4()), title='Project identity fixture', status=SessionStatus(status),
                session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key='default',
                project_id=project_id, created_at=now, updated_at=now)
            await sessions.save_session(record)
            return record

        def command(record, **updates):
            return AgentTaskCommand(task_id=str(uuid4()), session_id=record.id, turn_id=str(uuid4()),
                project_id=record.project_id, request_id=str(uuid4()), trace_id=str(uuid4())).model_copy(update=updates)

        async def submit(record, project_id):
            return await tasks.submit(command(record, project_id=project_id), SendMessageRequest(content='验证项目身份'),
                ChatMessage(id=str(uuid4()), role=MessageRole.user, content='验证项目身份', created_at=now), idempotency_key=str(uuid4()))

        bound, unbound = await session(project.id), await session(None)
        with pytest.raises(AgentSubmissionConflict, match='project changed'):
            await submit(bound, str(uuid4()))
        with pytest.raises(AgentSubmissionConflict, match='project changed'):
            await submit(bound, None)
        bound_task = await submit(bound, project.id)
        unbound_task = await submit(unbound, None)
        resumable = await session(project.id, 'interrupted')
        turn = str(uuid4())
        resumable.metadata['pending_turn'] = {'turn_id': turn}
        await sessions.save_session(resumable)
        snapshot = SessionSnapshot(id=str(uuid4()), session_id=resumable.id, version=1,
            stage='interrupted', created_at=now, graph_state={'turn_id': turn})
        await sessions.save_snapshot(resumable.id, snapshot)
        resume_command = command(resumable, operation='manual_resume', reference_id=snapshot.id, turn_id=turn)
        with pytest.raises(AgentSubmissionConflict, match='project changed'):
            await tasks.submit_resume(resume_command.model_copy(update={'project_id': str(uuid4())}),
                ResumeSessionRequest(), idempotency_key='foreign-resume')
        resumed = await tasks.submit_resume(resume_command, ResumeSessionRequest(), idempotency_key='same-project-resume')
        reread = await tasks.submit_resume(resume_command, ResumeSessionRequest(), idempotency_key='same-project-resume')
        assert resumed.id == reread.id and resumed.project_id == project.id
        assert bound_task.project_id == project.id and unbound_task.project_id is None
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT count(*) AS count FROM {settings.database.postgres_agent_task_table}')
            assert cur.fetchone()['count'] == 3
            cur.execute(f'SELECT count(*) AS count FROM {settings.database.postgres_task_outbox_table}')
            assert cur.fetchone()['count'] == 3
        (output / 'verification.json').write_text(json.dumps({'actual_postgres_uuid_to_string': True,
            'same_project_message_accepted': True, 'unbound_message_accepted': True,
            'same_project_resume_accepted': True, 'resume_idempotent': True,
            'foreign_project_message_resume_rejected': True, 'missing_bound_project_rejected': True,
            'task_rows': 3, 'outbox_rows': 3, 'external_ai_calls': 0}, ensure_ascii=False, indent=2), encoding='utf8')


@contextmanager
def worker_model():
    requests, failures = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                messages = request['messages']
                user = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
                previous = [m for m in messages if m['role'] == 'tool']
                match = re.search(r'Tool arguments:\s*(\{.*?\})\s*After the tool finishes', user, re.S)
                phase = 'child' if match else 'parent'
                campaign_report_ready = any(re.search(
                    r'"phase"\s*:\s*"report_ready"', str(message.get('content') or ''))
                    for message in previous)
                should_call_tool = (
                    (phase == "child" and not previous)
                    or (phase == "parent" and not campaign_report_ready)
                )
                if should_call_tool:
                    args = json.loads(match.group(1)) if match else {}
                    if match:
                        assert set(args) == {'worker_action', 'dispatch_ref'}
                    message = {'role': 'assistant', 'content': '执行固定接口任务', 'tool_calls': [{
                        'id': 'call-' + uuid4().hex, 'type': 'function',
                        'function': {'name': 'api-test-runner', 'arguments': json.dumps(args)}}]}
                    reason = 'tool_calls'
                else:
                    message = {'role': 'assistant', 'content': '固定接口任务已完成；以工具结果和保存的报告为准。'}
                    reason = 'stop'
                requests.append({'phase': phase, 'request': request})
                identity = 'local-' + uuid4().hex
                if request.get('stream'):
                    for index, call in enumerate(message.get('tool_calls', [])):
                        call['index'] = index
                    body = ('data: ' + json.dumps({'id': identity, 'object': 'chat.completion.chunk',
                        'created': 1, 'model': 'local-api-worker', 'choices': [{'index': 0,
                            'delta': message, 'finish_reason': reason}]}, ensure_ascii=False) + '\n\n'
                        'data: [DONE]\n\n').encode()
                    content_type = 'text/event-stream'
                else:
                    body = json.dumps({'id': identity, 'object': 'chat.completion', 'created': 1,
                        'model': 'local-api-worker', 'choices': [{'index': 0,
                            'message': message, 'finish_reason': reason}]}, ensure_ascii=False).encode()
                    content_type = 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                failures.append(type(exc).__name__)
                self.send_error(500, 'controlled fixture error')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize('durable', [False, True], ids=['same-process-child', 'durable-child-process'])
@pytest.mark.parametrize('model_source', ['controlled', 'configured-default'], ids=['controlled-model', 'default-model'])
@pytest.mark.skipif(os.getenv('RUN_LIVE_API_AGENT_WORKER') != '1',
    reason='independent production API and Agent CLI; real isolated MySQL/PG/Redis/RustFS')
async def test_real_agent_worker_private_auth_and_terminal_redelivery(tmp_path, durable, model_source):
    if model_source == 'configured-default' and os.getenv('RUN_LIVE_API_AGENT_WORKER_DEFAULT') != '1':
        pytest.skip('configured-default model acceptance is independently gated')
    variant = model_source + ('-durable' if durable else '-in-process')
    output = Path(os.environ['QA_API_AGENT_WORKER_DIR']) / variant
    output.mkdir(parents=True, exist_ok=False)
    facts = {'external_ai_calls': 0 if model_source == 'controlled' else None,
        'full_goal_complete': False, 'durable_children': durable,
        'model_source': model_source}
    secret_values = []
    parent_checkpoint_timeline = []
    with auth_target() as (business, events), (worker_model() if model_source == 'controlled'
            else nullcontext((None, [], []))) as (model_url, requests, failures):
        try:
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.api_auth_token = SecretStr(uuid4().hex)
                settings.orchestration.coordinator_durable_children = durable
                settings.orchestration.redis_task_outbox_streams = settings.orchestration.agent_task_stream
                # This scenario starts two consumers. Use the production reclaim window so
                # the idle test fixture's 1s default cannot steal an actively executing turn.
                settings.orchestration.redis_task_reclaim_idle_ms = 120000
                models = MySQLModelConfigStore(settings)
                models.initialize()
                if model_source == 'controlled':
                    model = models.upsert(ModelConfigUpdateRequest(model_name='local-api-worker', provider='openai',
                        transport='openai_chat_completions', base_url=model_url, api_key=uuid4().hex, is_active=True,
                        use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
                        context_window=128000, max_output_tokens=1024))
                else:
                    configured = MySQLModelConfigStore(get_settings()).get_default_active()
                    model = models.upsert(ModelConfigUpdateRequest(model_name=configured.model_id,
                        provider=configured.provider, transport=configured.transport,
                        request_profile=configured.request_profile, base_url=configured.api_base_url,
                        api_key=configured.api_key, auth_type=configured.auth_type,
                        oauth_provider=configured.oauth_provider, oauth_refresh_token=configured.oauth_refresh_token,
                        applications=configured.applications, is_active=True, use_provider_defaults=False,
                        capability_overrides=configured.capability_overrides,
                        context_window=configured.context_window, max_output_tokens=configured.max_output_tokens))
                    assert model_invocation_parameters(models.get_default_active()) == model_invocation_parameters(configured)
                    facts['configured_transport'] = configured.transport
                    facts['configured_model_id'] = configured.model_id
                processes.env = child_environment(settings)
                api_port = free_port()
                api_args = ('-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', str(api_port))
                api = processes.start('agent-api', *api_args)
                queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_stream,
                    group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{api_port}/api/v1', timeout=30,
                        headers={'Authorization': 'Bearer ' + settings.api_auth_token.get_secret_value()}) as client:
                        await wait_api(client, api)

                        async def post(path, body, status=200):
                            response = await client.post(path, json=body)
                            expected = status if isinstance(status, tuple) else (status,)
                            assert response.status_code in expected, (path, response.status_code, response.text)
                            return response.json()

                        async def get(path):
                            response = await client.get(path)
                            assert response.status_code == 200, (path, response.status_code)
                            return response.json()

                        project = await post('/projects', {'project_key': 'api-agent-worker',
                            'name': '独立 API Agent Worker 验收'}, 201)
                        graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
                            EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=business + path)
                            for key, method, path in [('login', 'POST', '/login'),
                                ('refresh', 'POST', '/auth/refresh'), ('profile', 'GET', '/profile')]]),
                            preconditions=PreconditionAnalysis())
                        state = ApiTestingState(phase=PHASE_CAMPAIGN_READY, campaign=ApiTestCampaign(
                            campaign_id=str(uuid4()), project_name=project['name'], tasks=graph.tasks,
                            execution_policy=ExecutionPolicy(max_retries=0)))
                        parent = await post('/sessions', {'title': '固定 API 认证链路', 'project_id': project['id'],
                            'session_mode': 'background_task', 'runtime_mode': 'background', 'mode_key': 'api_testing',
                            'selected_agent': 'api-testing-agent', 'preferred_model': model.key})
                        accepted = await post(f"/sessions/{parent['id']}/messages", {
                            'content': '执行已固定的接口测试任务，保存实际认证链路结果。', 'model_key': model.key,
                            'agent_key': 'api-testing-agent',
                            'context': {STATE_METADATA_KEY: state.model_dump(mode='json')}}, 202)
                        task_id = accepted['task']['id']
                        facts.update(project_id=project['id'], parent_session_id=parent['id'], parent_task_id=task_id)
                        workers = [processes.start('agent-worker-' + str(i), '-m', 'src.cli.run_agent_worker',
                            '--worker-id', 'api-auth-' + str(i)) for i in range(2 if durable else 1)]
                        deadline = monotonic() + float(os.getenv('QA_API_AGENT_OBSERVATION_SECONDS', '180'))
                        tasks, session = [], None
                        approvals = []
                        held_child_approvals = {}
                        approval_hold_seconds = float(os.getenv('QA_API_AGENT_APPROVAL_HOLD_SECONDS', '0'))
                        approval_wait_observations = []
                        last_checkpoint_signature = None
                        restart_child_wait = os.getenv('QA_API_AGENT_RESTART_WHILE_CHILD_WAIT') == '1'
                        restarted_child_wait = False
                        while monotonic() < deadline:
                            await PostgresTaskOutbox(settings).relay_once(queue, limit=10)
                            tasks = await get(f"/sessions/{parent['id']}/tasks")
                            session = await get(f"/sessions/{parent['id']}")
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(
                                    f"SELECT s.status AS parent_status, s.snapshot_count, "
                                    f"s.metadata->'pending_turn' AS parent_pending_turn, "
                                    f"snap.id AS snapshot_id, snap.version AS snapshot_version, "
                                    f"snap.stage AS snapshot_stage, "
                                    f"snap.graph_state->'pending_turn' AS snapshot_pending_turn "
                                    f"FROM {settings.database.postgres_session_table} AS s "
                                    f"LEFT JOIN LATERAL (SELECT id, version, stage, graph_state "
                                    f"FROM {settings.database.postgres_snapshot_table} "
                                    f"WHERE session_id = s.id ORDER BY version DESC LIMIT 1) AS snap ON TRUE "
                                    "WHERE s.id = %s",
                                    (parent['id'],),
                                )
                                parent_checkpoint_row = cur.fetchone()

                            def checkpoint_projection(pending):
                                pending = pending if isinstance(pending, dict) else {}
                                waits = pending.get('waiting_tools') or (
                                    [pending['waiting_tool']] if pending.get('waiting_tool') else [])
                                return {
                                    'turn_id': pending.get('turn_id'),
                                    'waiting_tools': [{key: item.get(key) for key in (
                                        'job_id', 'call_id', 'tool_key', 'child_session_ids')}
                                        for item in waits if isinstance(item, dict)],
                                    'pending_approval_ids': list(pending.get('pending_approval_ids') or []),
                                }

                            if parent_checkpoint_row:
                                parent_pending = checkpoint_projection(parent_checkpoint_row['parent_pending_turn'])
                                snapshot_pending = checkpoint_projection(parent_checkpoint_row['snapshot_pending_turn'])
                                checkpoint_sample = {
                                    'parent_status': parent_checkpoint_row['parent_status'],
                                    'snapshot_count': parent_checkpoint_row['snapshot_count'],
                                    'parent_pending_turn': parent_pending,
                                    'snapshot_id': parent_checkpoint_row['snapshot_id'],
                                    'snapshot_version': parent_checkpoint_row['snapshot_version'],
                                    'snapshot_stage': parent_checkpoint_row['snapshot_stage'],
                                    'snapshot_pending_turn': snapshot_pending,
                                }
                                checkpoint_signature = json.dumps(checkpoint_sample, sort_keys=True, default=str)
                                if checkpoint_signature != last_checkpoint_signature:
                                    parent_checkpoint_timeline.append({
                                        'elapsed_seconds': round(float(os.getenv(
                                            'QA_API_AGENT_OBSERVATION_SECONDS', '180')) - max(
                                                0.0, deadline - monotonic()), 3),
                                        **checkpoint_sample,
                                    })
                                    last_checkpoint_signature = checkpoint_signature
                            dispatches = session['metadata'].get('worker_dispatches', [])
                            child_sessions = [await get('/sessions/' + item['child_session_id'])
                                for item in dispatches]
                            wait = (session['metadata'].get('pending_turn') or {}).get('waiting_tool')
                            if restart_child_wait and wait and not restarted_child_wait:
                                snapshot = await PostgresSessionStore(settings).get_latest_snapshot(parent['id'])
                                assert snapshot.graph_state['pending_turn']['waiting_tool'] == wait
                                with postgres_connect(settings) as conn, conn.cursor() as cur:
                                    cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE id=%s",
                                        (wait['job_id'],))
                                    waiting_job = dict(cur.fetchone())
                                assert waiting_job['status'] == 'waiting_children'
                                assert waiting_job['call_id'] == wait['call_id']
                                assert waiting_job['input_payload'] == wait['input']
                                assert not waiting_job['output_payload'].get('report')
                                before_restart = len(events), len(requests)
                                await processes.stop(api)
                                api = processes.start('agent-api-child-wait-restarted', *api_args)
                                await wait_api(client, api)
                                saved = await PostgresSessionStore(settings).get_latest_snapshot(parent['id'])
                                assert saved.id == snapshot.id
                                assert saved.graph_state == snapshot.graph_state
                                assert (len(events), len(requests)) == before_restart
                                facts['child_wait_restart'] = {'snapshot_id': snapshot.id,
                                    'tool_job_id': waiting_job['id'], 'call_id': waiting_job['call_id'],
                                    'turn_id': snapshot.graph_state['turn_id'],
                                    'model_call_count': snapshot.graph_state['model_call_count'],
                                    'max_iterations': snapshot.graph_state['max_iterations'],
                                    'extra_http': 0, 'extra_model_http': 0, 'snapshot_preserved': True}
                                restarted_child_wait = True
                            # Observe the persisted parent boundary before any
                            # approval forwarding. A child may expose its
                            # approval only after the parent API runner has
                            # already returned, so checking only mirrored
                            # approval metadata would miss the actual defect.
                            if approval_hold_seconds > 0 and not approval_wait_observations:
                                waiting_tasks = [task for task in tasks
                                    if str(task.get('result_status') or '') == 'waiting_approval']
                                if waiting_tasks:
                                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                                        cur.execute(f"SELECT id,status,output_payload FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s",
                                            (parent['id'],))
                                        parent_jobs = [dict(row) for row in cur.fetchall()]
                                    reports = [job for job in parent_jobs if (job.get('output_payload') or {}).get('report')]
                                    approval_wait_observations.append({'observation': 'parent_boundary_before_approval',
                                        'parent_status': session['status'],
                                        'parent_report_already_saved': bool(reports),
                                        'parent_report_counts': [{key: report['output_payload']['report'][key]
                                            for key in ('completed', 'failed', 'skipped')} for report in reports],
                                        'waiting_task_ids': [task['id'] for task in waiting_tasks],
                                        'actual_target_statuses_before_approval': [event['status'] for event in events],
                                        'dispatches_before_approval': [{key: item.get(key)
                                            for key in ('task_id', 'child_session_id', 'status')}
                                            for item in session['metadata'].get('worker_dispatches', [])]})
                            for approval in session['pending_approvals']:
                                proxy = bool((approval.get('metadata') or {}).get('proxy_child_session_id'))
                                if approval['id'] not in approvals and (proxy or session['status'] == 'waiting_approval'):
                                    if approval_hold_seconds > 0:
                                        held_since = held_child_approvals.setdefault(approval['id'], monotonic())
                                        if monotonic() - held_since < approval_hold_seconds:
                                            continue
                                        # Read actual saved parent facts before forwarding the child decision.
                                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                                            cur.execute(f"SELECT id,status,output_payload FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s",
                                                (parent['id'],))
                                            parent_jobs = [dict(row) for row in cur.fetchall()]
                                        reports = [job for job in parent_jobs if (job.get('output_payload') or {}).get('report')]
                                        pending_session_id = str(
                                            approval['metadata'].get('proxy_child_session_id') or parent['id'])
                                        pending_child = await get('/sessions/' + pending_session_id)
                                        approval_wait_observations.append({'approval_id': approval['id'],
                                            'held_seconds': monotonic() - held_since,
                                            'parent_status': session['status'], 'child_status': pending_child['status'],
                                            'parent_report_already_saved': bool(reports),
                                            'parent_report_counts': [{key: report['output_payload']['report'][key]
                                                for key in ('completed', 'failed', 'skipped')} for report in reports],
                                            'actual_target_statuses_before_approval': [event['status'] for event in events],
                                            'dispatches_before_approval': [{key: item.get(key)
                                                for key in ('task_id', 'child_session_id', 'status')}
                                                for item in session['metadata'].get('worker_dispatches', [])]})
                                    await post(f"/sessions/{parent['id']}/approvals/{approval['id']}",
                                        {'decision': 'approved', 'reason': 'owned local API test fixture'}, (200, 202))
                                    approvals.append(approval['id'])
                            original = next(t for t in tasks if t['id'] == task_id)
                            if (not (session['metadata'].get('pending_turn') or {}).get('waiting_tool')
                                and original['status'] in {'completed', 'failed', 'cancelled', 'interrupted', 'recovery_required'}
                                and session['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}
                                and all(t['status'] in {'completed', 'failed', 'cancelled', 'interrupted', 'recovery_required'} for t in tasks)
                                and all(child['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}
                                    for child in child_sessions)):
                                break
                            assert all(w.poll() is None for w in workers), 'Agent CLI exited before terminal state'
                            await asyncio.sleep(.25)
                        facts.update(parent_tasks=tasks, parent_status=session['status'], events=events,
                            approval_wait_observations=approval_wait_observations,
                            physical_model_http=(len(requests) if model_source == 'controlled' else None), fixture_errors=failures,
                            tool_messages=[m for m in session['messages'] if m['role'] == 'tool'],
                            approved_parent_calls=len(approvals), worker_dispatches=session['metadata'].get('worker_dispatches', []),
                            parent_has_turn_lease=bool(session['metadata'].get('turn_lease_token')),
                            parent_has_waiting_tool=bool((session['metadata'].get('pending_turn') or {}).get('waiting_tool')))
                        children_observed = []
                        for dispatch in facts['worker_dispatches']:
                            child = await get('/sessions/' + dispatch['child_session_id'])
                            children_observed.append({'id': child['id'], 'mode_key': child['mode_key'],
                                'status': child['status'], 'pending_approvals': len(child['pending_approvals']),
                                'messages': [m for m in child['messages'] if m['role'] == 'tool']})
                        facts['children_observed'] = children_observed
                        facts['waiting_children_count'] = sum(
                            child['status'] in {'running', 'waiting_approval', 'idle'}
                            for child in children_observed)
                        internal = PostgresApiCredentialStore(settings)
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f'SELECT * FROM {internal.tables[1]}')
                            encrypted = [dict(row) for row in cur.fetchall()]
                        secret_values = [(await internal.load_credential(r['id'], owner_session_id=r['owner_session_id'],
                            project_id=str(r['project_id']) if r['project_id'] else None))['token'] for r in encrypted]
                        safe_public = json.dumps({'facts': facts, 'requests': requests}, default=str)
                        assert all(value not in safe_public for value in secret_values), 'Generated credentials in public evidence'
                        facts['observed_public_credential_scan_passed'] = True
                        (output / 'observed.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf8')
                        if model_source == 'configured-default' and durable:
                            session_ids = [parent['id'], *[
                                item['child_session_id'] for item in facts['worker_dispatches']
                                if item.get('child_session_id')]]
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(
                                    f"SELECT id,status,metadata,snapshot_count FROM {settings.database.postgres_session_table} "
                                    "WHERE id::text = ANY(%s)", (session_ids,))
                                session_rows = [dict(row) for row in cur.fetchall()]
                                cur.execute(
                                    f"SELECT id,session_id,operation,turn_id,parent_task_id,reference_id,status,"
                                    f"result_status,error_code,attempt_id,attempt_no,updated_at "
                                    f"FROM {settings.database.postgres_agent_task_table} WHERE session_id::text = ANY(%s) "
                                    "ORDER BY created_at", (session_ids,))
                                task_rows = [dict(row) for row in cur.fetchall()]
                                task_ids = [row['id'] for row in task_rows]
                                if task_ids:
                                    cur.execute(
                                        f"SELECT id,event_key,stream,attempts,published_at,created_at,payload "
                                        f"FROM {settings.database.postgres_task_outbox_table} "
                                        "WHERE payload->>'task_id' = ANY(%s) ORDER BY created_at", (task_ids,))
                                    outbox_rows = [dict(row) for row in cur.fetchall()]
                                else:
                                    outbox_rows = []
                                cur.execute(
                                    f"SELECT id,session_id,tool_key,status,created_at,resolved_at,metadata "
                                    f"FROM {settings.database.postgres_approval_table} WHERE session_id::text = ANY(%s) "
                                    "ORDER BY created_at", (session_ids,))
                                approval_rows = [dict(row) for row in cur.fetchall()]
                                cur.execute(
                                    f"SELECT session_id,version,stage,created_at FROM {settings.database.postgres_snapshot_table} "
                                    "WHERE session_id::text = ANY(%s) ORDER BY created_at", (session_ids,))
                                snapshot_rows = [dict(row) for row in cur.fetchall()]

                            safe_sessions = []
                            for row in session_rows:
                                metadata = row.get('metadata') or {}
                                pending = metadata.get('pending_turn') or {}
                                waits = pending.get('waiting_tools') or ([pending['waiting_tool']]
                                    if pending.get('waiting_tool') else [])
                                safe_sessions.append({
                                    'id': row['id'], 'status': row['status'],
                                    'snapshot_count': row['snapshot_count'],
                                    'metadata_keys': sorted(metadata),
                                    'control_state': (metadata.get('control') or {}).get('control_state'),
                                    'has_turn_lease': bool(metadata.get('turn_lease_token')),
                                    'pending_turn_keys': sorted(pending),
                                    'waiting_tool_checkpoints': [{key: item.get(key) for key in (
                                        'job_id', 'call_id', 'tool_key', 'child_session_ids')}
                                        for item in waits],
                                    'pending_approval_ids': list(pending.get('pending_approval_ids') or []),
                                })
                            safe_outbox = []
                            for row in outbox_rows:
                                payload = row.get('payload') or {}
                                safe_outbox.append({
                                    key: row.get(key) for key in (
                                        'id', 'event_key', 'stream', 'attempts', 'published_at', 'created_at')
                                } | {'payload': {key: payload.get(key) for key in (
                                    'task_id', 'parent_task_id', 'session_id', 'operation', 'reference_id',
                                    'parent_turn_id', 'child_task_id', 'child_session_id', 'status', 'result_status')
                                    if key in payload}})
                            diagnostic = {
                                'parent_session_id': parent['id'],
                                'parent_task_id': task_id,
                                'sessions': safe_sessions,
                                'tasks': task_rows,
                                'outbox': safe_outbox,
                                'approvals': [{
                                    'id': row['id'], 'session_id': row['session_id'], 'tool_key': row['tool_key'],
                                    'status': row['status'], 'created_at': row['created_at'],
                                    'resolved_at': row['resolved_at'],
                                    'metadata_keys': sorted(row.get('metadata') or {}),
                                } for row in approval_rows],
                                'snapshots': snapshot_rows,
                            }
                            (output / 'recovery-diagnostics.json').write_text(
                                json.dumps(diagnostic, ensure_ascii=False, indent=2, default=str), encoding='utf8')
                        if not events:
                            # No target response means this fixture has not generated any credentials.
                            (output / 'model-request-diagnostics.json').write_text(
                                json.dumps(requests, ensure_ascii=False, indent=2), encoding='utf8')
                        assert not failures
                        if restart_child_wait:
                            assert restarted_child_wait, 'No persisted original child wait was restarted'
                        if approval_hold_seconds > 0:
                            assert approval_wait_observations, 'No actual child approval wait was observed'
                            assert not any(item['parent_report_already_saved'] for item in approval_wait_observations), facts
                        assert [e['status'] for e in events] == [200, 200, 200], facts
                        assert original['status'] == 'completed', facts
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} ORDER BY created_at")
                            jobs = [dict(row) for row in cur.fetchall()]
                            cur.execute(f"SELECT * FROM {settings.database.postgres_session_table} ORDER BY created_at")
                            sessions = [dict(row) for row in cur.fetchall()]
                            cur.execute(f"SELECT payload FROM {settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (task_id,))
                            replay_command = cur.fetchone()['payload']
                        reports = [j for j in jobs if isinstance(j.get('output_payload'), dict)
                            and j['output_payload'].get('report')]
                        assert len(reports) == 1 and reports[0]['output_payload']['report']['completed'] == 3
                        assert reports[0]['output_payload']['report']['failed'] == 0
                        children = [s for s in sessions if (s.get('metadata') or {}).get('parent_session_id') == parent['id']]
                        assert len(children) == 3 and all(str(s['project_id']) == project['id'] for s in children)
                        internal = PostgresApiCredentialStore(settings)
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f'SELECT * FROM {internal.tables[1]}')
                            encrypted = [dict(row) for row in cur.fetchall()]
                        assert len(encrypted) == 2
                        secret_values = [(await internal.load_credential(r['id'], owner_session_id=parent['id'],
                            project_id=project['id']))['token'] for r in encrypted]
                        public = json.dumps({'requests': requests, 'sessions': sessions, 'jobs': jobs}, default=str)
                        assert all(value not in public for value in secret_values)
                        assert all(value not in r['ciphertext'] for value in secret_values for r in encrypted)
                        facts.update(real_child_sessions=len(children), parent_report=reports[0]['output_payload']['report'],
                            parent_job_id=reports[0]['id'], generated_credentials_absent_from_public_output=True)
                        for worker in workers:
                            await processes.stop(worker)
                        def observed_model_http_count():
                            if model_source == 'controlled':
                                return len(requests)
                            return sum(path.read_text(encoding='utf-8', errors='replace').count(
                                'model_http_request_attempt_started') for path in tmp_path.glob('agent-worker-*.stderr'))
                        before_terminal_model_http = observed_model_http_count()
                        if model_source == 'configured-default':
                            facts['external_ai_calls'] = before_terminal_model_http
                            assert before_terminal_model_http > 0, 'Default model made no observed HTTP attempts'
                        await processes.stop(api)
                        api = processes.start('agent-api-restarted', *api_args)
                        await wait_api(client, api)
                        assert (await get(f"/sessions/{parent['id']}/tasks")) == tasks
                        await queue.enqueue(replay_command)
                        before = observed_model_http_count(), len(events)
                        replay = processes.start('agent-worker-terminal', '-m', 'src.cli.run_agent_worker', '--once',
                            '--worker-id', 'api-auth-terminal')
                        assert await processes.wait(replay, timeout=30) == 0
                        assert (observed_model_http_count(), len(events)) == before
                        facts.update(api_restart_history_preserved=True, terminal_redelivery_new_http=0,
                            terminal_redelivery_new_model_http=0,
                            observed_model_http_before_terminal_redelivery=before_terminal_model_http,
                            physical_model_http=before_terminal_model_http)
                        (output / 'verification.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf8')
                        (output / 'public-records.json').write_text(public, encoding='utf8')
                finally:
                    await queue.close()
        finally:
            model_http_attempts = len(requests) if model_source == 'controlled' else sum(
                path.read_text(encoding='utf-8', errors='replace').count(
                    'model_http_request_attempt_started')
                for path in tmp_path.glob('agent-worker-*.stderr'))
            for path in tmp_path.iterdir():
                if path.suffix in {'.stdout', '.stderr'} or path.name in {'docker.json', 'postgres-finally.json'}:
                    assert all(value.encode() not in path.read_bytes() for value in secret_values), 'Generated credentials in preserved logs'
                    shutil.copyfile(path, output / path.name)
            (output / 'target-events-at-cleanup.json').write_text(json.dumps({'events': events,
                'physical_model_http': model_http_attempts, 'fixture_errors': failures,
                'external_ai_calls': model_http_attempts if model_source == 'configured-default' else 0,
                'public_credential_scan_passed': bool(facts.get('observed_public_credential_scan_passed'))},
                indent=2), encoding='utf8')
            (output / 'parent-checkpoint-timeline.json').write_text(json.dumps(
                parent_checkpoint_timeline, ensure_ascii=False, indent=2, default=str), encoding='utf8')
