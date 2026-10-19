"""Use saved actual business declarations, not hand-built ideal relations."""
from copy import deepcopy
import json
import base64
import os
from pathlib import Path

import pytest
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog, bind_evidence_references
from src.application.test_cases.planning_stages import merge_evidence_decision, seal_logical_decision, build_stage_prompts
from src.application.runtime.case_effect_evidence import validate_effect_declarations
from src.schemas.case_planning import CasePlanRecord
from src.schemas.case_planning_references import ReferencedEvidenceDecision, REFERENCE_PROTOCOL, REFERENCE_PROTOCOL_V2
from src.schemas.case_planning_task import PlanningTaskSeed, PlanningTaskCheckpoint

ROOT = Path(__file__).resolve().parents[2]


def reference_wire(value, catalog, reference_protocol=REFERENCE_PROTOCOL):
    """Test fixture adapter; production never chooses the model's assertions."""
    value = deepcopy(value)
    ids = {entry["evidence_id"]: ref for ref, entry in catalog["evidence"].items()}
    for binding in value.get("bindings", []):
        binding["evidence_ids"] = [ids[ref] for ref in binding["evidence_ids"]]
        for declaration in binding["effect_declarations"] or []:
            for check in declaration["checks"] + declaration["select_when"]:
                source = check.get("expected_source")
                if source is None:
                    continue
                ref = ids[source["evidence_id"]]
                text_ref = next((key for key, anchor in catalog["texts"].items() if
                    anchor["evidence_ref"] == ref and anchor["text"] == source.get("excerpt")), None)
                check["expected_source"] = ({"text_ref": text_ref, "literal": source["literal"], "encoding": source["encoding"]}
                    if text_ref else {"evidence_ref": ref, **{key: source[key] for key in
                        ("payload_path_tokens", "excerpt", "literal", "encoding") if source.get(key) is not None}})
                if reference_protocol == REFERENCE_PROTOCOL_V2:
                    reference = check["expected_source"]
                    alias = reference.pop("text_ref", None) or reference.pop("evidence_ref")
                    check["expected_source"] = {"reference": alias, **reference}
    return value


def actual():
    raw = json.loads((ROOT / "docs/upgrade_2026-10-03/p4-linked-source-identity-final/relations/linked-selection-identity-prefetch-document-internal-all/verification.json").read_bytes())
    plan = CasePlanRecord.model_validate(raw["frozen_plan"])
    canonical = plan.rounds[-1]["decision"]
    catalog = evidence_reference_catalog(plan.evidence)
    return plan, canonical, catalog


def test_actual_wire_restores_the_original_business_decision_and_checks():
    plan, canonical, catalog = actual()
    wire = reference_wire(canonical, catalog)
    assert '"text_ref"' in json.dumps(wire)
    restored = bind_evidence_references(ReferencedEvidenceDecision.model_validate(wire), catalog, plan.evidence)
    assert restored.model_dump(mode="json") == canonical
    logic = plan.rounds[0]["decision"].copy()
    selections = logic.pop("operation_selections")
    from src.schemas.case_planning import PlanningDecisionV3
    from src.schemas.case_planning_stages import PlanningOperationSelection
    stages = seal_logical_decision(PlanningDecisionV3.model_validate(logic), [PlanningOperationSelection.model_validate(x) for x in selections])
    merged = merge_evidence_decision(stages, restored, plan.evidence)
    validate_effect_declarations(merged, "case-effect-evidence-v2", plan.evidence, "case-effect-coverage-v2", "case-effect-document-expected-v1")
    assert next(e.payload['content'] for e in plan.evidence if e.kind == 'case')['steps'][-1]['business'] == {'false': False, 'zero': 0, 'nullable': None}


