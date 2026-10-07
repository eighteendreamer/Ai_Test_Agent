"""Evidence-backed receipts, with real files, ToolRuntime and local business HTTP."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from time import perf_counter
from uuid import uuid4

import pytest

from test_case_plan_run_creation import planned, LIVE
from test_case_planning import SequenceModel, finish, item
from test_plan_binding_paths import active_case
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.tool_job import ToolJobStatus


@pytest.fixture
def business_http():
    requests = []
    actual_id = 'actual-' + uuid4().hex
    body = {'order.id': actual_id, 'flag': False, 'count': 0, 'nullable': None,
            'message': '订单处于待支付状态，尚未进入出库流程'}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(('POST', self.path))
            self.reply(201)

        def do_GET(self):
            requests.append(('GET', self.path))
            self.reply(200 if self.path == '/orders/' + actual_id else 404)

        def do_DELETE(self):
            requests.append(('DELETE', self.path))
            self.reply(200)

        def reply(self, status):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, actual_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@asynccontextmanager
async def execution(tmp_path, business_http, live=False, perform_tools=True, with_business_instructions=False,
    services=None, start_item=True, planning_policy=None):
    async with planned(tmp_path, live) as (env, store, suites, creator, old, now, table):
        project = await env.projects.get(old.project_id)
        if planning_policy is not None:
            await env.service.save_policy(project.id, planning_policy)
        content = {'objective': '完整用例检查', '期望': {'code': 200, 'flag': False, 'count': 0,
                   'nullable': None, 'semantic': '订单仍待支付且未出库'},
                   'steps': [{'action': '核对完整实际响应'}]}
        if with_business_instructions:
            content['steps'][0].update(method='GET', url=business_http[0]+'/orders/{id}')
        source = tmp_path / 'assessment-case.json'
        source.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
        doc = await env.docs.upload_document(project_id=project.id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content'] == source.read_bytes()
        await active_case(env, project, 'receipt-case', content)

        def plan(c):
            bindings = {'id': {'source_item': 'seed', 'output_path_tokens': ['body', 'order.id']}} if with_business_instructions else {}
            target = item(c, 'receipt-case', key='target', depends_on=['seed'], input_bindings=bindings)
            refs = target['evidence_ids']
            seed_instructions = {'expected': 201, 'action': '创建本次数据'}
            cleanup_instructions = {'expected': 200, 'action': '删除本次数据'}
            if with_business_instructions:
                seed_instructions.update(method='POST', url=business_http[0]+'/orders')
                cleanup_instructions.update(method='DELETE', url=business_http[0]+'/orders/{id}')
            return finish({'key': 'seed', 'kind': 'prepare', 'reason': '准备目标所需数据',
                'evidence_ids': refs, 'instructions': seed_instructions},
                target, {'key': 'cleanup', 'kind': 'prepare', 'reason': '清理本次数据',
                'evidence_ids': refs, 'depends_on': ['target'],
                'input_bindings': bindings, 'instructions': cleanup_instructions})

        env.service._models = SequenceModel(plan)
        frozen = await env.service.create(project.id, CasePlanningRequest(objective='验证正式目标'))
        assert frozen.status == 'planned', frozen.summary
        detail = await creator.create(project.id, frozen.id, CasePlanRunRequest(creation_key='assessment', mode_key='api_testing'))
        claims = await store.claim_items(run_id=detail.run.id, worker_id='actual-tools', limit=1, lease_seconds=60, now=now)
        record, run_attempt = claims[0]
        if start_item:
            await store.start_item(record.id, run_attempt.lease_token, now)
        detail = await store.get_run(detail.run.id)
        context = build_agent_case_context(detail, record, await env.cases.get_version(record.case_version_id))
        tables = []
        if live:
            tables = ['up_ass_job_' + uuid4().hex[:12], 'up_ass_art_' + uuid4().hex[:12]]
            env.settings.database.postgres_tool_job_table, env.settings.database.postgres_tool_artifact_table = tables
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for name in tables:
                    cur.execute('SELECT to_regclass(%s) AS existing', (name,))
                    assert cur.fetchone()['existing'] is None
        jobs = ToolJobService(PostgresToolJobStore(env.settings) if live else InMemoryToolJobStore())
        if planning_policy is not None and planning_policy.action_admission_protocol is not None:
            if not live:
                raise ValueError('动作确认研究必须使用真实PG事务')
            from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission
            jobs._case_action_admission = PostgresCaseActionAdmission(runs=store, jobs=jobs._store)
            tables.append(jobs._case_action_admission.operations.table)
            tables.append(jobs._case_action_admission.consumption.table)
        await jobs.initialize()
        if live and jobs._case_action_admission is not None:
            tables.append(jobs._store.effect_evidence.table)
        runtime = ToolRuntimeService(tool_job_service=jobs)
        session, turn, attempt = str(uuid4()), str(uuid4()), run_attempt.id
        tool_context = ToolExecutionContext(session_id=session, turn_id=turn, trace_id=turn,
            user_message=context.objective, normalized_input=context.objective,
            context_bundle={**context.runtime_context(), 'attempt_id': attempt})
        tool = ToolRegistry().get('api-test-runner')
        records = []
        url, requests, actual_id = business_http
        try:
            if services is not None:
                services['environment'] = env
                services['creator'] = creator
            persisted_id = None
            for key, method in ([('seed', 'POST'), ('target', 'GET'), ('cleanup', 'DELETE')] if perform_tools else []):
                path = '/orders' if key == 'seed' else '/orders/' + persisted_id
                records.append(await runtime.execute(tool, ModelToolCall(id=key, name=tool.key,
                    arguments={'worker_action': 'execute_task', 'project_id': 'argument-cannot-change-project',
                        'task': {'task_id': key, 'method': method,
                        'path': url + path, 'full_url': url + path, 'timeout_seconds': 5}}), tool_context))
                if key == 'seed':
                    saved = await jobs.get_job_detail(records[-1].job_id)
                    persisted_id = saved.output_payload['task_result']['response_body']['order.id']
                    assert persisted_id == actual_id
            assert len(requests) == (3 if perform_tools else 0)
            assert all(r.job_id and r.status in {'completed', 'partial'} for r in records)
            yield context, jobs, records, session, turn, attempt, detail, store, now, content
        finally:
            if tables:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    for name in reversed(tables):
                        cur.execute(f'DROP TABLE "{name}"')


def reference(call_id, *path):
    return {'call_id': call_id, 'output_path_tokens': list(path)}


def receipt():
    steps = []
    for key, expected in [('seed', ['expected']), ('target', ['期望', 'code']), ('cleanup', ['expected'])]:
        steps.append({'item_key': key, 'status': 'passed', 'summary': key,
            'checks': [{'name': 'HTTP 状态', 'expected_path_tokens': expected,
                'observations': [reference(key, 'task_result', 'response_status')], 'operator': 'equals'}],
            'outputs': {'body': reference(key, 'task_result', 'response_body')}})
    return {'schema_version': 1, 'steps': steps}


async def assess(env, data=None, records=None):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    context, jobs, actual, session, turn, attempt, *_ = env
    return await AgentCaseAssessmentService(jobs).assess(context=context, receipt=data or receipt(),
        tool_records=actual if records is None else records, session_id=session, turn_id=turn,
        attempt_id=attempt, model_key='controlled-receipt', lease_token=env[6].items[0].lease_token)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_real_file_http_and_persisted_outputs_form_receipt_and_dependency_result(tmp_path, business_http, live):
    async with execution(tmp_path, business_http, live) as env:
        context, jobs, records, session, turn, attempt, detail, store, now, content = env
        saved = await jobs.get_job(records[0].job_id)
        assert saved.metadata['project_id'] == context.project_id
        assert saved.metadata['run_id'] == context.run_id
        assert saved.metadata['run_item_id'] == context.run_item_id
        assert saved.metadata['attempt_id'] == attempt
        # Compact or fabricated record output must never replace persisted evidence.
        records[1].output = {'fabricated': 'DO-NOT-USE'}
        outcome = await assess(env)
        assert outcome.completion.status == 'passed'
        actual = outcome.completion.actual
        assert actual['case_output']['body']['order.id'] == business_http[2]
        assert actual['case_output']['body']['flag'] is False
        assert actual['case_output']['body']['count'] == 0
        assert actual['preparations']['seed']['status'] == 'passed'
        assert actual['preparations']['cleanup']['status'] == 'passed'
        assert len(outcome.verification_results) == 3
        assert 'fabricated' not in outcome.tool_records[1].output
        assert {r.evidence_id for r in outcome.completion.evidence_refs if r.evidence_type == 'tool_job'} == {r.job_id for r in records}
        from src.schemas.run_management import RunItemCompletion
        result = await store.complete_item(detail.items[0].id, detail.items[0].lease_token,
            RunItemCompletion.model_validate({**outcome.completion.model_dump(exclude={'lease_token'}),
                'payload_hash': 'a' * 64}), now)
        assert result.actual == actual
        assert not await store.claim_items(run_id=detail.run.id, worker_id='again', limit=1, lease_seconds=60, now=now)


@pytest.mark.asyncio
async def test_numeric_and_negative_conditions_recomputed_and_semantic_evidence_labelled(tmp_path, business_http):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        checks = data['steps'][1]['checks']
        for key in ['flag', 'count', 'nullable']:
            checks.append({'name': key, 'expected_path_tokens': ['期望', key], 'operator': 'equals',
                'observations': [reference('target', 'task_result', 'response_body', key)]})
        checks.append({'name': '业务状态语义', 'expected_path_tokens': ['期望', 'semantic'],
            'operator': 'semantic', 'verdict': 'passed', 'reason': '响应说明订单待支付且尚未进入出库流程',
            'observations': [reference('target', 'task_result', 'response_body', 'message')]})
        result = await assess(env, data)
        verification = result.verification_results[1]
        assert verification.status.value == 'passed' and verification.assertion_count == 5
        evaluated = verification.metadata['checks']
        assert evaluated[-1]['method'] == 'model' and evaluated[-1]['model_key'] == 'controlled-receipt'
        assert evaluated[1]['actual'] is False and evaluated[2]['actual'] == 0
        assert evaluated[3]['actual'] is None
        env[-1]['期望']['code'] = 500
        env[0].steps[1].content_json['期望']['code'] = 500
        result = await assess(env, data)
        assert result.completion.status == 'failed'
        assert result.completion.actual['preparations']['seed']['status'] == 'passed'


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['missing_call', 'missing_path', 'foreign_attempt', 'foreign_turn',
    'foreign_run', 'running_job', 'missing_job', 'wrong_tool', 'missing_expected', 'missing_step', 'duplicate_step'])
async def test_fabricated_or_foreign_evidence_and_incomplete_receipts_rejected(tmp_path, business_http, corruption):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        record, jobs = env[2][1], env[1]
        job = await jobs.get_job(record.job_id)
        if corruption == 'missing_call': data['steps'][1]['checks'][0]['observations'][0]['call_id'] = 'invented'
        elif corruption == 'missing_path': data['steps'][1]['checks'][0]['observations'][0]['output_path_tokens'] = ['missing']
        elif corruption == 'missing_expected': data['steps'][1]['checks'][0]['expected_path_tokens'] = ['invented']
        elif corruption == 'missing_step': data['steps'].pop()
        elif corruption == 'duplicate_step': data['steps'].append(deepcopy(data['steps'][1]))
        elif corruption == 'missing_job': record.job_id = str(uuid4())
        else:
            if corruption == 'foreign_attempt': job.metadata['attempt_id'] = str(uuid4())
            elif corruption == 'foreign_turn': job.turn_id = str(uuid4())
            elif corruption == 'foreign_run': job.metadata['run_id'] = str(uuid4())
            elif corruption == 'running_job': job.status = ToolJobStatus.running
            elif corruption == 'wrong_tool': job.tool_key = 'other'
            await jobs._store.save_job(job)
        with pytest.raises(ValueError):
            await assess(env, data)


@pytest.mark.asyncio
async def test_empty_checks_cannot_prove_pass_and_model_actual_values_are_forbidden(tmp_path, business_http):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        data['steps'][1]['checks'] = []
        result = await assess(env, data)
        assert result.completion.status == 'blocked'
        assert result.verification_results[1].status.value == 'not_run'
        data['steps'][1]['outputs']['fake'] = {'value': 'made-up'}
        with pytest.raises(ValueError): await assess(env, data)


@pytest.mark.asyncio
async def test_injected_final_contract_only_when_execution_requests_receipt(tmp_path, business_http):
    from src.application.prompting.prompt_assembly_service import PromptAssemblyService
    from test_prompt_history_http import _state
    async with execution(tmp_path, business_http) as env:
        state = _state([])
        state['context_bundle'] = {**env[0].runtime_context(), 'case_assessment_requested': True}
        assembled = PromptAssemblyService().build_for_turn(state, [])
        section = next(s for s in assembled.system_sections if s.key == 'case_result_contract')
        assert 'expected_path_tokens' in section.content and 'output_path_tokens' in section.content
        assert 'semantic' in section.content
        state['context_bundle'].pop('case_assessment_requested')
        assert 'case_result_contract' not in {s.key for s in PromptAssemblyService().build_for_turn(state, []).system_sections}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['failed_tool', 'missing_check', 'semantic_unknown', 'cleanup_failure', 'whole_output'])
async def test_incomplete_or_failed_evidence_keeps_preparation_and_target_separate(tmp_path, business_http, case):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        target = data['steps'][1]
        if case == 'failed_tool':
            job = await env[1].get_job(env[2][1].job_id)
            job.status = ToolJobStatus.failed
            await env[1]._store.save_job(job)
        elif case == 'missing_check':
            target['checks'] = []
        elif case == 'semantic_unknown':
            target['checks'] = [{'name': '语义不确定', 'expected_path_tokens': ['期望', 'semantic'],
                'operator': 'semantic', 'verdict': 'not_run', 'reason': '证据不足以验证业务含义',
                'observations': [reference('target', 'task_result', 'response_body', 'message')]}]
        elif case == 'cleanup_failure':
            data['steps'][2]['status'] = 'error'
            data['steps'][2]['summary'] = 'cleanup-outcome-unknown'
        elif case == 'whole_output':
            target['outputs'] = {}
            target['output_source'] = reference('target', 'task_result')
        result = await assess(env, data)
        actual = result.completion.actual
        assert actual['preparations']['seed']['status'] == 'passed'
        if case == 'whole_output':
            assert result.completion.status == 'passed'
            assert actual['case_output']['response_body']['order.id'] == business_http[2]
            assert actual['case_output']['response_status'] == 200
        elif case == 'cleanup_failure':
            assert actual['case_status'] == 'passed' and result.completion.status == 'error'
            assert actual['preparations']['cleanup']['status'] == 'error'
            assert 'cleanup-outcome-unknown' in result.completion.error_message
        else:
            assert result.completion.status == 'blocked' and actual['case_status'] == 'blocked'


@pytest.mark.asyncio
async def test_graph_approval_job_keeps_execution_identity_before_tool_is_permitted():
    from src.application.permissions.permission_service import PermissionService
    from src.core.safety_gate import SafetyVerdict
    from src.graph.nodes.tool_executor import _resolve_tool_call
    from test_tool_executor_safety_gate import _state, _FixedGate, _RecordingRuntime
    identities = {key: str(uuid4()) for key in ['project_id', 'run_id', 'run_item_id', 'attempt_id']}
    state = _state('mock-target-runner', {'action': 'start'}, mode_key='performance_testing')
    state['context_bundle'] = identities
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = _RecordingRuntime()
    execution_context = ToolExecutionContext(session_id=state['session_id'], turn_id=state['turn_id'],
        trace_id=state['trace_id'], user_message=state['user_message'], normalized_input=state['normalized_input'],
        context_bundle=state['context_bundle'])
    result = await _resolve_tool_call(state=state, tool_call=ModelToolCall.model_validate(state['model_tool_calls'][0]),
        tool_registry=ToolRegistry(), permission_service=PermissionService(), tool_runtime_service=runtime,
        tool_job_service=jobs, tool_context=execution_context,
        safety_gate=_FixedGate(SafetyVerdict.require_approval('controlled admission test')))
    job = await jobs.get_job(result['tool_result']['job_id'])
    assert job.status == ToolJobStatus.waiting_approval
    assert all(job.metadata[key] == value for key, value in identities.items())
    assert not runtime.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('approval', [False, True])
async def test_governed_job_and_approval_keep_execution_identity(approval):
    from dataclasses import replace
    from test_deep_agent_tool_replay import _context, _service
    from src.application.permissions.permission_service import PermissionService

    async def handler(arguments, context):
        return {'status': 'completed', 'summary': 'scope-test', 'value': False}

    service, jobs = _service(handler)
    context = _context()
    identities = {key: str(uuid4()) for key in ['project_id', 'run_id', 'run_item_id', 'attempt_id']}
    context.execution.context_bundle.update(identities)
    if approval:
        tool = ToolRegistry().get('knowledge-rag')
        permissions = PermissionService()
        decision = permissions.get_tool_decision(permissions.evaluate(policy_context=context.policy, tools=[tool]), tool.key)
        context.permission_decisions[tool.key] = replace(decision, behavior='ask')
    first = await service.execute(ModelToolCall(id='scope-call', name='knowledge-rag', arguments={'query': 'scope'}),
        context, prepare_approval=approval)
    job = await jobs.get_job(first.record.job_id)
    assert all(job.metadata[key] == value for key, value in identities.items())
    assert job.status == (ToolJobStatus.waiting_approval if approval else ToolJobStatus.completed)


@pytest.mark.asyncio
async def test_present_false_is_not_satisfied_by_actual_numeric_zero(tmp_path, business_http):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        data['steps'][1]['checks'].append({'name': '禁止数字冒充布尔条件',
            'expected_path_tokens': ['期望', 'flag'], 'operator': 'equals',
            'observations': [reference('target', 'task_result', 'response_body', 'count')]})
        outcome = await assess(env, data)
        assert outcome.completion.status == 'failed'
        check = outcome.verification_results[1].metadata['checks'][-1]
        assert check['expected'] is False and check['actual'] == 0 and check['status'] == 'failed'


@pytest.mark.asyncio
async def test_rejected_real_model_source_reference_is_visible_to_read_only_correction(tmp_path, business_http):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    sample = json.loads(Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model-source-error-references.json').read_text(encoding='utf-8'))
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        bad = sample['steps'][0]['checks'][0]
        data['steps'][0]['checks'][0] = {'name': '实际模型错误来源样本', **bad}
        with pytest.raises(ValueError, match='expected_path_tokens') as failure:
            await assess(env, data)
        context, jobs, records, session, turn, attempt, *_ = env
        service = AgentCaseAssessmentService(jobs)
        request = await service.build_assessment_request(context=context, tool_records=records,
            session_id=session, turn_id=turn, attempt_id=attempt,
            validation_feedback={'error': str(failure.value), 'candidate_response': data})
        assert not request.tools
        assert 'validation_feedback' in request.messages[0]['content']
        assert 'execution_items' in request.messages[0]['content']
        assert 'expected_source' in request.messages[0]['content']
        assert request.trace_context['phase'] == 'case_assessment'
        assert len(business_http[1]) == 3


@pytest.mark.parametrize('actual,expected,operator,result', [
    (False, 0, 'equals', False), (True, 1, 'not_equals', True),
    ({'flag': [False]}, {'flag': [0]}, 'equals', False),
    ([{'flag': False}], {'flag': 0}, 'contains', False),
    ([False], 0, 'not_contains', True), (0, 0.0, 'equals', True),
    ({'nullable': None}, {'nullable': None}, 'equals', True),
])
def test_existing_verifier_compares_json_values_without_boolean_numeric_aliases(actual, expected, operator, result):
    from src.application.testing.verification_service import VerificationService
    assert VerificationService._matches_assertion(actual, expected, operator) is result


@pytest.mark.asyncio
async def test_read_only_assessment_can_request_one_complete_owned_item(tmp_path, business_http):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    async with execution(tmp_path, business_http) as env:
        context, jobs, records, session, turn, attempt, *_ = env
        service = AgentCaseAssessmentService(jobs)
        request = await service.build_assessment_request(context=context, tool_records=records,
            session_id=session, turn_id=turn, attempt_id=attempt, requested_item_keys=['target'])
        assert not request.tools
        content = request.messages[0]['content']
        assert '"requested_item_keys": ["target"]' in content
        assert content.count('"expected_source"') == 1
        assert content.count('"期望":') == 1
        assert '"phase": "before"' not in content
        assert '"phase": "after"' not in content
        assert len(business_http[1]) == 3
        with pytest.raises(ValueError, match='请求的执行项'):
            await service.build_assessment_request(context=context, tool_records=records,
                session_id=session, turn_id=turn, attempt_id=attempt, requested_item_keys=['foreign'])


@pytest.mark.parametrize('summary,text,error', [
    ({'mode': 'ok', 'finish_reason': 'length'}, json.dumps(receipt()), '模型输出未完整结束'),
    ({'mode': 'ok', 'stop_reason': 'max_tokens'}, json.dumps(receipt()), '模型输出未完整结束'),
    ({'mode': 'http_error'}, json.dumps(receipt()), '模型调用未成功'),
    ({'mode': 'ok', 'finish_reason': 'stop'}, '{"steps": [{"summary": "unfinished', 'JSON'),
], ids=['length', 'max_tokens', 'http_error', 'invalid_json'])
def test_response_parser_reports_provider_or_json_failure_without_repair(summary, text, error):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.schemas.model_config import ModelInvocationResult
    response = ModelInvocationResult(text=text, response_summary=summary)
    with pytest.raises(ValueError, match=error):
        AgentCaseAssessmentService.parse_response(response)


def test_response_parser_preserves_complete_json_and_rejects_assessment_tool_call():
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.schemas.model_config import ModelInvocationResult
    response = ModelInvocationResult(text='```json\n' + json.dumps(receipt()) + '\n```',
        response_summary={'mode': 'ok', 'finish_reason': 'stop'})
    assert AgentCaseAssessmentService.parse_response(response).model_dump(mode='json')['steps'][1]['item_key'] == 'target'
    response.tool_calls = [ModelToolCall(id='unexpected', name='api-test-runner', arguments={})]
    with pytest.raises(ValueError, match='只读判定'):
        AgentCaseAssessmentService.parse_response(response)


def test_actual_captured_length_response_never_becomes_a_passing_receipt():
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.schemas.model_config import ModelInvocationResult
    samples = json.loads(Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model-length-sample.json').read_text(encoding='utf-8'))
    sample = next(entry for entry in samples if entry['item_key'] == 'target' and entry['finish_reason'] == 'length')
    assert sample['received_text_chars'] == 0
    assert sample['usage']['completion_tokens'] == sample['max_output_tokens'] == 8192
    response = ModelInvocationResult(text='', response_summary={key: sample[key]
        for key in ['mode', 'finish_reason', 'stop_reason', 'usage']})
    with pytest.raises(ValueError, match='模型输出未完整结束: length'):
        AgentCaseAssessmentService.parse_response(response)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_AGENT_CASE_ASSESSMENT_MODEL') != '1',
    reason='requires current actual MySQL model config and isolated live PostgreSQL/RustFS')
async def test_actual_model_returns_receipt_using_full_case_and_persisted_real_http_evidence(tmp_path, business_http):
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.models import ModelRegistry

    async with execution(tmp_path, business_http, True) as env:
        context, jobs, records, session, turn, attempt, *_ = env
        assessor = AgentCaseAssessmentService(jobs)
        # Read existing MySQL config without reinitializing or writing its domain.
        from src.core.config import get_settings
        settings = get_settings().model_copy(deep=True)
        runtime = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(settings)), settings=settings)
        model = runtime.get_default_model_config()
        assert model is not None
        start = perf_counter()
        rounds = []
        invocations = []
        validation_feedback = None
        for round_index in range(2):
            candidate = {'schema_version': 1, 'steps': []}
            for step in context.steps:
                request = await assessor.build_assessment_request(context=context, tool_records=records,
                    session_id=session, turn_id=turn, attempt_id=attempt,
                    requested_item_keys=[step.item.key], validation_feedback=validation_feedback)
                observed = {'received_text_chars': 0}

                async def observe_delta(delta):
                    observed['received_text_chars'] += len(delta)
                    if 'first_text_seconds' not in observed:
                        observed['first_text_seconds'] = round(perf_counter() - start, 3)
                        print(json.dumps({'assessment_round': round_index + 1, 'item_key': step.item.key,
                            'first_text_seconds': observed['first_text_seconds']}, ensure_ascii=False), flush=True)

                if model.capabilities.streaming:
                    async with runtime.stream_handler(observe_delta):
                        response = await runtime.invoke(model.key, request)
                else:
                    response = await runtime.invoke(model.key, request)
                diagnostics = {'item_key': step.item.key, 'round': round_index + 1,
                    'max_output_tokens': model.max_output_tokens, **observed,
                    **{key: response.response_summary.get(key) for key in
                       ['mode', 'status_code', 'error_type', 'finish_reason', 'stop_reason', 'usage']}}
                invocations.append(diagnostics)
                Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model-diagnostics.json').write_text(
                    json.dumps(invocations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                parsed = assessor.parse_response(response)
                assert [entry.item_key for entry in parsed.steps] == [step.item.key]
                candidate['steps'].extend(parsed.model_dump(mode='json')['steps'])
            safe_refs = {'steps': [{'item_key': entry.get('item_key'), 'status': entry.get('status'),
                'checks': [{'expected_path_tokens': check.get('expected_path_tokens'),
                    'observations': check.get('observations'), 'operator': check.get('operator')}
                    for check in entry.get('checks', [])], 'outputs': entry.get('outputs'),
                    'output_source': entry.get('output_source')} for entry in candidate.get('steps', [])]}
            try:
                outcome = await assessor.assess(context=context, receipt=candidate, tool_records=records,
                    session_id=session, turn_id=turn, attempt_id=attempt, model_key=model.key,
                    lease_token=env[6].items[0].lease_token)
            except ValueError as exc:
                rounds.append({'status': 'validation_rejected', 'references': safe_refs})
                Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model-references.json').write_text(
                    json.dumps(rounds, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                if round_index == 1:
                    raise
                validation_feedback = {'error': str(exc), 'candidate_response': candidate}
                continue
            rounds.append({'status': 'assessed', 'references': safe_refs})
            Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model-references.json').write_text(
                json.dumps(rounds, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            break
        assert outcome.completion.status == 'passed'
        target = next(v for v in outcome.verification_results if v.metadata['phase'] == 'case')
        assert {tuple(check['expected_path_tokens']) for check in target.metadata['checks']} >= {
            ('期望', key) for key in ['code', 'flag', 'count', 'nullable', 'semantic']}
        output = outcome.completion.actual['case_output']
        assert any(isinstance(value, dict) and value.get('order.id') == business_http[2] for value in output.values()) or output.get('order.id') == business_http[2]
        report = {'model_key': model.key, 'elapsed_seconds': round(perf_counter() - start, 3),
            'real_business_http_requests': len(business_http[1]), 'tool_outputs_from_postgres': True,
            'original_uploaded_readback': True, 'receipt_status': outcome.completion.status,
            'target_check_count': target.assertion_count, 'preparation_count': 2,
            'assessment_rounds': len(rounds),
            'streaming': model.capabilities.streaming,
            'received_text_chars': sum(entry['received_text_chars'] for entry in invocations),
            'read_only_invocations': len(invocations),
            'model_did_execute_business_tools': False, 'full_production_entry_verified': False}
        Path('../docs/upgrade_2026-10-03/p2-agent-assessment-model.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
