"""Compose real original-tool values and consume them through the Agent graph."""
from copy import deepcopy
from html import unescape
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from test_agent_case_execution import adapter_environment, regression_business_http
from test_agent_case_session import ControlledAgent
from test_agent_case_assessment import execution, business_http, receipt, assess, reference
from test_case_shared_effect_research import SharedPlanningModel
from test_case_operations import POLICY
from test_case_planning import active_case, finish, item
from src.api.routes.run_management import router
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.case_execution import CaseStepReceipt
from src.schemas.model_config import ModelInvocationResult
from src.schemas.run_management import RunClaimRequest
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.tool_job import ToolJobStatus


def ref(call, *path):
    return {'kind': 'reference', 'source': {'call_id': call, 'output_path_tokens': list(path)}}


def composed():
    return {'kind': 'object', 'fields': {
        'ids': {'kind': 'array', 'items': [ref('create-first', 'task_result', 'response_body', 'order.id'),
            ref('create-second', 'task_result', 'response_body', 'order.id')]},
        'flags': {'kind': 'object', 'fields': {
            'literal.key': ref('create-first', 'task_result', 'response_body', 'flag'),
            'zero': ref('create-second', 'task_result', 'response_body', 'count'),
            'nullable': ref('create-first', 'task_result', 'response_body', 'nullable')}},
        '': {'kind': 'array', 'items': []}}}


def test_recursive_output_schema_preserves_old_receipt_and_rejects_mixed_styles():
    old = CaseStepReceipt(item_key='seed', status='passed', summary='原字段',
        outputs={'body': {'call_id': 'create-first', 'output_path_tokens': ['task_result', 'response_body']}})
    original = old.model_dump(mode='json')
    assert 'output_structure' not in original
    assert CaseStepReceipt.model_validate(original).model_dump(mode='json') == original
    tree = CaseStepReceipt(item_key='seed', status='passed', summary='组合', output_structure=composed())
    assert tree.model_dump(mode='json')['output_structure'] == composed()
    with pytest.raises(ValueError, match='输出'):
        CaseStepReceipt.model_validate({**original, 'output_structure': composed()})
    with pytest.raises(ValueError):
        CaseStepReceipt(item_key='seed', status='passed', summary='不能填写实际ID',
            output_structure={'kind': 'array', 'items': ['fabricated-id']})
    assert 'CaseOutputArray' in json.dumps(CaseStepReceipt.model_json_schema())


@pytest.mark.parametrize('mode', ['mapping', 'whole', 'none'])
def test_old_receipt_canonical_bytes_unchanged(mode):
    original = {'item_key': 'seed', 'status': 'passed', 'summary': '原字段', 'checks': [],
        'outputs': {'body': reference('seed', 'task_result', 'response_body')} if mode == 'mapping' else {},
        'output_source': reference('seed', 'task_result') if mode == 'whole' else None}
    canonical = json.dumps(original, ensure_ascii=False, separators=(',', ':'))
    assert CaseStepReceipt.model_validate(original).model_dump_json() == canonical
    assert CaseStepReceipt.model_validate({**original, 'output_structure': None}).model_dump_json() == canonical


@pytest.mark.parametrize('other', ['outputs', 'output_source'])
def test_output_structure_conflicts_with_each_old_shape(other):
    original = {'item_key': 'seed', 'status': 'passed', 'summary': '混用', 'output_structure': composed()}
    original[other] = {'id': reference('seed')} if other == 'outputs' else reference('seed')
    with pytest.raises(ValueError, match='输出'):
        CaseStepReceipt.model_validate(original)


@pytest.mark.asyncio
@pytest.mark.parametrize('root', ['object', 'array', 'reference'])
async def test_nested_output_uses_original_jobs_and_preserves_null_false_zero(tmp_path, business_http, root):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        fields = {'': ref('seed', 'task_result', 'response_body', 'flag'),
            'literal.key': {'kind': 'array', 'items': [ref('target', 'task_result', 'response_body', 'count'),
                ref('cleanup', 'task_result', 'response_body', 'nullable'),
                {'kind': 'object', 'fields': {}}, {'kind': 'array', 'items': []}]}}
        node = {'kind': 'object', 'fields': fields}
        expected = {'': False, 'literal.key': [0, None, {}, []]}
        if root == 'array':
            node, expected = {'kind': 'array', 'items': [node]}, [expected]
        elif root == 'reference':
            node, expected = ref('target', 'task_result', 'response_body', 'nullable'), None
        data['steps'][1]['outputs'] = {}
        data['steps'][1]['output_structure'] = node
        for record in env[2]:
            record.output = {'fake': 'not-the-original'}
        result = await assess(env, data)
        assert result.completion.status == 'passed'
        assert result.completion.actual['case_output'] == expected
        assert result.completion.actual['receipt']['steps'][1]['output_structure'] == node
        assert len(business_http[1]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['missing_call', 'missing_path', 'foreign_attempt', 'running_job'])
