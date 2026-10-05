"""Real failed citations, exact field bindings, full files/HTTP and immutable history."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.application.documents.impact_calls import DocumentImpactCallService
from src.application.documents.impact_case_evidence import fixed_impact_case_evidence, case_evidence_catalog
from src.application.documents.impact_response_protocol import capture_impact_response_protocol, DocumentImpactResponseProtocol
from src.application.documents.impact_sources import impact_record_hash
from src.schemas.document_impact import DocumentImpactSourceSeed, DocumentImpactInputEnvelope
from src.schemas.document_impact_batches import DocumentImpactBatchManifest
from src.schemas.document_impact_calls import DocumentImpactCallInput, DocumentImpactCallRecord, DocumentImpactCallResponse, DocumentImpactInvocationPolicy
from src.schemas.document_impact_grouped import impact_coordination_context
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.schemas.document_impact_grouped import DocumentImpactGroupedResult
from src.schemas.case_management import TestCaseBundle as CaseBundle
from test_document_impact_default_workflow import assert_default_fixture_impacts
from impact_bound_test_support import bound_fixture_response
from test_document_impact_tasks import task_environment, evidence
from test_document_impact_calls import prepared, provider
from test_document_impact_grouped_workflow import grouped_responder
from test_document_impact_workflow import workflow

BASE=Path(__file__).resolve().parents[2]/'docs/upgrade_2026-10-03/p4-default-model-single-attempt-first'


def saved_values():
    seed=DocumentImpactSourceSeed.model_validate_json((BASE/'seed.json').read_bytes())
    reference=next(item for item in json.loads((BASE/'object-references.json').read_text()) if item['name']=='input.json')
    envelope=DocumentImpactInputEnvelope.model_validate_json((BASE/'objects'/f"{reference['sha256']}.object").read_bytes())
    manifest=DocumentImpactBatchManifest.model_validate_json((BASE/'manifest.json').read_bytes())
    payload=DocumentImpactCallInput.model_validate_json((BASE/'logical-request-2.json').read_bytes())
    record=DocumentImpactCallRecord.model_validate(json.loads((BASE/'calls.json').read_text())[1])
    response=DocumentImpactCallResponse.model_validate_json((BASE/'logical-response-2.json').read_bytes())
    return seed,envelope,manifest,payload,record,response


def new_values():
    seed,envelope,manifest,payload,record,response=saved_values()
    seed=seed.model_copy(update={'response_protocol':capture_impact_response_protocol(version='impact-call-v7')})
    context=impact_coordination_context(json.loads(payload.request.messages[-1]['content'])['coordination_context'])
    request=DocumentImpactCallService.build_request(seed,envelope,manifest,payload.selection,coordination_context=context)
    payload=payload.model_copy(update={'request':request})
    # New controlled reply over the actual failed input; never convert the old
    # wire response or replace its stored rejected assessment.
    value=json.loads((BASE/'logical-assessment-2.json').read_text())['assessment']['decision']
    for candidate in value['candidates']:
        for impact in candidate['case_impacts']:
            impact['citations']=[item for item in impact['citations'] if item['path']!=['title']]
    wire=json.loads(bound_fixture_response({'messages':request.messages},value))
    return seed,envelope,manifest,payload,record,response,wire


def assess(values):
    seed,envelope,manifest,payload,record,response,wire=values
    return DocumentImpactResponseProtocol.assess(seed,envelope,manifest,record,payload,
        response.model_copy(update={'text':json.dumps(wire,ensure_ascii=False)}))


def test_actual_wrong_title_citation_and_v6_request_remain_rejected_and_exact():
    seed,envelope,manifest,payload,record,response=saved_values()
    assert seed.response_protocol==capture_impact_response_protocol(version='impact-call-v6')
    context=impact_coordination_context(json.loads(payload.request.messages[-1]['content'])['coordination_context'])
    assert DocumentImpactCallService.build_request(seed,envelope,manifest,payload.selection,coordination_context=context)==payload.request
    check=DocumentImpactResponseProtocol.assess(seed,envelope,manifest,record,payload,response)
    assert check.status=='rejected' and check.validator_version=='impact-response-validator-v2'
    assert any(item.code=='invalid_case_citation' for item in check.issues)
    bundle=next(c for c in envelope.input.cases if c.version.id==payload.selection.unit.id)
    assert 'title' not in bundle.version.content_json and bundle.case.title==bundle.version.content_json['objective']


def test_full_actual_case_fields_bind_paths_values_and_notes_without_model_copying():
    values=new_values();seed,envelope,manifest,payload,_,_,wire=values
    catalog=json.loads(payload.request.messages[-1]['content'])['fixed_case_evidence']
    assert catalog and all('value' not in entry for entry in catalog.values())
    assert not any(entry['path']==['title'] for entry in catalog.values())
    assert any(entry['path']==['steps',7,'expected','days'] for entry in catalog.values())
    assert any(entry['root']=='execution_notes' and entry['path']==[] for entry in catalog.values())
    assert all(isinstance(c,str) for rule in wire['candidates'] for impact in rule['case_impacts'] for c in impact['citations'])
    check=assess(values)
    assert check.status=='unit_finished' and check.validator_version=='impact-response-validator-v3'
    assert check.runtime_versions['identity_binding']=='impact-case-bindings-v1'
    assert check.semantic_status=='pending_review' and check.deployment_status=='unknown'
    assert any(c.path==['steps',7,'expected','days'] and c.value==7 for rule in check.decision.candidates
        for impact in rule.case_impacts for c in impact.citations)
    assert 'Other cases may be read as supplementary evidence' in seed.response_protocol.instruction


@pytest.mark.parametrize('mutation',['unknown','foreign_case','catalog_path','catalog_root','case_body','model_fields'])
def test_unknown_foreign_or_forged_field_citations_cannot_change_fixed_facts(mutation):
    values=new_values();seed,envelope,manifest,payload,record,response,wire=values
    impact=wire['candidates'][0]['case_impacts'][0]
    body=json.loads(payload.request.messages[-1]['content']);ref=impact['citations'][0]
    expected='invalid_fixed_reference'
    if mutation=='unknown':impact['citations']=['F999999']
    elif mutation=='foreign_case':impact['case_ref']='C2'
    elif mutation=='catalog_path':body['fixed_case_evidence'][ref]['path']=['title']
    elif mutation=='catalog_root':body['fixed_case_evidence'][ref]['root']='case'
    elif mutation=='case_body':
        body['primary']['case']['version']['content_json']['objective']='伪造'
        # A matching forged index is still not a fixed complete version.
    else:
        impact['citations']=[{'root':'content_json','path':['objective'],'value':'猜测'}]
        expected='invalid_protocol'
    payload.request.messages[-1]['content']=json.dumps(body,ensure_ascii=False)
    result=assess(values)
    assert result.status=='rejected' and expected in [item.code for item in result.issues]


def test_arbitrary_paths_false_zero_null_and_empty_values_remain_distinct():
    _,envelope,manifest,payload,_,_=saved_values()
    bundle=envelope.input.cases[0].model_copy(deep=True)
    bundle.version.content_json['任意.键/']={'0':[False,0,None,'',{},[]]}
    fixed=manifest.cases[0].model_copy(update={'version_record_sha256':impact_record_hash(bundle.version)})
    manifest=manifest.model_copy(update={'cases':[fixed,*manifest.cases[1:]]})
    body={'primary':{'case':bundle.model_dump(mode='json')},'additional_fixed_reads':[]}
    fields=fixed_impact_case_evidence(manifest,[body])
    values=[value for value in fields.values() if value['path'][:2]==['任意.键/','0'] and len(value['path'])==3]
    assert [entry['path'] for entry in values]==[['任意.键/','0',index] for index in range(6)]
    assert [type(entry['value']) for entry in values]==[bool,int,type(None),str,dict,list]
    assert [entry['value'] for entry in values]==[False,0,None,'',{},[]]
    assert fixed_impact_case_evidence(manifest,[body,body])==fields
    other={'primary':{'case':envelope.input.cases[1].model_dump(mode='json')},'additional_fixed_reads':[]}
    extended=fixed_impact_case_evidence(manifest,[body,other])
    assert len(extended)>len(fields) and all(extended[key]==value for key,value in fields.items())
    assert all('value' not in entry for entry in case_evidence_catalog(extended).values())


def test_new_protocol_never_reinterprets_old_failed_wire_response():
    seed,envelope,manifest,payload,record,response,_=new_values()
    result=DocumentImpactResponseProtocol.assess(seed,envelope,manifest,record,payload,response)
    assert result.status=='rejected' and result.issues[0].code=='invalid_protocol'


def test_first_v7_saved_requests_keep_original_frozen_guidance_and_hashes():
    directory=BASE.parent/'p4-default-model-case-v7-first'
    seed=DocumentImpactSourceSeed.model_validate_json((directory/'seed.json').read_bytes())
    reference=next(item for item in json.loads((directory/'object-references.json').read_text()) if item['name']=='input.json')
    envelope=DocumentImpactInputEnvelope.model_validate_json((directory/'objects'/f"{reference['sha256']}.object").read_bytes())
    manifest=DocumentImpactBatchManifest.model_validate_json((directory/'manifest.json').read_bytes())
    for item in json.loads((directory/'object-references.json').read_text()):
        if item['name']!='request.json':continue
        raw=(directory/'objects'/f"{item['sha256']}.object").read_bytes()
        assert hashlib.sha256(raw).hexdigest()==item['sha256']
        payload=DocumentImpactCallInput.model_validate_json(raw)
        context=impact_coordination_context(json.loads(payload.request.messages[-1]['content'])['coordination_context'])
        assert DocumentImpactCallService.build_request(seed,envelope,manifest,payload.selection,
            coordination_context=context)==payload.request


def test_first_v7_actual_fixture_judgments_and_strict_failure_are_both_retained():
    directory=BASE.parent/'p4-default-model-case-v7-first'
    result=json.loads((directory/'result.json').read_text())
    cases=json.loads((directory/'cases.json').read_text())
    identities={case['version']['id']:case['case']['case_key'] for case in cases}
    expected={'retention':{'modified':'major','removed':'none','added':'none'},
        'promotion':{'modified':'none','removed':'major','added':'none'},
        'unauth':{'modified':'none','removed':'none','added':'none'}}
    seen=0
    for unit in result['units']:
        if unit['unit']['kind']!='case':continue
        key=identities[unit['unit']['id']]
        assert {candidate['kind'] for candidate in unit['candidates']}==set(expected[key])
        for candidate in unit['candidates']:
            impact=next(value for value in candidate['case_impacts'] if value['version_id']==unit['unit']['id'])
            assert impact['level']==expected[key][candidate['kind']]
            seen+=1
    assert seen==9 and len(result['units'])==11
    assert all(unit['status']=='analyzed' for unit in result['units'])
    assert any(unit['unresolved'] for unit in result['units'])
    assert result['semantic_status']=='pending_review' and result['deployment_status']=='unknown'


def test_actual_successful_v7_stage_matches_strengthened_business_oracle_without_new_http():
    directory=BASE.parent/'p4-default-model-case-v7-stage'
    result=DocumentImpactGroupedResult.model_validate_json((directory/'result.json').read_bytes())
    cases=[CaseBundle.model_validate(value) for value in json.loads((directory/'cases.json').read_text())]
    assert all(unit.status=='analyzed' and not unit.unresolved and not unit.reason_codes for unit in result.units)
    assert_default_fixture_impacts(result,cases)
    assert result.basis_limitations==['difference_warning:extracted_text_positions']
    assert result.status=='partial' and result.semantic_status=='pending_review' and result.deployment_status=='unknown'


LIVE=pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_TASKS')!='1',reason='requires real files/MySQL/PG/RustFS/HTTP')


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize('extension',['md','docx','pdf'])
@pytest.mark.parametrize('adapter',[False,True])
async def test_actual_complete_case_fields_history_and_two_http_calls(tmp_path,monkeypatch,extension,adapter):
    async with task_environment(tmp_path,extension,protocol_version='impact-call-v7') as values:
        env,project,doc,paths,views,_,_,_,case,store,tasks=values
        holder={}
        with provider(responder=grouped_responder(holder,views,case)) as (endpoint,sends,_,_):
            claim,manifest,_,calls=await prepared(values,endpoint,adapter=adapter,
                policy=DocumentImpactInvocationPolicy(transport_policy='single_attempt'))
            holder.update(first=calls.units(manifest)[0],manifest=manifest)
            runner=workflow(env,tasks,store);saved=await runner.execute(claim)
            snapshot,result=await runner.read_result(project.id,doc.id,saved.id)
            assert len(sends)==2 and all(unit.status=='analyzed' for unit in result.units)
            assert len(holder['case_payloads'])==1 and holder['case_payloads'][0]['fixed_case_evidence']
            citations=result.units[-1].candidates[0].case_impacts[0].citations
            assert any(type(c.value) is bool and c.value is False for c in citations)
            assert any(type(c.value) is int and c.value==0 for c in citations)
            assert any(c.value is None for c in citations)
            assert all(unit.final_check.validator_version=='impact-response-validator-v3' for unit in result.units)
            restarted=PostgresDocumentImpactStore(env.settings)
            assert await restarted.claim(project.id,saved.id,worker_id='terminal') is None
            async def forbidden(*args,**kwargs):raise AssertionError('terminal field history cannot send or rebuild')
            with monkeypatch.context() as patch:
                patch.setattr(env.impact_runtime,'invoke',forbidden)
                patch.setattr(env.objects,'store_uploaded_bytes',forbidden)
                assert await workflow(env,tasks,restarted).read_result(project.id,doc.id,saved.id)==(snapshot,result)
            evidence(f'case-anchors/{extension}-{adapter}/verification.json',snapshot=snapshot.model_dump(mode='json'),
                result=result.model_dump(mode='json'),physical_requests=sends,actual_http_calls=len(sends),
                full_case_payload=holder['case_payloads'][0],default_model_judgement=False)
            if directory:=os.getenv('QA_IMPACT_TASK_EVIDENCE_DIR'):
                target=Path(directory)/'case-anchors'/f'{extension}-{adapter}'
                for path in paths:(target/path.name).write_bytes(path.read_bytes())
