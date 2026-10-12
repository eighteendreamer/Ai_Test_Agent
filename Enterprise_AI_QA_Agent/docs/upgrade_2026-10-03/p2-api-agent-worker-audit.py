"""Verify successful scoped fixes separately from failed full Agent acceptance."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / 'docs/upgrade_2026-10-03'
validation = directory / 'p2-api-agent-worker-validation.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


historical = {}
for name in ('p2-api-credential-reference-validation.json', 'p2-api-worker-auth-validation.json',
    'p2-receipt-stream-validation.json', 'p5-ui-observation-validation.json'):
    values = json.loads((directory / name).read_bytes())['artifact_hashes']
    for relative, expected in values.items():
        assert digest(root / relative) == expected, 'Historical artifact changed: ' + relative
    historical[name] = len(values)
protected = json.loads((directory / 'p5-default-ui-protected.json').read_bytes())
assert all(digest(root / rel) == expected for rel, expected in protected.items())
plans = [directory / 'PLAN.md',
    Path(r'C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt'),
    Path(r'C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md')]
assert len({digest(p) for p in plans}) == 1
artifacts = []
for path in sorted(directory.glob('p2-api-agent-worker-*')):
    artifacts.extend(path.rglob('*') if path.is_dir() else [path])
artifacts = [p for p in artifacts if p.is_file() and p != validation and p.suffix != '.py']
tests = {}
for p in artifacts:
    if p.suffix == '.xml':
        suites = ET.parse(p).getroot().findall('testsuite')
        tests[p.relative_to(directory).as_posix()] = {key: sum(int(s.get(key, 0)) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}
assert tests['p2-api-agent-worker-final.xml'] == {'tests': 4, 'failures': 2, 'errors': 0, 'skipped': 0}
assert tests['p2-api-agent-worker-auth-regression.xml'] == {'tests': 227, 'failures': 0, 'errors': 0, 'skipped': 21}
assert tests['p2-api-agent-worker-isolated-regression-final/regression.xml'] == {
    'tests': 62, 'failures': 0, 'errors': 0, 'skipped': 1}
assert tests['p2-api-agent-worker-regression.xml']['errors'] == 47
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, 'get_secret_value'):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in p.read_bytes() for p in artifacts), 'Configured credential in evidence'
identity = json.loads((directory / 'p2-api-agent-worker-final/identity/verification.json').read_bytes())
assert identity['same_project_message_accepted'] and identity['same_project_resume_accepted']
assert identity['foreign_project_message_resume_rejected'] and identity['missing_bound_project_rejected']
assert identity['task_rows'] == identity['outbox_rows'] == 3
observations = {}
for scenario, statuses in (('in-process', [200]), ('durable', [200, 401])):
    owned = directory / 'p2-api-agent-worker-final' / scenario
    observation = json.loads((owned / 'observed.json').read_bytes())
    terminal = json.loads((owned / 'target-events-at-cleanup.json').read_bytes())
    assert [e['status'] for e in terminal['events']] == statuses
    assert not terminal['fixture_errors'] and terminal['external_ai_calls'] == 0
    assert terminal['public_credential_scan_passed'] and observation['observed_public_credential_scan_passed']
    assert observation['children_observed'] and all(c['mode_key'] == 'api_testing' for c in observation['children_observed'])
    data = json.loads((owned / 'postgres-finally.json').read_bytes())
    reports = [j['output_payload']['report'] for j in data['postgres_tool_job_table']
        if (j.get('output_payload') or {}).get('report')]
    assert len(reports) == 1 and reports[0]['completed'] == 0 and reports[0]['failed'] == 2 and reports[0]['skipped'] == 1
    observations[scenario] = {'actual_target_statuses': statuses, 'saved_parent_report_counts':
        {k: reports[0][k] for k in ('completed', 'failed', 'skipped')},
        'physical_model_http': terminal['physical_model_http'], 'external_ai_calls': 0}
assert 'compileall_exit=0' in (directory / 'p2-api-agent-worker-compile.log').read_text(encoding='utf8')
validation.write_text(json.dumps({'full_goal_complete': False, 'independent_full_api_acceptance_passed': False,
    'external_ai_calls': 0, 'tests': tests, 'scope_fixes_validated': identity,
    'actual_independent_failures': observations, 'historical_artifacts_unchanged': historical,
    'protected_files_unchanged': True, 'three_plans_equal': True, 'plan_sha256': digest(plans[0]),
    'configured_credentials_present': False, 'artifact_hashes': {
        p.relative_to(root).as_posix(): digest(p) for p in artifacts}}, ensure_ascii=False, indent=2), encoding='utf8')
print('Agent evidence audited=' + str(len(artifacts)) + '; full acceptance remains failed; goal remains active')
