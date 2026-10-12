"""Audit mixed tool waits and retain every unsuccessful acceptance original."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / 'docs/upgrade_2026-10-03'
validation = directory / 'p2-tool-wait-batch-validation.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


previous = json.loads((directory / 'p2-tool-child-wait-validation.json').read_bytes())
plans = [directory / 'PLAN.md',
    Path(r'C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt'),
    Path(r'C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md')]
for path in plans[1:]:
    assert digest(path) in {previous['plan_sha256'], digest(plans[0])}, 'External plan changed; do not overwrite'
for path in plans[1:]:
    if digest(path) != digest(plans[0]):
        path.write_bytes(plans[0].read_bytes())
assert len({digest(path) for path in plans}) == 1
protected = json.loads((directory / 'p5-default-ui-protected.json').read_bytes())
assert all(digest(root / relative) == expected for relative, expected in protected.items())
historical = {}
for name in ('p2-tool-child-wait-validation.json', 'p2-api-result-readback-validation.json',
    'p2-api-agent-worker-validation.json', 'p0-sdk-fault-validation.json',
    'p2-api-credential-reference-validation.json', 'p2-api-worker-auth-validation.json',
    'p2-api-approval-wait-validation.json'):
    hashes = json.loads((directory / name).read_bytes())['artifact_hashes']
    for relative, expected in hashes.items():
        assert digest(root / relative) == expected, 'Historical artifact changed: ' + relative
    historical[name] = len(hashes)
old_source = subprocess.check_output(['git', 'show',
    '8df999ef:Enterprise_AI_QA_Agent/Agent_Server/src/application/runtime/runtime_service.py'],
    text=True, encoding='utf8')
old_function = next(node for node in ast.walk(ast.parse(old_source))
    if isinstance(node, ast.FunctionDef) and node.name == '_build_pending_turn')
new_source = (root / 'Agent_Server/src/application/runtime/state_persistence.py').read_text(encoding='utf8')
new_function = next(node for node in ast.walk(ast.parse(new_source))
    if isinstance(node, ast.FunctionDef) and node.name == 'build_pending_turn')
assert ast.dump(ast.Module(body=old_function.body, type_ignores=[]), include_attributes=False) == ast.dump(
    ast.Module(body=new_function.body, type_ignores=[]), include_attributes=False)
artifacts = []
for path in sorted(directory.glob('p2-tool-wait-batch-*')):
    artifacts.extend(path.rglob('*') if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != '.py']
tests = {}
for path in artifacts:
    if path.suffix == '.xml':
        suites = ET.parse(path).getroot().findall('testsuite')
        tests[path.relative_to(directory).as_posix()] = {
            key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}
for suffix, expected in {
    'before': (2, 2, 0, 0), 'first': (19, 0, 0, 1),
    'functional-first': (6, 0, 0, 2), 'live': (6, 2, 0, 0),
    'pg-probe': (2, 2, 0, 0), 'pg-settled': (9, 0, 0, 0),
    'notification-before': (2, 2, 0, 0), 'notification-verified': (11, 0, 0, 0),
    'agent': (2, 1, 0, 0), 'agent-final': (2, 0, 0, 0),
    'regression': (119, 0, 0, 41), 'regression-final': (121, 0, 0, 42),
    'notification-regression': (41, 0, 0, 0),
}.items():
    assert tuple(tests[f'p2-tool-wait-batch-{suffix}.xml'].values()) == expected, suffix
assert tuple(tests['p2-tool-wait-batch-agent-regression/regression.xml'].values()) == (62, 0, 0, 1)
mixed = {}
for storage in ('memory', 'postgres'):
    for decision in ('approved', 'denied'):
        owned = directory / 'p2-tool-wait-batch-notification-verified' / storage / decision
        record = json.loads((owned / 'facts.json').read_bytes())
        assert record['external_ai_calls'] == 0 and not record['full_goal_complete']
        assert record['controlled_wait_producer'] and not record['independent_agent_process']
        assert record['all_children_required'] and record['job_count'] == 4
        assert record['model_calls_before_settled'] == 1 and record['model_calls_final'] == 2
        assert record['max_iterations'] == 8 and record['extra_http'] == record['extra_model_calls'] == 0
        assert record['final_tool_ids'] == ['one', 'two', 'write-one', 'write-two']
        assert record['final_statuses'] == ['completed'] * 3 + [
            'completed' if decision == 'approved' else 'denied']
        assert [event[0] for event in record['events']] == ['POST'] * (2 if decision == 'approved' else 1) + ['GET', 'GET']
        assert record['job_statuses']['write-two'] == ('completed' if decision == 'approved' else 'denied')
        snapshots = json.loads((owned / 'snapshots.json').read_bytes())
        assert [item['stage'] for item in snapshots] == ['waiting_approval', 'waiting_approval', 'interrupted', 'completed']
        assert len({item['graph_state']['turn_id'] for item in snapshots}) == 1
        assert all(item['graph_state']['model_call_count'] == 1 and item['graph_state']['max_iterations'] == 8
            for item in snapshots[:3])
        mixed[f'{storage}/{decision}'] = record
    before = json.loads((directory / 'p2-tool-wait-batch-notification-before/notification-race' / storage / 'facts.json').read_bytes())
    after = json.loads((directory / 'p2-tool-wait-batch-notification-verified/notification-race' / storage / 'facts.json').read_bytes())
    assert before['status'] == 'running' and before['pending_turn'] != before['expected_pending_turn']
    assert after['status'] == 'interrupted' and after['pending_turn'] == after['expected_pending_turn']
    assert after['worker_dispatches'] and after['worker_failure_guard']['count'] == 0
for decision in ('approved', 'denied'):
    record = json.loads((directory / 'p2-tool-wait-batch-pg-probe/postgres' / decision / 'after-initial-service-close.json').read_bytes())
    assert record['parent_status'] == 'running' and record['snapshot']['stage'] == 'interrupted'
agents = {}
for scenario in ('in-process', 'durable'):
    owned = directory / 'p2-tool-wait-batch-agent-final' / scenario
    record = json.loads((owned / 'verification.json').read_bytes())
    terminal = json.loads((owned / 'target-events-at-cleanup.json').read_bytes())
    assert [event['status'] for event in terminal['events']] == [200, 200, 200]
    assert not terminal['fixture_errors'] and terminal['external_ai_calls'] == 0
    assert terminal['public_credential_scan_passed'] and record['generated_credentials_absent_from_public_output']
    assert record['parent_report']['completed'] == 3 and record['parent_report']['failed'] == 0
    assert record['api_restart_history_preserved']
    assert record['terminal_redelivery_new_http'] == record['terminal_redelivery_new_model_http'] == 0
    assert record['child_wait_restart']['snapshot_preserved']
    assert record['child_wait_restart']['extra_http'] == record['child_wait_restart']['extra_model_http'] == 0
    assert not any(item['parent_report_already_saved'] for item in record['approval_wait_observations'])
    pg = json.loads((owned / 'postgres-finally.json').read_bytes())
    snapshots = [item for item in pg['postgres_snapshot_table'] if item['session_id'] == record['parent_session_id']]
    assert len({item['graph_state']['turn_id'] for item in snapshots}) == 1
    waiting = [item for item in snapshots if item['graph_state'].get('pending_turn', {}).get('waiting_tool')]
    assert len(waiting) == 3 and all(len(item['graph_state']['pending_turn']['waiting_tools']) == 1 for item in waiting)
    assert all(item['graph_state']['model_call_count'] == 1 and item['graph_state']['max_iterations'] == 8 for item in waiting)
    agents[scenario] = {'target_statuses': [200, 200, 200], 'parent_report_completed': 3,
        'physical_model_http': terminal['physical_model_http'], 'snapshot_preserved': True,
        'terminal_extra_http': 0, 'terminal_extra_model_http': 0}
assert 'compileall_exit=0' in (directory / 'p2-tool-wait-batch-compile.log').read_text(encoding='utf8')
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, 'get_secret_value'):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts), 'Configured credential in evidence'
validation.write_text(json.dumps({'baseline': '8df999ef', 'full_goal_complete': False,
    'external_ai_calls': 0, 'mixed_batch_runtime_verified': True, 'controlled_wait_producer': True,
    'default_external_ai_acceptance': False, 'formal_case_run_wait_acceptance': False,
    'notification_stale_write_fixed': True, 'legacy_pending_serializer_body_identical': True,
    'tests': tests, 'mixed_batch_facts': mixed, 'fixed_campaign_agent_facts': agents,
    'historical_artifacts_unchanged': historical, 'protected_files_unchanged': True,
    'three_plans_equal': True, 'plan_sha256': digest(plans[0]), 'configured_credentials_present': False,
    'frontend_changed': False, 'frontend_build_rerun': False,
    'artifact_hashes': {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding='utf8')
print(f'Mixed wait artifacts audited={len(artifacts)}; real PG/HTTP and fixed Agent campaign passed; goal active')
