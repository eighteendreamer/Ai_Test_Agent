"""Verify actual pause/terminal evidence; no model calls or history rewriting."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-boundary-validation.json"


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
prior = load(DOCS / "p0-recording-drain-validation.json")
originals = prior["raw_files"] + load(DOCS / "p0-recording-embedded-validation.json")["raw_files"] + load(DOCS / "p2-consumption-return-validation.json")["raw_files"]
request = "".join(f"HEAD:Enterprise_AI_QA_Agent/{row['path']}\n" for row in originals).encode()
git_raw = subprocess.run(["git", "cat-file", "--batch"], input=request, capture_output=True, cwd=ROOT, check=True).stdout
offset = 0
for row in originals:
    end = git_raw.index(b"\n", offset)
    header = git_raw[offset:end].split()
    assert len(header) == 3 and header[1] == b"blob", row["path"]
    size = int(header[2])
    raw = git_raw[end + 1:end + 1 + size]
    assert sha(raw) == row["sha256"] and size == row["size_bytes"], row["path"]
    assert (ROOT / row["path"]).read_bytes() == raw, row["path"]
    offset = end + 1 + size + 1
assert offset == len(git_raw)
for path, expected in prior["unrelated_current_bytes_unchanged"].items():
    assert sha((ROOT / path).read_bytes()) == expected, path

runs = []
for path in sorted(DOCS.glob("p0-recording-boundary-*.xml")):
    cases = ET.parse(path).getroot().findall(".//testcase")
    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    runs.append({"path": path.relative_to(ROOT).as_posix(), "tests": len(cases), "failed": failed,
        "passed": len(cases) - failed - skipped, "skipped": skipped})
named = {Path(row["path"]).name: row for row in runs}
expected_runs = {"reproduction": (0, 2), "fixed": (2, 0), "regression-first": (153, 0),
    "protocol-first": (6, 0), "final": (161, 0), "product-final": (5, 0)}
for name, (passed, failed) in expected_runs.items():
    row = named[f"p0-recording-boundary-{name}.xml"]
    assert (row["passed"], row["failed"], row["skipped"]) == (passed, failed, 0), row

repro = DOCS / "p0-recording-boundary-reproduction"
assert not [row for row in load(repro / "pause-input/pause-input-after.json")["events"]
    if row["type"] == "fill" and row["value"] == "暂停前最后输入"]
late = load(repro / "bridge-loss/late-terminal.json")
assert late["http_status"] == 200
assert late["before_session"]["updated_at"] != late["after_session"]["updated_at"]
scenarios = []
for name in ("settled", "tail-input", "finish-timeout", "pause-input", "bridge-loss"):
    folder = DOCS / "p0-recording-boundary-product-final" / name
    created, session = load(folder / "created.json"), load(folder / "pg-session-final.json")
    events = load(folder / "pg-events-final.json")
    assert session["metadata"]["finish_settings"] == created["metadata"]["finish_settings"]
    bindings = [json.loads(line) for line in (folder / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
    for event in events:
        original = next(row for row in bindings if row["seq"] == event["seq"])
        for field in ("type", "page", "target", "pixel", "value", "page_effect"):
            assert event[field] == original.get(field), (name, event["seq"], field)
    if name != "finish-timeout":
        assert session["status"] == "completed"
        assert {row["seq"] for row in bindings} == {event["seq"] for event in events}
        finish = session["finalize_metrics"]["finish_receipt"]
        assert finish["operation"] == "stop" and finish["pg_verified"] and not finish["missing_seqs"]
        assert finish["pending"] == finish["frames_unconfirmed"] == finish["channel_dropped"] == finish["transport_dropped"] == 0
        assert set(finish["published_seqs"]) == {event["seq"] for event in events}
        assert finish["browser_saved_count"] == finish["emitted_count"]
        assert load(folder / "planning-knowledge.json")[0]["payload"]["graph"]["status"] == "success"
        pauses = session["finalize_metrics"]["pause_receipts"]
        assert len(pauses) == 1 and pauses[0]["operation"] == "pause" and pauses[0]["pg_verified"]
    else:
        assert session["status"] == "failed" and session["finalize_metrics"]["failure_reason"] == "finish_timeout"
        http = [json.loads(line) for line in (folder / "electron-http.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(row["url"].endswith("/capture-finished") and row["status"] == 503 for row in http)
    if name == "pause-input":
        paused = load(folder / "pause-input-after.json")
        assert paused["session"]["status"] == "paused"
        tail_events = [row for row in paused["events"] if row["type"] == "fill" and row["value"] == "暂停前最后输入"]
        assert len(tail_events) == 1
    if name == "bridge-loss":
        late = load(folder / "late-terminal.json")
        assert late["http_status"] == 409 and late["before_session"] == late["after_session"]
        assert late["before_events"] == late["after_events"]
    screenshot = load(folder / "screenshot-storage.json")
    raw = (folder / "screenshot-rustfs.png").read_bytes()
    assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"] and raw.startswith(b"\x89PNG\r\n\x1a\n")
    scenarios.append({"name": name, "status": session["status"], "pg_events": len(events)})

for terminal in ("completed", "failed", "discarded"):
    folder = DOCS / "p0-recording-boundary-pg-final" / terminal
    lock, result = load(folder / "lock-observed.json"), load(folder / "terminal-write.json")
    assert lock["wait_event_type"] == "Lock" and lock["blocked_append"] and lock["terminal"] == terminal
    assert result["before"] == result["after"] and result["before"]["status"] == terminal
    assert result["before_events"] == result["after_events"] and len(result["after_events"]) == 8
    assert result["parallel_accepted"] == result["after"]["step_count"] == 8
    assert result["replay_status"] == result["new_event_status"] == 409 and result["missing_status"] == 404
cdp = DOCS / "p0-recording-boundary-pause-cdp-final"
assert [row["value"] for row in load(cdp / "events.json") if row["type"] == "fill"] == ["暂停前最后输入", "继续后的输入"]
receipts = load(cdp / "receipts.json")
assert receipts["pause"]["operation"] == "pause" and receipts["stop"]["operation"] == "stop"
assert receipts["pause"]["pending"] == receipts["stop"]["pending"] == 0

files = []
for path in sorted(DOCS.glob("p0-recording-boundary-*")):
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
    if "p0-recording-boundary-" in str(target) or "P0_RECORDING_BOUNDARY_DESIGN" in str(target):
        assert target.exists() or target == OUTPUT, target
        links.add(str(target))
report = {"date": "2026-10-08", "baseline": "bd972911", "plans_byte_identical": True,
    "plan_sha256": sha(plans[0].read_bytes()), "raw_files": files, "xml_runs": runs,
    "prior_original_HEAD_and_current_bytes_exact": len(originals),
    "unrelated_current_bytes_unchanged": prior["unrelated_current_bytes_unchanged"],
    "actual_scenarios": scenarios, "actual_PG_terminal_Lock_waits": 3, "parallel_event_count": 8,
    "actual_CDP_pause_resume": True, "screenshot_RustFS_bytes_checked": 5,
    "limited_credential_format_scan_passed": True, "new_local_links_checked": len(links),
    "full_upgrade_complete": False, "full_recording_acceptance": False, "manual_deepseek": False,
    "model_use": False, "full_App": False, "actual_managed_finish": False,
    "remaining": ["iframe/multi-window identities", "more network/process recovery", "configuration product entry",
        "action-response", "cleaning/full knowledge/AI", "rest of P0-P5 and production migration"],
    "historical_limit": "Two prior Worker summary originals remain unrecovered; unrelated bytes unchanged."}
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Verified {len(files)} current files and {len(originals)} prior originals; 161 backend and 5 actual Electron scenarios; full goal incomplete.")
