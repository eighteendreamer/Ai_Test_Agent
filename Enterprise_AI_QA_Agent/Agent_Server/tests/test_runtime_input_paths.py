"""Actual files, fixed runs and physical model requests for runtime bindings."""
import base64
import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_case_plan_run_creation import LIVE, planned
from test_agent_case_assessment import business_http
from test_case_planning import SequenceModel, active_case, finish, item
from test_prompt_history_http import _state, model_http_server
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_execution import AgentCaseExecutionAdapter, CHECKPOINT_KEY
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.registry.agents import AgentRegistry
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.case_execution import AgentCaseExecutionCheckpoint
from src.schemas.model_config import ModelConfigRecord
from src.schemas.run_management import RunItemCompletion
from src.schemas.tool_runtime import ModelToolCall


def save_evidence(name, value, original):
    target = os.getenv('QA_RUNTIME_INPUT_PATH_DIR')
    if not target:
        return
    directory = Path(target) / name
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'original.json').write_bytes(original)
    (directory / 'verification.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
@pytest.mark.parametrize('langchain_enabled', [False, True])
async def test_runtime_paths_reach_actual_model_request(tmp_path, live, langchain_enabled, model_http_server, business_http, monkeypatch):
    async with planned(tmp_path, live) as (env, store, suites, creator, previous, now, table):
        project = await env.projects.get(previous.project_id)
        actual_id = business_http[2]
        settings = {'': {'enabled': False}, 'flags': {'order.id': 0}, 'items': [{'id': actual_id}],
                    'legacy': {'id': 'legacy-file-id'}, 'nullable': None}
        payload = {'objective': '核对输入子字段', 'steps': [{'order': n, 'action': '读取实际输入'} for n in range(1, 9)],
                   'settings': settings, 'tail': '完整第八步不能截断😀',
                   'expected': {'code': 200, 'flag': False, 'count': 0, 'id': actual_id}}
        file = tmp_path / 'runtime-input.json'
        file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        doc = await env.docs.upload_document(project_id=project.id, filename=file.name,
            content_base64=base64.b64encode(file.read_bytes()).decode())
        original = (await env.objects.read_object_uri(doc.original_asset.storage_uri))['content']
        assert original == file.read_bytes()
        content = json.loads(original)
        bundle = await active_case(env, project, 'runtime-path', content)
        paths = {'disabled': ['', 'enabled'], 'zero': ['flags', 'order.id'], 'array_id': ['items', '0', 'id'],
                 'whole': [], 'missing': ['flags', 'absent'], 'nullable': ['nullable']}
        bindings = {key: {'input_key': 'settings', 'output_path_tokens': path,
                         'required': key not in {'missing', 'nullable'}} for key, path in paths.items()}
        bindings['legacy'] = {'input_key': 'settings', 'output_path': 'legacy.id'}
        env.service._models = SequenceModel(lambda c: finish(item(c, 'runtime-path', input_bindings=bindings)))
        plan = await env.service.create(project.id, CasePlanningRequest(objective=content['objective'],
            candidate_case_ids=[bundle.case.id], runtime_context={'settings': content['settings']}))
        assert plan.status == 'planned', plan.summary
        detail = await creator.create(project.id, plan.id, CasePlanRunRequest(creation_key='runtime-input', mode_key='api_testing'))
        claims = await store.claim_items(run_id=detail.run.id, worker_id='input-consumer', limit=10, lease_seconds=60, now=now)
        assert len(claims) == 1
        claimed, attempt = claims[0]
        detail = await store.get_run(detail.run.id)
        basis = build_agent_case_context(detail, claimed, await env.cases.get_version(claimed.case_version_id))
        inputs = basis.steps[0].inputs
        assert inputs['disabled'].resolved and inputs['disabled'].value is False
        assert inputs['zero'].resolved and inputs['zero'].value == 0 and type(inputs['zero'].value) is int
        assert inputs['array_id'].value == actual_id
        assert inputs['legacy'].value == 'legacy-file-id'
        assert inputs['whole'].value == settings
        assert not inputs['missing'].resolved and inputs['missing'].value is None
        assert not inputs['nullable'].resolved and inputs['nullable'].value is None
        assert detail.run.plan_snapshot.plan.request.runtime_context['settings'] == settings
        assert basis.steps[0].content_json == content and len(content['steps']) == 8
        cloned = deepcopy(inputs['whole'].value)
        inputs['whole'].value['items'][0]['id'] = 'modified-copy'
        assert detail.run.plan_snapshot.plan.request.runtime_context['settings']['items'][0]['id'] == actual_id
        inputs['whole'].value = cloned
        state = _state([])
        state['context_bundle'] = basis.runtime_context()
        monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
        monkeypatch.setenv('LANGSMITH_TRACING', 'false')
        url, requests = model_http_server
        config = ModelConfigRecord(key='local-model', name='Local', provider='openai',
            transport='openai_chat_completions', model_id='local-model', api_base_url=url, api_key=uuid4().hex)
        runtime = ModelRuntimeService(model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
            settings=SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=10,
                langchain_model_adapter_enabled=langchain_enabled)))
        build_prompt_assembler_node(PromptAssemblyService(), AgentRegistry())(state)
        await build_model_invoker_node(runtime, ToolRegistry())(state)
        assert state['model_response_summary']['mode'] == 'ok' and len(requests) == 1
        system = '\n'.join(m['content'] for m in requests[0]['messages'] if m['role'] == 'system')
        assert json.dumps(basis.execution_json(), ensure_ascii=False, allow_nan=False) in system
        assert 'DO-NOT-INJECT-TEMPLATE' not in system
        tables = []
        if live:
            tables = ['up_rip_job_' + uuid4().hex[:12], 'up_rip_art_' + uuid4().hex[:12]]
            env.settings.database.postgres_tool_job_table, env.settings.database.postgres_tool_artifact_table = tables
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for name in tables:
                    cur.execute('SELECT to_regclass(%s) AS existing', (name,))
                    assert cur.fetchone()['existing'] is None
        jobs = ToolJobService(PostgresToolJobStore(env.settings) if live else InMemoryToolJobStore())
        try:
            await jobs.initialize()
            await store.start_item(claimed.id, attempt.lease_token, now)
            session, turn = str(uuid4()), str(uuid4())
            tool_context = ToolExecutionContext(session_id=session, turn_id=turn, trace_id=turn,
                user_message=basis.objective, normalized_input=basis.objective,
                context_bundle={**basis.runtime_context(), 'attempt_id': attempt.id})
            url = business_http[0] + '/orders/' + inputs['array_id'].value
            tool = ToolRegistry().get('api-test-runner')
            record = await ToolRuntimeService(tool_job_service=jobs).execute(tool, ModelToolCall(
                id='query-runtime-id', name=tool.key, arguments={'worker_action': 'execute_task',
                'task': {'task_id': 'query-runtime-id', 'method': 'GET', 'path': url, 'full_url': url,
                         'timeout_seconds': 5}}), tool_context)
            assert business_http[1] == [('GET', '/orders/' + actual_id)]
            saved_job = await jobs.get_job_detail(record.job_id)
            assert saved_job.input_payload['task']['full_url'] == url
            assert saved_job.output_payload['task_result']['response_body']['order.id'] == actual_id
            key = basis.target_key
            checks = []
            for expected, path in [('code', ['response_status']), ('flag', ['response_body', 'flag']),
                                   ('count', ['response_body', 'count']), ('id', ['response_body', 'order.id'])]:
                checks.append({'name': expected, 'expected_path_tokens': ['expected', expected], 'operator': 'equals',
                    'observations': [{'call_id': record.call_id, 'output_path_tokens': ['task_result', *path]}]})
            receipt = {'schema_version': 1, 'steps': [{'item_key': key, 'status': 'passed', 'summary': '核对实际字段',
                'checks': checks, 'outputs': {'body': {'call_id': record.call_id,
                    'output_path_tokens': ['task_result', 'response_body']}}}]}
            outcome = await AgentCaseAssessmentService(jobs).assess(context=basis, receipt=receipt,
                tool_records=[record], session_id=session, turn_id=turn, attempt_id=attempt.id,
                model_key='controlled-runtime-input-receipt', lease_token=attempt.lease_token)
            assert outcome.completion.status == 'passed'
            completion = RunItemCompletion.model_validate({**outcome.completion.model_dump(exclude={'lease_token'}),
                'payload_hash': 'a' * 64})
            saved_result = await store.complete_item(claimed.id, attempt.lease_token, completion, now)
            assert saved_result.actual['case_output']['body']['flag'] is False
            assert saved_result.actual['case_output']['body']['count'] == 0
            assert not await store.claim_items(run_id=detail.run.id, worker_id='terminal', limit=1, lease_seconds=60, now=now)
            assert business_http[1] == [('GET', '/orders/' + actual_id)]
        finally:
            if tables:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    for name in reversed(tables):
                        cur.execute(f'DROP TABLE "{name}"')
        save_evidence(f'paths-{live}-{langchain_enabled}', {'live': live, 'controlled_model': True,
            'langchain_enabled': langchain_enabled, 'original': doc.model_dump(mode='json'),
            'plan': plan.model_dump(mode='json'), 'actual_saved_run': detail.model_dump(mode='json'),
            'context': basis.execution_json(), 'physical_model_requests': requests,
            'business_HTTP': business_http[1], 'tool_job': saved_job.model_dump(mode='json'),
            'saved_result': saved_result.model_dump(mode='json'), 'autonomous_agent': False}, original)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
