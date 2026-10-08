"""Audit real saved API/native binding/PG artifacts; never infer AI acceptance."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def facts(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


plans = [DOCS / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
for name, expected in read(DOCS / "p0-electron-scopes-verified-source-sha.json").items():
    assert facts(ROOT / name)["sha256"] == expected, name

prior = {}
for name in ["p0-recording-scopes-validation.json", "p0-recording-boundary-validation.json",
             "p0-recording-drain-validation.json", "p0-recording-embedded-validation.json",
             "p2-consumption-return-validation.json"]:
    for entry in read(DOCS / name)["raw_files"]:
        prior[entry["path"]] = entry
for name, expected in prior.items():
    assert facts(ROOT / name) == {k: expected[k] for k in ("sha256", "size_bytes")}, name
refs = ["HEAD:Enterprise_AI_QA_Agent/" + name for name in prior]
objects = subprocess.check_output(["git", "cat-file", "--batch"], cwd=ROOT,
    input=("\n".join(refs) + "\n").encode())
offset = 0
for name, expected in prior.items():
    end = objects.index(b"\n", offset)
    header = objects[offset:end].split()
    assert header[1] == b"blob", name
    size = int(header[2]); data = objects[end + 1:end + 1 + size]
    assert len(data) == expected["size_bytes"] and hashlib.sha256(data).hexdigest() == expected["sha256"], name
    offset = end + size + 2

protected = read(DOCS / "p0-recording-scopes-validation.json")["unrelated_current_bytes_unchanged"]
for name, expected in protected.items():
    assert facts(ROOT / name)["sha256"] == expected, name

runs = []
for path in sorted(DOCS.glob("p0-electron-scopes-*.xml")):
    suites = ET.parse(path).getroot().findall("testsuite")
    row = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    runs.append({"path": path.relative_to(ROOT).as_posix(), **row})
verified = next(row for row in runs if row["path"].endswith("p0-electron-scopes-verified.xml"))
assert verified == {"path": "docs/upgrade_2026-10-03/p0-electron-scopes-verified.xml",
    "tests": 193, "failures": 0, "errors": 0, "skipped": 0}, verified
web = next(row for row in runs if row["path"].endswith("p0-electron-scopes-web-verified.xml"))
assert web["tests"] == 28 and not (web["failures"] or web["errors"] or web["skipped"])

scenarios = []
for folder in sorted((DOCS / "p0-electron-scopes-verified").iterdir()):
    if not folder.is_dir():
        continue
    final = read(folder / "pg-session-final.json")
    events = read(folder / "pg-events-final.json")
    screenshot = read(folder / "screenshot-storage.json")
    assert facts(folder / "screenshot-rustfs.png") == {k: screenshot[k] for k in ("sha256", "size_bytes")}
    replay = read(folder / "native-replay.json")
    for key in ("forwardedEvents", "bufferedEvents", "droppedMalformed"):
        assert replay["host_before"][key] == replay["host_after"][key]
    assert replay["host_after"]["droppedMalformed"] == 0
    assert not any((event["target"] or {}).get("locators", {}).get("id") == "unrelated-target" for event in events)
    if folder.name in ("finish-timeout", "identity-conflict"):
        assert final["status"] == "failed"
        if folder.name == "identity-conflict":
            assert not read(folder / "identity-conflict.json")["response"]["accepted"]
            assert final["finalize_metrics"]["finish_receipt"]["transport_dropped"] == 1
        scenarios.append({"scenario": folder.name, "status": "expected_failed", "screenshot_verified": True})
        continue
    assert final["status"] == "completed"
    assert (folder / "planning-knowledge.json").is_file() and read(folder / "planning-knowledge.json")
    receipt = final["finalize_metrics"]["finish_receipt"]
    assert receipt["protocol"] == "recording-scopes-v2" and receipt["pg_verified"]
    assert [event["seq"] for event in events] == list(range(receipt["next_seq"]))
    bindings = [json.loads(line) for line in (folder / "binding.jsonl").read_text(encoding="utf-8").splitlines()]
    contexts = {}
    for line in (folder / "native-contexts.jsonl").read_text(encoding="utf-8").splitlines():
        native = json.loads(line)
        if native["method"] == "Runtime.executionContextCreated":
            context = native["params"]["context"]
            if context.get("auxData", {}).get("isDefault"):
                contexts[(native["web_contents_id"], native.get("session_id", ""), context["id"])] = context["auxData"]["frameId"]
    native_sources = {}
    for line in (folder / "native-binding.jsonl").read_text(encoding="utf-8").splitlines():
        native = json.loads(line)
        payload = json.loads(native["envelope"]["payload"])
        if payload.get("recording_document_id"):
            identity = (payload["recording_document_id"], payload["seq"])
            native_sources[identity] = (native["web_contents_id"], native.get("session_id", ""), native["execution_context_id"])
    native_pages = {}
    for event in events:
        scope = event["page"]["recording_scope"]
        native_source = native_sources[(scope["document_id"], scope["local_seq"])]
        assert scope["frame_id"].endswith(":" + contexts[native_source])
        page_id = native_pages.setdefault(native_source[0], scope["page_id"])
        assert page_id == scope["page_id"]
        raw = next(raw for raw in bindings if raw["recording_document_id"] == scope["document_id"] and raw["seq"] == scope["local_seq"])
        assert event["page"] == {**raw["page"], "recording_scope": scope}
        target = {**raw["target"], "in_iframe": True} if scope["parent_frame_id"] and raw.get("target") else raw.get("target")
        assert event["target"] == target
        for key in ("type", "pixel", "value", "page_effect"):
            assert event[key] == raw.get(key)
    for scope in receipt["scopes"]:
        assert scope["confirmed"] and scope["pg_verified"]
        local = [event["page"]["recording_scope"] for event in events if event["page"]["recording_scope"]["document_id"] == scope["document_id"]]
        assert {row["local_seq"] for row in local} == set(range(scope["next_seq"]))
        assert all(all(row[key] == scope[key] for key in ("page_id", "frame_id", "parent_frame_id", "frame_path", "document_url")) for row in local)
    if folder.name in ("nested-frame", "pause-frame"):
        assert {len(scope["frame_path"]) for scope in receipt["scopes"]} >= {0, 1, 2}
        assert any(event["value"] == "跨来源嵌套最后输入" for event in events)
    if folder.name == "popup":
        assert len({scope["page_id"] for scope in receipt["scopes"]}) == 2
        assert len(native_pages) == 2
        assert any(event["value"] == "弹窗最后输入" for event in events)
    scenarios.append({"scenario": folder.name, "status": "completed", "events": len(events),
        "scopes": len(receipt["scopes"]), "knowledge_verified": True, "screenshot_verified": True})
assert len(scenarios) == 12

links = set(re.findall(r"\]\((G:/[^)]+)\)", plans[0].read_text(encoding="utf-8")))
for url in links:
    path = Path(re.sub(r":\d+$", "", url))
    assert path.is_file() or path == DOCS / "p0-electron-scopes-validation.json", url

raw_files = [path for path in DOCS.glob("p0-electron-scopes-*") if path.is_file() and path.name != "p0-electron-scopes-validation.json"]
for folder in DOCS.glob("p0-electron-scopes-*"):
    if folder.is_dir():
        raw_files.extend(path for path in folder.rglob("*") if path.is_file())
for path in raw_files:
    assert not re.search(rb"\bsk-[A-Za-z0-9_-]{20,}", path.read_bytes()), path
record = {"date": "2026-10-08", "baseline": "a6f43697", "plans_byte_identical": True,
    "plan_sha256": facts(plans[0])["sha256"], "raw_files": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), **facts(p)} for p in sorted(raw_files)],
    "xml_runs": runs, "prior_original_HEAD_and_current_bytes_exact": len(prior),
    "unrelated_current_bytes_unchanged": protected, "actual_Electron_API_PG_scenarios": scenarios,
    "native_CDP_context_source_identity_checked": True, "local_links_checked": len(links),
    "limited_credential_format_scan_passed": True, "Electron_scopes_v2": True,
    "full_upgrade_complete": False, "full_recording_acceptance": False, "manual_deepseek": False,
    "model_use": False, "full_App": False, "full_knowledge_originals_for_AI": False,
    "remaining": ["closed/removed frames, BFCache and parallel attach", "network/process recovery",
        "response association and cleaned full knowledge for AI", "manual DeepSeek/full App/scenes/reports",
        "P0-P5 remaining objectives and production migration"]}
(DOCS / "p0-electron-scopes-validation.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Verified {len(raw_files)} new raw files, {len(prior)} prior originals and twelve Electron scenarios; full goal incomplete.")
