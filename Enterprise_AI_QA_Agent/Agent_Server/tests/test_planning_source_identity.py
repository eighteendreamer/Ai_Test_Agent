"""Exact captured failures, immutable old protocols and real parsed originals."""
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_document_parse_tasks import parsing_environment
from test_document_parsers import actual_docx, actual_pdf
from test_case_planning import SequenceModel
from test_case_effect_relations import policy
from src.infrastructure.postgres_runtime import postgres_connect
from src.application.test_cases.planning_stages import build_stage_prompts, stage_prompt_version
from src.application.test_cases.planning_sources import FULL_SOURCE_IDENTITY_PROTOCOL
from src.application.runtime.case_effect_expected_sources import document_expected_value
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest, PlanningEvidence, PlanningDecisionV3
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint
from src.schemas.document_parsing import DocumentParseTaskRequest
from src.schemas.project import ProjectCreateRequest

ROOT=Path(__file__).resolve().parents[2]


def test_actual_three_wrong_references_remain_rejected_by_old_identity_and_resolve_the_same_full_hash():
    saved=json.loads((ROOT/'docs/upgrade_2026-10-03/p4-operation-prefetch-default-first/PG-planning.json').read_bytes())
    docs=[e for e in saved['checkpoint']['plan']['evidence'] if e['kind']=='document']
    document=PlanningEvidence.model_validate(docs[0])
    cases=0
    for p in (ROOT/'docs/upgrade_2026-10-03/p4-operation-prefetch-default-first/physical').glob('*.request.json'):
        context=json.loads(json.loads(p.read_bytes())['messages'][-1]['content'])
        feedback=context.get('validation_feedback')
        if not feedback or not feedback.get('candidate_response'):continue
        decision=PlanningDecisionV3.model_validate(json.loads(feedback['candidate_response']))
        target=decision.items[0]
        reference={'evidence_id':f'document:{document.source_id}:{document.content_hash}',
            'content_hash':document.content_hash,'payload_path_tokens':['content']}
        assert reference['evidence_id'] in target.evidence_ids and reference['evidence_id']!=document.id
        with pytest.raises(ValueError,match='唯一已读来源'):
            document_expected_value(reference,target,[document])
        current=document.model_copy(update={'id':reference['evidence_id']})
        text,proof=document_expected_value(reference,target,[current])
        assert text==document.payload['content'] and proof['version']==document.version
        assert proof['content_hash']==document.content_hash
        cases+=1
    assert cases==3


@pytest.mark.parametrize('directory',['p4-operation-prefetch-live-final/none','p4-operation-prefetch-live-final/read-ack',
    'p4-expected-source-logged-final/document-durable-recovery'])
def test_actual_old_seed_checkpoint_and_prompts_are_byte_equivalent(directory):
    old=json.loads((ROOT/'docs/upgrade_2026-10-03'/directory/'verification.json').read_bytes())['actual_PG']
    assert PlanningTaskSeed.model_validate(old['seed']).model_dump(mode='json')==old['seed']
    assert PlanningTaskCheckpoint.model_validate(old['checkpoint']).model_dump(mode='json')==old['checkpoint']
    policy=CasePlanningPolicy.model_validate(old['seed']['plan']['config_snapshot']['policy'])
    prompts=build_stage_prompts(policy.planning_stage_protocol,policy.effect_expected_source_protocol,policy.operation_prefetch_protocol)
    assert prompts.model_dump(mode='json')==old['seed']['stage_prompts']
    assert stage_prompt_version(policy,prompts)==old['seed']['plan']['prompt_version']
    assert 'source_identity_protocol' not in policy.model_dump(mode='json')


def test_full_identity_policy_requires_fixed_versioned_sources():
    with pytest.raises(ValidationError,match='完整摘要来源身份'):
        CasePlanningPolicy(source_identity_protocol=FULL_SOURCE_IDENTITY_PROTOCOL)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_PARSERS')!='1',reason='actual MD/DOCX/PDF parser process/MySQL/PG/RustFS')