async def test_output_leaves_reject_unreliable_or_foreign_originals(tmp_path, business_http, corruption):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        node = ref('target', 'task_result', 'response_body', 'order.id')
        data['steps'][1]['outputs'] = {}
        data['steps'][1]['output_structure'] = {'kind': 'array', 'items': [node]}
        if corruption == 'missing_call':
            node['source']['call_id'] = 'invented'
        elif corruption == 'missing_path':
            node['source']['output_path_tokens'].append('invented')
        else:
            job = await env[1].get_job(env[2][1].job_id)
            if corruption == 'foreign_attempt':
                job.metadata['attempt_id'] = 'another-attempt'
            else:
                job.status = ToolJobStatus.running
            await env[1]._store.save_job(job)
        with pytest.raises(ValueError):
            await assess(env, data)


@pytest.mark.asyncio
async def test_failed_output_source_cannot_prove_pass_even_when_checks_pass(tmp_path, business_http):
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        target = data['steps'][1]
        target['checks'] = [{'name': '原可靠false', 'operator': 'equals', 'expected_path_tokens': ['期望', 'flag'],
            'observations': [reference('seed', 'task_result', 'response_body', 'flag')]}]
        target['outputs'] = {}
        target['output_structure'] = ref('target', 'task_result', 'response_body', 'order.id')
        job = await env[1].get_job(env[2][1].job_id)
        job.status = ToolJobStatus.failed
        await env[1]._store.save_job(job)
        result = await assess(env, data)
        assert result.completion.status == 'blocked'
        assert result.completion.actual['case_status'] == 'blocked'
        assert result.verification_results[1].passed_count == 1
        assert result.verification_results[1].status.value == 'partial'


