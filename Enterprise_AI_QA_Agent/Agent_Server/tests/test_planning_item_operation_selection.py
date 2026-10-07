"""Captured formal-only failure, immutable operation choices and actual PG reads."""
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
    ITEM_PREFETCH_PROTOCOL, declared_operation_queries, validate_operation_selections,
)
from src.application.test_cases.planning_stages import build_stage_prompts, seal_logical_decision
from src.application.test_cases.planning_tool_contracts import query_tool_contracts
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.schemas.case_planning import CasePlanningPolicy, PlanningDecisionV3, PlanningEvidence
from src.schemas.case_planning_stages import LogicalPlanningDecisionV4, PlanningOperationSelection, PlanningStageCheckpoint
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskSeed, PlanningTaskCheckpoint
from src.schemas.project import ProjectCreateRequest

ROOT=Path(__file__).resolve().parents[2]


def captured():
    return json.loads((ROOT/'docs/upgrade_2026-10-03/p4-source-identity-default-first/PG-planning.json').read_bytes())


def choice(key,refs,operation='execute_task'):
    return PlanningOperationSelection(item_key=key,tool_key='api-test-runner',operation=operation,evidence_ids=refs)


def test_actual_formal_only_logic_can_read_the_same_complete_contract_without_adding_prepare():
    old=captured();before=deepcopy(old)
    logical=PlanningDecisionV3.model_validate(old['checkpoint']['stages']['logical_decision'])
    refs=logical.items[0].evidence_ids
    assert declared_operation_queries(logical)==([],[])
    selections=[choice(logical.items[0].key,refs)]
    validate_operation_selections(logical,selections,[PlanningEvidence.model_validate(e) for e in old['checkpoint']['plan']['evidence']])
    queries,origins=declared_operation_queries(logical,selections)
    assert len(queries)==1 and origins[0]['item_keys']==[logical.items[0].key]
    assert origins[0]['evidence_ids']==refs
    evidence,metadata=query_tool_contracts(old['seed']['tool_contracts'],queries[0],20,operations=old['seed']['tool_operations'])
    assert metadata['scope']=='named-operation'
    assert evidence[0].payload==old['seed']['tool_operations']['api-test-runner']['execute_task']
    assert len(evidence[0].payload['models']['ApiTestTask']['properties'])==32
    frozen=seal_logical_decision(logical,selections)
    assert frozen.logical_decision==logical and frozen.logical_hash!=old['checkpoint']['stages']['logical_hash']
    assert all(i.kind=='target' for i in frozen.logical_decision.items) and old==before


@pytest.mark.parametrize('fault',['foreign-item','unread','tool-basis','modified-choice'])
def test_choices_require_actual_item_business_references_and_fixed_hash(fault):
    old=captured();logical=PlanningDecisionV3.model_validate(old['checkpoint']['stages']['logical_decision'])
    refs=logical.items[0].evidence_ids
    selections=[choice(logical.items[0].key,refs)]
    evidence=[PlanningEvidence.model_validate(e) for e in old['checkpoint']['plan']['evidence']]
    if fault=='modified-choice':
        stage=seal_logical_decision(logical,selections).model_dump(mode='json')
        stage['operation_selections'][0]['operation']='replacement'
        with pytest.raises(ValidationError,match='哈希'):PlanningStageCheckpoint.model_validate(stage)
        return
    if fault=='foreign-item':selections[0].item_key='other-project-item'
    if fault=='unread':selections[0].evidence_ids=['document:unread']
    if fault=='tool-basis':
        tool=next(e for e in evidence if e.kind=='tool')
        selections[0].evidence_ids=[tool.id];logical.items[0].evidence_ids.append(tool.id)
    with pytest.raises(ValueError,match='执行项|业务依据'):validate_operation_selections(logical,selections,evidence)


def test_query_choices_and_incomplete_policy_are_explicit_errors():
    with pytest.raises(ValidationError,match='已完成'):
        LogicalPlanningDecisionV4(action='query',summary='读取原文',queries=[{'kind':'document','source_id':'fixed'}],
            operation_selections=[choice('target',['case:fixed'])])
    with pytest.raises(ValidationError,match='完整来源身份'):
        CasePlanningPolicy(operation_prefetch_protocol=ITEM_PREFETCH_PROTOCOL)
    queries,origins=declared_operation_queries(PlanningDecisionV3(action='finish',summary='没有目标'),[])
    assert queries==origins==[]


