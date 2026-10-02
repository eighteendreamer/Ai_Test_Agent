"""Run a declared pytest scope and preserve its coverage and execution evidence.

Uses the public pytest hooks and pytest.main documented at
https://docs.pytest.org/en/stable/reference/reference.html. Each invocation gets
a new result directory; a partial diagnostic run is never a release approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_FLAGS = (
    "RUN_LIVE_BROWSER_TESTS", "RUN_LIVE_CDP_RECORDING", "RUN_LIVE_DEEP_AGENTS_HITL",
    "RUN_LIVE_DOCKER_TESTS", "RUN_LIVE_MYSQL_TESTS", "RUN_LIVE_P5_ACCEPTANCE", "RUN_LIVE_POSTGRES_CAPACITY",
    "RUN_LIVE_POSTGRES_SOAK", "RUN_LIVE_POSTGRES_TESTS", "RUN_LIVE_RECORDING_MEMGRAPH",
    "RUN_LIVE_RECORDING_PG", "RUN_LIVE_REDIS_VECTOR_TESTS", "RUN_LIVE_REPLAY",
    "RUN_LIVE_TEST_CASE_EMBEDDING_TESTS", "RUN_LIVE_VECTOR_REBUILD_PIPELINE_TESTS",
)
# Core groups are intentionally explicit, rather than claiming every external
# service has been exercised merely because one PostgreSQL test passed.
PROFILES = {
    "diagnostic": {"targets": ("tests",), "flags": ()},
    "targeted": {"targets": (), "flags": ()},
    "postgres-core": {
        "targets": ("tests/test_live_compaction_ownership.py",),
        "flags": ("RUN_LIVE_POSTGRES_TESTS",),
    },
    "session-store": {
        "targets": ("tests/test_session_store_value_contract.py",),
        "flags": ("RUN_LIVE_POSTGRES_TESTS",),
    },
    "metrics-core": {
        "targets": ("tests/test_metrics_core.py", "tests/test_metrics_business.py", "tests/test_metrics_multiprocess.py"),
        "flags": ("RUN_LIVE_POSTGRES_TESTS",),
    },
    "mcp-core": {
        "targets": ("tests/test_mcp_management_route_status.py",),
        "flags": ("RUN_LIVE_POSTGRES_TESTS",),
    },
    "sql-lifecycle": {
        "targets": ("tests/test_lifespan_resource_cleanup.py",),
        "flags": ("RUN_LIVE_POSTGRES_TESTS", "RUN_LIVE_MYSQL_TESTS"),
    },
    "redis-core": {
        "targets": ("tests/test_live_redis_task_delivery.py", "tests/test_live_hot_memory_store.py"),
        "flags": ("RUN_LIVE_POSTGRES_TESTS",),
    },
    "browser-core": {
        "targets": ("tests/test_live_browser_lifecycle.py",),
        "flags": ("RUN_LIVE_BROWSER_TESTS",),
    },
    "docker-core": {
        "targets": ("tests/test_live_resource_leases.py",),
        "flags": ("RUN_LIVE_DOCKER_TESTS", "RUN_LIVE_POSTGRES_TESTS"),
    },
    "model-hitl": {
        "targets": ("tests/test_live_deep_agents_hitl.py",),
        "flags": ("RUN_LIVE_DEEP_AGENTS_HITL", "RUN_LIVE_POSTGRES_TESTS"),
    },
}


class ExecutionEvidence:
    """Preserve phase results so teardown errors cannot become passing cases."""

    def __init__(self) -> None:
        self.cases: dict[str, dict] = {}
        self.deselected: list[str] = []
        self.collection_errors: list[str] = []
        self.test_files: dict[str, str] = {}

    def pytest_collection_finish(self, session) -> None:
        for item in session.items:
            self.cases[item.nodeid] = {"nodeid": item.nodeid, "phases": [], "outcome": "not_run"}
            path = Path(item.path)
            self.test_files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    def pytest_deselected(self, items) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report) -> None:
        case = self.cases.setdefault(report.nodeid, {"nodeid": report.nodeid, "phases": [], "outcome": "not_run"})
        phase = {"phase": report.when, "outcome": report.outcome, "seconds": report.duration}
        expected_failure = hasattr(report, "wasxfail")
        if report.skipped:
            # Skip reasons are part of the execution contract. Failure text,
            # captured logs and local variables stay out of this JSON report.
            phase["reason"] = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else "skipped"
        if expected_failure:
            phase["expected_failure"] = True
        case["phases"].append(phase)

    def summary(self) -> dict:
        for case in self.cases.values():
            phases = case["phases"]
            if any(p["outcome"] == "failed" for p in phases):
                outcome = "failed"
            elif any(p.get("expected_failure") and p["outcome"] == "passed" for p in phases):
                outcome = "xpassed"
            elif any(p.get("expected_failure") for p in phases):
                outcome = "xfailed"
            elif any(p["outcome"] == "skipped" for p in phases):
                outcome = "skipped"
            elif {p["phase"] for p in phases if p["outcome"] == "passed"} == {"setup", "call", "teardown"}:
                outcome = "passed"
            else:
                outcome = "not_run"
            case["outcome"] = outcome
        counts = dict.fromkeys(("passed", "failed", "skipped", "xfailed", "xpassed", "not_run"), 0)
        counts.update(Counter(case["outcome"] for case in self.cases.values()))
        return {
            "selected": len(self.cases), "counts": counts, "cases": list(self.cases.values()),
            "deselected": self.deselected, "collection_errors": self.collection_errors,
            "test_files": self.test_files,
        }


def git_output(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, check=True)
    return result.stdout.decode("utf-8")


def source_manifest() -> dict:
    tracked = set(git_output("ls-files", "--", "tests").splitlines())
    entries = []
    paths = [*PROJECT_ROOT.joinpath("src").rglob("*.py"), *PROJECT_ROOT.joinpath("tests").rglob("*.py")]
    paths += list(PROJECT_ROOT.joinpath("scripts").glob("*.py"))
    paths += [PROJECT_ROOT / "pyproject.toml"]
    for path in sorted(paths):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        entry = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if relative.startswith("tests/"):
            entry["tracked"] = relative in tracked
        entries.append(entry)
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(), "files": entries,
        "unversioned_tests": [e["path"] for e in entries if e.get("tracked") is False],
    }


def classify_run(evidence: dict, pytest_exit_code: int, *, diagnostic: bool) -> tuple[str, int]:
    if pytest_exit_code != 0 or evidence["collection_errors"] or evidence["counts"]["failed"]:
        return "failed", pytest_exit_code or 1
    complete = evidence["selected"] > 0 and evidence["counts"]["passed"] == evidence["selected"] and not evidence["deselected"]
    if complete:
        return "passed", 0
    # Diagnostic exit 0 only means its executed assertions passed. The report
    # explicitly says partial; all environment profiles require full execution.
    return "partial", 0 if diagnostic else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--target", action="append", default=[], help="pytest node ID; only for the targeted profile")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts" / "test-gates")
    args = parser.parse_args()
    if bool(args.target) != (args.profile == "targeted"):
        parser.error("--target is required for targeted, and cannot replace a fixed profile's scope")
    if any(target.startswith("-") for target in args.target):
        parser.error("--target accepts test paths/node IDs, not pytest options")

    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    enabled = PROFILES[args.profile]["flags"]
    for flag in LIVE_FLAGS:
        os.environ[flag] = "1" if flag in enabled else "0"
    # Profiles own selection; inherited pytest options must not silently hide
    # tests or redirect reports. Plugin versions remain recorded below.
    os.environ.pop("PYTEST_ADDOPTS", None)
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S')}-{args.profile}-{uuid4().hex[:12]}"
    output = args.output_root.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    targets = args.target or list(PROFILES[args.profile]["targets"])
    pytest_args = [*targets, "-q", "--tb=short", "-o", "addopts=", f"--junitxml={output / 'junit.xml'}"]
    manifest = source_manifest()
    metadata = {
        "schema_version": 1, "run_id": run_id, "profile": args.profile,
        "started_at": started.isoformat(), "commit": git_output("rev-parse", "HEAD").strip(),
        "dirty": bool(git_output("status", "--porcelain", "--", ".")),
        "platform": platform.platform(), "python": platform.python_version(),
        "dependencies": sorted({f"{d.metadata['Name']}=={d.version}" for d in distributions()}),
        "environment_flags": {flag: os.environ[flag] for flag in LIVE_FLAGS},
        "pytest_args": pytest_args, "release_ready": False,
        "scope": "diagnostic" if args.profile == "diagnostic" else "declared_tests_only",
        "source_manifest": "sources.json", "source_sha256": manifest["sha256"],
        "status": "running",
    }
    (output / "sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # Save metadata before collection, leaving a visible running record even if
    # the process is killed. A rerun always uses a new directory.
    report_path = output / "report.json"
    report_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    observer = ExecutionEvidence()
    pytest_exit = int(pytest.main(pytest_args, plugins=[observer]))
    evidence = observer.summary()
    status, gate_exit = classify_run(evidence, pytest_exit, diagnostic=args.profile == "diagnostic")
    metadata.update(evidence)
    metadata.update({"finished_at": datetime.now(UTC).isoformat(), "status": status,
                     "pytest_exit_code": pytest_exit, "gate_exit_code": gate_exit})
    report_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"profile": args.profile, "status": status, "counts": evidence["counts"],
                      "gate_exit_code": gate_exit, "report": str(report_path)}, ensure_ascii=False))
    return gate_exit


if __name__ == "__main__":
    raise SystemExit(main())