@pytest.mark.parametrize("mutation", ["unknown-e", "unknown-t", "tool-as-document", "catalog-text", "catalog-hash", "wrong-literal"])
def test_rejects_invalid_reference_from_actual_business_response(mutation):
    plan, canonical, catalog = actual()
    wire = reference_wire(canonical, catalog)
    check = wire["bindings"][0]["effect_declarations"][0]["checks"][0]
    if mutation == "unknown-e":wire['bindings'][0]['evidence_ids'] = ['E999']
    elif mutation == "unknown-t":check['expected_source']['text_ref'] = 'T999'
    elif mutation == "tool-as-document":
        ref = next(k for k, v in catalog['evidence'].items() if v['kind'] == 'tool')
        check['expected_source'] = {'evidence_ref': ref, 'payload_path_tokens': []}
    elif mutation == "catalog-text":catalog['texts'][next(iter(catalog['texts']))]['text'] += 'changed'
    elif mutation == "catalog-hash":catalog['sha256'] = '0' * 64
    else:check['expected_source']['literal'] = '9999'
    with pytest.raises(ValueError):
        restored = bind_evidence_references(ReferencedEvidenceDecision.model_validate(wire), catalog, plan.evidence)
        logical = deepcopy(plan.decision.model_dump(mode='json'))
        for binding in restored.bindings:
            next(x for x in logical['items'] if x['key'] == binding.item_key)['effect_declarations'] = [x.model_dump(mode='json') for x in binding.effect_declarations]
        from src.schemas.case_planning import PlanningDecisionV3
        validate_effect_declarations(PlanningDecisionV3.model_validate(logical), 'case-effect-evidence-v2', plan.evidence, 'case-effect-coverage-v2', 'case-effect-document-expected-v1')


