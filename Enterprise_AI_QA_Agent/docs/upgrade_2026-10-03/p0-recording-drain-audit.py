"""Validate actual finish evidence and unchanged historical bytes, no model calls."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-drain-validation.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def item(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "size_bytes": len(raw)}


plans = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({path.read_bytes() for path in plans}) == 1
prior = load(DOCS / "p0-recording-embedded-validation.json")
older = load(DOCS / "p2-consumption-return-validation.json")
originals = prior["raw_files"] + older["raw_files"]
request = "".join(f"HEAD:Enterprise_AI_QA_Agent/{row['path']}\n" for row in originals).encode()
raw_git = subprocess.run(["git", "cat-file", "--batch"], input=request, capture_output=True, cwd=ROOT, check=True).stdout
offset = 0
for row in originals:
    end = raw_git.index(b"\n", offset)
    header = raw_git[offset:end].split()
    assert len(header) == 3 and header[1] == b"blob", row["path"]
    size = int(header[2])
    raw = raw_git[end + 1:end + 1 + size]
    assert sha(raw) == row["sha256"] and size == row["size_bytes"], row["path"]
    assert (ROOT / row["path"]).read_bytes() == raw, row["path"]
    offset = end + 1 + size + 1
assert offset == len(raw_git)
for path, expected in prior["unrelated_current_bytes_unchanged"].items():
    assert sha((ROOT / path).read_bytes()) == expected, path

runs = []
for path in sorted(DOCS.glob("p0-recording-drain-*.xml")):
    cases = ET.parse(path).getroot().findall(".//testcase")
    failures = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case for case in cases if case.find("skipped") is not None]
    runs.append({"path": path.relative_to(ROOT).as_posix(), "tests": len(cases), "failed": len(failures),
        "passed": len(cases) - len(failures) - len(skipped), "skipped": len(skipped)})
named = {Path(row["path"]).name: row for row in runs}
assert named["p0-recording-drain-first.xml"]["failed"] == 2
assert named["p0-recording-drain-regression-first.xml"]["failed"] == 1
assert named["p0-recording-drain-final.xml"]["passed"] == 155
assert named["p0-recording-drain-product-final.xml"]["passed"] == 3
assert named["p0-recording-drain-web-fixed.xml"]["passed"] == 26
assert named["p0-recording-drain-routes-final.xml"]["passed"] == 27
assert all(row["skipped"] == 0 for row in runs)
scenarios = []
for name in ("settled", "tail-input", "finish-timeout"):
    folder = DOCS / "p0-recording-drain-product-final" / name
    created, session = load(folder / "created.json"), load(folder / "pg-session-final.json")
    events = load(folder / "pg-events-final.json")
    current = load(folder / "finish-settings-current.json")
    fixed = created["metadata"]["finish_settings"]
    assert session["metadata"]["finish_settings"] == fixed
    assert current["payload"]["timeout_seconds"] == 1
    expected_timeout = 1 if name == "finish-timeout" else 30
    assert fixed["payload"]["timeout_seconds"] == expected_timeout
    assert current["revision"] >= 1 and fixed["revision"] >= 1
    bindings = [json.loads(line) for line in (folder / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
    for event in events:
        source = next(row for row in bindings if row["seq"] == event["seq"])
        for field in ("type", "page", "target", "pixel", "value", "page_effect"):
            assert event[field] == source.get(field), (name, event["seq"], field)
    if name != "finish-timeout":
        assert session["status"] == "completed"
        finish = session["finalize_metrics"]["finish_receipt"]
        assert finish["pg_verified"] and not finish["missing_seqs"]
        assert finish["pending"] == finish["channel_dropped"] == finish["transport_dropped"] == 0
        assert finish["frames_unconfirmed"] == 0
        assert set(finish["published_seqs"]) == {event["seq"] for event in events}
        assert finish["browser_saved_count"] == finish["emitted_count"]
        assert {row["seq"] for row in bindings} == {event["seq"] for event in events}
        assert load(folder / "planning-knowledge.json")[0]["payload"]["graph"]["status"] == "success"
        if name == "tail-input":
            tail = load(folder / "tail-input-result.json")
            assert len(tail["tail_events"]) == 1 and tail["tail_events"][0]["value"] == "最后一步实际输入"
    else:
        assert session["status"] == "failed" and session["finalize_metrics"]["failure_reason"] == "finish_timeout"
        assert session["finalize_metrics"]["finish_receipt"] is None
        http = [json.loads(line) for line in (folder / "electron-http.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(row["url"].endswith("/capture-finished") and row["status"] == 503 for row in http)
    screenshot = load(folder / "screenshot-storage.json")
    raw = (folder / "screenshot-rustfs.png").read_bytes()
    assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"]
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    scenarios.append({"scenario": name, "status": session["status"], "pg_events": len(events),
        "frozen_timeout_seconds": expected_timeout, "current_mysql_timeout_seconds": 1})
cdp = DOCS / "p0-recording-drain-cdp-final"
assert load(cdp / "events.json")[0]["value"] == "最后一步实际输入"
assert load(cdp / "receipt.json")["pending"] == 0

files = []
for path in sorted(DOCS.glob("p0-recording-drain-*")):
    if path == OUTPUT:
        continue
    for child in ([path] if path.is_file() else sorted(path.rglob("*"))):
        if child.is_file() and "__pycache__" not in child.parts:
            files.append(item(child))
            if child.suffix != ".png":
                assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", child.read_text(encoding="utf-8", errors="replace")), child
links = set()
for match in re.finditer(r"\]\((G:/[^)\n]+)\)", plans[0].read_text(encoding="utf-8")):
    target = Path(re.sub(r":\d+$", "", match[1]))
    if "p0-recording-drain-" in str(target) or "P0_RECORDING_DRAIN_DESIGN" in str(target):
        assert target.exists() or target == OUTPUT, str(target)
        links.add(str(target))
report = {"date": "2026-10-08", "baseline": "33f15e99", "plans_byte_identical": True,
    "plan_sha256": sha(plans[0].read_bytes()), "raw_files": files, "prior_original_HEAD_and_current_bytes_exact": len(originals),
    "unrelated_current_bytes_unchanged": prior["unrelated_current_bytes_unchanged"], "xml_runs": runs, "actual_scenarios": scenarios,
    "actual_cdp_finish": True, "screenshot_RustFS_bytes_checked": 3, "limited_credential_format_scan_passed": True,
    "new_local_links_checked": len(links), "full_upgrade_complete": False, "full_recording_acceptance": False,
    "manual_deepseek": False, "model_use": False, "full_App": False, "actual_managed_finish": False,
    "remaining": ["iframe/multi-window finish identities", "pause delivery boundary", "bridge-loss late write/history",
        "configuration product entry", "action-response", "cleaning/full knowledge/AI", "rest of P0-P5 and production migration"],
    "historical_limit": "Two prior Worker summary originals remain unrecovered; unrelated bytes unchanged."}
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Verified {len(files)} batch files, {len(originals)} prior originals; 155 backend, 26 JS, three actual scenarios; full goal incomplete.")