class MultipleOutputAgent(ControlledAgent):
    async def invoke(self, key, request):
        self.requests.append(request.model_copy(deep=True))
        section = next(s for s in request.system_prompt_sections if s.key == 'frozen_case_execution')
        frozen = json.loads(unescape(section.content.split('\n', 1)[1].split('\n</untrusted_content>', 1)[0]))
        assert 'LAST-MULTI-OUTPUT-STEP' in request.system_prompt
        assert 'DO-NOT-INJECT-TEMPLATE' not in request.system_prompt
        previous = [json.loads(m['content']) for m in request.messages if m.get('role') == 'tool']
        steps = {s['item']['key']: s for s in frozen['steps']}
        left = frozen['target_key'] == 'left'
        operations = [('seed', 'create-first', 'POST'), ('seed', 'create-second', 'POST'), ('left', 'query-left', 'GET')] if left else [
            ('right', 'query-right', 'GET'), ('cleanup', 'delete-first', 'DELETE'), ('cleanup', 'delete-second', 'DELETE')]
        if len(previous) < len(operations):
            item_key, operation, method = operations[len(previous)]
            step = steps[item_key]
            source = step['content_json']['steps'][0] if step['phase'] == 'case' else step['item']['instructions']
            url = source['url']
            if method != 'POST':
                if left:
                    actual_id = previous[0]['output']['task_result']['response_body']['order.id']
                else:
                    name = 'id' if method == 'GET' else 'first' if operation == 'delete-first' else 'second'
                    bound = step['inputs'][name]
                    assert bound['resolved']
                    actual_id = bound['value']
                url = url.replace('{id}', actual_id)
            return ModelInvocationResult(text='使用原工具返回与固定输入', tool_calls=[ModelToolCall(id=operation,
                name='api-test-runner', arguments={'_case_action': {'item_key': item_key, 'operation_key': operation},
                'worker_action': 'execute_task', 'task': {'task_id': operation, 'method': method,
                    'path': url, 'full_url': url, 'timeout_seconds': 5}})],
                response_summary={'mode': 'ok', 'finish_reason': 'tool_calls'})
        entries = []
        for item_key, step in steps.items():
            calls = [call for name, call, method in operations if name == item_key]
            expected = ['期望', 'code'] if step['phase'] == 'case' else ['expected']
            entry = {'item_key': item_key, 'status': 'passed', 'summary': item_key,
                'checks': [{'name': call, 'operator': 'equals', 'expected_path_tokens': expected,
                    'observations': [{'call_id': call, 'output_path_tokens': ['task_result', 'response_status']}]}
                    for call in calls]}
            if item_key == 'seed':
                entry['output_structure'] = composed()
            else:
                entry['outputs'] = {'body': {'call_id': calls[-1], 'output_path_tokens': ['task_result', 'response_body']}}
            entries.append(entry)
        return ModelInvocationResult(text=json.dumps({'schema_version': 1, 'steps': entries}, ensure_ascii=False),
            response_summary={'mode': 'ok', 'finish_reason': 'stop'})


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_PLAN_RUN_CREATION') != '1', reason='actual isolated MySQL/PG/RustFS and original Agent graph')
async def test_two_actual_operations_form_array_and_shared_consumer_uses_fixed_output(tmp_path, regression_business_http):
    business = regression_business_http
    agent = MultipleOutputAgent()
    policy = POLICY.model_copy(update={'preparation_scope_protocol': 'preparation-scope-v1'})
    async with adapter_environment(tmp_path, business[0], live=True, agent=agent, start_item=False, planning_policy=policy) as values:
        env, _, reader, _, runs, _, adapter, _, _, services = values
        base = services['environment']
        project = await base.projects.get(env[0].project_id)
        files, documents, cases = [], [], []
        for key in ['multi-left', 'multi-right']:
            content = {'objective': key, '期望': {'code': 200},
                'steps': [{'method': 'GET', 'url': business[0][0]+'/orders/{id}'}] +
                    [{'order': n, 'action': '完整业务步骤'} for n in range(2, 8)] +
                    [{'order': 8, 'action': 'LAST-MULTI-OUTPUT-STEP', 'fields': [False, 0, None, {'': 'literal.key'}]}],
                'data_lifecycle': {'prepare': {'method': 'POST', 'url': business[0][0]+'/orders', 'expected': 201,
                    'count': 2, 'output_notes': '两次实际返回ID按创建顺序保存为ids数组'},
                    'cleanup': {'method': 'DELETE', 'url': business[0][0]+'/orders/{id}', 'expected': 200,
                    'when': '两个消费者读取完成后分别删除两个真实ID'}}}
            path = tmp_path/(key+'.json')
            path.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            document = await base.docs.upload_document(project_id=project.id, filename=path.name,
                content_base64=base64.b64encode(path.read_bytes()).decode())
            assert (await base.objects.read_object_uri(document.original_asset.storage_uri))['content'] == path.read_bytes()
            cases.append(await active_case(base, project, key, content))
            files.append(path)
            documents.append(document.model_dump(mode='json'))

        def decision(context):
            left = item(context, 'multi-left', key='left', depends_on=['seed'],
                input_bindings={'id': {'source_item': 'seed', 'output_path_tokens': ['ids', '0']}})
            right = item(context, 'multi-right', key='right', depends_on=['seed'], input_bindings={
                'id': {'source_item': 'seed', 'output_path_tokens': ['ids', '1']},
                'disabled': {'source_item': 'seed', 'output_path_tokens': ['flags', 'literal.key']},
                'zero': {'source_item': 'seed', 'output_path_tokens': ['flags', 'zero']}})
            refs = left['evidence_ids']+right['evidence_ids']
            return finish({'key': 'seed', 'kind': 'prepare', 'reason': '当前项目两个目标需要两份实际数据',
                'evidence_ids': refs, 'instructions': {'method': 'POST', 'url': business[0][0]+'/orders', 'expected': 201,
                    'output_notes': '保存两次实际返回ID为ids数组，不凭空填写'}}, left, right,
                {'key': 'cleanup', 'kind': 'prepare', 'reason': '两个消费者之后清理两份实际数据',
                'evidence_ids': refs, 'cleanup_sources': ['seed'], 'depends_on': ['left'],
                'input_bindings': {'first': {'source_item': 'seed', 'output_path_tokens': ['ids', '0']},
                    'second': {'source_item': 'seed', 'output_path_tokens': ['ids', '1']}},
                'instructions': {'method': 'DELETE', 'url': business[0][0]+'/orders/{id}', 'expected': 200}})

        base.service._models = SharedPlanningModel(decision)
        frozen = await base.service.create(project.id, CasePlanningRequest(objective='实际多对象组合与固定消费',
            candidate_case_ids=[bundle.case.id for bundle in cases]))
        assert frozen.status == 'planned', frozen.summary
        detail = await services['creator'].create(project.id, frozen.id, CasePlanRunRequest(creation_key='multi-output', mode_key='api_testing'))
        executor = ExecutionService(run_service=runs, test_case_service=base.cases, adapter=None,
            session_store=reader, tool_job_service=env[1])
        executor.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service, app.state.test_run_service = executor, runs
        app.include_router(router, prefix='/api/v1')
        responses = []

        async def save(name):
            folder = Path(os.environ['QA_CASE_OUTPUT_STRUCTURE_DIR'])/name
            folder.mkdir(parents=True, exist_ok=False)
            for path in files:(folder/path.name).write_bytes(path.read_bytes())
            jobs = [j for j in await env[1].list_jobs() if j.metadata.get('run_id') == detail.run.id]
            payload = {'controlled_model': True, 'autonomous_external_model': False,
                'original_graph_and_API': True, 'actual_MySQL_policy': (await base.service.get_policy(project.id)).model_dump(mode='json'),
                'documents': documents, 'frozen_plan': frozen.model_dump(mode='json'),
                'run': (await runs.get(detail.run.id)).model_dump(mode='json'), 'API_responses': responses,
                'jobs': [j.model_dump(mode='json') for j in jobs],
                'actual_model_requests': [r.model_dump(mode='json') for r in agent.requests],
                'actual_HTTP': business[0][1], 'created_ids': business[2], 'remaining_orders': business[1]}
            (folder/'verification.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://graph') as client:
            async def execute(claim):
                path = '/api/v1/run-items/'+claim.item.id
                response = await client.post(path+'/execute', json={'lease_token': claim.lease_token})
                for _ in range(8):
                    responses.append({'status_code': response.status_code, 'body': response.json()})
                    data = response.json()
                    if response.status_code != 200 or data.get('status') != 'waiting_approval':return data
                    response = await client.post(path+'/approval', json={'approval_id': data['approval_id'], 'decision': 'approved'})
                raise AssertionError('Unexpected approval loop')

            claims = await runs.claim(detail.run.id, RunClaimRequest(worker_id='left', limit=10, lease_seconds=60))
            assert [c.item.plan_item_key for c in claims.claims] == ['left']
            first = await execute(claims.claims[0])
            await save('left-result')
            assert first.get('status') == 'passed', first
            assert first['actual']['preparations']['seed']['output']['ids'] == business[2]
            assert len(business[1]) == len(business[2]) == 2
            claims = await runs.claim(detail.run.id, RunClaimRequest(worker_id='right', limit=10, lease_seconds=60))
            assert [c.item.plan_item_key for c in claims.claims] == ['right']
            second = await execute(claims.claims[0])
            await save('right-result')
            assert second.get('status') == 'passed', second
            right_contexts = [json.loads(unescape(s.content.split('\n', 1)[1].split('\n</untrusted_content>', 1)[0]))
                for request in agent.requests for s in request.system_prompt_sections if s.key == 'frozen_case_execution']
            right_step = next(s for context in right_contexts if context['target_key'] == 'right'
                for s in context['steps'] if s['item']['key'] == 'right')
            assert right_step['inputs']['id']['value'] == business[2][1]
            assert right_step['inputs']['disabled']['value'] is False
            assert right_step['inputs']['zero']['value'] == 0
            assert business[0][1] == [('POST','/orders'),('POST','/orders'),('GET','/orders/'+business[2][0]),
                ('GET','/orders/'+business[2][1]),('DELETE','/orders/'+business[2][0]),('DELETE','/orders/'+business[2][1])]
            assert not business[1]
            again = await runs.claim(detail.run.id, RunClaimRequest(worker_id='terminal', limit=10))
            assert not again.claims and len(business[0][1]) == 6
            await save('terminal')
