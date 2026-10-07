"""Actual linked originals, immutable parser basis and durable first reads."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_case_planning import finish, SequenceModel
from test_case_planning_tasks import tasks_environment, delivery, expire, live
from test_case_effect_relations import policy
from test_document_parse_tasks import parsing_environment
from test_document_parsers import actual_docx, actual_pdf
from src.application.test_cases.planning_stages import build_stage_prompts
from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
from src.schemas.case_planning import CasePlanningPolicy, PlanningQuery, CasePlanningRequest
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskSeed, PlanningTaskCheckpoint
from src.schemas.case_planning_sources import LINKED_SOURCE_PROTOCOL
from src.schemas.case_management import TestCaseDraftCreateRequest as DraftRequest
from src.schemas.document_parsing import DocumentParseTaskRequest
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.project import ProjectCreateRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore

ROOT=Path(__file__).resolve().parents[2]


def linked_policy():
    return policy(effect_expected_source_protocol='case-effect-document-expected-v1',
        source_identity_protocol='case-planning-source-full-hash-v1',
        operation_prefetch_protocol='case-planning-item-operation-prefetch-v2',linked_source_protocol=LINKED_SOURCE_PROTOCOL)


async def linked_case(env, project, refs, key='linked'):
    content=json.loads((ROOT/'docs/upgrade_2026-10-03/p4-item-operation-default-first/full-case.json').read_bytes())
    bundle=await env.cases.create_draft(project_id=project.id,payload=DraftRequest(case_key=key,title=content['objective'],
        case_type='json',mode_key='api_testing',content_json=content,execution_notes='读取全部八步及来源，不使用生成模板',
        source_refs=refs,model_key='manual',prompt_version='manual-v1',skill_versions={'manual':'v1'},generation_metadata={}))
    await env.cases.submit_review(bundle.case.id);await env.cases.activate(bundle.case.id)
    return bundle,content


@pytest.mark.parametrize('directory',['p4-item-operation-default-first','p4-source-identity-default-first','p4-operation-prefetch-default-first'])
def test_actual_old_seed_checkpoint_prompt_unchanged(directory):
    old=json.loads((ROOT/'docs/upgrade_2026-10-03'/directory/'PG-planning.json').read_bytes())
    assert PlanningTaskSeed.model_validate(old['seed']).model_dump(mode='json')==old['seed']
    assert PlanningTaskCheckpoint.model_validate(old['checkpoint']).model_dump(mode='json')==old['checkpoint']
    p=CasePlanningPolicy.model_validate(old['seed']['plan']['config_snapshot']['policy'])
    assert build_stage_prompts(p.planning_stage_protocol,p.effect_expected_source_protocol,p.operation_prefetch_protocol,
        p.source_identity_protocol,p.linked_source_protocol).model_dump(mode='json')==old['seed']['stage_prompts']
    assert 'linked_source_protocol' not in p.model_dump(mode='json')


@pytest.mark.asyncio
@live
@pytest.mark.parametrize('fault',['none','queue-ack','authored-queue-ack','capture-ack','read-ack','pagination','duplicate','two-cases','foreign','wrong-version','legacy-version','wrong-asset','wrong-hash','manual'])
async def test_actual_linked_read_before_first_model_and_original_checkpoint(tmp_path,monkeypatch,fault):
    async with tasks_environment(tmp_path) as env:
        project=await env.projects.create(ProjectCreateRequest(project_key='linked-'+fault,name='明确关联资料'))
        foreign=await env.projects.create(ProjectCreateRequest(project_key='foreign',name='隔离来源'))
        raw=(ROOT/'docs/upgrade_2026-10-03/p4-item-operation-default-first/requirements.md').read_bytes()
        if fault=='pagination':raw=b'\n'.join([('line-'+str(i)).encode() for i in range(1,506)])+b'\nLAST-FIXED-LINE'
        document=await env.docs.upload_document(project_id=foreign.id if fault=='foreign' else project.id,
            filename='requirements.md',content_base64=base64.b64encode(raw).decode())
        source={'source_type':'api_doc','source_id':document.id}
        if fault=='wrong-version':source['version']=str(uuid4())
        if fault=='legacy-version':source['version']=document.updated_at.isoformat()
        if fault=='wrong-asset':source.update(version=document.current_version_id,metadata={'content_asset_id':str(uuid4())})
        if fault=='authored-queue-ack':source.update(version=document.current_version_id,metadata={'content_asset_id':document.original_asset.id,'content_sha256':document.original_asset.sha256})
        if fault=='wrong-hash':source.update(version=document.current_version_id,metadata={'content_asset_id':document.original_asset.id,'content_sha256':'0'*64})
        if fault=='manual':source={'source_type':'manual','source_id':'explicit-manual'}
        bundle,content=await linked_case(env,project,[source,deepcopy(source)] if fault=='duplicate' else [source])
        candidates=[bundle.case.id]
        if fault=='two-cases':
            other,_=await linked_case(env,project,[source],key='other-linked');candidates.append(other.case.id)
        await env.service.save_policy(project.id,linked_policy())
        task=await env.tasks.submit(project.id,CasePlanningTaskRequest(creation_key='fixed',objective=content['objective'],candidate_case_ids=candidates))
        bad=fault in {'foreign','wrong-version','legacy-version','wrong-asset','wrong-hash','manual'}
        def respond(context):
            reads=context['linked_source_reads'];assert reads['next_index']==1 and len(reads['entries'])==1
            entry=reads['entries'][0]
            assert len(entry['case_evidence_ids'])==(2 if fault=='two-cases' else 1)
            assert next(e['payload']['content'] for e in context['evidence'] if e['kind']=='case')==content
            docs=[e for e in context['evidence'] if e['kind']=='document']
            if bad:
                assert entry['error'] and not entry['read'] and docs==[]
                return finish()
            assert entry['read'] and docs
            if fault=='pagination' and len(docs)==1:
                assert docs[0]['payload']['has_more'] and docs[0]['payload']['end_line']==500
                return {'action':'query','summary':'读取固定来源剩余行','queries':[{'kind':'document','source_id':document.id,'start_line':501,'max_lines':500}]}
            if fault=='pagination':assert docs[-1]['payload']['content'].endswith('LAST-FIXED-LINE') and not docs[-1]['payload']['has_more']
            else:assert docs[0]['payload']['content']=='\n'.join(raw.decode().splitlines())
            return finish()
        env.model.replies=[respond]*2
        original=env.jobs.save_checkpoint;lost=[]
        async def lose_ack(claim,checkpoint,*,message):
            await original(claim,checkpoint,message=message)
            p=checkpoint.linked_sources
            if checkpoint.phase=='sources' and p is not None:
                target=(fault in {'queue-ack','authored-queue-ack'} and p.entries[0].basis is None and p.next_index==0
                    or fault=='capture-ack' and p.entries[0].basis is not None and p.next_index==0
                    or fault=='read-ack' and p.next_index==1)
                if target:lost.append(claim);raise RuntimeError('Actual committed source checkpoint acknowledgement lost')
        before=None
        fixed_write_rejections=[]
        if fault in {'queue-ack','authored-queue-ack','capture-ack','read-ack'}:
            monkeypatch.setattr(env.jobs,'save_checkpoint',lose_ack)
            with pytest.raises(PlanningCheckpointUnavailable):await PlanningTaskHandler(env.service,env.jobs,worker_id='first')(delivery(task))
            before=await env.jobs.get(project.id,task.id);assert len(env.model.requests)==0
            if fault=='capture-ack':
                with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                    cur.execute(f'SELECT checkpoint FROM {env.jobs._table} WHERE id=%s',(task.id,));fixed=cur.fetchone()['checkpoint']
                changed=PlanningTaskCheckpoint.model_validate(fixed)
                changed.linked_sources.entries[0].basis.role='version_linked_source'
                with pytest.raises(ValueError,match='固定版本'):
                    await original(lost[0],changed,message='不能改写固定来源')
                missing=PlanningTaskCheckpoint.model_validate(fixed);missing.linked_sources=None
                with pytest.raises(ValueError,match='不能丢弃'):
                    await original(lost[0],missing,message='不能丢弃固定来源')
                with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                    cur.execute(f'SELECT checkpoint FROM {env.jobs._table} WHERE id=%s',(task.id,));assert cur.fetchone()['checkpoint']==fixed
                fixed_write_rejections=['changed_basis','missing_progress']
            await env.docs.upload_version(document.id,ApiDocVersionUploadRequest(project_id=project.id,request_id='later',
                expected_parent_version_id=document.current_version_id,filename='requirements.md',content_base64=base64.b64encode(b'NEW-V2-DO-NOT-MIX').decode()))
            if fault=='queue-ack':
                # No capture was committed yet: an ID-only link deliberately
                # captures V2. The original file is not an authored assertion.
                raw=b'NEW-V2-DO-NOT-MIX'
            elif fault!='authored-queue-ack':
                def forbid(*a,**k):raise AssertionError('Do not recapture a committed document basis')
                monkeypatch.setattr(env.sources,'capture_document_basis',forbid)
            await env.service.save_policy(project.id,CasePlanningPolicy(max_rounds=1))
            monkeypatch.setattr(env.jobs,'save_checkpoint',original)
            expire(env,task)
        restored=PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service,restored,worker_id='continued')(delivery(task))
        terminal=await restored.get(project.id,task.id)
        plan=await env.service.get(project.id,terminal.plan_id)
        assert terminal.status=='completed' and plan.status=='no_match',plan.summary
        assert len(env.model.requests)==(2 if fault=='pagination' else 1) and plan.prompt_version=='case-planning-linked-documents-v10'
        if before:assert terminal.attempt_no==2 and terminal.deadline_at==before.deadline_at
        await PlanningTaskHandler(env.service,restored,worker_id='terminal')(delivery(task))
        assert len(env.model.requests)==(2 if fault=='pagination' else 1)
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status,attempt_no,deadline_at FROM {restored._table} WHERE id=%s',(task.id,));saved=dict(cur.fetchone())
        saved['deadline_at']=saved['deadline_at'].isoformat()
        if os.getenv('QA_LINKED_SOURCE_DIR'):
            out=Path(os.environ['QA_LINKED_SOURCE_DIR'])/fault;out.mkdir(parents=True,exist_ok=False)
            (out/'requirements.md').write_bytes(raw)
            (out/'verification.json').write_text(json.dumps({'actual_PG':saved,'actual_requests':[x.model_dump(mode='json') for x in env.model.requests],
                'actual_current_MySQL':(await env.service.get_policy(project.id)).model_dump(mode='json'),
                'original_document':document.model_dump(mode='json'),'controlled_model':True,'business_HTTP':0,
                'actual_SQL_rejections':fixed_write_rejections},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_PARSERS')!='1',reason='actual originals/isolated parser processes/MySQL/PG/RustFS')
@pytest.mark.parametrize('extension',['md','docx','pdf'])
async def test_actual_authored_parser_basis_survives_new_version_and_reparse(tmp_path,monkeypatch,extension):
    async with parsing_environment(tmp_path) as (env,parsing,store,configs,outbox):
        project=await env.projects.create(ProjectCreateRequest(project_key='fixed-'+extension,name='当时解析依据'))
        path=tmp_path/('original.'+extension)
        if extension=='md':path.write_bytes(b'# Original\nFalse 0 null LAST-ORIGINAL')
        elif extension=='docx':actual_docx(path)
        else:actual_pdf(path)
        document=await env.docs.upload_document(project_id=project.id,filename=path.name,content_base64=base64.b64encode(path.read_bytes()).decode())
        task=await parsing.submit(project.id,document.id,DocumentParseTaskRequest(creation_key='first',version_id=document.current_version_id))
        terminal=await parsing.execute(await store.claim(project.id,task.id,worker_id='original'))
        assert terminal.status=='completed'
        snapshot,result=await parsing.read_snapshot(project.id,document.id,terminal.snapshot_id)
        ref={'source_type':'api_doc','source_id':document.id,'version':document.current_version_id,
            'metadata':{'content_asset_id':document.original_asset.id,'content_sha256':hashlib.sha256(result.text.encode()).hexdigest(),
                'parse_snapshot':snapshot.model_dump(mode='json')}}
        basis=await env.docs.capture_content_basis(project.id,ref)
        assert basis.role=='authored_snapshot'
        later=await parsing.submit(project.id,document.id,DocumentParseTaskRequest(creation_key='reparse',version_id=document.current_version_id))
        reparsed=await parsing.execute(await store.claim(project.id,later.id,worker_id='reparse'));assert reparsed.status=='completed'
        current_basis=await env.docs.capture_content_basis(project.id,{'source_type':'api_doc','source_id':document.id})
        assert current_basis.role=='current_linked_source' and current_basis.parse_snapshot.id==reparsed.snapshot_id
        assert 'authored_version_not_recorded' in current_basis.warnings
        updated=await env.docs.upload_version(document.id,ApiDocVersionUploadRequest(project_id=project.id,request_id='v2',expected_parent_version_id=document.current_version_id,
            filename='new.md',content_base64=base64.b64encode(b'# Current\nNEW-CURRENT-V2').decode()))
        def forbid(*a,**k):raise AssertionError('Fixed read must never select latest parser/current text')
        monkeypatch.setattr(parsing,'latest_snapshot',forbid);monkeypatch.setattr(parsing,'list_snapshots',forbid)
        monkeypatch.setattr(env.docs,'read_document_content',forbid);monkeypatch.setattr(env.docs,'read_version_content',forbid)
        read=await env.docs.read_content_basis(basis)
        current_read=await env.docs.read_content_basis(current_basis)
        assert current_read['parse_snapshot']['id']==reparsed.snapshot_id and current_read['document']['current_version_id']==document.current_version_id
        assert read['content']==result.text and read['parse_snapshot']['id']==snapshot.id!=reparsed.snapshot_id
        assert read['document']['current_version_id']==document.current_version_id!=updated.document.current_version_id
        evidence,_=await env.sources.query(project.id,PlanningQuery(kind='document',source_id=document.id,max_lines=500),20,
            identity_protocol='case-planning-source-full-hash-v1',fixed_documents={document.id:[basis]})
        assert evidence[0].version==document.current_version_id and evidence[0].payload['parse_snapshot']['id']==snapshot.id
        with pytest.raises(ValueError,match='多个固定'):
            await env.sources.query(project.id,PlanningQuery(kind='document',source_id=document.id),20,fixed_documents={document.id:[basis,basis]})
        if extension!='md':
            with pytest.raises(ValueError,match='历史二进制'):
                await env.docs.capture_content_basis(project.id,{'source_type':'api_doc','source_id':document.id,'version':document.current_version_id})
        assert (await env.objects.read_object_uri(document.original_asset.storage_uri))['content']==path.read_bytes()
        bundle,content=await linked_case(env,project,[ref])
        await env.service.save_policy(project.id,linked_policy())
        def respond(context):
            assert context['round']==1 and context['linked_source_reads']['next_index']==1
            delivered=next(e for e in context['evidence'] if e['kind']=='document')
            assert delivered['version']==document.current_version_id and delivered['payload']['parse_snapshot']['id']==snapshot.id
            assert next(e['payload']['content'] for e in context['evidence'] if e['kind']=='case')==content
            return finish()
        model=SequenceModel(respond);env.service._models=model
        plan=await env.service.create(project.id,CasePlanningRequest(objective='读取完整用例及当时来源',candidate_case_ids=[bundle.case.id]))
        assert plan.status=='no_match' and len(model.requests)==1 and plan.prompt_version=='case-planning-linked-documents-v10'
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT record FROM {env.settings.database.postgres_case_plan_table} WHERE id=%s',(plan.id,));actual_plan=cur.fetchone()['record']
        assert actual_plan==plan.model_dump(mode='json')
        if os.getenv('QA_LINKED_SOURCE_FORMAT_DIR'):
            out=Path(os.environ['QA_LINKED_SOURCE_FORMAT_DIR'])/extension;out.mkdir(parents=True,exist_ok=False)
            (out/path.name).write_bytes(path.read_bytes())
            (out/'verification.json').write_text(json.dumps({'basis':basis.model_dump(mode='json'),'fixed_read':read,
                'original_document':document.model_dump(mode='json'),'original_parse':result.model_dump(mode='json'),
                'reparse':reparsed.model_dump(mode='json'),'new_version':updated.model_dump(mode='json'),
                'current_basis':current_basis.model_dump(mode='json'),'current_fixed_read':current_read,
                'actual_PG_plan':actual_plan,'model_calls':len(model.requests),'controlled_model':True,'business_HTTP':0,
                'actual_evidence':evidence[0].model_dump(mode='json'),'RustFS_original_verified':True},ensure_ascii=False,indent=2),encoding='utf-8')
