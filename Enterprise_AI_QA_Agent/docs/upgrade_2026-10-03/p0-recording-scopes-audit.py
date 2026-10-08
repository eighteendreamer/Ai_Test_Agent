"""Audit actual native scopes, PG fields, RustFS screenshots and original bytes."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-scopes-validation.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


plans = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({path.read_bytes() for path in plans}) == 1
prior = load(DOCS / "p0-recording-boundary-validation.json")
originals = []
for name in ("p0-recording-boundary-validation.json", "p0-recording-drain-validation.json",
    "p0-recording-embedded-validation.json", "p2-consumption-return-validation.json"):
    originals.extend(load(DOCS / name)["raw_files"])
request = "".join(f"HEAD:Enterprise_AI_QA_Agent/{row['path']}\n" for row in originals).encode()
raw_git = subprocess.run(["git", "cat-file", "--batch"], input=request, capture_output=True, check=True, cwd=ROOT).stdout
offset = 0
for row in originals:
    end = raw_git.index(b"\n", offset)
    header = raw_git[offset:end].split()
    assert len(header) == 3 and header[1] == b"blob", row["path"]
    size = int(header[2]); raw = raw_git[end + 1:end + 1 + size]
    assert size == row["size_bytes"] and sha(raw) == row["sha256"], row["path"]
    assert (ROOT / row["path"]).read_bytes() == raw, row["path"]
    offset = end + 1 + size + 1
assert offset == len(raw_git)
for path, expected in prior["unrelated_current_bytes_unchanged"].items():
    assert sha((ROOT / path).read_bytes()) == expected, path

runs = []
for path in sorted(DOCS.glob("p0-recording-scopes-*.xml")):
    cases = ET.parse(path).getroot().findall(".//testcase")
    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    runs.append({"path": path.relative_to(ROOT).as_posix(), "tests": len(cases), "failed": failed,
        "passed": len(cases) - failed - skipped, "skipped": skipped})
named = {Path(row["path"]).name: row for row in runs}
expected_runs = {"reproduction": (0, 4), "fixed-first": (2, 2), "fixed-second": (32, 2),
    "regression-first": (161, 0), "product-first": (12, 0), "product-final": (12, 0),
    "identity": (6, 0), "pause-first": (2, 0), "final": (186, 0), "verified": (186, 0), "web": (26, 0), "web-final": (28, 0)}
for name, expected in expected_runs.items():
    row = named[f"p0-recording-scopes-{name}.xml"]
    assert (row["passed"], row["failed"], row["skipped"]) == (*expected, 0), row

scenarios = []
known_missing_knowledge = []
for kind in ("cdp-attach", "playwright-managed"):
    for name in ("navigation", "cross-origin", "nested-frame", "popup", "pause-new-frame"):
        folder = DOCS / "p0-recording-scopes-verified" / kind / name
        assert not (folder / "finish-error.json").exists(), (kind, name)
        old_folder = DOCS / "p0-recording-scopes-final" / kind / name
        old_error = load(old_folder / "finish-error.json")
        assert old_error["type"] == "TypeError" and "limit" in old_error["message"]
        assert not (old_folder / "planning-knowledge.json").exists()
        known_missing_knowledge.append({"driver": kind, "scenario": name, "error": old_error})
        created, session = load(folder / "created.json"), load(folder / "pg-session.json")
        events, observed = load(folder / "pg-events.json"), load(folder / "events.json")
        receipt, verification = load(folder / "receipt.json"), load(folder / "verification.json")
        assert session["status"] == "completed" and session["driver_kind"] == kind
        assert session["metadata"]["finish_settings"] == created["metadata"]["finish_settings"]
        assert receipt == session["finalize_metrics"]["finish_receipt"] and receipt["protocol"] == "recording-scopes-v2"
        assert receipt["pg_verified"] and receipt["frames_unconfirmed"] == receipt["pending"] == receipt["channel_dropped"] == 0
        assert [event["seq"] for event in events] == list(range(len(events)))
        assert len(events) == len(observed)
        for event, native in zip(events, observed):
            for field in ("seq", "type", "page", "target", "pixel", "value", "page_effect"):
                assert event[field] == native.get(field), (kind, name, event["seq"], field)
        assert all(target in verification["actual_target_ids"] for target in verification["expected_target_ids"])
        scopes = receipt["scopes"]
        assert len({row["document_id"] for row in scopes}) == len(scopes)
        for scope in scopes:
            saved = [event["page"]["recording_scope"] for event in events
                if event["page"]["recording_scope"]["document_id"] == scope["document_id"]]
            assert scope["confirmed"] and scope["pg_verified"]
            assert len(saved) == scope["next_seq"] == scope["received_count"] == scope["emitted_count"]
            assert {row["local_seq"] for row in saved} == set(range(scope["next_seq"]))
            for row in saved:
                for field in ("page_id", "frame_id", "parent_frame_id", "frame_path", "document_url"):
                    assert row[field] == scope[field]
        if name in ("navigation", "cross-origin"):
            assert len(scopes) == 2 and scopes[0]["ended"] and not scopes[1]["ended"]
            assert scopes[0]["page_id"] == scopes[1]["page_id"] and scopes[0]["frame_id"] == scopes[1]["frame_id"]
        if name == "nested-frame":
            assert sorted(len(row["frame_path"]) for row in scopes) == [0, 1, 2]
            assert any(event["type"] == "fill" and event["value"] == "嵌套最后输入" for event in events)
        if name == "popup": assert len({row["page_id"] for row in scopes}) == 2
        if name == "pause-new-frame":
            assert load(folder / "pause.json")["status"] == "paused"
            assert not load(folder / "states.json")[0]["state"]["enabled"]
            assert len(session["finalize_metrics"]["pause_receipts"]) == 1
        unrelated = load(folder / "unrelated-page.json")
        assert unrelated["owned"] is False and unrelated["state"]["enabled"] is False
        assert load(folder / "planning-knowledge.json")[0]["payload"]["graph"]["status"] == "success"
        screenshot, raw = load(folder / "screenshot-storage.json"), (folder / "screenshot-rustfs.png").read_bytes()
        assert sha(raw) == screenshot["sha256"] and len(raw) == screenshot["size_bytes"] and raw.startswith(b"\x89PNG\r\n\x1a\n")
        scenarios.append({"driver": kind, "scenario": name, "status": "completed", "pg_events": len(events), "documents": len(scopes)})

files = []
for path in sorted(DOCS.glob("p0-recording-scopes-*")):
    if path == OUTPUT: continue
    for child in ([path] if path.is_file() else sorted(path.rglob("*"))):
        if child.is_file() and "__pycache__" not in child.parts:
            raw = child.read_bytes()
            files.append({"path": child.relative_to(ROOT).as_posix(), "sha256": sha(raw), "size_bytes": len(raw)})
            if child.suffix != ".png":
                assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", child.read_text(encoding="utf-8", errors="replace")), child
links = set()
for match in re.finditer(r"\]\((G:/[^)\n]+)\)", plans[0].read_text(encoding="utf-8")):
    target = Path(re.sub(r":\d+$", "", match[1]))
    if "p0-recording-scopes-" in str(target) or "P0_RECORDING_SCOPES_DESIGN" in str(target):
        assert target.exists() or target == OUTPUT, target
        links.add(str(target))
report = {"date": "2026-10-08", "baseline": "d03ab1e5", "plans_byte_identical": True,
    "plan_sha256": sha(plans[0].read_bytes()), "raw_files": files, "xml_runs": runs,
    "prior_original_HEAD_and_current_bytes_exact": len(originals),
    "unrelated_current_bytes_unchanged": prior["unrelated_current_bytes_unchanged"],
    "actual_API_PG_graph_scenarios": scenarios, "RustFS_screenshot_bytes_checked": 10,
    "known_test_verification_gap": {
        "affected_runs": ["product-first", "product-final", "pause-first", "final"],
        "cause": "Missing limit and wrong plural query kind; test saved TypeError without rethrow after receipt assignment.",
        "historical_green_XML_is_not_knowledge_success": True,
        "confirmed_missing_knowledge_artifacts": known_missing_knowledge,
        "verified_run_rethrows_all_completion_errors": True,
        "correct_query": "query(project_id, PlanningQuery(kind='recording', source_id=recording_id), 1)"},
    "limited_credential_format_scan_passed": True, "new_local_links_checked": len(links),
    "full_upgrade_complete": False, "full_recording_acceptance": False, "Electron_scopes_v2": False,
    "manual_deepseek": False, "model_use": False, "full_App": False, "full_knowledge_originals_for_AI": False,
    "remaining": ["Electron native scopes", "closed/removed frames and BFCache", "repeated/parallel attach",
        "more network/process recovery", "action-response", "cleaning/full knowledge/AI", "rest of P0-P5 and production migration"],
    "historical_limit": "Two prior Worker summary originals remain unrecovered; unrelated bytes unchanged."}
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Verified {len(files)} current files, {len(originals)} prior originals, ten native API/PG/RustFS scenarios; full goal incomplete.")
