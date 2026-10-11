"""Audit only this UI batch and preserved prior evidence; no private response export."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore


root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p5-default-ui-validation.json"
paths = []
for candidate in sorted(directory.glob("p5-default-ui-*")):
    paths.extend(candidate.rglob("*") if candidate.is_dir() else [candidate])
paths = [p for p in paths if p.is_file() and p != validation and p.suffix != ".py"]
secret = MySQLModelConfigStore(get_settings()).get_default_active().api_key
if hasattr(secret, "get_secret_value"): secret = secret.get_secret_value()
if secret:
    assert all(str(secret).encode() not in p.read_bytes() for p in paths), "configured credential present"
previous = json.loads((directory / "p2-default-receipt-validation.json").read_bytes())["artifact_hashes"]
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == value for p, value in previous.items())
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == value for p, value in protected.items())
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({p.read_bytes() for p in plans}) == 1
tests = {}
for p in paths:
    if p.suffix == ".xml":
        suites = ET.parse(p).getroot().findall("testsuite")
        tests[p.name] = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
for trial in ("p5-default-ui-live1/positive", "p5-default-ui-live2/positive", "p5-default-ui-live2/negative", "p5-default-ui-live3/positive"):
    p = directory / trial
    for name in ("downloads.json", "downloads-followup.json", "correction-downloads.json"):
        manifest = p / name
        if not manifest.exists(): continue
        for row in json.loads(manifest.read_bytes()):
            exported = p / row["saved_relative_path"]
            if row.get("response_public_projection"): continue
            assert hashlib.sha256(exported.read_bytes()).hexdigest() == row["sha256"]
            assert len(exported.read_bytes()) == row["size_bytes"]
result = {"full_goal_complete": False, "default_ui_formal_passed": False,
    "scope": "actual fixed scene read and click; formal result remains error; regression/Chromium passed",
    "tests": tests, "three_plans_equal": True, "prior_artifacts_unchanged": len(previous),
    "protected_files_unchanged": True, "configured_secret_present": False,
    "artifact_hashes": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
validation.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print("UI artifacts audited=" + str(len(paths)) + "; previous unchanged=" + str(len(previous)))