@pytest.mark.parametrize('extension',['md','docx','pdf'])
async def test_actual_parsed_originals_share_payload_and_version_but_keep_explicit_old_and_new_ids(tmp_path,extension):
    async with parsing_environment(tmp_path) as (env,parsing,store,_,_):
        store.lease_seconds=30
        project=await env.projects.create(ProjectCreateRequest(project_key='source-'+extension,name='实际来源身份'))
        other=await env.projects.create(ProjectCreateRequest(project_key='other-'+extension,name='隔离项目'))
        path=tmp_path/('original.'+extension)
        if extension=='md':path.write_bytes((ROOT/'docs/upgrade_2026-10-03/p4-operation-prefetch-default-first/requirements.md').read_bytes())
        elif extension=='docx':actual_docx(path)
        else:actual_pdf(path)
        document=await env.docs.upload_document(project_id=project.id,filename=path.name,
            content_base64=base64.b64encode(path.read_bytes()).decode())
        task=await parsing.submit(project.id,document.id,DocumentParseTaskRequest(creation_key='fixed',version_id=document.current_version_id))
        terminal=await parsing.execute(await store.claim(project.id,task.id,worker_id='source-identity'))
        assert terminal.status=='completed',terminal
        snapshot,result=await parsing.read_snapshot(project.id,document.id,terminal.snapshot_id)
        from src.schemas.case_planning import PlanningQuery
        query=PlanningQuery(kind='document',source_id=document.id,max_lines=500)
        old,_=await env.sources.query(project.id,query,20)
        new,_=await env.sources.query(project.id,query,20,identity_protocol=FULL_SOURCE_IDENTITY_PROTOCOL)
        assert old[0].payload==new[0].payload and old[0].version==new[0].version==document.current_version_id
        assert old[0].content_hash==new[0].content_hash
        assert old[0].id==f'document:{document.id}:{new[0].content_hash[:16]}'
        assert new[0].id==f'document:{document.id}:{new[0].content_hash}'
        assert new[0].payload['parse_snapshot']['id']==snapshot.id
        assert result.source_sha256==hashlib.sha256(path.read_bytes()).hexdigest()
        assert (await env.objects.read_object_uri(document.original_asset.storage_uri))['content']==path.read_bytes()
        with pytest.raises(ValueError,match='不属于当前项目'):
            await env.sources.query(other.id,query,20,identity_protocol=FULL_SOURCE_IDENTITY_PROTOCOL)
        with pytest.raises(ValueError,match='身份协议'):
            await env.sources.query(project.id,query,20,identity_protocol='unknown-policy')
        frozen_policy=await env.service.save_policy(project.id,policy(
            effect_expected_source_protocol='case-effect-document-expected-v1',
            source_identity_protocol=FULL_SOURCE_IDENTITY_PROTOCOL))
        model=SequenceModel({'action':'query','summary':'读取实际解析版本',
            'queries':[query.model_dump(mode='json')]},
            {'action':'finish','summary':'当前项目没有正式目标用例','items':[]})
        env.service._models=model
        plan=await env.service.create(project.id,CasePlanningRequest(objective='读取该固定文档并检查正式用例'))
        assert plan.status=='no_match' and len(model.requests)==2,plan.model_dump(mode='json')
        assert plan.prompt_version=='case-planning-source-identity-v8'
        assert plan.config_snapshot==frozen_policy
        captured=next(e for e in plan.evidence if e.kind=='document')
        assert captured==new[0]
        assert await env.service.get(project.id,plan.id)==plan
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT record FROM {env.settings.database.postgres_case_plan_table} WHERE id=%s AND project_id=%s',
                (plan.id,project.id))
            actual_pg=cur.fetchone()['record']
        assert actual_pg==plan.model_dump(mode='json')
        delivered=json.loads(model.requests[1].messages[0]['content'])
        assert next(e for e in delivered['evidence'] if e['kind']=='document')==new[0].model_dump(mode='json')
        if os.getenv('QA_SOURCE_IDENTITY_DIR'):
            out=Path(os.environ['QA_SOURCE_IDENTITY_DIR'])/extension;out.mkdir(parents=True,exist_ok=False)
            (out/path.name).write_bytes(path.read_bytes())
            (out/'verification.json').write_text(json.dumps({'document':document.model_dump(mode='json'),
                'parse_task':terminal.model_dump(mode='json'),'snapshot':snapshot.model_dump(mode='json'),
                'parse_result':result.model_dump(mode='json'),'old':old[0].model_dump(mode='json'),
                'new':new[0].model_dump(mode='json'),'RustFS_original_verified':True,
                'actual_PG_plan':actual_pg,'MySQL_policy':frozen_policy.model_dump(mode='json'),
                'delivered_evidence':delivered['evidence'],'business_HTTP':0,'controlled_model':True,
                'model_calls':len(model.requests),'full_upgrade_complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
