"""Keep original failures separate from the fixed-campaign Agent acceptance."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / 'docs/upgrade_2026-10-03'
validation = directory / 'p2-tool-child-wait-validation.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


prior = json.loads((directory / 'p2-api-result-readback-validation.json').read_bytes())
plans = [directory / 'PLAN.md',
    Path(r'C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt'),
    Path(r'C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md')]
for path in plans[1:]:
    assert digest(path) in {prior['plan_sha256'], digest(plans[0])}, 'External plan changed; do not overwrite'
for path in plans[1:]:
    if digest(path) != digest(plans[0]):
        path.write_bytes(plans[0].read_bytes())
assert len({digest(path) for path in plans}) == 1
protected = json.loads((directory / 'p5-default-ui-protected.json').read_bytes())
assert all(digest(root / relative) == expected for relative, expected in protected.items())
historical = {}
for name in ('p2-api-result-readback-validation.json', 'p2-api-agent-worker-validation.json',
    'p0-sdk-fault-validation.json', 'p2-api-credential-reference-validation.json',
    'p2-api-worker-auth-validation.json', 'p2-api-approval-wait-validation.json'):
    hashes = json.loads((directory / name).read_bytes())['artifact_hashes']
    for relative, expected in hashes.items():
        assert digest(root / relative) == expected, 'Historical artifact changed: ' + relative
    historical[name] = len(hashes)
artifacts = []
for path in sorted(directory.glob('p2-tool-child-wait-*')):
    artifacts.extend(path.rglob('*') if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != '.py']
tests = {}
for path in artifacts:
    if path.suffix == '.xml':
        suites = ET.parse(path).getroot().findall('testsuite')
        tests[path.relative_to(directory).as_posix()] = {
            key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}
for relative, expected in {
    'p2-tool-child-wait-first.xml': (2, 0, 0, 0),
    'p2-tool-child-wait-restart.xml': (2, 0, 0, 0),
    'p2-tool-child-wait-final.xml': (2, 0, 0, 0),
    'p2-tool-child-wait-regression.xml': (60, 0, 0, 4),
    'p2-tool-child-wait-pg-races.xml': (4, 1, 0, 0),
    'p2-tool-child-wait-pg-races-final.xml': (4, 1, 0, 0),
    'p2-tool-child-wait-pg-races-verified.xml': (12, 0, 0, 0),
    'p2-tool-child-wait-owner-final.xml': (41, 0, 0, 3),
    'p2-tool-child-wait-anthropic.xml': (14, 0, 0, 0),
    'p2-tool-child-wait-agent-regression/regression.xml': (62, 0, 0, 1),
}.items():
    assert tuple(tests[relative].values()) == expected, relative
facts = {}
for batch in ('first', 'restart', 'final'):
    for scenario in ('in-process', 'durable'):
        owned = directory / f'p2-tool-child-wait-{batch}' / scenario
        record = json.loads((owned / 'verification.json').read_bytes())
        terminal = json.loads((owned / 'target-events-at-cleanup.json').read_bytes())
        assert [event['status'] for event in terminal['events']] == [200, 200, 200]
        assert terminal['external_ai_calls'] == 0 and not terminal['fixture_errors']
        assert terminal['public_credential_scan_passed']
        assert record['generated_credentials_absent_from_public_output']
        assert record['parent_report']['completed'] == 3 and record['parent_report']['failed'] == 0
        assert record['real_child_sessions'] == 3 and record['api_restart_history_preserved']
        assert record['terminal_redelivery_new_http'] == record['terminal_redelivery_new_model_http'] == 0
        assert record['approval_wait_observations']
        assert not any(item['parent_report_already_saved'] for item in record['approval_wait_observations'])
        if batch != 'first':
            assert record['child_wait_restart']['snapshot_preserved']
            assert record['child_wait_restart']['extra_http'] == record['child_wait_restart']['extra_model_http'] == 0
        pg = json.loads((owned / 'postgres-finally.json').read_bytes())
        parent_snapshots = [row for row in pg['postgres_snapshot_table']
            if row['session_id'] == record['parent_session_id']]
        turns = {row['graph_state']['turn_id'] for row in parent_snapshots}
        assert len(turns) == 1
        waiting = [row for row in parent_snapshots if row['graph_state'].get('pending_turn', {}).get('waiting_tool')]
        assert len(waiting) == 3
        assert all(row['stage'] == 'interrupted' and row['graph_state']['model_call_count'] == 1
            and row['graph_state']['max_iterations'] == 8 for row in waiting)
        completed = [row for row in parent_snapshots if row['stage'] == 'completed']
        assert len(completed) == 1 and completed[0]['graph_state']['model_call_count'] == 2
        assert completed[0]['graph_state']['max_iterations'] == 8
        assert len(pg['postgres_tool_job_table']) == 4
        assert len(pg['postgres_test_run_table']) == 0  # Fixed campaign, not a formal case Run.
        facts[f'{batch}/{scenario}'] = {
            'target_statuses': [event['status'] for event in terminal['events']],
            'physical_model_http': terminal['physical_model_http'],
            'parent_report_counts': {key: record['parent_report'][key] for key in ('completed', 'failed', 'skipped')},
            'same_original_turn': True, 'waiting_parent_model_calls': [1, 1, 1],
            'final_parent_model_calls': 2, 'max_iterations': 8,
            'waiting_restart': record.get('child_wait_restart'),
            'terminal_redelivery_new_http': 0, 'terminal_redelivery_new_model_http': 0}
pg_races = json.loads((directory / 'p2-tool-child-wait-pg-races-owner-final/postgres-races.json').read_bytes())
assert pg_races['parallel_claims'] == 8 and pg_races['successful_claims'] == 1
assert pg_races['cancelled_original_preserved'] and pg_races['external_ai_calls'] == 0
anthropic_requests = sorted((directory / 'p2-tool-child-wait-anthropic').rglob('request-*.json'))
assert anthropic_requests
for path in anthropic_requests:
    request = json.loads(path.read_bytes())
    assert request['path'] == '/v1/messages'
    assert 'messages' in request['body'] and 'system' in request['body']
    assert 'input' not in request['body'] and 'instructions' not in request['body']
assert 'compileall_exit=0' in (directory / 'p2-tool-child-wait-compile.log').read_text(encoding='utf8')
build = (directory / 'p2-tool-child-wait-build.log').read_text(encoding='utf8')
assert 'vue-tsc' in build and 'built in 9.70s' in build
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, 'get_secret_value'):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts), 'Configured credential in evidence'
validation.write_text(json.dumps({'baseline': '2df2e1ca', 'full_goal_complete': False,
    'external_ai_calls': 0, 'fixed_campaign_api_agent_verified': True,
    'default_external_ai_acceptance': False, 'formal_case_run_acceptance': False,
    'original_tool_wait_snapshot_resume_verified': True, 'tests': tests,
    'live_agent_facts': facts, 'pg_parallel_claims': 8, 'pg_successful_claims': 1,
    'anthropic_messages_request_count': len(anthropic_requests), 'anthropic_responses_protocol': False,
    'historical_artifacts_unchanged': historical, 'protected_files_unchanged': True,
    'three_plans_equal': True, 'plan_sha256': digest(plans[0]), 'configured_credentials_present': False,
    'artifact_hashes': {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding='utf8')
print(f'Child-wait artifacts audited={len(artifacts)}; fixed campaign passed; full P0-P5 goal active')
