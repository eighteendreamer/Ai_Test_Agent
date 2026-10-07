"""Validate saved consumer evidence; no model calls or live storage writes."""
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
sys.path.insert(0, str(ROOT / 'Agent_Server'))
from src.application.runtime.case_action_binding import _digest

prior = json.loads((FOLDER / 'p2-output-structure-validation.json').read_text('utf-8'))
plans = [Path(p) for p in prior['plans']]
plan_raw = plans[0].read_bytes()
assert all(p.read_bytes() == plan_raw for p in plans)
prefix = subprocess.check_output(['git', 'rev-parse', '--show-prefix'], cwd=ROOT, text=True).strip()


def record(path):
    raw = path.read_bytes()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


for entry in prior['raw_files']:
    raw = (ROOT / entry['path']).read_bytes()
    assert raw == subprocess.check_output(['git', 'show', 'a4d6c7e4:' + prefix + entry['path']], cwd=ROOT)
    assert hashlib.sha256(raw).hexdigest() == entry['sha256'] and len(raw) == entry['size_bytes']
for path, expected in prior['unrelated_current_bytes_unchanged'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
baseline_path = FOLDER / 'p2-consumer-receipt-assessment-baseline.py'
assert baseline_path.read_bytes() == subprocess.check_output(['git', 'show',
    'a4d6c7e4:' + prefix + 'Agent_Server/src/application/test_runs/agent_case_assessment.py'], cwd=ROOT)

runs = []
for path in sorted((FOLDER / 'p2-consumer-receipt-verified').rglob('terminal.json')):
    data = json.loads(path.read_text('utf-8'))
    pending = json.loads(path.with_name('pending.json').read_text('utf-8'))
    partial = json.loads(path.with_name('partial.json').read_text('utf-8'))
    before = json.loads(path.with_name('before.json').read_text('utf-8'))
    assert data['controlled_model'] and not data['autonomous_external_model']
    assert not data['consumer_completion_checkpoint_implemented'] and not data['early_cleanup_prevented']
    assert partial['run_and_cleanup_unchanged'] and before['before_run'] == partial['before_run']
    assert pending['run'] == before['before_run'] and pending['pending_cleanup'] == partial['pending_cleanup']
    raw = path.with_name('consumer-case.json').read_bytes()
    asset = data['document']['original_asset']
    assert len(raw) == asset['size_bytes'] and hashlib.sha256(raw).hexdigest() == asset['sha256']
    assert asset['storage_uri'].startswith('rustfs://')
    original = json.loads(raw)
    assert len(original['steps']) == 8 and original['steps'][-1]['action'] == 'LAST-CONSUMER-STEP'
    assert original['steps'][-1]['values'] == [False, 0, None, {'': 'literal.key'}]
    context = data['full_context']
    assert next(s['content_json'] for s in context['steps'] if s['item']['key'] == 'target') == original
    checkpoint = data['before_run']['attempts'][-1]['checkpoint_payload']
    assert data['context_sha256'] == checkpoint['context_hash'] == _digest(context)
    assert data['pending_cleanup']['status'] == 'waiting_approval' and data['pending_cleanup']['started_at'] is None
    assert data['pending_job_ids'] == [data['pending_cleanup']['id']]
    jobs = {j['call_id']: j for j in data['original_jobs']}
    assert set(jobs) == {'seed', 'target', 'cleanup'}
    for job in jobs.values():
        binding = job['metadata']['case_action_binding']
        assert binding['context_sha256'] == data['context_sha256']
        assert binding['project_id'] == context['project_id'] and binding['run_id'] == context['run_id']
        assert binding['run_item_id'] == context['run_item_id'] and binding['attempt_id'] == checkpoint['execution_attempt_id']
        if job['status'] == 'completed':
            assert job['metadata']['case_action_admission']
        else:
            assert job['call_id'] == 'cleanup' and job['status'] == 'waiting_approval'
            assert 'case_action_admission' not in job['metadata']
    assert jobs['seed']['status'] == jobs['target']['status'] == 'completed'
    request = data['assessment_request']
    assert len(data['physical_model_requests']) == 1
    assert data['physical_model_requests'][0]['messages'] == [{'role': 'system', 'content': request['system_prompt']}, *request['messages']]
    delivered = json.loads(unescape(request['messages'][0]['content']).split('\n', 1)[1].split('\n</untrusted_content>', 1)[0])
    assert delivered['requested_item_keys'] == ['target']
    assert delivered['execution_items'][0]['expected_source'] == original
    assert len(delivered['tool_evidence']) == 3
    assert next(j['status'] for j in delivered['tool_evidence'] if j['call_id'] == 'cleanup') == 'waiting_approval'
    expected = 'failed' if data['wrong_expected'] else 'passed'
    assert set(data['partial_results']) == {'target'} and data['partial_results']['target']['status'] == expected
    body = data['partial_results']['target']['output']['body']
    assert body['flag'] is False and body['nullable'] is None and body['count'] == 1
    verification = data['partial_verifications'][0]
    assert verification['assertion_count'] == 5 and verification['status'] == expected
    assert all(e['source_id'] == jobs['target']['id'] for e in verification['evidence'])
    assert [m for m, _ in data['business_HTTP_before']] == ['POST', 'GET']
    assert len(pending['remaining_orders']) == 1
    assert [m for m, _ in data['business_HTTP']] == ['POST', 'GET', 'DELETE']
    assert not data['remaining_orders'] and data['terminal_non_reclaim']
    assert data['terminal_run']['run']['status'] == 'completed'
    assert data['terminal_run']['items'][0]['status'] == expected
    runs.append({'fixture': path.parent.name, 'langchain_enabled': data['langchain_enabled'],
        'wrong_expected': data['wrong_expected'], 'status': expected, 'complete_case_steps': 8,
        'physical_model_HTTP': 1, 'business_HTTP': 3, 'partial_checks': 5, 'actual_MySQL_PG_RustFS': True,
        'original_graph_API_approvals': True, 'pending_cleanup_preserved': True, 'context_hash_preserved': True,
        'remaining_objects': 0, 'terminal_non_reclaim': True})
assert len(runs) == 4 and len({(r['langchain_enabled'], r['wrong_expected']) for r in runs}) == 4

counts = []
for name, expected in [('before', (8, 8, 0)), ('reproduction', (8, 8, 0)),
    ('pending-reproduction', (8, 8, 0)), ('fixed', (8, 0, 0)), ('final', (15, 4, 0)),
    ('verified', (15, 0, 0)), ('regression', (106, 0, 3))]:
    suite = ET.parse(FOLDER / f'p2-consumer-receipt-{name}.xml').getroot().find('testsuite')
    actual = tuple(int(suite.attrib[k]) for k in ['tests', 'failures', 'skipped'])
    assert actual == expected and int(suite.attrib['errors']) == 0
    command = json.loads((FOLDER / f'p2-consumer-receipt-{name}.command.json').read_text('utf-8'))
    assert command['exit_code'] == (1 if actual[1] else 0)
    counts.append({'batch': name, 'tests': actual[0], 'passed': actual[0] - actual[1] - actual[2],
        'failures': actual[1], 'skipped': actual[2], 'XML_seconds': float(suite.attrib['time']),
        'skipped_nodes': [c.attrib['name'] for c in suite.findall('testcase') if c.find('skipped') is not None]})
source_paths = ['Agent_Server/src/application/test_runs/agent_case_assessment.py', 'Agent_Server/tests/test_case_step_assessment.py']
for path in source_paths:
    py_compile.compile(str(ROOT / path), doraise=True)
raws = []
for path in sorted(FOLDER.glob('p2-consumer-receipt-*')):
    candidates = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
    raws.extend(record(p) for p in candidates if p.name != 'p2-consumer-receipt-validation.json' and '__pycache__' not in p.parts)
links = re.findall(r'\]\((G:/[^)]+)\)', plan_raw.decode('utf-8'))
for target in set(links):
    path = Path(re.sub(r':\d+$', '', target))
    if path.name != 'p2-consumer-receipt-validation.json':
        assert path.exists(), str(path)
for entry in raws + [record(ROOT / p) for p in source_paths]:
    raw = (ROOT / entry['path']).read_text(encoding='utf-8', errors='replace')
    assert not re.search(r'\bsk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9_.-]{32,}', raw), entry['path']
result = {'date': '2026-10-07', 'baseline': 'a4d6c7e4', 'plans': [str(p) for p in plans],
    'plans_byte_identical': True, 'plan_sha256': hashlib.sha256(plan_raw).hexdigest(),
    'sources': [record(ROOT / p) for p in source_paths], 'raw_files': raws, 'batches': counts, 'actual_runs': runs,
    'previous_raw_rechecked': len(prior['raw_files']), 'previous_raw_HEAD_bytes_exact': True,
    'unrelated_current_bytes_unchanged': prior['unrelated_current_bytes_unchanged'],
    'baseline_assessment_source_exact': True, 'local_links_checked': len(set(links)), 'source_compile_passed': True,
    'limited_credential_format_scan_passed': True, 'old_full_request_byte_test_passed': True,
    'full_result_normalization': 'generated verification IDs and creation clocks only',
    'no_new_config_dependency_table_or_mode': True, 'full_upgrade_complete': False,
    'consumer_completion_checkpoint_implemented': False, 'early_cleanup_prevented': False,
    'current_fixture_failures_preserved': ['running-only loader', 'original snapshot contains pending third record', 'JSON order substring assertion'],
    'audit_first_failure': 'KeyError case_action_admission: waiting approval has binding but no execution admission; checked saved original metadata',
    'not_run': ['default autonomous external model', 'full App/frontend build', 'independent Redis Worker',
        'actual shared-consumption completion engine and full preparation lifecycle', 'unknown read-only reconciliation',
        'automatic restoration of previously admitted incorrect runtime contexts'],
    'original_scope': 'full P0-P5 remains active'}
(FOLDER / 'p2-consumer-receipt-validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'raw_files': len(raws), 'previous_HEAD_raw': len(prior['raw_files']), 'plans_identical': True,
    'verified_tests': 15, 'regression_passed': 103, 'regression_skipped': 3, 'runs': runs}, ensure_ascii=False))
