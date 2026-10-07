"""Verify saved original bytes and phase evidence without sending or mutating storage."""
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

prior = json.loads((FOLDER/'p2-consumer-receipt-validation.json').read_text('utf-8'))
plans = [Path(p) for p in prior['plans']]
raw_plan = plans[0].read_bytes()
assert all(p.read_bytes() == raw_plan for p in plans)
prefix = subprocess.check_output(['git','rev-parse','--show-prefix'],cwd=ROOT,text=True).strip()
for entry in prior['raw_files']:
    raw = (ROOT/entry['path']).read_bytes()
    assert raw == subprocess.check_output(['git','show','e356905d:'+prefix+entry['path']],cwd=ROOT)
    assert len(raw) == entry['size_bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
for path, sha in prior['unrelated_current_bytes_unchanged'].items():
    assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == sha

def file_record(path):
    raw = path.read_bytes()
    return {'path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}

scenarios = []
no_closure = {'empty-checks','missing-call','foreign-target','pending-ordinary','unknown-ordinary'}
for path in sorted((FOLDER/'p2-consumption-phase-final').glob('*/verification.json')):
    data = json.loads(path.read_text('utf-8'))
    scenario = data['scenario']
    original_raw = path.with_name('consumption-case.json').read_bytes()
    original = json.loads(original_raw)
    asset = data['original']['original_asset']
    assert asset['size_bytes'] == len(original_raw) and asset['sha256'] == hashlib.sha256(original_raw).hexdigest()
    assert asset['storage_uri'].startswith('rustfs://') and len(original['steps']) == 8
    assert original['期望']['flag'] is False and original['期望']['nullable'] is None
    assert not data['default_autonomous_model']
    assert data['frozen_plan']['config_snapshot']['policy']['consumption_protocol'] == 'case-consumption-phase-v1'
    if scenario.startswith('graph-'):
        assert data['original_graph_API_approvals'] and len(data['approvals']) == 6 and len(data['requests']) == 7
        section = next(s for s in data['requests'][0]['system_prompt_sections'] if s['key'] == 'frozen_case_execution')
        context = json.loads(unescape(section['content'].split('\n',1)[1].split('\n</untrusted_content>',1)[0]))
        checkpoint = data['before_cleanup']['attempts'][-1]['checkpoint_payload']
        pending = next(j for j in data['jobs'] if j['call_id'] == 'cleanup-first')
        assert checkpoint['context_hash'] == _digest(context)
        assert pending['metadata']['case_action_admission']['consumption']['closure_sha256'] == data['closure']['sha256']
    else:
        context = data['full_context']
        checkpoint = data['before_run']['attempts'][-1]['checkpoint_payload']
        assert checkpoint['context_hash'] == _digest(context)
    assert context['steps'][0]['content_json'] == original
    jobs = {j['id']:j for j in data['jobs']}
    for job in jobs.values():
        assert job['metadata']['case_action_binding']['context_sha256'] == _digest(context)
        assert job['metadata']['case_action_binding']['project_id'] == context['project_id']
    closure = data['closure']
    if scenario in no_closure:
        assert closure is None and len(data['remaining']) == 2 and len(data['actual_HTTP']) == 3
    else:
        assert closure['version'] == 'case-consumption-phase-v1' and not closure['run_item_completed']
        assert closure['sha256'] == _digest({k:v for k,v in closure.items() if k != 'sha256'})
        assert closure['context_sha256'] == _digest(context) and closure['run_item_id'] == context['run_item_id']
        close_job = jobs[closure['closure_job_id']]
        assert close_job['metadata']['case_action_binding'] == closure['closing_binding']
        assert _digest(close_job['input_payload']['_case_action']['consumption_closure']) == closure['closure_payload_sha256']
        assert len(closure['normal_job_refs']) == 3
        for ref in closure['normal_job_refs']:
            job = jobs[ref['job_id']]
            assert job['call_id'] == ref['call_id'] and job['status'] == ref['status']
            assert _digest(job['output_payload']) == ref['output_sha256']
            assert _digest(job['metadata']['case_action_binding']) == ref['binding_sha256']
        assert closure['verification']['status'] == ('failed' if scenario in {'failed','graph-failed'} else 'passed')
        if scenario == 'lost-claim':
            unknown = data['unknown_original']
            assert unknown['status'] == 'running' and unknown['started_at'] and not unknown['output_payload']
            assert unknown['id'] == closure['closure_job_id']
            assert len(data['remaining']) == 2 and len(data['actual_HTTP']) == 3
            assert data['records'][-1]['output']['error'] == 'case_operation_outcome_unknown'
            assert data['records'][-1]['output']['action_sent'] is False
        else:
            assert not data['remaining'] and [m for m,p in data['actual_HTTP']] == ['POST','POST','GET','DELETE','DELETE']
            result = data['formal_result'] if 'formal_result' in data else data['after_run']['results'][0]
            assert result['status'] == ('failed' if scenario in {'failed','graph-failed'} else 'passed')
            assert data['after_run']['run']['status'] == 'completed'
    if scenario == 'parallel':
        assert data['parallel_actual_claims'] == 4
        assert sum(j['started_at'] is not None for j in jobs.values() if j['input_payload']['_case_action']['operation_key'] == 'cleanup-first') == 1
    if scenario == 'unknown-ordinary':
        assert data['pending_original']['status'] == 'running' and not data['pending_original']['output_payload']
    if scenario == 'rollback':
        assert data['rollback_actual_SQL']
    scenarios.append({'scenario':scenario,'actual_HTTP_count':len(data['actual_HTTP']),
        'remaining_objects':len(data['remaining']),'closure_sha256':closure['sha256'] if closure else None,
        'original':file_record(path.with_name('consumption-case.json'))})
assert len(scenarios) == 13

xml_runs = []
for path in sorted(FOLDER.glob('p2-consumption-phase-*.xml')):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    failures = sum(c.find('failure') is not None for c in cases)
    errors = sum(c.find('error') is not None for c in cases)
    skipped = [c for c in cases if c.find('skipped') is not None]
    suites = list(root.iter('testsuite'))
    summary = {'path':path.name,'tests':len(cases),'passed':len(cases)-failures-errors-len(skipped),
        'failures':failures,'errors':errors,'skipped':len(skipped),
        'time_seconds':sum(float(s.get('time','0')) for s in suites),
        'skip_nodes':[{'node':c.get('classname','')+'::'+c.get('name',''),
            'reason':c.find('skipped').get('message')} for c in skipped]}
    if path.name == 'p2-consumption-phase-final.xml':
        assert (summary['passed'],failures,errors,len(skipped)) == (14,0,0,0)
    if path.name == 'p2-consumption-phase-regression.xml':
        assert (summary['passed'],failures,errors,len(skipped)) == (137,0,0,8)
    if path.name == 'p2-consumption-phase-binding-live.xml':
        assert (summary['passed'],failures,errors,len(skipped)) == (7,0,0,0)
    xml_runs.append(summary)

source_paths = [ROOT/p for p in [
    'Agent_Server/src/application/prompting/prompt_assembly_service.py',
    'Agent_Server/src/application/runtime/case_action_binding.py',
    'Agent_Server/src/application/runtime/case_effect_relations.py',
    'Agent_Server/src/application/runtime/case_operation.py',
    'Agent_Server/src/application/test_runs/agent_case_assessment.py',
    'Agent_Server/src/application/test_runs/agent_case_context.py',
    'Agent_Server/src/runtime/postgres_case_action_admission.py',
    'Agent_Server/src/runtime/postgres_case_operations.py',
    'Agent_Server/src/runtime/postgres_case_consumption.py',
    'Agent_Server/src/schemas/case_execution.py','Agent_Server/src/schemas/case_planning.py',
    'Agent_Server/tests/test_agent_case_assessment.py','Agent_Server/tests/test_case_consumption_phase.py']]
for path in source_paths:
    py_compile.compile(str(path),doraise=True)

raw_paths = sorted(p for p in FOLDER.rglob('p2-consumption-phase-*') if p.is_file())
for directory in sorted(FOLDER.glob('p2-consumption-phase-*')):
    if directory.is_dir():
        raw_paths.extend(p for p in directory.rglob('*') if p.is_file())
raw_paths = sorted(set(raw_paths)-{FOLDER/'p2-consumption-phase-validation.json'})
for path in raw_paths:
    if path.suffix in {'.py','.json','.xml','.log'}:
        assert not re.search(rb'\bsk-[A-Za-z0-9]{20,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',path.read_bytes()),path

output = {'date':'2026-10-07','baseline':'e356905d','plans':[str(p) for p in plans],
    'plans_byte_identical':True,'plan_sha256':hashlib.sha256(raw_plan).hexdigest(),
    'sources':[file_record(p) for p in source_paths],'raw_files':[file_record(p) for p in raw_paths],
    'scenarios':scenarios,'xml_runs':xml_runs,'previous_raw_rechecked':len(prior['raw_files']),
    'previous_raw_HEAD_bytes_exact':True,'unrelated_current_bytes_unchanged':prior['unrelated_current_bytes_unchanged'],
    'source_compile_passed':True,'limited_credential_format_scan_passed':True,
    'config':'optional field in existing MySQL case planning policy',
    'business':'new immutable PG phase record; original claim and logical index transaction',
    'originals':'RustFS original case documents verified by live tests and saved SHA/size',
    'internal_phase_and_original_graph_verified':True,'full_upgrade_complete':False,
    'not_run':['external multiple consumers','lease recovery and multiple Workers','dedicated phase report UI',
        'full App','default autonomous external model','full preparation lifecycle',
        'unknown read-only reconciliation','production historical migration'],
    'early_commands':'original logs/XML retained; complete early command arguments not saved',
    'historical_limit':'two previous Worker original summaries not restored; current bytes protected'}
output['frontend_sources'] = [file_record(ROOT/p) for p in ['agent_web/src/types.ts',
    'agent_web/src/features/projects/CasePlanDialog.vue','agent_web/src/features/projects/CasePlanDialog.test.ts']]
output['documentation'] = [file_record(FOLDER/p) for p in ['PLAN.md','IMPLEMENTATION.md',
    'P2_CONSUMPTION_PHASE_DESIGN.md','.gitattributes']]
assert re.search(r'Tests\s+65 passed', (FOLDER/'p2-consumption-phase-frontend-final.log').read_text('utf-8'))
assert 'built in 6.90s' in (FOLDER/'p2-consumption-phase-build-final.log').read_text('utf-8')
output['frontend'] = {'component_tests_passed':65,'time_seconds':2.42,'build_exit_code':0,
    'typecheck_included':True,'vite_seconds':6.90,'full_browser_App_run':False}
(FOLDER/'p2-consumption-phase-validation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
links = re.findall(r'\]\((G:/[^\s)]+)\)',raw_plan.decode('utf-8'))
for target in links:
    assert Path(re.sub(r':\d+$','',target)).exists(),target
output['local_links_checked'] = len(set(links))
(FOLDER/'p2-consumption-phase-validation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'scenarios':len(scenarios),'raw_files':len(raw_paths),'previous_raw':len(prior['raw_files']),
    'plans_byte_identical':True,'source_compile_passed':True,'full_upgrade_complete':False},ensure_ascii=False))
