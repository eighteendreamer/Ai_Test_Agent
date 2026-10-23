"""References replay the actual default document, never a rewritten case."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.application.runtime.case_action_binding import _digest
from src.application.runtime.case_effect_relations import compile_relations
from src.application.test_cases.planning_stages import build_stage_prompts
from src.schemas.case_effect_sources import SOURCE_PROTOCOL, SourceEffectCheck
from src.schemas.case_planning import PlanningDecisionV3, PlanningEvidence
from src.schemas.case_planning_stages import RELATION_PROMPT_VERSION

ROOT = Path(__file__).resolve().parents[2]


def saved_basis():
    saved = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-formal-effects-default-first/PG-planning.json').read_bytes())
    doc = next(entry for entry in saved['checkpoint']['plan']['evidence'] if entry['kind'] == 'document')
    logical = deepcopy(saved['checkpoint']['stages']['logical_decision'])
    for entry in logical['items']:
        entry['effect_schema'] = 3
    item = PlanningDecisionV3.model_validate(logical).items[1]
    reference = {'evidence_id':doc['id'],'content_hash':doc['content_hash'],'payload_path_tokens':['content'],
        'excerpt':'DELETE同一路径返回200及刚删除的订单JSON。','literal':'DELETE','encoding':'text'}
    item.effect_declarations = []
    from src.schemas.case_effect_sources import SourceEffectRelationDeclaration
    item.effect_declarations.append(SourceEffectRelationDeclaration.model_validate({'declaration_id':'deleted','role':'cleanup',
        'sources':[{'item_key':'prepare-orders','declaration_id':'created'}],
        'object_path_tokens':['task_result','response_body','order.id'],
        'checks':[{'output_path_tokens':['task_result','response_status'],'expected_source':{**reference,'literal':'200','encoding':'json'},'operator':'equals'}],
        'select_when':[{'input_path_tokens':['task','method'],'expected_source':reference}]}))
    content = json.loads((ROOT/'docs/upgrade_2026-10-03/p4-formal-effects-default-first/full-case.json').read_bytes())
    return saved, item, PlanningEvidence.model_validate(doc), content


def test_actual_default_case_uses_document_expected_without_added_fields_and_preserves_old_prompt():
    saved, item, doc, content = saved_basis()
    original = deepcopy(content)
    fixed = compile_relations(item, content, [doc], SOURCE_PROTOCOL)[0]
    assert fixed['select_when'][0]['expected'] == 'DELETE'
    assert fixed['checks'][0]['expected'] == 200
    proof = fixed['select_when'][0]['expected_source_proof']
    assert doc.payload['content'][proof['char_start']:proof['char_end']] == 'DELETE'
    assert proof['line'] == 4 and proof['version'] == doc.version
    assert content == original and '证据契约' not in content
    frozen_prompts = saved['seed']['stage_prompts']
    assert 'case-planning-staged-evidence-v5' in saved['seed']['plan']['prompt_version']
    assert '已读取的项目正式用例' not in frozen_prompts['logic']
    current_prompts = build_stage_prompts('case-planning-staged-evidence-v2')
    assert current_prompts.model_dump(mode='json') != frozen_prompts
    assert '已读正式用例可提供条件，必须加入dependency' in current_prompts.logic
    assert RELATION_PROMPT_VERSION == 'case-planning-staged-evidence-v8'
    with pytest.raises(ValueError, match='来源协议'):
        compile_relations(item, content, [doc])


@pytest.mark.parametrize('fault',['unread','hash','changed-payload','ambiguous-excerpt','ambiguous-literal','invalid-json'])
def test_actual_document_reference_rejects_only_confirmed_invalid_basis(fault):
    _, item, doc, content = saved_basis()
    ref = item.effect_declarations[0].select_when[0].expected_source
    if fault == 'unread':item.evidence_ids.remove(doc.id)
    elif fault == 'hash':ref.content_hash = '0'*64
    elif fault == 'changed-payload':doc.payload['content'] += '\nchanged'
    elif fault == 'ambiguous-excerpt':
        doc.payload['content'] += '\n'+ref.excerpt
        doc.content_hash = _digest(doc.payload);ref.content_hash = doc.content_hash
    elif fault == 'ambiguous-literal':ref.literal = 'E'
    else:ref.encoding = 'json'
    with pytest.raises(ValueError):compile_relations(item, content, [doc], SOURCE_PROTOCOL)


@pytest.mark.parametrize('literal,value',[('false',False),('0',0),('null',None)])
def test_actual_document_scalars_preserve_types(literal,value):
    _, item, doc, content = saved_basis()
    ref = item.effect_declarations[0].select_when[0].expected_source
    ref.excerpt = '=null。' if literal == 'null' else '第二条count=0；flag=false、nullable=null。'
    ref.literal = literal;ref.encoding = 'json'
    actual = compile_relations(item, content, [doc], SOURCE_PROTOCOL)[0]['select_when'][0]['expected']
    assert type(actual) is type(value) and actual == value


def test_expected_source_cannot_coexist_with_guessed_own_path():
    _, item, _, _ = saved_basis()
    with pytest.raises(ValueError,match='之一'):
        SourceEffectCheck.model_validate({'output_path_tokens':[],'operator':'equals','expected_path_tokens':[],
            'expected_source':item.effect_declarations[0].checks[0].expected_source.model_dump(mode='json')})