@pytest.mark.parametrize('directory',['p4-source-identity-default-first',
    'p4-operation-prefetch-default-first','p4-formal-effects-default-first'])
def test_actual_old_prompt_and_seed_checkpoint_bytes_preserved(directory):
    old=json.loads((ROOT/'docs/upgrade_2026-10-03'/directory/'PG-planning.json').read_bytes())
    assert PlanningTaskSeed.model_validate(old['seed']).model_dump(mode='json')==old['seed']
    assert PlanningTaskCheckpoint.model_validate(old['checkpoint']).model_dump(mode='json')==old['checkpoint']
    policy=CasePlanningPolicy.model_validate(old['seed']['plan']['config_snapshot']['policy'])
    assert build_stage_prompts(policy.planning_stage_protocol,policy.effect_expected_source_protocol,
        policy.operation_prefetch_protocol,policy.source_identity_protocol).model_dump(mode='json')==old['seed']['stage_prompts']


@pytest.mark.asyncio
@live
@pytest.mark.parametrize('fault',['none','queue-ack','read-ack','duplicate','unknown-operation','batches','fixed-choice','full-tool'])
async def test_actual_formal_operation_choices_durable_queue_and_original_deadline(tmp_path,monkeypatch,fault):
    async with tasks_environment(tmp_path) as env:
        project=await env.projects.create(ProjectCreateRequest(project_key='item-selection',name='正式项操作选择'))
        content=json.loads((ROOT/'docs/upgrade_2026-10-03/p4-source-identity-default-first/full-case.json').read_bytes())
        case=await active_case(env,project,'actual-formal',content)
        source=ROOT/'docs/upgrade_2026-10-03/p4-source-identity-default-first/requirements.md'
        doc=await env.docs.upload_document(project_id=project.id,filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content']==source.read_bytes()
        await env.service.save_policy(project.id,policy(effect_expected_source_protocol='case-effect-document-expected-v1',
            source_identity_protocol='case-planning-source-full-hash-v1',operation_prefetch_protocol=ITEM_PREFETCH_PROTOCOL))
        task=await env.tasks.submit(project.id,CasePlanningTaskRequest(creation_key='original',objective=content['objective'],candidate_case_ids=[case.case.id]))

        def respond(context):
            if context['planning_stage']=='logic':
                if not any(e['kind']=='document' for e in context['evidence']):
                    return {'action':'query','summary':'读取原件','queries':[{'kind':'document','source_id':doc.id}]}
                refs=[e['id'] for e in context['evidence'] if e['kind']!='tool']
                target=item(context,'actual-formal',key='target',effect_schema=3,cleanup_sources=['target'])
                target['evidence_ids']=refs
                selected_operation=None if fault=='full-tool' else 'missing' if fault=='unknown-operation' else 'execute_task'
                selections=[choice('target',refs,selected_operation).model_dump(mode='json')]
                if fault=='duplicate':selections*=2
                if fault=='batches':selections.extend(choice('target',refs,'unregistered-'+str(i)).model_dump(mode='json') for i in range(8))
                return finish(target,operation_selections=selections)
            assert context['logical_plan']['items'][0]['kind']=='target'
            assert len(context['logical_plan']['items'])==1 and context['operation_selections']
            assert next(e['payload']['content'] for e in context['evidence'] if e['kind']=='case')==content
            if not any(e['kind']=='tool' for e in context['evidence']):
                assert fault=='unknown-operation' and 'error' in context['query_results'][0]
                return {'action':'query','summary':'读取登记操作','queries':[{'kind':'tool','source_id':'api-test-runner','operation':'execute_task'}]}
            if fault!='unknown-operation':
                assert len(context['query_results'])==(9 if fault=='batches' else 1)
                assert context['query_results'][0]['origin']==ITEM_PREFETCH_PROTOCOL
            tool=next(e for e in context['evidence'] if e['kind']=='tool')
            if fault=='full-tool':
                assert tool['version']=='registered-tool-contract-v1'
                assert len(tool['payload']['output_variants'][0]['schema']['properties'])==32
            document=next(e for e in context['evidence'] if e['kind']=='document')
            return {'action':'finish','summary':'原正式内容内产生及清理','bindings':[{'item_key':'target',
                'evidence_ids':[tool['id'],document['id']],
                'effect_declarations':[document_declaration(role,'target',document) for role in ['produced','cleanup']]}]}

        env.model.replies=[respond]*4
        original=env.jobs.save_checkpoint
        lost_claims=[]
        async def lose_ack(claim,checkpoint,*,message):
            await original(claim,checkpoint,message=message)
            if checkpoint.phase=='query' and checkpoint.stages.stage=='effects' and checkpoint.query_index==(1 if fault=='read-ack' else 0):
                lost_claims.append(claim)
                raise RuntimeError('actual PG selection commit acknowledgement lost')
        before=None
        if fault in {'queue-ack','read-ack','fixed-choice'}:
            monkeypatch.setattr(env.jobs,'save_checkpoint',lose_ack)
            with pytest.raises(PlanningCheckpointUnavailable):
                await PlanningTaskHandler(env.service,env.jobs,worker_id='first')(delivery(task))
            before=await env.jobs.get(project.id,task.id)
            assert before.phase=='query' and len(env.model.requests)==2
            if fault=='fixed-choice':
                with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                    cur.execute(f'SELECT seed,checkpoint FROM {env.jobs._table} WHERE id=%s',(task.id,))
                    fixed=dict(cur.fetchone())
                changed=PlanningTaskCheckpoint.model_validate(fixed['checkpoint'])
                changed.stages.operation_selections[0].operation='replacement'
                from src.schemas.case_planning_stages import logical_decision_hash
                changed.stages.logical_hash=logical_decision_hash(changed.stages.logical_decision,changed.stages.operation_selections)
                with pytest.raises(ValueError,match='固定逻辑'):
                    await env.jobs.save_checkpoint(lost_claims[-1],changed,message='不能替换已冻结选择')
                missing=PlanningTaskCheckpoint.model_validate(fixed['checkpoint'])
                missing.stages.operation_selections=None
                missing.stages.logical_hash=logical_decision_hash(missing.stages.logical_decision)
                with pytest.raises(ValueError,match='原规划协议'):
                    await env.jobs.save_checkpoint(lost_claims[-1],missing,message='不能丢弃已冻结选择')
                with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                    cur.execute(f'SELECT seed,checkpoint FROM {env.jobs._table} WHERE id=%s',(task.id,))
                    assert dict(cur.fetchone())==fixed
            await env.service.save_policy(project.id,CasePlanningPolicy(max_rounds=1))
            monkeypatch.setattr(env.jobs,'save_checkpoint',original)
            def forbidden(*args,**kwargs):raise AssertionError('Cannot recapture current registry')
            monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_contracts',forbidden)
            monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_operations',forbidden)
            expire(env,task)
        restored=PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service,restored,worker_id='continued')(delivery(task))
        terminal=await restored.get(project.id,task.id)
        plan=await env.service.get(project.id,terminal.plan_id)
        assert terminal.status=='completed' and plan.status=='planned' and plan.prompt_version=='case-planning-item-operation-prefetch-v9'
        assert len(env.model.requests)==(4 if fault=='unknown-operation' else 3)
        if before:assert terminal.deadline_at==before.deadline_at and terminal.attempt_no==2
        await PlanningTaskHandler(env.service,restored,worker_id='terminal')(delivery(task))
        assert len(env.model.requests)==(4 if fault=='unknown-operation' else 3)
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status,deadline_at,attempt_no FROM {restored._table} WHERE id=%s',(task.id,))
            saved=dict(cur.fetchone())
        saved['deadline_at']=saved['deadline_at'].isoformat()
        if os.getenv('QA_ITEM_OPERATION_DIR'):
            out=Path(os.environ['QA_ITEM_OPERATION_DIR'])/fault;out.mkdir(parents=True,exist_ok=False)
            (out/source.name).write_bytes(source.read_bytes())
            (out/'verification.json').write_text(json.dumps({'actual_PG':saved,
                'actual_requests':[r.model_dump(mode='json') for r in env.model.requests],
                'actual_current_MySQL':(await env.service.get_policy(project.id)).model_dump(mode='json'),
                'RustFS_original_verified':True,'controlled_model':True,'business_HTTP':0,
                'full_upgrade_complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
