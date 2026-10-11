"""Preserve prior evidence and audit only this receipt transport batch."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore

root = Path(__file__).resolve().parents[2]
directory = root / "docs/upgrade_2026-10-03"
validation = directory / "p2-receipt-stream-validation.json"
previous = json.loads((directory / "p5-ui-observation-validation.json").read_bytes())["artifact_hashes"]
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == digest for p, digest in previous.items())
protected = json.loads((directory / "p5-default-ui-protected.json").read_bytes())
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == digest for p, digest in protected.items())
paths = []
for path in sorted(directory.glob("p2-receipt-stream-*")):
    paths.extend(path.rglob("*") if path.is_dir() else [path])
paths = [p for p in paths if p.is_file() and p != validation and p.suffix != ".py"]
secret = MySQLModelConfigStore(get_settings()).get_default_active().api_key
if hasattr(secret, "get_secret_value"):
    secret = secret.get_secret_value()
if secret:
    assert all(str(secret).encode() not in p.read_bytes() for p in paths), "configured credential present"
plans = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({p.read_bytes() for p in plans}) == 1
tests = {}
for p in paths:
    if p.suffix == ".xml":
        suites = ET.parse(p).getroot().findall("testsuite")
        tests[p.name] = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    if p.name == "downloads.json":
        for row in json.loads(p.read_bytes()):
            if row.get("response_public_projection"):
                assert row["original_hash_verified"]
                continue
            raw = (p.parent / row["saved_relative_path"]).read_bytes()
            assert len(raw) == row["size_bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"]
    if p.name.endswith(".request.json"):
        request = json.loads(p.read_bytes())
        assert all(not any(m.get(k) for k in ("reasoning", "reasoning_content", "reasoning_details"))
            for m in request.get("messages", [])), "private provider reasoning in request"
recovery = {}
for boundary in ("invoking", "response"):
    path = directory / "p2-receipt-stream-process" / boundary / "summary.json"
    if path.exists():
        recovery[boundary] = json.loads(path.read_bytes())
        assert recovery[boundary]["response_streaming"] is True
        assert recovery[boundary]["terminal_replay_business_effects"] == 0
        assert recovery[boundary]["terminal_replay_model_calls"] == 0
result = {"full_goal_complete": False, "default_stream_correction_formally_passed": False,
    "tests": tests, "three_plans_equal": True, "prior_artifacts_unchanged": len(previous),
    "protected_files_unchanged": True, "configured_secret_present": False,
    "controlled_process_recovery": recovery,
    "artifact_hashes": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
validation.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print("Receipt artifacts audited=" + str(len(paths)) + "; previous unchanged=" + str(len(previous)))
