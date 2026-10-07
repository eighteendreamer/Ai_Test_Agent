"""Explicit phase closure over full originals, actual Jobs and the original PG claim."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from html import unescape
import json
import os
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI

from test_agent_case_execution import adapter_environment, regression_business_http
from test_case_effect_relations import policy, declaration
from test_case_operations import POLICY
from test_case_planning import active_case, item, finish
from test_case_shared_effect_research import SharedPlanningModel
from test_case_cleanup_coverage import receipt_for
from test_agent_case_session import ControlledAgent
from src.api.routes.run_management import router
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable, _digest, action_input_schema
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest, CasePlanRunRequest
from src.schemas.run_management import RunClaimRequest, RunItemLeaseRequest
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.model_config import ModelInvocationResult

ROOT = Path(__file__).resolve().parents[2]
LIVE = pytest.mark.skipif(os.getenv('RUN_LIVE_PLAN_RUN_CREATION') != '1', reason='actual MySQL/PG/RustFS/native business HTTP')


def phase_policy():
    return policy(consumption_protocol='case-consumption-phase-v1')


def consumption_receipt():
    return {'schema_version':1,'steps':[{'item_key':'target','status':'passed','summary':'消费结束',
        'checks':[{'name':field,'operator':'equals','expected_path_tokens':['期望',field],
            'observations':[{'call_id':'query-first','output_path_tokens':['task_result','response_status'] if field == 'code' else
                ['task_result','response_body',field]}]} for field in ['code','flag','count','nullable']]}]}


class PhaseAgent(ControlledAgent):
    async def invoke(self, key, request):
        self.requests.append(request)
        section = next(s for s in request.system_prompt_sections if s.key == 'frozen_case_execution')
        frozen = json.loads(unescape(section.content.split('\n',1)[1].split('\n</untrusted_content>',1)[0]))
        assert len(frozen['steps'][0]['content_json']['steps']) == 8
        assert frozen['consumption_protocol'] == 'case-consumption-phase-v1'
        previous = [{'call_id':m['tool_call_id'], **json.loads(m['content'])}
            for m in request.messages if m.get('role') == 'tool']
        operations = [('too-early','DELETE'),('create-first','POST'),('create-second','POST'),
            ('query-first','GET'),('cleanup-first','DELETE'),('cleanup-second','DELETE')]
        if len(previous) == len(operations):
            receipt = consumption_receipt()
            receipt['steps'][0]['outputs'] = {'body':{'call_id':'query-first','output_path_tokens':['task_result','response_body']}}
            return ModelInvocationResult(text=json.dumps(receipt,ensure_ascii=False),response_summary={'mode':'ok','finish_reason':'stop'})
        name, method = operations[len(previous)]
        root = frozen['steps'][0]['content_json']['steps'][0]['url'].split('/orders/')[0]+'/orders'
        path = root
        if name == 'too-early':
            path += '/absent'
        elif name in {'query-first','cleanup-first','cleanup-second'}:
            producer = next(p for p in previous if p['call_id'] == ('create-second' if name == 'cleanup-second' else 'create-first'))
            path += '/'+producer['output']['task_result']['response_body']['order.id']
        selection = {'item_key':'target','operation_key':name}
        if name == 'cleanup-first':
            selection['consumption_closure'] = {'reason':'原查询证据已齐备，之后只执行固定清理','receipt':consumption_receipt()}
        call = ModelToolCall(id=name,name='api-test-runner',arguments={ARGUMENT:selection,'worker_action':'execute_task',
            'task':{'task_id':name,'method':method,'path':path,'full_url':path,'timeout_seconds':5}})
        return ModelInvocationResult(text='执行固定正式项下一操作',tool_calls=[call],response_summary={'mode':'ok','finish_reason':'tool_calls'})


async def original_graph_phase(values, detail, plan, document, original, business, scenario, agent):
    env, _, reader, _, runs, _, adapter, _, _, services = values
    base = services['environment']
    executor = ExecutionService(run_service=runs,test_case_service=base.cases,adapter=None,
        session_store=reader,tool_job_service=env[1])
    executor.set_agent_execution_adapter(adapter())
    app = FastAPI()
    app.state.test_run_execution_service,app.state.test_run_service = executor,runs
    app.include_router(router,prefix='/api/v1')
    claim = (await runs.claim(detail.run.id,RunClaimRequest(worker_id='phase-graph',limit=1,lease_seconds=120))).claims[0]
    path = '/api/v1/run-items/'+claim.item.id
    responses, before_cleanup = [], None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://phase') as client:
        response = await client.post(path+'/execute',json={'lease_token':claim.lease_token})
        for index in range(6):
            assert response.status_code == 200 and response.json()['status'] == 'waiting_approval',response.text
            pending = response.json()
            responses.append(pending)
            job = await env[1].get_job(pending['tool_job_id'])
            if index == 1:
                early = [j for j in await env[1].list_jobs() if j.call_id == 'too-early'][0]
                assert early.status.value == 'denied' and early.output_payload['error'] == 'case_consumption_receipt_required'
                assert not business[0][1]
            if index == 4:
                assert job.call_id == 'cleanup-first' and job.started_at is None
                with postgres_connect(base.settings) as conn,conn.cursor() as cur:
                    assert env[1]._case_action_admission.consumption.read(cur,claim.item.id) is None
                assert len(business[1]) == 2 and len(business[0][1]) == 3
                before_cleanup = (await runs.get(detail.run.id)).model_dump(mode='json')
                await base.service.save_policy(plan.project_id,policy())
            response = await client.post(path+'/approval',json={'approval_id':pending['approval_id'],'decision':'approved'})
        assert response.status_code == 200 and response.json()['status'] == ('failed' if scenario == 'graph-failed' else 'passed'),response.text
    final = await runs.get(detail.run.id)
    assert len(agent.requests) == 7 and not business[1] and len(business[0][1]) == 5
    assert not (await runs.claim(detail.run.id,RunClaimRequest(worker_id='terminal',limit=1))).claims
    with postgres_connect(base.settings) as conn,conn.cursor() as cur:
        closure = env[1]._case_action_admission.consumption.read(cur,claim.item.id)
    assert closure and not closure['run_item_completed']
    assert closure['verification']['status'] == ('failed' if scenario == 'graph-failed' else 'passed')
    folder = Path(os.environ['QA_CONSUMPTION_PHASE_DIR'])/scenario
    folder.mkdir(parents=True,exist_ok=False)
    folder.joinpath(original.name).write_bytes(original.read_bytes())
    folder.joinpath('verification.json').write_text(json.dumps({'baseline':'e356905d','scenario':scenario,
        'original':document.model_dump(mode='json'),'frozen_plan':plan.model_dump(mode='json'),
        'before_cleanup':before_cleanup,'after_run':final.model_dump(mode='json'),'approvals':responses,
        'requests':[r.model_dump(mode='json') for r in agent.requests],
        'jobs':[(await env[1].get_job_detail(j.id)).model_dump(mode='json') for j in await env[1].list_jobs()
            if j.metadata.get('run_id') == detail.run.id], 'closure':closure,'actual_HTTP':business[0][1],
        'remaining':business[1],'created_ids':business[2],'original_graph_API_approvals':True,
        'controlled_agent':True,'default_autonomous_model':False},ensure_ascii=False,indent=2),encoding='utf-8')


def test_phase_requires_original_multi_effect_policy_and_old_bytes_remain():
    with pytest.raises(ValueError):
        CasePlanningPolicy(consumption_protocol='case-consumption-phase-v1')
    original = policy().model_dump(mode='json')
    assert 'consumption_protocol' not in original
    assert phase_policy().model_dump(mode='json')['consumption_protocol'] == 'case-consumption-phase-v1'


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize('scenario', ['passed', 'failed', 'rollback', 'parallel', 'lost-claim', 'pending-ordinary',
    'empty-checks', 'missing-call', 'foreign-target', 'changed-closure', 'graph-passed', 'graph-failed', 'unknown-ordinary'])
async def test_actual_mixed_formal_consumption_and_cleanup_share_original_transaction(
    tmp_path, regression_business_http, monkeypatch, scenario,
):
    business = regression_business_http
    agent = PhaseAgent() if scenario.startswith('graph-') else None
    async with adapter_environment(tmp_path, business[0], live=True, start_item=False, planning_policy=POLICY,agent=agent) as values:
        env, _, _, _, runs, _, adapter, _, _, services = values
        base = services['environment']
        project = await base.projects.get(env[0].project_id)
        await base.service.save_policy(project.id,phase_policy())
        content = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-shared-effect-wire/first-consumer-only-left-passed-finished/shared-left.json').read_bytes())
        content['objective'] = '两个真实对象的消费结束与清理'
        content['steps'][0]['url'] = business[0][0]+'/orders/{id}'
        content['期望'] = {'code':201 if scenario in {'failed','graph-failed'} else 200, 'flag':False, 'count':1, 'nullable':None}
        content['证据契约'] = {'created_method':'POST', 'deleted_method':'DELETE', 'created_status':201, 'deleted_status':200}
        original = tmp_path/'consumption-case.json'
        original.write_text(json.dumps(content,ensure_ascii=False),encoding='utf-8')
        document = await base.docs.upload_document(project_id=project.id,filename=original.name,
            content_base64=base64.b64encode(original.read_bytes()).decode())
        assert (await base.objects.read_object_uri(document.original_asset.storage_uri))['content'] == original.read_bytes()
        case = await active_case(base,project,'consumer-phase',content)

        def decision(context):
            if context['planning_stage'] == 'logic':
                return finish(item(context,'consumer-phase',key='target',effect_schema=2,cleanup_sources=['target']))
            if not any(e['kind'] == 'tool' for e in context['evidence']):
                return {'action':'query','summary':'读取原工具契约','queries':[{'kind':'tool','source_id':'api-test-runner','operation':'execute_task'}]}
            contract = next(e for e in context['evidence'] if e['kind'] == 'tool')
            return {'action':'finish','summary':'固定实际产生与清理范围','bindings':[{'item_key':'target',
                'evidence_ids':[contract['id']], 'effect_declarations':[declaration('produced','target',True),declaration('cleanup','target',True)]}]}

        base.service._models = SharedPlanningModel(decision,decision,decision,decision)
        plan = await base.service.create(project.id,CasePlanningRequest(objective='完整消费阶段结束后清理',candidate_case_ids=[case.case.id]))
        assert plan.status == 'planned', plan.summary
        detail = await services['creator'].create(project.id,plan.id,CasePlanRunRequest(creation_key='phase',mode_key='api_testing'))
        if agent is not None:
            await original_graph_phase(values,detail,plan,document,original,business,scenario,agent)
            return
        claim = (await runs.claim(detail.run.id,RunClaimRequest(worker_id='phase',limit=1,lease_seconds=120))).claims[0]
        running = await runs.start_item(claim.item.id,RunItemLeaseRequest(lease_token=claim.lease_token))
        attempt = await runs.get_latest_attempt(running.id)
        context, checkpoint, attempt = await adapter()._load(run=await runs.get_record(detail.run.id),item=running,
            version=await base.cases.get_version(running.case_version_id),attempt_id=attempt.id)
        await adapter()._save(running,attempt,checkpoint.model_copy(update={'phase':'running'}))
        execution = ToolExecutionContext(session_id=checkpoint.session_id,turn_id=checkpoint.turn_id,
            trace_id='phase',user_message=context.objective,normalized_input=context.objective,selected_model_key='controlled-phase',
            context_bundle={**context.runtime_context(),'case_execution_managed':True,'attempt_id':checkpoint.execution_attempt_id})
        tool, runtime = ToolRegistry().get('api-test-runner'), services['tool_runtime']
        schema = action_input_schema(tool.input_schema,execution.context_bundle)
        assert 'consumption_closure' in schema['properties'][ARGUMENT]['properties']
        import jsonschema
        records = []

        def call(key,method,path,closure=None,operation=None):
            operation = operation or key
            arguments = {ARGUMENT:{'item_key':'target','operation_key':operation},'worker_action':'execute_task',
                'task':{'task_id':operation,'method':method,'path':path,'full_url':path,'timeout_seconds':5}}
            if closure is not None:
                arguments[ARGUMENT]['consumption_closure'] = closure
            jsonschema.validate(arguments,schema)
            return ModelToolCall(id=key,name=tool.key,arguments=arguments)

        async def action(*args,**kwargs):
            record = await runtime.execute(tool,call(*args,**kwargs),execution)
            records.append(record)
            return record

        early = await action('too-early','DELETE',business[0][0]+'/orders/absent')
        assert early.status == 'denied' and early.output['error'] == 'case_consumption_receipt_required' and not business[0][1]
        await action('create-first','POST',business[0][0]+'/orders')
        first = business[2][0]
        await action('create-second','POST',business[0][0]+'/orders')
        second = business[2][1]
        query = await action('query-first','GET',business[0][0]+'/orders/'+first)
        closure = {'reason':'完整查询阶段已结束；保留失败预期，接下来仅清理本次对象',
            'receipt':consumption_receipt()}
        admission = env[1]._case_action_admission
        before = await runs.get(detail.run.id)
        await base.service.save_policy(project.id,policy())
        cleanup = call('cleanup-first','DELETE',business[0][0]+'/orders/'+first,closure)
        extra = {}

        invalid_receipt = scenario in {'empty-checks', 'missing-call', 'foreign-target'}
        if invalid_receipt:
            invalid = deepcopy(closure)
            if scenario == 'empty-checks':
                invalid['receipt']['steps'][0]['checks'] = []
            elif scenario == 'missing-call':
                invalid['receipt']['steps'][0]['checks'][0]['observations'][0]['call_id'] = 'not-persisted'
            else:
                invalid['receipt']['steps'][0]['item_key'] = 'another-project-target'
            denied = await action('invalid-closure','DELETE',business[0][0]+'/orders/'+first,invalid)
            assert denied.status == 'denied' and denied.output['error'] == 'case_consumption_receipt_invalid'
            assert len(business[1]) == 2 and len(business[0][1]) == 3
            extra['invalid_closure'] = invalid
        elif scenario in {'pending-ordinary','unknown-ordinary'}:
            pending_call = call('pending-read','GET',business[0][0]+'/orders/'+second)
            pending = await env[1].create_job(tool=tool,call_id=pending_call.id,
                session_id=execution.session_id,turn_id=execution.turn_id,trace_id=execution.trace_id,
                input_payload=pending_call.arguments,metadata=execution.bind_call(pending_call).job_metadata())
            if scenario == 'unknown-ordinary':
                claimed = await env[1].claim_execution(pending.id,context_bundle=execution.context_bundle)
                assert claimed.status.value == 'running' and not claimed.output_payload
            denied = await runtime.execute(tool,cleanup,execution)
            assert denied.status == 'denied' and denied.output['error'] == 'case_consumption_receipt_invalid'
            assert len(business[1]) == 2 and len(business[0][1]) == 3
            extra['pending_original'] = (await env[1].get_job(pending.id)).model_dump(mode='json')
            records.append(denied)
        elif scenario == 'lost-claim':
            original_claim = admission._claim
            def lost(*args):
                original_claim(*args)
                raise ConnectionError('actual commit response lost before business dispatch')
            monkeypatch.setattr(admission,'_claim',lost)
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool,cleanup,execution)
            monkeypatch.setattr(admission,'_claim',original_claim)
            count = len(business[0][1])
            originals = [j for j in await env[1].list_jobs() if j.call_id == cleanup.id]
            assert len(originals) == 1 and originals[0].status.value == 'running'
            origin = originals[0]
            alias = await runtime.execute(tool,cleanup,execution)
            assert alias.status == 'denied' and alias.output['error'] == 'case_operation_outcome_unknown'
            assert alias.output['origin_job_id'] == origin.id and alias.output['action_sent'] is False
            records.append(alias)
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool,cleanup,replace(execution,tool_job_id=origin.id))
            saved = await env[1].get_job(origin.id)
            assert saved.status.value == 'running' and saved.output_payload == origin.output_payload
            assert saved.metadata == origin.metadata
            assert len(business[0][1]) == count == 3 and len(business[1]) == 2
            extra['unknown_original'] = saved.model_dump(mode='json')
            extra['unknown_preserved'] = True
        else:
            if scenario == 'rollback':
                original_save = admission.consumption.save
                def rollback(cur,prepared):
                    original_save(cur,prepared)
                    raise ConnectionError('actual closure and running claim rolled back')
                monkeypatch.setattr(admission.consumption,'save',rollback)
                with pytest.raises(CaseActionClaimUnavailable):
                    await runtime.execute(tool,cleanup,execution)
                monkeypatch.setattr(admission.consumption,'save',original_save)
                with postgres_connect(base.settings) as conn,conn.cursor() as cur:
                    assert admission.consumption.read(cur,running.id) is None
                assert len(business[0][1]) == 3
                extra['rollback_actual_SQL'] = True
            if scenario == 'parallel':
                calls = [call('parallel-'+str(i),'DELETE',business[0][0]+'/orders/'+first,closure,operation='cleanup-first') for i in range(4)]
                outcomes = await asyncio.gather(*(runtime.execute(tool,c,execution) for c in calls),return_exceptions=True)
                records.extend(r for r in outcomes if not isinstance(r,Exception))
                assert any(not isinstance(r,Exception) and r.status == 'completed' for r in outcomes)
                assert [m for m,p in business[0][1]].count('DELETE') == 1
                extra['parallel_actual_claims'] = 4
            else:
                records.append(await runtime.execute(tool,cleanup,execution))
            closed_read = await action('new-read-after-closure','GET',business[0][0]+'/orders/'+second)
            assert closed_read.status == 'denied' and closed_read.output['error'] == 'case_consumption_closed'
            alias = await action('known-read-alias','GET',business[0][0]+'/orders/'+first,operation='query-first')
            assert alias.status == 'completed' and alias.output['case_operation_replay']['action_sent'] is False
            if scenario == 'changed-closure':
                changed = deepcopy(closure)
                changed['reason'] = '试图替换已经固定的关闭依据'
                denied = await action('replacement-closure','DELETE',business[0][0]+'/orders/'+second,changed)
                assert denied.status == 'denied' and denied.output['error'] == 'case_consumption_closure_conflict'
                assert len(business[0][1]) == 4 and len(business[1]) == 1
            await action('cleanup-second','DELETE',business[0][0]+'/orders/'+second)
            assert not business[1] and len(business[0][1]) == 5
            full = await AgentCaseAssessmentService(env[1]).assess(context=context,receipt=receipt_for(context,{'target':'query-first'}),
                tool_records=records,session_id=execution.session_id,turn_id=execution.turn_id,attempt_id=checkpoint.execution_attempt_id,
                model_key='controlled-phase',lease_token=claim.lease_token)
            result = await runs.complete_item(running.id,full.completion)
            assert result.status == ('failed' if scenario == 'failed' else 'passed')
            assert not (await runs.claim(detail.run.id,RunClaimRequest(worker_id='terminal',limit=1))).claims
            extra['formal_result'] = result.model_dump(mode='json')
        with postgres_connect(base.settings) as conn,conn.cursor() as cur:
            record = admission.consumption.read(cur,running.id)
            cur.execute(f'SELECT count(*) AS n FROM {admission.consumption.table} WHERE run_item_id=%s',(running.id,))
            assert cur.fetchone()['n'] == (0 if scenario in {'pending-ordinary','unknown-ordinary'} or invalid_receipt else 1)
        if record:
            assert record['sha256'] == _digest({k:v for k,v in record.items() if k != 'sha256'})
            assert not record['run_item_completed']
            assert record['verification']['status'] == ('failed' if scenario == 'failed' else 'passed')
        folder = Path(os.environ['QA_CONSUMPTION_PHASE_DIR'])/scenario
        folder.mkdir(parents=True,exist_ok=False)
        folder.joinpath(original.name).write_bytes(original.read_bytes())
        folder.joinpath('verification.json').write_text(json.dumps({'baseline':'e356905d','scenario':scenario,
            'original':document.model_dump(mode='json'),'frozen_plan':plan.model_dump(mode='json'),'full_context':context.execution_json(),
            'before_run':before.model_dump(mode='json'),'after_run':(await runs.get(detail.run.id)).model_dump(mode='json'),
            'records':[r.model_dump(mode='json') for r in records], 'jobs':[(await env[1].get_job_detail(j.id)).model_dump(mode='json')
                for j in await env[1].list_jobs() if j.metadata.get('run_id') == detail.run.id],
            'closure':record,'actual_HTTP':business[0][1],'remaining':business[1],'created_ids':business[2],
            'controlled_receipt':True,'default_autonomous_model':False, **extra},ensure_ascii=False,indent=2),encoding='utf-8')
