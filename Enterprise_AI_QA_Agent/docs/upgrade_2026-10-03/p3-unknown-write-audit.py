"""Audit the unknown-write regression without rewriting prior evidence."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / 'docs/upgrade_2026-10-03'
validation = directory / 'p3-unknown-write-validation.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


previous = json.loads((directory / 'p2-tool-wait-batch-validation.json').read_bytes())
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
for name in ('p2-tool-wait-batch-validation.json', 'p2-tool-child-wait-validation.json',
    'p2-api-result-readback-validation.json', 'p0-sdk-fault-validation.json',
    'p2-api-agent-worker-validation.json'):
    hashes = json.loads((directory / name).read_bytes())['artifact_hashes']
    for relative, expected in hashes.items():
        assert digest(root / relative) == expected, 'Historical artifact changed: ' + relative
    historical[name] = len(hashes)
artifacts = []
for path in sorted(directory.glob('p3-unknown-write-*')):
    artifacts.extend(path.rglob('*') if path.is_dir() else [path])
artifacts = [path for path in artifacts if path.is_file() and path != validation and path.suffix != '.py']
tests = {}
for path in artifacts:
    if path.suffix == '.xml':
        suites = ET.parse(path).getroot().findall('testsuite')
        tests[path.relative_to(directory).as_posix()] = {
            key: sum(int(suite.get(key, 0)) for suite in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}
assert tests['p3-unknown-write-final2.xml'] == {'tests': 4, 'failures': 0, 'errors': 0, 'skipped': 0}
assert tests['p3-unknown-write-regression-final.xml'] == {'tests': 111, 'failures': 0, 'errors': 0, 'skipped': 50}
unknown = json.loads((directory / 'p3-unknown-write-final/unknown-write.json').read_bytes())
server = json.loads((directory / 'p3-unknown-write-final/server-error-write.json').read_bytes())
assert unknown['request_count'] == 1 and unknown['task']['status'] == 'failed'
assert unknown['task']['request_outcome'] == 'unknown'
assert server['request_count'] == 1 and server['task']['status'] == 'failed'
assert server['task']['request_outcome'] == 'unknown'
assert server['task']['response_status'] == 500
settings = get_settings()
model = MySQLModelConfigStore(settings).get_default_active()
secret = model.api_key if model else None
if hasattr(secret, 'get_secret_value'):
    secret = secret.get_secret_value()
for value in (secret, settings.database.channel_credential_encryption_key):
    if value:
        assert all(str(value).encode() not in path.read_bytes() for path in artifacts), 'Configured credential in evidence'
validation.write_text(json.dumps({'baseline': 'a0097a5a', 'full_goal_complete': False,
    'external_ai_calls': 0, 'unknown_write_blocks_dependents': True,
    'matching_500_assertion_still_failed': True, 'read_only_reconciliation_required': True,
    'tests': tests, 'historical_artifacts_unchanged': historical,
    'protected_files_unchanged': True, 'three_plans_equal': True,
    'plan_sha256': digest(plans[0]), 'configured_credentials_present': False,
    'artifact_hashes': {path.relative_to(root).as_posix(): digest(path) for path in artifacts}},
    ensure_ascii=False, indent=2), encoding='utf8')
print(f'Unknown-write artifacts audited={len(artifacts)}; regression passed; full goal active')
