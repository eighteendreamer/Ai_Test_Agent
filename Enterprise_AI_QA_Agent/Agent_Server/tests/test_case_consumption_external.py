"""Shared consumption through actual original graph, approvals and durable stores."""
import base64
from copy import deepcopy
from html import unescape
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from test_agent_case_execution import adapter_environment, regression_business_http
from test_agent_case_session import ControlledAgent
from test_case_consumption_phase import phase_policy
from test_case_effect_relations import declaration
from test_case_operations import POLICY
from test_case_planning import active_case, item, finish
from test_case_shared_effect_research import SharedPlanningModel
from src.api.routes.run_management import router
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.test_runs.plan_mapping import build_plan_run
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.model_config import ModelInvocationResult
from src.schemas.run_management import RunClaimRequest, TestRunRecord as RunRecord
from src.schemas.tool_runtime import ModelToolCall

LIVE = pytest.mark.skipif(os.getenv('RUN_LIVE_PLAN_RUN_CREATION') != '1',reason='actual MySQL/PG/RustFS/graph/API/approvals')


def phase_receipt(frozen, calls):
    return {'schema_version':1,'steps':[{'item_key':s['item']['key'],'status':'passed','summary':'原固定预期',
        'checks':[{'name':'固定状态','operator':'equals',
            'expected_path_tokens':['expected'] if s['phase'] != 'case' else ['期望','code'],
            'observations':[{'call_id':calls[s['item']['key']],'output_path_tokens':['task_result','response_status']}]}],
        'outputs':{'body':{'call_id':calls[s['item']['key']],'output_path_tokens':['task_result','response_body']}}}
        for s in frozen['steps']]}


class ExternalAgent(ControlledAgent):
    def __init__(self,scenario):
        super().__init__()
        self.scenario,self.inject,self.injected = scenario,None,False

    async def invoke(self,key,request):
        self.requests.append(request.model_copy(deep=True))
        section = next(s for s in request.system_prompt_sections if s.key == 'frozen_case_execution')
        frozen = json.loads(unescape(section.content.split('\n',1)[1].split('\n</untrusted_content>',1)[0]))
        formal = next(s for s in frozen['steps'] if s['phase'] == 'case')
        assert len(formal['content_json']['steps']) == 8
        assert formal['content_json']['steps'][-1]['typed'] == [False,0,None]
        previous = [{'call_id':m['tool_call_id'],**json.loads(m['content'])} for m in request.messages if m.get('role') == 'tool']
        target = frozen['target_key']
        cleanup_key = 'right' if 'formal-' in self.scenario else 'cleanup'
        operations = [('seed','seed','POST'),('left.query','left','GET')] if target == 'left' else (
            [('right.early',cleanup_key,'DELETE'),('right.query','right','GET'),('right.cleanup',cleanup_key,'DELETE')]
            if self.scenario == 'after-passed-early' else [('right.query','right','GET'),('right.cleanup',cleanup_key,'DELETE')])
        if len(previous) == len(operations):
            if target == 'left' and self.inject and not self.injected:
                self.injected = True
                await self.inject(frozen)
            calls = {'seed':'seed','left':'left.query'} if target == 'left' else {'right':'right.query','cleanup':'right.cleanup'}
            receipt = phase_receipt(frozen,calls)
            denied = next((p for p in previous if p['call_id'] == 'right.cleanup' and p['status'] == 'denied'),None)
            if denied:
                entry = next(s for s in receipt['steps'] if s['item_key'] == cleanup_key)
                entry.update(status='blocked',summary='原清理调用被拒绝：'+denied['output']['error'],checks=[],outputs={})
            return ModelInvocationResult(text=json.dumps(receipt,ensure_ascii=False),
                response_summary={'mode':'ok','finish_reason':'stop'})
        call_id,item_key,method = operations[len(previous)]
        step = next(s for s in frozen['steps'] if s['item']['key'] == item_key)
        source = step['content_json']['steps'][0] if step['phase'] == 'case' else step['item']['instructions']
        url = source['url']
        if '{id}' in url:
            bound = step['inputs']['id']
            id = bound['value'] if bound['resolved'] else previous[0]['output']['task_result']['response_body']['order.id']
            url = url.replace('{id}',id)
        selection = {'item_key':item_key,'operation_key':call_id}
        if call_id == 'right.cleanup':
            query = phase_receipt({**frozen,'steps':[formal]},{'right':'right.query'})
            selection['consumption_closure'] = {'reason':'当前消费查询已有实际证据，后续只清理','receipt':query}
        call = ModelToolCall(id=call_id,name='api-test-runner',arguments={ARGUMENT:selection,
            'worker_action':'execute_task','task':{'task_id':call_id,'method':method,'path':url,'full_url':url,'timeout_seconds':5}})
        return ModelInvocationResult(text='根据完整固定用例执行',tool_calls=[call],response_summary={'mode':'ok','finish_reason':'tool_calls'})


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize('scenario',['after-passed-early','after-failed','after-cancelled-unknown',
    'after-returned-cancelled','after-returned-partial','formal-failed','formal-explicit-failed',
    'formal-bound-failed','legacy-formal-failed'])
