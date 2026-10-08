"""Audit this batch's actual results and preserved bytes, without retesting models."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
OUTPUT = DOCS / "p0-recording-embedded-validation.json"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def entry(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(raw), "size_bytes": len(raw)}


plans = [DOCS / "PLAN.md",
    Path("C:/Users/32734/.codex/attachments/ec189608-bb79-41e6-925a-3d70ba54a017/已粘贴的文本.txt"),
    Path("C:/Users/32734/.codex/plans/01a1006b-4507-7e32-b83d-af8d1598e810/01a1009d-d8b2-7dc1-af17-13a77fada84d/PLAN.md")]
assert len({path.read_bytes() for path in plans}) == 1
prior = load(DOCS / "p2-consumption-return-validation.json")
for path, expected in prior["unrelated_current_bytes_unchanged"].items():
    assert digest((ROOT / path).read_bytes()) == expected, path

# One Git batch avoids launching hundreds of commands for the prior large originals.
records = prior["raw_files"]
requests = "".join(f"HEAD:Enterprise_AI_QA_Agent/{item['path']}\n" for item in records).encode()
stored = subprocess.run(["git", "cat-file", "--batch"], input=requests, cwd=ROOT, check=True, capture_output=True).stdout
offset = 0
for item in records:
    end = stored.index(b"\n", offset)
    header = stored[offset:end].split()
    assert len(header) == 3 and header[1] == b"blob", item["path"]
    size = int(header[2])
    raw = stored[end + 1:end + 1 + size]
    assert size == item["size_bytes"] and digest(raw) == item["sha256"], item["path"]
    assert (ROOT / item["path"]).read_bytes() == raw, item["path"]
    offset = end + 1 + size + 1
assert offset == len(stored)

xml_runs = []
for path in sorted(DOCS.glob("p0-recording-embedded-*.xml")):
    xml = ET.parse(path).getroot()
    cases = xml.findall(".//testcase")
    failed = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case for case in cases if case.find("skipped") is not None]
    xml_runs.append({"path": path.relative_to(ROOT).as_posix(), "tests": len(cases), "failed": len(failed), "skipped": len(skipped),
        "passed": len(cases) - len(failed) - len(skipped), "failed_nodes": [case.attrib["name"] for case in failed]})
by_name = {Path(item["path"]).name: item for item in xml_runs}
assert by_name["p0-recording-embedded-final.xml"]["passed"] == 1
assert by_name["p0-recording-embedded-regression.xml"]["passed"] == 143
assert by_name["p0-recording-embedded-web.xml"]["passed"] == 23
assert by_name["p0-recording-embedded-scenarios.xml"]["passed"] == 1
assert by_name["p0-recording-embedded-scenarios.xml"]["failed"] == 1
assert all(item["skipped"] == 0 for item in xml_runs)

scenario = DOCS / "p0-recording-embedded-scenarios"
settled = scenario / "settled"
session = load(settled / "pg-session-final.json")
events = load(settled / "pg-events-final.json")
assert session["status"] == "completed" and session["step_count"] == len(events)
assert len({event["seq"] for event in events}) == len(events)
assert len([event for event in events if event["type"] == "click"]) == 4
duplicate = load(settled / "duplicate-delivery.json")
assert duplicate["accepted"] == 0 and duplicate["duplicates"] == len(events)
assert load(settled / "projection.json")["status"] == "success"
knowledge = load(settled / "planning-knowledge.json")
assert knowledge[0]["payload"]["graph"]["status"] == "success"
assert "close command from backend" in (settled / "electron.stdout").read_text(encoding="utf-8")
binding = [json.loads(line) for line in (settled / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
for event in events:
    original = next(item for item in binding if item["seq"] == event["seq"])
    for field in ("type", "page", "target", "pixel", "value", "page_effect"):
        assert event[field] == original.get(field), (event["seq"], field)

tail = scenario / "tail-input"
before = load(tail / "tail-input-before-stop.json")
result = load(tail / "tail-input-result.json")
assert before["value"] == "最后一步实际输入" and before["recorder"]["enabled"]
assert result["status"] == "completed" and not result["tail_events"]
assert result["finalize_metrics"]["integrity"]["reconciled"]
assert not result["finalize_metrics"]["integrity"]["degraded"]
assert before["recorder"]["seq"] > len(load(tail / "pg-events-final.json"))
for folder in (DOCS / "p0-recording-embedded-verified", DOCS / "p0-recording-embedded-final", settled, tail):
    metadata = load(folder / "screenshot-storage.json")
    raw = (folder / "screenshot-rustfs.png").read_bytes()
    assert digest(raw) == metadata["sha256"] and len(raw) == metadata["size_bytes"]
    assert raw.startswith(b"\x89PNG\r\n\x1a\n") and metadata["uri"].startswith("rustfs://")

raw_files = []
for path in sorted(DOCS.glob("p0-recording-embedded-*")):
    if path == OUTPUT:
        continue
    children = [path] if path.is_file() else sorted(path.rglob("*"))
    for child in children:
        if child.is_file() and "__pycache__" not in child.parts:
            raw_files.append(entry(child))
            if child.suffix in {".json", ".jsonl", ".log", ".stdout", ".stderr"}:
                text = child.read_text(encoding="utf-8", errors="replace")
                assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", text), child

sources = [ROOT / "agent_web/electron/recorder-window.mjs", ROOT / "agent_web/tests/fixtures/recorder-live.mjs",
    ROOT / "Agent_Server/tests/test_recording_embedded_product.py"]
links = set()
for source in (DOCS / "PLAN.md", DOCS / "P0_RECORDING_EMBEDDED_RESEARCH.md"):
    for match in re.finditer(r"\]\((G:/[^)\n]+)\)", source.read_text(encoding="utf-8")):
        target = re.sub(r":\d+$", "", match[1])
        if "p0-recording-embedded-" in target or "P0_RECORDING_EMBEDDED_RESEARCH" in target:
            assert Path(target).exists() or Path(target) == OUTPUT, target
            links.add(target)

report = {"date": "2026-10-08", "baseline": "05f7403d", "plans_byte_identical": True, "plan_sha256": digest(plans[0].read_bytes()),
    "sources": [entry(path) for path in sources], "raw_files": raw_files, "xml_runs": xml_runs,
    "prior_raw_HEAD_and_current_bytes_exact": len(records), "unrelated_current_bytes_unchanged": prior["unrelated_current_bytes_unchanged"],
    "settled_original_Electron_HTTP_storage_verified": True, "event_fields_preserved_from_binding": True,
    "tail_input_actual_failure_preserved": True, "tail_input_status": result["status"], "tail_fill_count": 0,
    "screenshot_RustFS_bytes_checked": 4, "new_local_links_checked": len(links), "limited_credential_format_scan_passed": True,
    "manual_deepseek": False, "full_Vue_App": False, "model_invoked_by_scenario": False, "end_drain_implemented": False,
    "full_upgrade_complete": False, "historical_limit": "Two previous Worker summaries' original bytes remain unrecovered; protected current bytes unchanged."}
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Audit passed: {len(raw_files)} batch files, {len(records)} prior original files, settled pass and tail failure preserved; three plans identical.")
