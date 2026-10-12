"""Read operation evidence from actual failed inputs and immutable task seeds."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from test_case_effect_evidence import NEW_POLICY
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import tasks_environment, delivery
from src.application.runtime.case_action_binding import _digest
from src.application.test_cases.planning_service import CasePlanningService
from src.application.test_cases.planning_sources import json_text
from src.application.test_cases.planning_tool_contracts import (
    OPERATION_PROMPT_VERSION, NAVIGATION_PROMPT_VERSION, capture_tool_operations,
    query_tool_contracts, tool_catalog,
)
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.schemas.case_planning import CasePlanningPolicy, PlanningQuery
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskSeed
from src.schemas.project import ProjectCreateRequest

ROOT = Path(__file__).resolve().parents[2]


def captured():
    return json.loads((ROOT/'docs/upgrade_2026-10-03/p4-sdk-observation-default/PG-planning.json').read_bytes())


def test_actual_failed_contract_keeps_complete_native_model_and_explicit_scope():
    contracts = captured()['seed']['tool_contracts']
    original = deepcopy(contracts)
    operations = capture_tool_operations(contracts)
    request = PlanningQuery(kind='tool', source_id='api-test-runner', operation='execute_task')
    values, metadata = query_tool_contracts(contracts, request, 20, operations=operations)
    full, _ = query_tool_contracts(contracts, request.model_copy(update={'operation':None}), 20)
    evidence = values[0]
    assert full[0].payload == original['api-test-runner'] and contracts == original
    frozen_variant = next(value for value in original['api-test-runner']['output_variants']
        if value['when'] == {'worker_action': 'execute_task'})
    assert evidence.payload['models']['ApiTestTask'] == frozen_variant['schema']
    assert metadata['scope'] == evidence.payload['scope'] == 'named-operation'
    assert evidence.payload['parent_contract_hash'] == _digest(full[0].payload)
    assert evidence.source_id != full[0].source_id and evidence.id != full[0].id
    assert evidence.content_hash == _digest(evidence.payload)
    assert evidence.payload['input']['properties']['task']['model_ref'] == 'ApiTestTask'
    assert evidence.payload['output']['returned']['task_result']['model_ref'] == 'ApiTestTask'
    assert evidence.payload['output']['known_validation_failure']['task_result_present'] is False
    model=evidence.payload['models']['ApiTestTask']
    assert model['properties']['request_body']['default'] is None
    assert 'execution_checkpoint' in evidence.payload['input']['properties']
    for definition in model.get('$defs',{}).values():
        assert definition
    assert len(json_text(evidence.payload)) < len(json_text(full[0].payload))
    values[0].payload['models'].clear()
    assert operations['api-test-runner']['execute_task']['models']
    assert contracts == original


@pytest.mark.parametrize('missing', ['old-seed','unknown-operation','changed-parent'])
def test_operation_reads_do_not_fall_back_or_mix_parent_versions(missing):
    contracts = captured()['seed']['tool_contracts']
    operations = capture_tool_operations(contracts)
    request = PlanningQuery(kind='tool', source_id='api-test-runner', operation='execute_task')
    if missing == 'old-seed':operations = None
    elif missing == 'unknown-operation':request.operation = 'guessed-action'
    else:contracts['api-test-runner']['description'] += 'changed'
    with pytest.raises(ValueError):query_tool_contracts(contracts, request, 20, operations=operations)


def test_old_query_and_seed_serialization_and_navigation_remain_unchanged():
    saved = captured()['seed']
    old = PlanningTaskSeed.model_validate(saved).model_dump(mode='json')
    assert old == saved
    assert 'tool_operations' not in old
    query = captured()['checkpoint']['plan']['rounds'][0]['decision']['queries'][0]
    assert PlanningQuery.model_validate(query).model_dump(mode='json') == query
    assert '"operation":' not in CasePlanningService._system_prompt(effect_coverage=True)
    contracts=saved['tool_contracts']
    old_catalog=tool_catalog(contracts,20,navigation=True)
    catalog=tool_catalog(contracts,20,navigation=True,operations=capture_tool_operations(contracts))
    assert all('operations' not in value for value in old_catalog['tools'])
    native=next(value for value in catalog['tools'] if value['key']=='api-test-runner')
    assert native['operations']==[{'operation':'execute_task','detail_query':{
        'kind':'tool','source_id':'api-test-runner','operation':'execute_task'}}]
    assert catalog['contract_loaded'] is False
    with pytest.raises(ValueError):PlanningQuery(kind='document',source_id='document',operation='execute_task')


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_CASE_PLANNING_TASKS')!='1', reason='actual MySQL/PG/RustFS')
@pytest.mark.parametrize('legacy', [False, True])
async def test_actual_fixed_operation_read_and_v2_recovery(tmp_path,monkeypatch,legacy):
    async with tasks_environment(tmp_path) as env:
        project=await env.projects.create(ProjectCreateRequest(project_key='operation-views',name='固定操作依据'))
        content={'objective':'完整用例和固定原生入口','steps':[{'order':i+1,'action':'完整步骤'} for i in range(8)],'typed':[False,0,None]}
        await active_case(env,project,'operation-view',content)
        source=tmp_path/'native-requirements.md'
        source.write_text('# 原生操作\n依据原件和完整用例使用api-test-runner的execute_task，保留八步及False/0/null。\n',encoding='utf-8')
        doc=await env.docs.upload_document(project_id=project.id,filename=source.name,content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content']==source.read_bytes()
        await env.service.save_policy(project.id,NEW_POLICY.model_copy(update={'effect_coverage_protocol':'case-effect-coverage-v1'}))
        if legacy:
            prepare=env.service.prepare_task
            async def old_seed(*args,**kwargs):
                seed=await prepare(*args,**kwargs);old=captured()['seed']
                seed.plan.prompt_version=old['plan']['prompt_version'];seed.system_prompt=old['system_prompt']
                seed.tool_contracts=deepcopy(old['tool_contracts']);seed.tool_operations=None
                return seed
            monkeypatch.setattr(env.service,'prepare_task',old_seed)
        request=CasePlanningTaskRequest(creation_key='operation-'+str(legacy),objective=content['objective'])
        task=await env.tasks.submit(project.id,request)
        def forbidden(*args,**kwargs):raise AssertionError('Resume cannot read current registry/models')
        monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_contracts',forbidden)
        monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_operations',forbidden)
        await env.service.save_policy(project.id,CasePlanningPolicy(max_rounds=1))
        queries=[{'kind':'document','source_id':doc.id},{'kind':'tool','source_id':'api-test-runner'}]
        if not legacy:queries.append({'kind':'tool','source_id':'api-test-runner','operation':'execute_task'})
        env.model.replies=[{'action':'query','summary':'原件、完整工具及指定操作分别读取','queries':queries},lambda c:finish(item(c,'operation-view'))]
        handler=PlanningTaskHandler(env.service,env.jobs,worker_id='operation-fixed')
        await handler(delivery(task));terminal=await env.jobs.get(project.id,task.id)
        assert terminal.status=='completed' and terminal.round_count==2
        plan=await env.service.get(project.id,terminal.plan_id)
        assert plan.prompt_version==(NAVIGATION_PROMPT_VERSION if legacy else OPERATION_PROMPT_VERSION)
        assert next(e for e in plan.evidence if e.kind=='case').payload['content']==content
        assert next(e for e in plan.evidence if e.kind=='document').payload['content']==source.read_text(encoding='utf-8').rstrip('\n')
        tools=[e for e in plan.evidence if e.kind=='tool'];assert len(tools)==(1 if legacy else 2)
        if not legacy:
            native=next(e for e in tools if e.payload.get('scope')=='named-operation')
            assert native.payload['models']['ApiTestTask']==ApiTestTask.model_json_schema()
            second=json.loads(env.model.requests[1].messages[0]['content'])
            assert next(e for e in second['evidence'] if e['id']==native.id)['payload']==native.payload
            assert not any('error' in q for q in second['query_results'])
        count=len(env.model.requests)
        assert await env.tasks.submit(project.id,request)==terminal
        await handler(delivery(task));assert len(env.model.requests)==count
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status FROM {env.jobs._table} WHERE id=%s',(task.id,));saved=dict(cur.fetchone())
        assert ('tool_operations' in saved['seed']) is (not legacy)
        if os.getenv('QA_TOOL_OPERATIONS_DIR'):
            directory=Path(os.environ['QA_TOOL_OPERATIONS_DIR'])/('v2' if legacy else 'v3');directory.mkdir(parents=True,exist_ok=False)
            (directory/source.name).write_bytes(source.read_bytes())
            (directory/'verification.json').write_text(json.dumps({'actual_PG':saved,'actual_requests':[r.model_dump(mode='json') for r in env.model.requests],
                'current_MySQL_policy':(await env.service.get_policy(project.id)).model_dump(mode='json'),
                'full_eight_steps_and_typed_values_preserved':True,'original_RustFS_verified':True,'terminal_no_new_calls':True,
                'current_registry_read_forbidden':True,'controlled_model':True,'default_autonomous_Agent_verified':False,'full_P0_P5_complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