async def test_actual_external_consumers_share_original_source_and_cleanup(tmp_path,regression_business_http,monkeypatch,scenario):
    business = regression_business_http
    agent = ExternalAgent(scenario)
    folder = Path(os.environ['QA_CONSUMPTION_EXTERNAL_DIR'])/scenario
    folder.mkdir(parents=True,exist_ok=False)
    async with adapter_environment(tmp_path,business[0],live=True,agent=agent,start_item=False,planning_policy=POLICY) as values:
        env,_,reader,_,runs,_,adapter,_,_,services = values
        base = services['environment']
        project = await base.projects.get(env[0].project_id)
        await base.service.save_policy(project.id,phase_policy())
        formal_cleanup = 'formal-' in scenario
        requires_pass = scenario in {'formal-explicit-failed','formal-bound-failed','legacy-formal-failed'}
        originals = []
        for key in ['left','right']:
            content = {'objective':key,'期望':{'code':201 if key == 'left' and scenario.endswith('failed') else 200},
                '证据契约':{'deleted_method':'DELETE','deleted_status':200},
                'steps':[{'method':'GET','url':business[0][0]+'/orders/{id}'}]+
                    [{'action':'完整原用例步骤','order':i} for i in range(2,8)]+[{'action':'LAST-SHARED-STEP','typed':[False,0,None]}]}
            file = tmp_path/(key+'.json')
            file.write_text(json.dumps(content,ensure_ascii=False),encoding='utf-8')
            doc = await base.docs.upload_document(project_id=project.id,filename=file.name,
                content_base64=base64.b64encode(file.read_bytes()).decode())
            assert (await base.objects.read_object_uri(doc.original_asset.storage_uri))['content'] == file.read_bytes()
            await active_case(base,project,key,content)
            folder.joinpath(file.name).write_bytes(file.read_bytes())
            originals.append(doc.model_dump(mode='json'))

        def decide(c):
            if c['planning_stage'] == 'logic':
                left = item(c,'left',key='left',effect_schema=2,depends_on=['seed'])
                right = item(c,'right',key='right',effect_schema=2,
                    depends_on=['seed','left'] if scenario == 'formal-explicit-failed' else ['seed'],
                    **({'cleanup_sources':['seed']} if formal_cleanup else {}))
                refs = left['evidence_ids']+right['evidence_ids']
                seed = {'key':'seed','kind':'prepare','effect_schema':2,'reason':'共享实际数据','evidence_ids':refs,
                    'instructions':{'method':'POST','url':business[0][0]+'/orders','expected':201,'created_method':'POST','created_status':201}}
                cleanup = {'key':'cleanup','kind':'prepare','effect_schema':2,'reason':'消费结束再清理','evidence_ids':refs,
                    'depends_on':['left','right'],'cleanup_sources':['seed'],
                    'instructions':{'method':'DELETE','url':business[0][0]+'/orders/{id}','expected':200,'deleted_method':'DELETE','deleted_status':200}}
                return finish(seed,left,right,*([] if formal_cleanup else [cleanup]))
            if not any(e['kind'] == 'tool' for e in c['evidence']):
                return {'action':'query','summary':'读取原生工具契约','queries':[{'kind':'tool','source_id':'api-test-runner','operation':'execute_task'}]}
            tool = next(e for e in c['evidence'] if e['kind'] == 'tool')
            bindings = [{'item_key':'seed','evidence_ids':[tool['id']],'effect_declarations':[declaration('produced','seed',False)]}]
            for key in ['left','right']:
                bindings.append({'item_key':key,'evidence_ids':[tool['id']],
                    'input_bindings':{'id':{'source_item':'left' if key == 'right' and scenario == 'formal-bound-failed' else 'seed',
                        'output_path_tokens':['body','order.id']}}})
            if formal_cleanup:
                bindings[-1]['effect_declarations'] = [declaration('cleanup','seed',True)]
            else:
                bindings.append({'item_key':'cleanup','evidence_ids':[tool['id']],
                    'input_bindings':deepcopy(bindings[-1]['input_bindings']),'effect_declarations':[declaration('cleanup','seed',False)]})
            return {'action':'finish','summary':'原固定逻辑的实际证据','bindings':bindings}

        base.service._models = SharedPlanningModel(decide,decide,decide,decide)
        plan = await base.service.create(project.id,CasePlanningRequest(objective='两个消费者完整使用共享数据后清理'))
        assert plan.status == 'planned',plan.summary
        request = CasePlanRunRequest(creation_key='external-phase',mode_key='api_testing')
        creator = services['creator']
        legacy = None
        if scenario == 'legacy-formal-failed':
            suite,run,items = build_plan_run(plan,request,now=datetime.now(timezone.utc),dependency_mapping_version=None)
            legacy = await creator._runs.create_planned_run(suite,run,items,suite_store=creator._suites,
                outbox_table=None,event_key='legacy-fixture',stream=None,payload={})
        detail = await creator.create(project.id,plan.id,request)
        if legacy:
            assert detail == legacy and 'dependency_mapping_version' not in detail.run.plan_snapshot.model_dump(mode='json')
        else:
            assert detail.run.plan_snapshot.dependency_mapping_version == 'case-cleanup-waits-v1'
        executor = ExecutionService(run_service=runs,test_case_service=base.cases,adapter=None,session_store=reader,tool_job_service=env[1])
        executor.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service,app.state.test_run_service = executor,runs
        app.include_router(router,prefix='/api/v1')
        injected = []

        async def inject(frozen):
            jobs = [j for j in await env[1].list_jobs() if j.metadata.get('run_item_id') == frozen['run_item_id']]
            seed = next(j for j in jobs if j.call_id == 'seed')
            context = AgentCaseExecutionContext.model_validate(frozen)
            execution = ToolExecutionContext(session_id=seed.session_id,turn_id=seed.turn_id,trace_id='external-return',
                user_message=context.objective,normalized_input=context.objective,
                context_bundle={**context.runtime_context(),'case_execution_managed':True,'attempt_id':seed.metadata['attempt_id']})
            url = business[0][0]+'/orders/'+business[2][0]
            call = ModelToolCall(id='left.extra',name='api-test-runner',arguments={ARGUMENT:{'item_key':'left','operation_key':'left.extra'},
                'worker_action':'execute_task','task':{'task_id':'left.extra','method':'GET','path':url,'full_url':url,'timeout_seconds':5}})
            runtime,tool = services['tool_runtime'],ToolRegistry().get('api-test-runner')
            handler,finish_output = runtime._handlers[tool.key],env[1].finish_output_processing
            if scenario == 'after-cancelled-unknown':
                async def lost(args,ctx):
                    await handler(args,ctx)
                    raise ConnectionError('actual consumer response lost before original checkpoint')
                monkeypatch.setitem(runtime._handlers,tool.key,lost)
                with pytest.raises(CaseActionClaimUnavailable):
                    await runtime.execute(tool,call,execution)
                monkeypatch.setitem(runtime._handlers,tool.key,handler)
            else:
                if scenario == 'after-returned-partial':
                    async def interrupted(*args,**kwargs):
                        raise RuntimeError('actual returned output saved; delivery interrupted')
                    monkeypatch.setattr(env[1],'finish_output_processing',interrupted)
                await runtime.execute(tool,call,execution)
                monkeypatch.setattr(env[1],'finish_output_processing',finish_output)
            job = next(j for j in await env[1].list_jobs() if j.call_id == 'left.extra')
            if scenario != 'after-returned-partial':
                job = await env[1].cancel_job(job.id,reason='取消不改写实际可靠返回状态')
            injected.append(job.model_dump(mode='json'))
        if scenario in {'after-cancelled-unknown','after-returned-cancelled','after-returned-partial'}:
            agent.inject = inject

        async def save(name,**extra):
            admission = env[1]._case_action_admission
            with postgres_connect(base.settings) as conn,conn.cursor() as cur:
                cur.execute(f'SELECT record FROM {admission.consumption.table} ORDER BY created_at')
                closures = [r['record'] for r in cur.fetchall()]
            data = {'baseline':'67d134ae','scenario':scenario,'originals':originals,'plan':plan.model_dump(mode='json'),
                'run':(await runs.get(detail.run.id)).model_dump(mode='json'),
                'jobs':[j.model_dump(mode='json') for j in await env[1].list_jobs() if j.metadata.get('run_id') == detail.run.id],
                'requests':[r.model_dump(mode='json') for r in agent.requests],'closures':closures,
                'actual_HTTP':deepcopy(business[0][1]),'remaining':deepcopy(business[1]),'injected':injected,
                'actual_MySQL_policy':(await base.service.get_policy(project.id)).model_dump(mode='json'),
                'original_graph_API_approvals':True,'controlled_agent':True,'default_autonomous_model':False,**extra}
            folder.joinpath(name+'.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            return data

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://external') as client:
            async def execute(claim):
                prefix = '/api/v1/run-items/'+claim.item.id
                response = await client.post(prefix+'/execute',json={'lease_token':claim.lease_token})
                for index in range(5):
                    assert response.status_code == 200,response.text
                    if response.json()['status'] != 'waiting_approval':
                        return response.json()
                    await save(claim.item.plan_item_key+'-approval-'+str(index),response=response.json())
                    response = await client.post(prefix+'/approval',json={'approval_id':response.json()['approval_id'],'decision':'approved'})
                raise AssertionError('unexpected extra approvals')
            first = (await runs.claim(detail.run.id,RunClaimRequest(worker_id='left',limit=10,lease_seconds=120))).claims
            assert [c.item.plan_item_key for c in first] == ['left']
            left_result = await execute(first[0])
            assert left_result['status'] == ('failed' if scenario.endswith('failed') else 'passed')
            assert len(business[1]) == 1 and not any(m == 'DELETE' for m,p in business[0][1])
            await base.service.save_policy(project.id,POLICY)
            second = (await runs.claim(detail.run.id,RunClaimRequest(worker_id='right',limit=10,lease_seconds=120))).claims
            saved = await save('left-result',left_result=left_result,right_claim_count=len(second))
            assert await creator.create(project.id,plan.id,request) == await runs.get(detail.run.id)
            if requires_pass:
                assert not second and len(business[1]) == 1 and not saved['closures']
                right_item = next(i for i in saved['run']['items'] if i['plan_item_key'] == 'right')
                assert any(d['item_id'] == first[0].item.id and d['completion_condition'] == 'passed' for d in right_item['dependencies'])
                assert not any(m == 'DELETE' for m,p in business[0][1])
                return
            assert [c.item.plan_item_key for c in second] == ['right']
            right_result = await execute(second[0])
            saved = await save('final',left_result=left_result,right_result=right_result)
            if scenario == 'after-cancelled-unknown':
                assert right_result['status'] == 'blocked' and len(business[1]) == 1 and not saved['closures']
                cleanup = next(j for j in saved['jobs'] if j['call_id'] == 'right.cleanup')
                assert cleanup['output_payload']['error'] == 'case_consumption_consumer_unknown'
                assert not any(m == 'DELETE' for m,p in business[0][1])
            else:
                assert right_result['status'] == 'passed' and not business[1]
                assert [m for m,p in business[0][1]].count('POST') == 1 and [m for m,p in business[0][1]].count('DELETE') == 1
                assert len(saved['closures']) == 1
                cleanup = next(j for j in saved['jobs'] if j['call_id'] == 'right.cleanup')
                consumers = cleanup['metadata']['case_action_admission']['consumption']['consumers']
                left = next(c for c in consumers if c['item_key'] == 'left')
                assert left['status'] == left_result['status'] and left['result_id'] == left_result['id']
                assert len(left['started_actions']) == (3 if injected else 2)
            assert not (await runs.claim(detail.run.id,RunClaimRequest(worker_id='terminal',limit=10))).claims


def test_actual_67d_saved_snapshot_keeps_original_hash_and_mapping():
    root = Path(__file__).resolve().parents[2]/'docs/upgrade_2026-10-03'
    for scenario in ['graph-passed','graph-failed']:
        raw = json.loads((root/'p2-consumption-phase-final'/scenario/'verification.json').read_text('utf-8'))
        original = raw['after_run']['run']
        stored = RunRecord.model_validate(original)
        assert stored.plan_snapshot.dependency_mapping_version is None
        assert stored.plan_snapshot.model_dump(mode='json') == original['plan_snapshot']
        _, rebuilt, items = build_plan_run(stored.plan_snapshot.plan,
            CasePlanRunRequest(creation_key=stored.creation_key,mode_key=stored.mode_key,session_id=stored.session_id),
            now=stored.created_at,created_by=stored.created_by,dependency_mapping_version=None)
        assert rebuilt.creation_hash == stored.creation_hash and rebuilt.plan_snapshot == stored.plan_snapshot
        assert [(i.id,i.dependencies) for i in items] == [(i['id'],[]) for i in raw['after_run']['items']]


@pytest.mark.parametrize('tamper',['declaration','logical_hash','final_dependency'])
def test_actual_failed_input_rejects_changed_wait_provenance(tamper):
    root = Path(__file__).resolve().parents[2]/'docs/upgrade_2026-10-03'
    raw = json.loads((root/'p2-consumption-external-first/formal-failed/left-result.json').read_text('utf-8'))
    stored = RunRecord.model_validate(raw['run']['run'])
    plan = stored.plan_snapshot.plan.model_copy(deep=True)
    origin = next(r for r in plan.rounds if r.get('planning_stage') == 'logic' and r.get('logical_hash'))
    if tamper == 'declaration':
        origin['preparation_scope_declaration']['right']['depends_on'].append('left')
    elif tamper == 'logical_hash':
        origin['logical_hash'] = '0'*64
    else:
        next(i for i in plan.decision.items if i.key == 'right').depends_on.remove('left')
    with pytest.raises(ValueError):
        build_plan_run(plan,CasePlanRunRequest(creation_key='tampered',mode_key='api_testing'),now=stored.created_at)
