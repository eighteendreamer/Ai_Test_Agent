"""Read saved actual evidence; no sends or database mutations."""
from pathlib import Path
from html import unescape
import hashlib
import json
import py_compile
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'Agent_Server'))
from src.application.runtime.case_action_binding import _digest
from src.application.test_runs.plan_mapping import build_plan_run
from src.schemas.case_planning import CasePlanRunRequest
from src.schemas.run_management import TestRunRecord as RunRecord
from src.schemas.tool_job import ToolJobRecord
from src.runtime.postgres_case_consumption import PostgresCaseConsumption

prior = json.loads((FOLDER/'p2-consumption-phase-validation.json').read_text('utf-8'))
plans = [Path(p) for p in prior['plans']]
raw_plan = plans[0].read_bytes()
assert all(p.read_bytes() == raw_plan for p in plans)
prefix = subprocess.check_output(['git','rev-parse','--show-prefix'],cwd=ROOT,text=True).strip()
for entry in prior['raw_files']:
    raw = (ROOT/entry['path']).read_bytes()
    assert raw == subprocess.check_output(['git','show','67d134ae:'+prefix+entry['path']],cwd=ROOT)
    assert len(raw) == entry['size_bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
for path,sha in prior['unrelated_current_bytes_unchanged'].items():
    assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == sha

def file_record(path):
    raw = path.read_bytes()
    return {'path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}

def asset_check(path,document):
    raw = path.read_bytes()
    asset = document['original_asset']
    assert asset['size_bytes'] == len(raw) and asset['sha256'] == hashlib.sha256(raw).hexdigest()
    assert asset['storage_uri'].startswith('rustfs://')
    content = json.loads(raw)
    assert len(content['steps']) == 8
    return content

internal = []
for path in sorted((FOLDER/'p2-consumption-return-final').glob('*/verification.json')):
    if path.parent.name.endswith('-cleanup-attempt'):
        continue
    d = json.loads(path.read_text('utf-8'))
    content = asset_check(path.with_name('consumption-case.json'),d['original'])
    scenario = d['scenario']
    if scenario.startswith('graph-'):
        section = next(s for s in d['requests'][0]['system_prompt_sections'] if s['key'] == 'frozen_case_execution')
        context = json.loads(unescape(section['content'].split('\n',1)[1].split('\n</untrusted_content>',1)[0]))
        assert len(d['approvals']) == 6 and len(d['requests']) == 7
        assert d['original_graph_API_approvals']
    else:
        context = d['full_context']
    assert context['steps'][0]['content_json'] == content
    assert not d['default_autonomous_model']
    if d.get('closure'):
        closure = d['closure']
        assert closure['sha256'] == _digest({k:v for k,v in closure.items() if k != 'sha256'})
        assert not closure['run_item_completed'] and closure['context_sha256'] == _digest(context)
        jobs = {j['id']:j for j in d['jobs']}
        for ref in closure['normal_job_refs']:
            original = jobs[ref['job_id']]
            assert ref['return_observation_sha256'] == _digest(original['metadata'].get('tool_output_observation'))
            assert PostgresCaseConsumption.action_finished(ToolJobRecord.model_validate(original))
    if scenario == 'cancelled-unknown-ordinary':
        job = d['pending_original']
        assert job['status'] == 'cancelled' and job['started_at'] and not job['output_payload']
        assert 'tool_output_observation' not in job['metadata']
        assert d['closure'] is None and len(d['remaining']) == 2
        assert not any(m == 'DELETE' for m,p in d['actual_HTTP'])
    internal.append({'scenario':scenario,'HTTP_count':len(d['actual_HTTP']),
        'remaining_objects':len(d['remaining']),'record':file_record(path)})
assert len(internal) == 14
before = json.loads((FOLDER/'p2-consumption-return-before/cancelled-unknown-ordinary-cleanup-attempt/verification.json').read_text('utf-8'))
assert before['cancelled_original']['started_at'] and 'tool_output_observation' not in before['cancelled_original']['metadata']
assert before['cleanup']['status'] == 'completed' and before['closure'] and len(before['remaining']) == 1
assert [m for m,p in before['actual_HTTP']].count('DELETE') == 1

external = []
for directory in sorted((FOLDER/'p2-consumption-external-final').iterdir()):
    if not directory.is_dir():
        continue
    path = directory/'final.json' if (directory/'final.json').exists() else directory/'left-result.json'
    d = json.loads(path.read_text('utf-8'))
    scenario = d['scenario']
    contents = {key:asset_check(directory/(key+'.json'),doc) for key,doc in zip(['left','right'],d['originals'])}
    for content in contents.values():
        assert content['steps'][-1]['typed'] == [False,0,None]
    for request in d['requests']:
        section = next(s for s in request['system_prompt_sections'] if s['key'] == 'frozen_case_execution')
        context = json.loads(unescape(section['content'].split('\n',1)[1].split('\n</untrusted_content>',1)[0]))
        formal = next(step for step in context['steps'] if step['phase'] == 'case')
        assert formal['content_json'] == contents[context['target_key']]
        assert context['consumption_protocol'] == 'case-consumption-phase-v1'
    assert d['actual_MySQL_policy'].get('consumption_protocol') is None
    assert d['plan']['config_snapshot']['policy']['consumption_protocol'] == 'case-consumption-phase-v1'
    assert d['controlled_agent'] and d['original_graph_API_approvals'] and not d['default_autonomous_model']
    run = RunRecord.model_validate(d['run']['run'])
    _, rebuilt, items = build_plan_run(run.plan_snapshot.plan,
        CasePlanRunRequest(creation_key=run.creation_key,mode_key=run.mode_key,session_id=run.session_id),
        now=run.created_at,created_by=run.created_by,dependency_mapping_version=run.plan_snapshot.dependency_mapping_version)
    assert rebuilt.creation_hash == run.creation_hash and rebuilt.plan_snapshot == run.plan_snapshot
    raw_dependencies = {i['id']:i['dependencies'] for i in d['run']['items']}
    assert all([dep.model_dump(mode='json') for dep in item.dependencies] == raw_dependencies[item.id] for item in items)
    if scenario == 'legacy-formal-failed':
        assert run.plan_snapshot.dependency_mapping_version is None
        assert 'dependency_mapping_version' not in d['run']['run']['plan_snapshot']
    else:
        assert run.plan_snapshot.dependency_mapping_version == 'case-cleanup-waits-v1'
    if 'final.json' == path.name:
        if scenario == 'after-cancelled-unknown':
            assert d['right_result']['status'] == 'blocked' and len(d['remaining']) == 1 and not d['closures']
            assert not any(m == 'DELETE' for m,p in d['actual_HTTP'])
            original = d['injected'][0]
            assert original['status'] == 'cancelled' and original['started_at'] and not original['output_payload']
            assert 'tool_output_observation' not in original['metadata']
        else:
            assert d['right_result']['status'] == 'passed' and not d['remaining'] and len(d['closures']) == 1
            assert [m for m,p in d['actual_HTTP']].count('DELETE') == 1
            jobs = {j['id']:j for j in d['jobs']}
            closure = d['closures'][0]
            assert closure['sha256'] == _digest({k:v for k,v in closure.items() if k != 'sha256'})
            cleanup = jobs[closure['closure_job_id']]
            proof = cleanup['metadata']['case_action_admission']['consumption']
            consumer = next(c for c in proof['consumers'] if c['item_key'] == 'left')
            assert consumer['result_sha256'] == _digest(d['left_result'])
            for ref in consumer['started_actions']:
                job = jobs[ref['job_id']]
                assert ref['output_sha256'] == _digest(job['output_payload'])
                assert ref['return_observation_sha256'] == _digest(job['metadata']['tool_output_observation'])
                assert PostgresCaseConsumption.action_finished(ToolJobRecord.model_validate(job))
            if scenario == 'after-returned-partial':
                job = d['injected'][0]
                assert job['status'] == 'partial' and 'output_delivery' in job['output_payload']
                assert _digest(job['output_payload']) != job['metadata']['tool_output_observation']['output_hash']
    else:
        assert not d['right_claim_count'] and len(d['remaining']) == 1 and not d['closures']
        assert len(d['actual_HTTP']) == 2 and d['left_result']['status'] == 'failed'
    external.append({'scenario':scenario,'HTTP_count':len(d['actual_HTTP']),'remaining_objects':len(d['remaining']),
        'left_status':d['left_result']['status'],'right_status':d.get('right_result',{}).get('status'),
        'mapping_version':run.plan_snapshot.dependency_mapping_version,'record':file_record(path)})
assert len(external) == 9

xml_runs = []
for path in sorted([*FOLDER.glob('p2-consumption-return-*.xml'),*FOLDER.glob('p2-consumption-external-*.xml')]):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    fail = sum(c.find('failure') is not None for c in cases)
    err = sum(c.find('error') is not None for c in cases)
    skipped = [c for c in cases if c.find('skipped') is not None]
    result = {'path':path.name,'tests':len(cases),'passed':len(cases)-fail-err-len(skipped),
        'failures':fail,'errors':err,'skipped':len(skipped),
        'seconds':sum(float(s.get('time','0')) for s in root.iter('testsuite')),
        'skip_nodes':[{'node':c.get('classname','')+'::'+c.get('name',''),
            'reason':c.find('skipped').get('message')} for c in skipped]}
    if path.name in {'p2-consumption-return-final.xml','p2-consumption-external-final.xml','p2-consumption-return-regression.xml'}:
        assert not fail and not err
    xml_runs.append(result)
assert {'p2-consumption-return-regression.xml','p2-consumption-return-final.xml','p2-consumption-external-final.xml'} <= {x['path'] for x in xml_runs}

sources = [ROOT/p for p in ['Agent_Server/src/runtime/postgres_case_consumption.py',
    'Agent_Server/src/application/test_runs/preparation_scopes.py','Agent_Server/src/application/test_runs/plan_mapping.py',
    'Agent_Server/src/application/test_runs/plan_run_service.py','Agent_Server/src/schemas/case_planning.py',
    'Agent_Server/tests/test_case_consumption_phase.py','Agent_Server/tests/test_case_consumption_external.py']]
for path in sources:
    py_compile.compile(str(path),doraise=True)
paths = set()
for pattern in ['p2-consumption-return-*','p2-consumption-external-*']:
    for path in FOLDER.glob(pattern):
        if path.is_dir():
            paths.update(p for p in path.rglob('*') if p.is_file())
        else:
            paths.add(path)
paths.discard(FOLDER/'p2-consumption-return-validation.json')
for path in paths:
    if path.suffix in {'.json','.py','.xml','.log'}:
        assert not re.search(rb'\bsk-[A-Za-z0-9]{20,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',path.read_bytes()),path
output = {'date':'2026-10-08','baseline':'67d134ae','plans':[str(p) for p in plans],
    'plans_byte_identical':True,'plan_sha256':hashlib.sha256(raw_plan).hexdigest(),
    'sources':[file_record(p) for p in sources],'frontend_sources':[file_record(ROOT/'agent_web/src/types.ts')],
    'raw_files':[file_record(p) for p in sorted(paths)],'internal_scenarios':internal,'external_scenarios':external,
    'xml_runs':xml_runs,'previous_raw_rechecked':len(prior['raw_files']),'previous_raw_HEAD_bytes_exact':True,
    'unrelated_current_bytes_unchanged':prior['unrelated_current_bytes_unchanged'],
    'source_compile_passed':True,'limited_credential_format_scan_passed':True,
    'frontend_typecheck_exit_code':0,'frontend_full_App_run':False,
    'controlled_Agent_only':True,'default_model_HTTP':0,'full_upgrade_complete':False,
    'historical_limit':'two previous Worker original summaries not restored; current bytes protected',
    'not_run':['more consumers/source combinations','multiple Workers/lease recovery','default autonomous model',
        'full App','dedicated consumption report','full preparation lifecycle','unknown read-only reconciliation',
        'production historical migration'],
    'documentation':[file_record(FOLDER/p) for p in ['PLAN.md','IMPLEMENTATION.md','P2_CONSUMPTION_RETURN_RESEARCH.md','.gitattributes']]}
assert 'vue-tsc --noEmit' in (FOLDER/'p2-consumption-return-typecheck.log').read_text('utf-8')
target = FOLDER/'p2-consumption-return-validation.json'
target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
links = re.findall(r'\]\((G:/[^\s)]+)\)',raw_plan.decode('utf-8'))
for link in links:
    assert Path(re.sub(r':\d+$','',link)).exists(),link
output['local_links_checked'] = len(set(links))
target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'internal_scenarios':len(internal),'external_scenarios':len(external),
    'previous_raw_files':len(prior['raw_files']),'raw_files':len(paths),'plans_byte_identical':True,
    'source_compile_passed':True,'full_upgrade_complete':False},ensure_ascii=False))
