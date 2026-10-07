"""Captured failure replay plus actual fixed stores and durable query recovery."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_case_effect_relations import policy, document_declaration
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import tasks_environment, delivery, expire, live
from src.application.test_cases.planning_operation_prefetch import (
    PREFETCH_PROTOCOL, PREFETCH_PROMPT_VERSION, declared_operation_queries,
)
from src.application.test_cases.planning_tool_contracts import query_tool_contracts
from src.application.test_cases.planning_stages import build_stage_prompts
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.schemas.case_planning import PlanningDecisionV2, CasePlanningPolicy
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskSeed, PlanningTaskCheckpoint
from src.schemas.project import ProjectCreateRequest

ROOT = Path(__file__).resolve().parents[2]


def captured():
    return json.loads((ROOT/'docs/upgrade_2026-10-03/p4-formal-effects-default-first/PG-planning.json').read_bytes())


def test_actual_failed_logic_selects_the_exact_subsequent_query_without_changing_full_model():
    old = captured()
    before = deepcopy(old)
    queries, origins = declared_operation_queries(PlanningDecisionV2.model_validate(old['checkpoint']['stages']['logical_decision']))
    assert len(queries) == 1
    actual = old['checkpoint']['plan']['rounds'][2]['decision']['queries'][0]
    assert queries[0].source_id == actual['source_id'] and queries[0].operation == actual['operation']
    assert origins[0]['item_keys'] == ['prepare-orders']
    read, metadata = query_tool_contracts(old['seed']['tool_contracts'], queries[0], 20, operations=old['seed']['tool_operations'])
    assert metadata['scope'] == 'named-operation'
    original = old['seed']['tool_operations']['api-test-runner']['execute_task']
    assert read[0].payload == original and len(original['models']['ApiTestTask']['properties']) == 32
    assert old == before


@pytest.mark.parametrize('mode', ['missing', 'no-inference', 'duplicate', 'unaddressable'])
def test_declarations_do_not_guess_tool_from_methods_and_do_not_duplicate_reads(mode, caplog):
    value = deepcopy(captured()['checkpoint']['stages']['logical_decision'])
    prepare = value['items'][0]
    if mode == 'missing': prepare['instructions'].pop('operation')
    if mode == 'no-inference': prepare['instructions'] = {'method':'POST', 'url':'/orders'}
    if mode == 'duplicate':
        duplicate = deepcopy(prepare); duplicate['key']='another-preparation';value['items'].insert(1, duplicate)
    if mode == 'unaddressable': prepare['instructions']['tool']='a'*161
    queries, origins = declared_operation_queries(PlanningDecisionV2.model_validate(value))
    assert len(queries) == (1 if mode == 'duplicate' else 0)
    if mode == 'duplicate': assert origins[0]['item_keys'] == ['prepare-orders', 'another-preparation']
    if mode == 'unaddressable': assert 'case_planning_operation_prefetch_unaddressable' in caplog.text


def test_old_v6_seed_checkpoint_and_prompts_preserve_original_bytes_and_new_policy_requires_sources():
    old = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-expected-source-logged-final/document-durable-recovery/verification.json').read_bytes())['actual_PG']
    assert PlanningTaskSeed.model_validate(old['seed']).model_dump(mode='json') == old['seed']
    assert PlanningTaskCheckpoint.model_validate(old['checkpoint']).model_dump(mode='json') == old['checkpoint']
    original = old['seed']['stage_prompts']
    assert build_stage_prompts(original['protocol'], 'case-effect-document-expected-v1').model_dump(mode='json') == original
    with pytest.raises(ValidationError, match='明确操作预读'):
        CasePlanningPolicy(operation_prefetch_protocol=PREFETCH_PROTOCOL)


@pytest.mark.asyncio
@live
@pytest.mark.parametrize('fault', ['none', 'queue-ack', 'read-ack', 'unknown-operation', 'batches'])
async def test_actual_fixed_operation_queue_read_and_commit_recovery(tmp_path, monkeypatch, fault):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='prefetch', name='固定操作阶段交接'))
        content = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-formal-effects-default-first/full-case.json').read_bytes())
        case = await active_case(env, project, 'original-target', content)
        source = ROOT/'docs/upgrade_2026-10-03/p4-formal-effects-default-first/requirements.md'
        doc = await env.docs.upload_document(project_id=project.id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content'] == source.read_bytes()
        chosen = policy(effect_expected_source_protocol='case-effect-document-expected-v1', operation_prefetch_protocol=PREFETCH_PROTOCOL)
        await env.service.save_policy(project.id, chosen)
        request = CasePlanningTaskRequest(creation_key='original', objective=content['objective'], candidate_case_ids=[case.case.id])
        task = await env.tasks.submit(project.id, request)

        def respond(context):
            if context['planning_stage']=='logic':
                tools = next(t for t in context['available_tools'] if t['key']=='api-test-runner')
                assert 'execute_task' in tools['operations']
                if not any(e['kind']=='document' for e in context['evidence']):
                    return {'action':'query','summary':'读取原文','queries':[{'kind':'document','source_id':doc.id}]}
                refs=[e['id'] for e in context['evidence']]
                extras=[{'key':'extra-'+str(i),'kind':'prepare','reason':'验证多个明确声明按批次读取而非溢出查询Schema',
                    'effect_schema':3,'evidence_ids':refs,'instructions':{'tool':'api-test-runner','operation':'unregistered-'+str(i)}}
                    for i in range(8)] if fault=='batches' else []
                return finish({'key':'seed','kind':'prepare','reason':'缺实际订单，依据原文创建','effect_schema':3,
                    'evidence_ids':refs,'instructions':{'tool':'api-test-runner','operation':'missing' if fault=='unknown-operation' else 'execute_task'}},
                    *extras, item(context,'original-target',key='target',effect_schema=3,depends_on=['seed',*[e['key'] for e in extras]],cleanup_sources=['seed']))
            actual = next(e['payload']['content'] for e in context['evidence'] if e['kind']=='case')
            assert actual==content and actual['steps'][-1]['business']=={'false':False,'zero':0,'nullable':None}
            if fault=='unknown-operation':
                if not any(e['kind']=='tool' for e in context['evidence']):
                    assert context['query_results'][0]['origin']==PREFETCH_PROTOCOL and 'error' in context['query_results'][0]
                    return {'action':'query','summary':'明确改读已登记操作','queries':[{'kind':'tool','source_id':'api-test-runner','operation':'execute_task'}]}
                return effects(context)
            assert context['query_results'][0]['origin']==PREFETCH_PROTOCOL
            assert context['query_results'][0]['logical_hash']==context['logical_hash']
            if fault=='batches':
                assert len(context['query_results'])==9 and sum('error' in q for q in context['query_results'])==8
            return effects(context)

        def effects(context):
            tool=next(e for e in context['evidence'] if e['kind']=='tool')
            document=next(e for e in context['evidence'] if e['kind']=='document')
            return {'action':'finish','summary':'按固定原文绑定','bindings':[
                {'item_key':'seed','evidence_ids':[tool['id'],document['id']], 'effect_declarations':[document_declaration('produced','seed',document)]},
                {'item_key':'target','evidence_ids':[tool['id'],document['id']], 'effect_declarations':[document_declaration('cleanup','seed',document)]}]}

        env.model.replies=[respond]*4
        original=env.jobs.save_checkpoint
        async def lose_ack(claim, checkpoint, *, message):
            saved=await original(claim,checkpoint,message=message)
            if (checkpoint.phase=='query' and checkpoint.stages.stage=='effects'
                and checkpoint.query_index==(1 if fault=='read-ack' else 0)):
                raise RuntimeError('actual PG prefetch commit acknowledgement lost')
            return saved
        before=None
        if fault in {'queue-ack','read-ack'}:
            monkeypatch.setattr(env.jobs,'save_checkpoint',lose_ack)
            with pytest.raises(PlanningCheckpointUnavailable):
                await PlanningTaskHandler(env.service,env.jobs,worker_id='before-loss')(delivery(task))
            before=await env.jobs.get(project.id,task.id)
            assert before.phase=='query' and len(env.model.requests)==2
            await env.service.save_policy(project.id,CasePlanningPolicy(max_rounds=1))
            monkeypatch.setattr(env.jobs,'save_checkpoint',original)
            def forbidden(*args,**kwargs):raise AssertionError('Current registry is not the frozen seed')
            monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_contracts',forbidden)
            monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_operations',forbidden)
            expire(env,task)
        restored=PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service,restored,worker_id='continue')(delivery(task))
        terminal=await restored.get(project.id,task.id)
        plan=await env.service.get(project.id,terminal.plan_id)
        assert terminal.status=='completed' and plan.status=='planned'
        assert len(env.model.requests)==(4 if fault=='unknown-operation' else 3)
        assert plan.prompt_version==PREFETCH_PROMPT_VERSION and plan.config_snapshot.policy.operation_prefetch_protocol==PREFETCH_PROTOCOL
        if before: assert terminal.deadline_at==before.deadline_at and terminal.attempt_no==2
        origin=next(r['operation_prefetch'] for r in plan.rounds if 'operation_prefetch' in r)
        assert origin['declarations'][0]['item_keys']==['seed']
        assert next(r for r in plan.rounds if 'operation_prefetch' in r)['decision']['action']=='finish'
        await PlanningTaskHandler(env.service,restored,worker_id='terminal')(delivery(task))
        assert len(env.model.requests)==(4 if fault=='unknown-operation' else 3)
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status,deadline_at,attempt_no FROM {restored._table} WHERE id=%s',(task.id,))
            saved=dict(cur.fetchone())
        saved['deadline_at']=saved['deadline_at'].isoformat()
        if os.getenv('QA_OPERATION_PREFETCH_DIR'):
            path=Path(os.environ['QA_OPERATION_PREFETCH_DIR'])/fault;path.mkdir(parents=True,exist_ok=False)
            (path/source.name).write_bytes(source.read_bytes())
            (path/'verification.json').write_text(json.dumps({'actual_PG':saved,
                'actual_requests':[r.model_dump(mode='json') for r in env.model.requests],
                'actual_current_MySQL':(await env.service.get_policy(project.id)).model_dump(mode='json'),
                'RustFS_original_verified':True,'controlled_model':True,'business_HTTP':0,
                'full_upgrade_complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