@pytest.mark.parametrize('path', [['absent'], ['nullable']])
async def test_required_runtime_subfield_is_not_satisfied_by_parent_object(tmp_path, live, path):
    async with planned(tmp_path, live) as (env, store, suites, creator, previous, now, table):
        env.service._models = SequenceModel(lambda c: finish(item(c, 'item-0', input_bindings={
            'required_value': {'input_key': 'settings', 'output_path_tokens': path}})))
        plan = await env.service.create(previous.project_id, CasePlanningRequest(objective='必要字段未提供',
            runtime_context={'settings': {'nullable': None, 'unrelated': 'cannot satisfy the selected path'}}))
        assert plan.status == 'planned', plan.summary
        detail = await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key='required-runtime', mode_key='api_testing'))
        claim = (await store.claim_items(run_id=detail.run.id, worker_id='missing-input', limit=1, lease_seconds=60, now=now))[0][0]
        with pytest.raises(ValueError, match='必要输入 required_value 没有实际来源'):
            build_agent_case_context(await store.get_run(detail.run.id), claim, await env.cases.get_version(claim.case_version_id))
        assert (await store.get_item(claim.id)).started_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_previously_admitted_parent_value_is_not_silently_replaced_on_recovery(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, creator, previous, now, table):
        settings = {'enabled': False}
        env.service._models = SequenceModel(lambda c: finish(item(c, 'item-0', input_bindings={
            'flag': {'input_key': 'settings', 'output_path_tokens': ['enabled']}})))
        plan = await env.service.create(previous.project_id, CasePlanningRequest(objective='恢复固定旧上下文',
            runtime_context={'settings': settings}))
        assert plan.status == 'planned'
        detail = await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key='old-runtime', mode_key='api_testing'))
        claimed, attempt = (await store.claim_items(run_id=detail.run.id, worker_id='original', limit=1, lease_seconds=60, now=now))[0]
        await store.start_item(claimed.id, attempt.lease_token, now)
        detail = await store.get_run(detail.run.id)
        claimed = detail.items[0]
        version = await env.cases.get_version(claimed.case_version_id)
        basis = build_agent_case_context(detail, claimed, version)
        assert basis.steps[0].inputs['flag'].value is False
        # The original implementation injected the full parent object. Preserve
        # its actual context identity instead of rewriting an admitted operation.
        original = basis.model_copy(deep=True)
        original.steps[0].inputs['flag'].value = settings
        raw = json.dumps(original.execution_json(), sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(',', ':')).encode()
        checkpoint = AgentCaseExecutionCheckpoint(project_id=plan.project_id, run_id=detail.run.id,
            run_item_id=claimed.id, plan_id=plan.id, context_hash=hashlib.sha256(raw).hexdigest(),
            execution_attempt_id=attempt.id, session_id=str(uuid4()), turn_id=str(uuid4()), phase='admitted')
        await store.save_checkpoint(claimed.id, attempt.lease_token, CHECKPOINT_KEY, checkpoint.model_dump(mode='json'), 1, now)
        before = await store.get_latest_attempt(claimed.id)
        runs = SimpleNamespace(get_execution_detail=store.get_execution_detail)
        adapter = AgentCaseExecutionAdapter(run_service=runs, session_service=None, session_store=None, tool_job_service=None)
        with pytest.raises(ValueError, match='完整上下文不一致'):
            await adapter._load(run=detail.run, item=claimed, version=version, attempt_id=attempt.id)
        assert await store.get_latest_attempt(claimed.id) == before
        save_evidence(f'old-checkpoint-{live}', {'live': live, 'historical_context_fixture': original.execution_json(),
            'current_context': basis.execution_json(), 'checkpoint_before': before.model_dump(mode='json'),
            'checkpoint_after': (await store.get_latest_attempt(claimed.id)).model_dump(mode='json'),
            'model_HTTP': 0, 'business_HTTP': 0, 'recovered': False}, raw)
