"""Audit saved real evidence without executing models, tools or migrations."""
from pathlib import Path
from html import unescape
import hashlib
import json
import py_compile
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
prior = json.loads((FOLDER / 'p2-runtime-input-validation.json').read_text(encoding='utf-8'))
plans = [Path(p) for p in prior['plans']]
plan_raw = plans[0].read_bytes()
assert all(p.read_bytes() == plan_raw for p in plans)
prefix = subprocess.check_output(['git', 'rev-parse', '--show-prefix'], cwd=ROOT, text=True).strip()


def record(path):
    raw = path.read_bytes()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


for entry in prior['raw_files']:
    raw = (ROOT / entry['path']).read_bytes()
    assert raw == subprocess.check_output(['git', 'show', 'HEAD:' + prefix + entry['path']], cwd=ROOT)
    assert hashlib.sha256(raw).hexdigest() == entry['sha256'] and len(raw) == entry['size_bytes']
for path, expected in prior['unrelated_current_bytes_unchanged'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected

runs = []
for batch in ['fixed', 'final']:
    directory = FOLDER / f'p2-output-structure-{batch}'
    data = json.loads((directory / 'terminal/verification.json').read_text(encoding='utf-8'))
    assert data['controlled_model'] and data['original_graph_and_API'] and not data['autonomous_external_model']
    assert data['actual_MySQL_policy']['policy']['preparation_scope_protocol'] == 'preparation-scope-v1'
    ids = data['created_ids']
    assert len(set(ids)) == 2 and not data['remaining_orders']
    assert data['actual_HTTP'] == [['POST', '/orders'], ['POST', '/orders'], ['GET', '/orders/' + ids[0]],
        ['GET', '/orders/' + ids[1]], ['DELETE', '/orders/' + ids[0]], ['DELETE', '/orders/' + ids[1]]]
    assert {r['status'] for r in data['run']['items']} == {'passed'}
    originals = {}
    for doc in data['documents']:
        raw = (directory / 'terminal' / doc['filename']).read_bytes()
        asset = doc['original_asset']
        assert len(raw) == asset['size_bytes'] and hashlib.sha256(raw).hexdigest() == asset['sha256']
        assert asset['storage_uri'].startswith('rustfs://')
        source = json.loads(raw)
        assert len(source['steps']) == 8 and source['steps'][-1]['action'] == 'LAST-MULTI-OUTPUT-STEP'
        originals[source['objective']] = source
    jobs = {j['call_id']: j for j in data['jobs']}
    assert set(jobs) == {'create-first', 'create-second', 'query-left', 'query-right', 'delete-first', 'delete-second'}
    for job in jobs.values():
        assert job['status'] == 'completed' and job['metadata']['case_action_admission']
        assert job['metadata']['project_id'] == data['frozen_plan']['project_id']
        assert job['metadata']['run_id'] == data['run']['run']['id']
    assert [jobs[c]['output_payload']['task_result']['response_body']['order.id'] for c in ['create-first', 'create-second']] == ids
    seed = next(r['actual']['preparations']['seed'] for r in data['run']['results'] if 'seed' in r['actual']['preparations'])
    assert seed['output'] == {'ids': ids, 'flags': {'literal.key': False, 'zero': 0, 'nullable': None}, '': []}
    contexts = []
    for request in data['actual_model_requests']:
        assert 'DO-NOT-INJECT-TEMPLATE' not in request['system_prompt']
        section = next(s for s in request['system_prompt_sections'] if s['key'] == 'frozen_case_execution')
        context = json.loads(unescape(section['content'].split('\n', 1)[1].split('\n</untrusted_content>', 1)[0]))
        contexts.append(context)
        for step in context['steps']:
            if step['phase'] == 'case':
                assert step['content_json'] == originals['multi-' + step['item']['key']]
    right = next(c for c in contexts if c['target_key'] == 'right')
    bound = next(s['inputs'] for s in right['steps'] if s['item']['key'] == 'right')
    assert bound['id']['value'] == ids[1] and bound['disabled']['value'] is False and bound['zero']['value'] == 0
    assert all(value['resolved'] for value in bound.values())
    assert len([r for r in data['API_responses'] if r['body']['status'] == 'waiting_approval']) == 6
    runs.append({'batch': batch, 'actual_MySQL_PG_RustFS': True, 'original_JSON_files': 2, 'full_case_steps': 8,
        'graph_and_API': True, 'approvals': 6, 'model_rounds_controlled': len(data['actual_model_requests']),
        'business_HTTP': 6, 'real_ID_count': 2, 'consumer_statuses': ['passed', 'passed'], 'remaining': 0,
        'bound_second_ID_false_zero': True, 'autonomous_external_model': False})

before = json.loads((FOLDER / 'p2-output-structure-before/left-result/verification.json').read_text(encoding='utf-8'))
assert len(before['actual_HTTP']) == 3 and len(before['remaining_orders']) == 2
assert all('right' not in j['call_id'] and 'delete' not in j['call_id'] for j in before['jobs'])
counts = []
for name, expected in [('before', (2, 2, 0)), ('fixed', (2, 0, 0)), ('final', (15, 0, 0)), ('regression', (89, 0, 3))]:
    suite = ET.parse(FOLDER / f'p2-output-structure-{name}.xml').getroot().find('testsuite')
    actual = tuple(int(suite.attrib[k]) for k in ['tests', 'failures', 'skipped'])
    assert actual == expected and int(suite.attrib['errors']) == 0
    counts.append({'batch': name, 'tests': actual[0], 'passed': actual[0] - actual[1] - actual[2],
        'failures': actual[1], 'skipped': actual[2], 'XML_seconds': float(suite.attrib['time']),
        'skipped_nodes': [c.attrib['name'] for c in suite.findall('testcase') if c.find('skipped') is not None]})
source_paths = ['Agent_Server/src/schemas/case_execution.py', 'Agent_Server/src/application/test_runs/agent_case_assessment.py',
    'Agent_Server/src/application/test_runs/agent_case_contract.py', 'Agent_Server/tests/test_case_output_structure.py']
for path in source_paths:
    py_compile.compile(str(ROOT / path), doraise=True)
raws = []
for path in sorted(FOLDER.glob('p2-output-structure-*')):
    candidates = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
    raws.extend(record(p) for p in candidates if p.name != 'p2-output-structure-validation.json' and '__pycache__' not in p.parts)
links = re.findall(r'\]\((G:/[^)]+)\)', plan_raw.decode('utf-8'))
for target in set(links):
    path = Path(re.sub(r':\d+$', '', target))
    if path.name != 'p2-output-structure-validation.json':
        assert path.exists(), str(path)
for entry in raws + [record(ROOT / p) for p in source_paths]:
    raw = (ROOT / entry['path']).read_text(encoding='utf-8', errors='replace')
    assert not re.search(r'\bsk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9_.-]{32,}', raw), entry['path']
result = {'date': '2026-10-07', 'baseline': '55475b8e', 'plans': [str(p) for p in plans],
    'plans_byte_identical': True, 'plan_sha256': hashlib.sha256(plan_raw).hexdigest(),
    'sources': [record(ROOT / p) for p in source_paths], 'raw_files': raws, 'batches': counts, 'actual_runs': runs,
    'baseline_failure_preserved': {'HTTP': 3, 'remaining': 2, 'second_consumer_executed': False},
    'previous_raw_rechecked': len(prior['raw_files']), 'previous_raw_HEAD_bytes_exact': True,
    'unrelated_current_bytes_unchanged': prior['unrelated_current_bytes_unchanged'],
    'local_links_checked': len(set(links)), 'source_compile_passed': True, 'limited_credential_format_scan_passed': True,
    'old_receipt_canonical_byte_tests': 3, 'new_prompt_version': 'case-execution-receipt-v3',
    'no_new_config_dependency_table_or_mode': True, 'full_upgrade_complete': False,
    'audit_first_failed': 'KeyError value: saved DTO has policy; corrected after reading actual saved keys',
    'not_run': ['default autonomous external model', 'full App/frontend build', 'independent Redis Worker',
        'actual shared-consumption completion engine and full preparation lifecycle', 'unknown read-only reconciliation',
        'automatic restoration of previously admitted incorrect runtime contexts'],
    'original_scope': 'full P0-P5 remains active'}
(FOLDER / 'p2-output-structure-validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'validated_raw_files': len(raws), 'prior_HEAD_raw': len(prior['raw_files']), 'plans_identical': True, 'runs': runs}, ensure_ascii=False))
