"""Assess a fixed consumer while original cleanup is still awaiting approval."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
from html import unescape
from pathlib import Path
from threading import Thread
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_agent_case_execution import adapter_environment, regression_business_http
from test_agent_case_session import ControlledAgent
from test_agent_case_assessment import business_http, execution, receipt
from test_case_shared_effect_research import SharedPlanningModel
from test_case_operations import POLICY
from test_case_planning import active_case, item, finish
from src.api.routes.run_management import router
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_execution import AgentCaseExecutionCheckpoint
from src.application.runtime.case_action_binding import _digest
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.model_config import ModelConfigRecord
from src.schemas.run_management import RunClaimRequest
from src.schemas.tool_runtime import ToolExecutionRecord


class BoundAgent(ControlledAgent):
    async def invoke(self, key, request):
        result = await super().invoke(key, request)
        for call in result.tool_calls:
            call.arguments['_case_action'] = {'item_key': call.id, 'operation_key': call.id+'.request'}
        return result


@pytest.fixture
def receipt_http():
    calls, content = [], {'text': ''}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            data = {'id': 'receipt-'+str(len(calls)), 'object': 'chat.completion', 'created': 1,
                'model': 'local-model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content['text']},
                    'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
            raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls, content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_PLAN_RUN_CREATION') != '1', reason='actual MySQL/PG/RustFS and original graph')
@pytest.mark.parametrize('langchain_enabled,wrong_expected', [(False, False), (True, False), (False, True), (True, True)])
async def test_original_graph_partial_receipt_before_cleanup_preserves_running_identity(
    tmp_path, regression_business_http, receipt_http, langchain_enabled, wrong_expected, monkeypatch,
):
    business = regression_business_http
    agent = BoundAgent()
    async with adapter_environment(tmp_path, business[0], live=True, agent=agent, start_item=False,
        planning_policy=POLICY) as values:
        env, _, reader, _, runs, _, adapter, _, _, services = values
        base = services['environment']
        project = await base.projects.get(env[0].project_id)
        content = {'objective': '消费查询', '期望': {'code': 201 if wrong_expected else 200, 'flag': False, 'count': 1,
            'nullable': None, 'semantic': '订单仍待支付且未出库'},
            'steps': [{'method': 'GET', 'url': business[0][0]+'/orders/{id}'}] +
                [{'action': '完整业务验证步骤', 'order': i} for i in range(2, 8)] +
                [{'action': 'LAST-CONSUMER-STEP', 'order': 8, 'values': [False, 0, None, {'': 'literal.key'}]}]}
        original = tmp_path/'consumer-case.json'
        original.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
        document = await base.docs.upload_document(project_id=project.id, filename=original.name,
            content_base64=base64.b64encode(original.read_bytes()).decode())
        assert (await base.objects.read_object_uri(document.original_asset.storage_uri))['content'] == original.read_bytes()
        bundle = await active_case(base, project, 'partial-consumer', content)

        def decision(context):
            target = item(context, 'partial-consumer', key='target', depends_on=['seed'])
            refs = target['evidence_ids']
            return finish({'key': 'seed', 'kind': 'prepare', 'reason': '提供本次查询数据', 'evidence_ids': refs,
                'instructions': {'method': 'POST', 'url': business[0][0]+'/orders', 'expected': 201}}, target,
                {'key': 'cleanup', 'kind': 'prepare', 'reason': '查询完成后删除本次数据', 'evidence_ids': refs,
                'depends_on': ['target'], 'instructions': {'method': 'DELETE', 'url': business[0][0]+'/orders/{id}', 'expected': 200}})

        base.service._models = SharedPlanningModel(decision)
        plan = await base.service.create(project.id, CasePlanningRequest(objective='逐项核验与清理终态分别保存',
            candidate_case_ids=[bundle.case.id]))
        assert plan.status == 'planned', plan.summary
        detail = await services['creator'].create(project.id, plan.id, CasePlanRunRequest(creation_key='partial-consumer', mode_key='api_testing'))
        executor = ExecutionService(run_service=runs, test_case_service=base.cases, adapter=None,
            session_store=reader, tool_job_service=env[1])
        executor.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service, app.state.test_run_service = executor, runs
        app.include_router(router, prefix='/api/v1')
        responses = []
        folder = Path(os.environ['QA_CONSUMER_RECEIPT_DIR'])/f'graph-{langchain_enabled}-{wrong_expected}'
        folder.mkdir(parents=True, exist_ok=False)
        (folder/original.name).write_bytes(original.read_bytes())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://partial') as client:
            claim = (await runs.claim(detail.run.id, RunClaimRequest(worker_id='consumer', limit=1, lease_seconds=120))).claims[0]
            path = '/api/v1/run-items/'+claim.item.id
            response = await client.post(path+'/execute', json={'lease_token': claim.lease_token})
            for _ in range(2):
                assert response.status_code == 200 and response.json()['status'] == 'waiting_approval', response.text
                responses.append(response.json())
                response = await client.post(path+'/approval', json={'approval_id': response.json()['approval_id'], 'decision': 'approved'})
            assert response.status_code == 200 and response.json()['status'] == 'waiting_approval', response.text
            pending = response.json()
            job = await env[1].get_job(pending['tool_job_id'])
            assert job.call_id == 'cleanup' and job.started_at is None
            assert len(business[1]) == 1 and [m for m, _ in business[0][1]] == ['POST', 'GET']
            before = await runs.get(detail.run.id)
            checkpoint = AgentCaseExecutionCheckpoint.model_validate(before.attempts[-1].checkpoint_payload)
            current = await runs.get_item(claim.item.id)
            context = build_agent_case_context(before, current, await base.cases.get_version(current.case_version_id))
            assert _digest(context.execution_json()) == checkpoint.context_hash
            snapshot = await adapter()._snapshot(checkpoint)
            records = [ToolExecutionRecord.model_validate(r) for r in snapshot.graph_state['tool_results']]
            assert len(records) == 3 and records[-1].status == 'waiting_approval'
            (folder/'pending.json').write_text(json.dumps({'document': document.model_dump(mode='json'),
                'run': before.model_dump(mode='json'), 'full_context': context.execution_json(),
                'context_sha256': _digest(context.execution_json()), 'snapshot': snapshot.model_dump(mode='json'),
                'pending_cleanup': job.model_dump(mode='json'), 'business_HTTP': business[0][1],
                'remaining_orders': business[1]}, ensure_ascii=False, indent=2), encoding='utf-8')
            service = AgentCaseAssessmentService(env[1])
            request = await service.build_assessment_request(context=context, tool_records=records,
                session_id=checkpoint.session_id, turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id,
                requested_item_keys=['target'])
            data = receipt()
            data['steps'] = [data['steps'][1]]
            for field in ['flag', 'count', 'nullable']:
                data['steps'][0]['checks'].append({'name': field, 'operator': 'equals', 'expected_path_tokens': ['期望', field],
                    'observations': [{'call_id': 'target', 'output_path_tokens': ['task_result', 'response_body', field]}]})
            data['steps'][0]['checks'].append({'name': '语义状态', 'expected_path_tokens': ['期望', 'semantic'],
                'operator': 'semantic', 'verdict': 'passed', 'reason': '受控判定：原响应表明订单待支付且未出库',
                'observations': [{'call_id': 'target', 'output_path_tokens': ['task_result', 'response_body', 'message']}]})
            receipt_http[2]['text'] = json.dumps(data, ensure_ascii=False)
            monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
            monkeypatch.setenv('LANGSMITH_TRACING', 'false')
            config = ModelConfigRecord(key='local-model', name='Local', provider='openai', transport='openai_chat_completions',
                model_id='local-model', api_base_url=receipt_http[0], api_key=uuid4().hex)
            runtime = ModelRuntimeService(model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
                settings=SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=10, langchain_model_adapter_enabled=langchain_enabled)))
            model_response = await runtime.invoke('local-model', request)
            parsed = service.parse_response(model_response)
            proof = {'controlled_model': True, 'autonomous_external_model': False, 'langchain_enabled': langchain_enabled,
                'wrong_expected': wrong_expected, 'document': document.model_dump(mode='json'), 'plan': plan.model_dump(mode='json'),
                'before_run': before.model_dump(mode='json'), 'snapshot': snapshot.model_dump(mode='json'),
                'full_context': context.execution_json(), 'context_sha256': _digest(context.execution_json()),
                'original_jobs': [(await env[1].get_job_detail(r.job_id)).model_dump(mode='json') for r in records],
                'pending_cleanup': job.model_dump(mode='json'), 'assessment_request': request.model_dump(mode='json'),
                'physical_model_requests': receipt_http[1], 'model_response': model_response.model_dump(mode='json'),
                'parsed_receipt': parsed.model_dump(mode='json'), 'business_HTTP_before': deepcopy(business[0][1])}
            (folder/'before.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
            with pytest.raises(ValueError, match='逐项对应'):
                await service.assess(context=context, receipt=parsed, tool_records=records, session_id=checkpoint.session_id,
                    turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id, model_key='local-model', lease_token=current.lease_token)
            partial = await service.assess_steps(context=context, receipt=parsed, tool_records=records,
                session_id=checkpoint.session_id, turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id,
                model_key='local-model', requested_item_keys=['target'])
            assert set(partial.results) == {'target'}
            assert partial.results['target']['status'] == ('failed' if wrong_expected else 'passed')
            assert not hasattr(partial, 'completion')
            assert partial.pending_job_ids == [job.id]
            assert all(j.id != job.id for j in partial.used_jobs)
            assert _digest(context.execution_json()) == checkpoint.context_hash
            delivered = json.loads(unescape(request.messages[0]['content']).split('\n', 1)[1].split('\n</untrusted_content>', 1)[0])
            assert delivered['execution_items'][0]['expected_source'] == content
            assert receipt_http[1][0]['messages'] == [{'role': 'system', 'content': request.system_prompt}, *request.messages]
            with pytest.raises(ValueError, match='尚未结束'):
                await service.assess(context=context, receipt=receipt(), tool_records=records, session_id=checkpoint.session_id,
                    turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id, model_key='local-model', lease_token=current.lease_token)
            with pytest.raises(ValueError):
                await service.assess_steps(context=context.model_copy(update={'steps': [s for s in context.steps if s.item.key == 'target']}),
                    receipt=parsed, tool_records=records, session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
                    attempt_id=checkpoint.execution_attempt_id, model_key='local-model', requested_item_keys=['target'])
            assert await runs.get(detail.run.id) == before
            assert await env[1].get_job(job.id) == job
            assert len(receipt_http[1]) == 1 and len(business[0][1]) == 2 and len(business[1]) == 1
            assert partial.results['target']['output']['body']['flag'] is False
            assert partial.results['target']['output']['body']['nullable'] is None
            proof['partial_results'] = partial.results
            proof['pending_job_ids'] = partial.pending_job_ids
            proof['partial_verifications'] = [v.model_dump(mode='json') for v in partial.verification_results]
            proof['run_and_cleanup_unchanged'] = True
            (folder/'partial.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
            response = await client.post(path+'/approval', json={'approval_id': pending['approval_id'], 'decision': 'approved'})
            assert response.status_code == 200 and response.json()['status'] == ('failed' if wrong_expected else 'passed'), response.text
            terminal = await runs.get(detail.run.id)
            assert terminal.run.status == 'completed' and not business[1]
            assert not (await runs.claim(detail.run.id, RunClaimRequest(worker_id='terminal', limit=10))).claims
            proof.update(terminal_run=terminal.model_dump(mode='json'), business_HTTP=business[0][1], remaining_orders=business[1],
                terminal_non_reclaim=True, consumer_completion_checkpoint_implemented=False, early_cleanup_prevented=False)
            (folder/'terminal.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.asyncio
@pytest.mark.parametrize('requested', [[], ['target', 'target'], ['unknown'], ['target', 'seed']])
async def test_partial_requested_set_is_explicit_and_exact(tmp_path, business_http, requested):
    async with execution(tmp_path, business_http) as env:
        service = AgentCaseAssessmentService(env[1])
        data = receipt()
        data['steps'] = [data['steps'][1]]
        with pytest.raises(ValueError):
            await service.assess_steps(context=env[0], receipt=data, tool_records=env[2], session_id=env[3],
                turn_id=env[4], attempt_id=env[5], model_key='controlled', requested_item_keys=requested)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['pending_check', 'pending_output', 'pending_tree', 'missing_call', 'foreign_attempt', 'duplicate_receipt'])
async def test_partial_evidence_uses_actual_persisted_status_and_identity(tmp_path, business_http, fault):
    from src.schemas.tool_job import ToolJobStatus
    async with execution(tmp_path, business_http) as env:
        data = receipt()
        data['steps'] = [data['steps'][1]]
        saved = await env[1].get_job(env[2][2].job_id)
        saved.status = ToolJobStatus.running
        await env[1]._store.save_job(saved)
        # The original Job remains unsettled even if the compact record claims success.
        env[2][2].status = 'completed'
        env[2][2].output = {'fake': 'must-not-be-used'}
        if fault == 'pending_check':
            data['steps'][0]['checks'][0]['observations'][0]['call_id'] = 'cleanup'
        elif fault == 'pending_output':
            data['steps'][0]['outputs']['body']['call_id'] = 'cleanup'
        elif fault == 'pending_tree':
            data['steps'][0]['outputs'] = {}
            data['steps'][0]['output_structure'] = {'kind': 'array', 'items': [{'kind': 'reference', 'source': {'call_id': 'cleanup'}}]}
        elif fault == 'missing_call':
            data['steps'][0]['checks'][0]['observations'][0]['call_id'] = 'invented'
        elif fault == 'foreign_attempt':
            saved.metadata['attempt_id'] = 'another-attempt'
            await env[1]._store.save_job(saved)
        else:
            data['steps'].append(deepcopy(data['steps'][0]))
        with pytest.raises(ValueError):
            await AgentCaseAssessmentService(env[1]).assess_steps(context=env[0], receipt=data, tool_records=env[2],
                session_id=env[3], turn_id=env[4], attempt_id=env[5], model_key='controlled', requested_item_keys=['target'])


@pytest.mark.asyncio
async def test_full_legacy_request_and_resolved_receipt_match_committed_baseline(tmp_path, business_http):
    path = Path(__file__).resolve().parents[2]/'docs/upgrade_2026-10-03/p2-consumer-receipt-assessment-baseline.py'
    module = ModuleType('_qa_committed_assessment')
    sys.modules[module.__name__] = module
    try:
        exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
        async with execution(tmp_path, business_http) as env:
            kwargs = {'context': env[0], 'tool_records': env[2], 'session_id': env[3], 'turn_id': env[4], 'attempt_id': env[5]}
            current, previous = AgentCaseAssessmentService(env[1]), module.AgentCaseAssessmentService(env[1])
            request = await current.build_assessment_request(**kwargs)
            old_request = await previous.build_assessment_request(**kwargs)
            assert request.model_dump_json() == old_request.model_dump_json()
            outcomes = [await service.assess(**kwargs, receipt=receipt(), model_key='controlled', lease_token=env[6].items[0].lease_token)
                for service in [current, previous]]
            normalized = []
            for outcome in outcomes:
                mapping = {v.id: 'verification-'+str(i) for i, v in enumerate(outcome.verification_results)}
                def normalize(value):
                    if isinstance(value, dict):
                        return {k: 'verification-clock' if k == 'created_at' and value.get('verifier') == 'agent_case_receipt'
                            else normalize(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [normalize(v) for v in value]
                    return mapping.get(value, value) if isinstance(value, str) else value
                normalized.append(normalize(outcome.completion.model_dump(mode='json')))
            assert normalized[0] == normalized[1]
            assert [r.model_dump(mode='json') for r in outcomes[0].tool_records] == [r.model_dump(mode='json') for r in outcomes[1].tool_records]
    finally:
        sys.modules.pop(module.__name__, None)
