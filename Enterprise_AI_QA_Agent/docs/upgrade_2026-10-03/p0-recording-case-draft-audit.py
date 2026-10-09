"""Audit this batch without overwriting historical validation or changing Git."""
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
SOURCES = [
    "Agent_Server/src/api/routes/recordings.py",
    "Agent_Server/src/application/recorder/recorder_session_service.py",
    "Agent_Server/src/application/recorder/recording_case_draft_service.py",
    "Agent_Server/src/application/test_cases/case_service.py",
    "Agent_Server/src/application/test_cases/case_store.py",
    "Agent_Server/src/infrastructure/recording_store.py",
    "Agent_Server/src/schemas/recording.py",
    "Agent_Server/tests/test_recording_case_draft.py",
    "Agent_Server/tests/test_recording_case_draft_route.py",
    "Agent_Server/tests/test_recording_draft_evidence.py",
    "Agent_Server/tests/test_recording_routes.py",
    "agent_web/src/components/chat/RecordingTimelinePanel.vue",
    "agent_web/src/features/recorder/recordingApi.ts",
    "agent_web/src/components/chat/RecordingTimelinePanel.test.ts",
    "agent_web/src/features/recorder/recordingApi.test.ts",
    "agent_web/src/locales/en-US.ts",
    "agent_web/src/locales/zh-CN.ts",
    "docs/upgrade_2026-10-03/.gitattributes",
    "docs/upgrade_2026-10-03/PLAN.md",
    "docs/upgrade_2026-10-03/IMPLEMENTATION.md",
    "docs/upgrade_2026-10-03/P0_RECORDING_CASE_DRAFT_ROUTE_RESEARCH.md",
    "docs/upgrade_2026-10-03/p0-recording-case-draft-audit.py",
]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def main():
    evidence = set()
    for pattern in ("p0-recording-case-draft-*", "p0-recording-draft-route-*"):
        for path in DOCS.glob(pattern):
            if path.name in {"p0-recording-case-draft-validation.json", Path(__file__).name}:
                continue
            evidence.update(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else evidence.add(path)
    old = read(DOCS / "p0-recording-draft-validation.json")
    for row in old["file_hashes"]:
        actual = fingerprint(DOCS / row["path"])
        assert actual["sha256"] == row["sha256"] and actual["bytes"] == row["bytes"], row["path"]
    plan = DOCS / "PLAN.md"
    copies = [
        Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md"),
    ]
    assert all(p.read_bytes() == plan.read_bytes() for p in copies)
    assert b"\r\r\n" not in plan.read_bytes()
    actual = DOCS / "p0-recording-draft-route-http-verified"
    concurrent = read(actual / "route-concurrent.json")
    assert sorted(r["status"] for r in concurrent) == [201, 201, 503]
    original = read(actual / "route-response.json")
    assert read(actual / "route-replay.json")["body"] == original
    restarted = read(actual / "route-restarted.json")
    assert restarted["project_case_count"] == restarted["version_count"] == 1
    assert restarted["restored"] == original
    assert read(actual / "route-archived.json")["version"] == original["version"]
    session = read(actual / "pg-session.json")
    assert session["step_count"] == 8 and session["finalize_metrics"]["integrity"]["degraded"] is False
    traces = [json.loads(line) for line in (actual / "server-http.jsonl").read_text().splitlines()]
    assert len(traces) == 11
    # Failed-test logs/XML intentionally echo fixture source with this synthetic
    # sentinel. It must be absent from captured JSON data, not from source traces.
    for path in evidence:
        if path.suffix == ".json":
            assert b"synthetic-redaction-sentinel" not in path.read_bytes(), str(path)
    suite = ET.parse(DOCS / "p0-recording-case-draft-recovery-expanded.xml").getroot()[0]
    assert int(suite.attrib["tests"]) - int(suite.attrib["skipped"]) == 221
    assert int(suite.attrib["skipped"]) == 72
    assert int(suite.attrib["failures"]) == int(suite.attrib["errors"]) == 0
    protected = [ROOT / ".gitignore", DOCS / "p2-planning-worker-cli.json",
                 DOCS / "p2-regression-planning-worker-cli.json"]
    result = {
        "protocol": "recording-case-draft-route-v2", "date": "2026-10-10",
        "head_before": "2509a96b",
        "scope": "recording conversion entry, original V1 recovery, session-scoped paging",
        "tests": {"recording": "221 passed, 72 skipped, 1 existing warning, 17.10s",
                  "physical_http_browser_store": "1 passed, 2 deselected, 6.41s",
                  "frontend": "18 passed, 3 files, 1.98s",
                  "build": "vue-tsc/Vite exit 0, 3288 modules, 7.86s",
                  "compileall": "7 production files, 4 tests, exit 0"},
        "physical_http": {"requests": 11, "concurrent_statuses": [201, 201, 503],
                          "cases": 1, "versions": 1, "original_replay_rebuild_archived": True,
                          "project_session_filter_before_paging": True,
                          "step_count": 8, "integrity_degraded": False},
        "storage": {"config": "MySQL", "business": "PostgreSQL", "large_evidence": "RustFS",
                    "projection": "Memgraph", "new_tables_migrations_config_dependencies": False},
        "prior_recording_evidence_checked": len(old["file_hashes"]),
        "plans": {"copies_equal": 3, "sha256": hashlib.sha256(plan.read_bytes()).hexdigest()},
        "protected_unrelated_files": [fingerprint(p) for p in protected],
        "source_files": [fingerprint(ROOT / p) for p in SOURCES],
        "evidence_files": [fingerprint(p) for p in sorted(evidence)],
        "not_proven": ["full Vue App and independent production main process",
                       "default Step model and AI business quality", "replay report API/persistence",
                       "scenes steps aliases cleaning omissions failure localization",
                       "human DeepSeek authenticated business recording", "full P0-P5 completion"],
    }
    (DOCS / "p0-recording-case-draft-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(evidence)} new evidence files, {len(SOURCES)} source/docs files, "
          f"{len(old['file_hashes'])} prior evidence files and 3 equal plans")


if __name__ == "__main__":
    main()