def test_v10_seed_checkpoint_and_prompts_are_unchanged():
    raw = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-linked-source-default-fixed/PG-planning.json').read_bytes())
    seed=PlanningTaskSeed.model_validate(raw['seed'])
    assert seed.model_dump(mode='json') == raw['seed']
    assert PlanningTaskCheckpoint.model_validate(raw['checkpoint']).model_dump(mode='json') == raw['checkpoint']
    p=seed.plan.config_snapshot.policy
    assert build_stage_prompts(p.planning_stage_protocol,p.effect_expected_source_protocol,p.operation_prefetch_protocol,p.source_identity_protocol,p.linked_source_protocol) == seed.stage_prompts
    assert REFERENCE_PROTOCOL not in json.dumps(seed.model_dump(mode='json'))


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_CASE_PLANNING_TASKS') != '1', reason='actual MySQL/PG/RustFS planning task')
@pytest.mark.parametrize('fault', ['none', 'response-ack', 'unknown-reference'])
async def test_actual_durable_references_survive_response_commit_and_current_changes(tmp_path, monkeypatch, fault):
    from test_case_planning_tasks import tasks_environment, delivery, expire
    from test_planning_linked_sources import linked_case, linked_policy
    from test_case_effect_relations import document_declaration
    from src.schemas.project import ProjectCreateRequest
    from src.schemas.case_planning import CasePlanningPolicy
    from src.schemas.case_planning_task import CasePlanningTaskRequest
    from src.schemas.api_docs import ApiDocVersionUploadRequest
    from src.runtime.planning_task_handler import PlanningTaskHandler
    from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
    from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
    from src.infrastructure.postgres_runtime import postgres_connect
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='references-'+fault, name='固定证据短引用'))
        raw = (ROOT/'docs/upgrade_2026-10-03/p4-linked-source-default-fixed/requirements.md').read_bytes()
        source_path = tmp_path/'requirements.md'; source_path.write_bytes(raw)
        document = await env.docs.upload_document(project_id=project.id,filename=source_path.name,content_base64=base64.b64encode(raw).decode())
        case, content = await linked_case(env, project, [{'source_type':'api_doc','source_id':document.id}])
        policy = linked_policy().model_copy(update={'evidence_reference_protocol':REFERENCE_PROTOCOL})
        await env.service.save_policy(project.id,policy)
        task = await env.tasks.submit(project.id,CasePlanningTaskRequest(creation_key='original',objective=content['objective'],candidate_case_ids=[case.case.id]))
        def respond(context):
            assert next(e['payload']['content'] for e in context['evidence'] if e['kind']=='case') == content
            document_evidence = next(e for e in context['evidence'] if e['kind']=='document')
            assert document_evidence['version']==document.current_version_id
            if context['planning_stage']=='logic':
                return {'action':'finish','summary':'完整正式项自行产生及清理','items':[{'key':'target','kind':'target','effect_schema':3,
                    'case_id':case.case.id,'case_version_id':case.version.id,'reason':'完整业务用例', 'cleanup_sources':['target'],
                    'evidence_ids':[e['id'] for e in context['evidence']]}],
                    'operation_selections':[{'item_key':'target','tool_key':'api-test-runner','operation':'execute_task','evidence_ids':[document_evidence['id']]}]}
            tool = next(e for e in context['evidence'] if e['kind']=='tool')
            value = {'action':'finish','summary':'实际固定原文证据','bindings':[{'item_key':'target','evidence_ids':[tool['id'],document_evidence['id']],
                'effect_declarations':[document_declaration('produced','target',document_evidence),document_declaration('cleanup','target',document_evidence)]}]}
            wire=reference_wire(value,context['evidence_reference_catalog'])
            if fault=='unknown-reference' and not context['validation_feedback']:wire['bindings'][0]['evidence_ids']=['E999']
            return wire
        env.model.replies=[respond]*3
        original=env.jobs.save_checkpoint;claims=[]
        async def lose_response(claim,checkpoint,*,message):
            await original(claim,checkpoint,message=message)
            if checkpoint.phase=='response' and checkpoint.stages.stage=='effects':
                claims.append(claim)
                raise RuntimeError('Actual saved response acknowledgement lost')
        before=None
        if fault=='response-ack':
            monkeypatch.setattr(env.jobs,'save_checkpoint',lose_response)
            with pytest.raises(PlanningCheckpointUnavailable):
                await PlanningTaskHandler(env.service,env.jobs,worker_id='first')(delivery(task))
            before=await env.jobs.get(project.id,task.id)
            with postgres_connect(env.settings) as conn,conn.cursor() as cur:
                cur.execute(f'SELECT checkpoint FROM {env.jobs._table} WHERE id=%s',(task.id,))
                fixed_checkpoint=cur.fetchone()['checkpoint']
            changed=PlanningTaskCheckpoint.model_validate(fixed_checkpoint)
            changed.plan.rounds[-1]['evidence_reference_catalog']['sha256']='0'*64
            with pytest.raises(ValueError,match='短引用目录'):
                await original(claims[0],changed,message='不可改写本次调用目录')
            await env.service.save_policy(project.id,CasePlanningPolicy(max_rounds=1))
            await env.docs.upload_version(document.id,ApiDocVersionUploadRequest(project_id=project.id,request_id='later',
                expected_parent_version_id=document.current_version_id,filename='new.md',content_base64=base64.b64encode(b'NEW-DOCUMENT').decode()))
            monkeypatch.setattr(env.jobs,'save_checkpoint',original)
            expire(env,task)
        restored=PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service,restored,worker_id='resume')(delivery(task))
        terminal=await restored.get(project.id,task.id)
        plan=await env.service.get(project.id,terminal.plan_id)
        assert terminal.status=='completed' and plan.status=='planned',plan.summary
        assert plan.prompt_version=='case-planning-evidence-references-v11+linked-navigation-v1'
        assert len(env.model.requests)==(3 if fault=='unknown-reference' else 2)
        assert plan.rounds[-1]['canonical_evidence_decision']['bindings'][0]['effect_declarations']
        assert ReferencedEvidenceDecision.model_validate_json(plan.rounds[-1]['wire_response_text']).model_dump(mode='json') == plan.rounds[-1]['decision']
        if before:assert terminal.attempt_no==2 and terminal.deadline_at==before.deadline_at
        count=len(env.model.requests)
        await PlanningTaskHandler(env.service,restored,worker_id='terminal')(delivery(task))
        assert len(env.model.requests)==count
        with postgres_connect(env.settings) as conn,conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status,attempt_no,deadline_at FROM {restored._table} WHERE id=%s',(task.id,))
            saved=dict(cur.fetchone())
        saved['deadline_at']=saved['deadline_at'].isoformat()
        if os.getenv('QA_EVIDENCE_REFERENCE_DIR'):
            out=Path(os.environ['QA_EVIDENCE_REFERENCE_DIR'])/fault;out.mkdir(parents=True,exist_ok=False)
            (out/'requirements.md').write_bytes(raw)
            (out/'verification.json').write_text(json.dumps({'actual_PG':saved,'actual_requests':[x.model_dump(mode='json') for x in env.model.requests],
                'actual_MySQL':(await env.service.get_policy(project.id)).model_dump(mode='json'), 'controlled_model':True,'business_HTTP':0,
                'raw_response_catalog_immutable':fault=='response-ack'},ensure_ascii=False,indent=2),encoding='utf-8')
