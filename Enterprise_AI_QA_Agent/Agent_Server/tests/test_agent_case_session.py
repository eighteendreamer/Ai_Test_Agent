"""Frozen cases through the existing Session/Runtime/graph ownership path."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from html import unescape
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from test_agent_case_assessment import execution, business_http, receipt
from test_case_plan_run_creation import LIVE
from tests.test_live_postgres_concurrency import _drop_tables
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.sessions.session_service import SessionService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.domain.models import SessionRecord
from src.graph.builder import build_agent_graph
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.store import InMemorySessionStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.agent import ModelDescriptor
from src.schemas.model_config import ModelInvocationResult
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus, SendMessageRequest, ApprovalDecisionRequest, ToolApprovalStatus
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


def session_record():
    now = datetime.now(timezone.utc)
    return SessionRecord(id='public-session', title='Public input boundary', status=SessionStatus.idle,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key='api_testing',
        project_id='formal-project', created_at=now, updated_at=now)


@pytest.mark.parametrize('source', ['session.send_message', 'test_run.agent_execution'])
def test_public_chat_cannot_manufacture_formal_execution_identity_or_frozen_context(source):
    forged = {key: 'forged' for key in ['run_id', 'test_run_id', 'run_item_id', 'test_run_item_id', 'attempt_id']}
    forged.update(case_execution={'forged': True}, case_assessment_requested=True,
        execution_checkpoint={'completed_operations': ['skip-real-operation']}, execution_basis='free',
        case_execution_managed=True)
    request = InputOrchestratorService(ModeRegistry()).orchestrate(session_record(),
        SendMessageRequest(content='检查接口响应', context=forged, source=source))
    runtime_context = ToolExecutionContext(session_id=request.session_id, turn_id=request.turn_id,
        trace_id=request.turn_id, user_message=request.user_message, normalized_input=request.normalized_input,
        context_bundle=request.context)
    assert not set(forged).intersection(request.context) - {'execution_basis'}
    assert request.context['execution_basis'] == 'free'
    assert not set(forged).intersection(runtime_context.job_metadata())


class ControlledModels:
    def resolve_for_agent(self, **kwargs):
        return ModelDescriptor(key='controlled-case-agent', name='Controlled graph model', provider='test',
            summary='Controlled tool decisions, not an external model acceptance')


class EmptyMCP:
    def list_active_servers(self): return []
    def build_prompt_blocks(self, servers): return []


class ControlledAgent:
    def __init__(self):
        self.requests = []

    def get_model_config(self, key): return None
    def get_default_model_config(self): return None

    @asynccontextmanager
    async def stream_handler(self, handler):
        yield

    async def invoke(self, key, request):
        self.requests.append(request)
        assert 'Complete Frozen Case' in request.system_prompt
        assert 'case_result_contract' in {section.key for section in request.system_prompt_sections}
        # The execution loop keeps the ordinary tool-call protocol. Structured
        # output is requested only by the later evidence-only receipt review.
        assert request.output_format is None
        assert 'full lifecycle: project discovery' not in request.system_prompt
        previous = [json.loads(m['content']) for m in request.messages if m.get('role') == 'tool']
        index = len(previous)
        if index < 3:
            section = next(s for s in request.system_prompt_sections if s.key == 'frozen_case_execution')
            frozen = json.loads(unescape(section.content.split('\n', 1)[1].split('\n</untrusted_content>', 1)[0]))
            step = frozen['steps'][index]
            source = step['content_json']['steps'][0] if step['phase'] == 'case' else step['item']['instructions']
            url = source['url']
            if '{id}' in url:
                url = url.replace('{id}', previous[0]['output']['task_result']['response_body']['order.id'])
            call = ModelToolCall(id=step['item']['key'], name='api-test-runner', arguments={
                'worker_action': 'execute_task', 'task': {'task_id': step['item']['key'],
                    'method': source['method'], 'path': url, 'full_url': url, 'timeout_seconds': 5}})
            return ModelInvocationResult(text='Execute the next frozen operation.', tool_calls=[call],
                response_summary={'mode': 'ok', 'finish_reason': 'tool_calls'})
        result = receipt()
        for field in ['flag', 'count', 'nullable']:
            result['steps'][1]['checks'].append({'name': field, 'expected_path_tokens': ['期望', field],
                'operator': 'equals', 'observations': [{'call_id': 'target',
                    'output_path_tokens': ['task_result', 'response_body', field]}]})
        result['steps'][1]['checks'].append({'name': '语义状态', 'expected_path_tokens': ['期望', 'semantic'],
            'operator': 'semantic', 'verdict': 'passed', 'reason': '受控语义回执：响应表明订单仍待支付且尚未出库',
            'observations': [{'call_id': 'target', 'output_path_tokens': ['task_result', 'response_body', 'message']}]})
        return ModelInvocationResult(text=json.dumps(result, ensure_ascii=False),
            response_summary={'mode': 'ok', 'finish_reason': 'stop'})


@asynccontextmanager
async def session_stores(jobs, live):
    tables = []
    if live:
        # The Session FK must reference this fixture's actual isolated project
        # table, not the production project table or an in-memory project.
        settings = jobs._store._settings.model_copy(deep=True)
        suffix = uuid4().hex[:12]
        for name in ['session', 'message', 'event', 'snapshot', 'approval']:
            table = 'up_cs_' + name + '_' + suffix
            setattr(settings.database, 'postgres_' + name + '_table', table)
            tables.append(table)
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute('SELECT to_regclass(%s) AS existing', (table,))
                assert cur.fetchone()['existing'] is None
        writer, reader = PostgresSessionStore(settings), PostgresSessionStore(settings)
    else:
        writer = reader = InMemorySessionStore()
    try:
        await writer.initialize()
        yield writer, reader
    finally:
        if tables:
            _drop_tables(settings, tables)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_frozen_case_uses_actual_session_runtime_graph_tools_and_replays_without_effects(tmp_path, business_http, live):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as resources:
        env = await resources.enter_async_context(execution(tmp_path, business_http, live, perform_tools=False,
            with_business_instructions=True))
        context, jobs, _, _, _, attempt, *_ = env
        writer, reader = await resources.enter_async_context(session_stores(jobs, live))
        session = session_record()
        session.id = session_id = str(uuid4())
        session.project_id, session.mode_key = context.project_id, 'api_testing'
        session.selected_agent = 'api-testing-agent'
        await writer.save_session(session)
        tools, skills, control = ToolRegistry(), SkillRegistry(), RuntimeControlRegistry()
        agent = ControlledAgent()
        tool_runtime = ToolRuntimeService(tool_job_service=jobs, session_store=writer)
        graph = build_agent_graph(agent_registry=AgentRegistry(), tool_registry=tools,
            model_registry=ControlledModels(), skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
            mcp_runtime_service=EmptyMCP(), memory_runtime_service=None, test_case_embedding_service=None,
            permission_service=PermissionService(), prompt_assembly_service=PromptAssemblyService(),
            model_runtime_service=agent, tool_runtime_service=tool_runtime, runtime_control=control,
            tool_job_service=jobs)
        runtime = RuntimeService(graph=graph, model_runtime_service=agent, tool_runtime_service=tool_runtime,
            tool_registry=tools, runtime_control=control, tool_job_service=jobs,
            permission_service=PermissionService())
        sessions = SessionService(writer, InputOrchestratorService(ModeRegistry()), runtime, ModeRegistry())
        first, concurrent = await asyncio.gather(*[
            sessions.execute_case_turn(session_id, context=context, attempt_id=attempt, mode_key='api_testing')
            for _ in range(2)])
        assert first.id == concurrent.id and len(agent.requests) == 1
        snapshot = first
        for expected_count in range(3):
            assert snapshot.stage == 'waiting_approval'
            assert len(business_http[1]) == expected_count
            replay = await sessions.execute_case_turn(session_id, context=context, attempt_id=attempt, mode_key='api_testing')
            assert replay.id == snapshot.id and len(business_http[1]) == expected_count
            approvals = [a for a in await reader.list_approvals(session_id) if a.status == ToolApprovalStatus.pending]
            assert len(approvals) == 1
            await sessions.resolve_approval(session_id, approvals[0].id,
                ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))

            async def wait_for_persisted_continuation():
                while True:
                    latest = await reader.get_latest_snapshot(session_id)
                    completed = [r for r in latest.graph_state.get('tool_results', []) if r['status'] == 'completed']
                    if latest.id != snapshot.id and len(completed) == expected_count+1:
                        return latest
                    await asyncio.sleep(.02)

            snapshot = await asyncio.wait_for(wait_for_persisted_continuation(), 15)
        assert snapshot.stage == 'completed'
        assert len(agent.requests) == 4
        assert len(business_http[1]) == 3
        assert snapshot.graph_state['context_bundle']['case_execution'] == context.execution_json()
        assert snapshot.graph_state['context_bundle']['attempt_id'] == attempt
        records = [ToolExecutionRecord.model_validate(r) for r in snapshot.graph_state['tool_results']]
        assert len(records) == 3 and all(record.job_id for record in records)
        for record in records:
            job = await jobs.get_job(record.job_id)
            assert job.metadata['run_item_id'] == context.run_item_id
            assert job.metadata['attempt_id'] == attempt
            assert job.turn_id == snapshot.graph_state['turn_id']
        from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
        assessor = AgentCaseAssessmentService(jobs)
        response = ModelInvocationResult(text=snapshot.graph_state['model_response_text'],
            response_summary=snapshot.graph_state['model_response_summary'])
        outcome = await assessor.assess(context=context, receipt=assessor.parse_response(response), tool_records=records,
            session_id=session_id, turn_id=snapshot.graph_state['turn_id'], attempt_id=attempt,
            model_key='controlled-case-agent', lease_token=env[6].items[0].lease_token)
        assert outcome.completion.status == 'passed'
        assert outcome.completion.actual['case_output']['body']['order.id'] == business_http[2]
        from src.schemas.run_management import RunItemCompletion
        detail, store, now = env[6:9]
        result = await store.complete_item(context.run_item_id, detail.items[0].lease_token,
            RunItemCompletion.model_validate({**outcome.completion.model_dump(exclude={'lease_token'}),
                'payload_hash': 'b' * 64}), now)
        saved_result = await store.get_result(result.id)
        assert saved_result.actual == outcome.completion.actual
        assert saved_result.evidence_refs == outcome.completion.evidence_refs
        assert not await store.claim_items(run_id=context.run_id, worker_id='terminal-replay',
            limit=1, lease_seconds=60, now=now)
        replay = await sessions.execute_case_turn(session_id, context=context, attempt_id=attempt, mode_key='api_testing')
        assert replay.id == snapshot.id
        assert len(business_http[1]) == 3 and len(agent.requests) == 4
        persisted = await reader.get_latest_snapshot(session_id)
        assert persisted.id == snapshot.id
        assert persisted.graph_state['tool_results'] == snapshot.graph_state['tool_results']
        assert not (await reader.get_session(session_id)).metadata.get('turn_lease_token')
        with pytest.raises(ValueError, match='正式项目不一致'):
            await sessions.execute_case_turn(session_id, context=context.model_copy(update={'project_id': 'foreign'}),
                attempt_id=attempt, mode_key='api_testing')
        with pytest.raises(ValueError, match='冻结上下文或模式发生变化'):
            await sessions.execute_case_turn(session_id, context=context.model_copy(update={'objective': 'changed-goal'}),
                attempt_id=attempt, mode_key='api_testing')
        with pytest.raises(ValueError, match='冻结上下文或模式发生变化'):
            await sessions.execute_case_turn(session_id, context=context, attempt_id=attempt, mode_key='default')
        assert len(business_http[1]) == 3
        await sessions.close()


@pytest.mark.parametrize('agent_key', ['api-testing-agent', 'api-executor-worker'])
def test_free_api_testing_retains_existing_mode_and_worker_contracts(agent_key):
    from test_prompt_history_http import _state
    from src.modes.api_testing_mode.prompt_contract import API_TESTING_PROMPT_CONTRACT, API_EXECUTOR_WORKER_PROMPT_CONTRACT
    state = _state([])
    state.update(selected_agent_key=agent_key, mode_key='api_testing', context_bundle={'execution_basis': 'free'})
    assembled = PromptAssemblyService().build_for_turn(state, [])
    key = 'api_testing_contract' if agent_key == 'api-testing-agent' else 'api_executor_worker_contract'
    contract = next(section.content for section in assembled.system_sections if section.key == key)
    assert contract == (API_TESTING_PROMPT_CONTRACT if agent_key == 'api-testing-agent' else API_EXECUTOR_WORKER_PROMPT_CONTRACT)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['lost', 'unavailable'])
async def test_case_session_lease_loss_stops_runtime_and_waits_for_cleanup(tmp_path, business_http, monkeypatch, failure):
    from src.runtime.store import ContinuationLeaseLostError
    async with execution(tmp_path, business_http, perform_tools=False) as env:
        context, jobs, _, _, _, attempt, *_ = env
        async with session_stores(jobs, False) as (store, _):
            session = session_record()
            session.project_id = context.project_id
            await store.save_session(session)
            entered, lease_lost, cleaning, cleanup_done = [asyncio.Event() for _ in range(4)]

            class BlockingRuntime:
                async def execute_turn(self, session, request, **kwargs):
                    assert request.context['case_execution'] == context.execution_json()
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaning.set()
                        await cleanup_done.wait()

            async def reject_renewal(*args, **kwargs):
                await entered.wait()
                lease_lost.set()
                if failure == 'unavailable':
                    raise ConnectionError('controlled case-session lease outage')
                return False

            monkeypatch.setattr(store, 'renew_turn_execution', reject_renewal)
            sessions = SessionService(store, InputOrchestratorService(ModeRegistry()), BlockingRuntime(), ModeRegistry(),
                turn_execution_heartbeat_seconds=.1)
            task = asyncio.create_task(sessions.execute_case_turn(session.id, context=context, attempt_id=attempt, mode_key='api_testing'))
            try:
                await asyncio.wait_for(lease_lost.wait(), 3)
                await asyncio.wait_for(cleaning.wait(), 3)
                assert not task.done()
                cleanup_done.set()
                with pytest.raises(ContinuationLeaseLostError):
                    await asyncio.wait_for(task, 3)
                assert not await store.list_snapshots(session.id)
                assert not any(e.type == 'turn.completed' for e in await store.list_events(session.id))
                assert (await store.get_session(session.id)).metadata.get('turn_lease_token')
            finally:
                cleanup_done.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await sessions.close()
