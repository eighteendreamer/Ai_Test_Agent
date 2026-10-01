"""Verify the report contract against actual pytest sessions, not app bugs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "scripts" / "run_test_quality_gate.py"


def run_gate(output: Path, target: str):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTEST_ADDOPTS="--deselect=unused")
    process = subprocess.run(
        [sys.executable, str(RUNNER), "--profile", "targeted", "--target", target,
         "--output-root", str(output)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    reports = sorted(output.glob("*/report.json"))
    assert reports, process.stdout + process.stderr
    return process, reports[-1], json.loads(reports[-1].read_text(encoding="utf-8"))


def test_real_route_regression_report_preserves_versions_and_results(tmp_path):
    process, path, report = run_gate(tmp_path, "tests/test_integration_route_status.py")
    assert process.returncode == 0, process.stdout + process.stderr
    assert report["status"] == "passed"
    assert report["counts"]["passed"] == report["selected"] == 11
    assert report["release_ready"] is False
    assert report["environment_flags"]["RUN_LIVE_POSTGRES_TESTS"] == "0"
    assert len(report["commit"]) == 40
    manifest = json.loads((path.parent / "sources.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == report["source_sha256"]
    expected = hashlib.sha256((PROJECT_ROOT / "tests/test_integration_route_status.py").read_bytes()).hexdigest()
    assert report["test_files"][str(PROJECT_ROOT / "tests/test_integration_route_status.py")] == expected
    assert (path.parent / "junit.xml").is_file()
    saved = path.read_bytes()
    again, _, _ = run_gate(tmp_path, "tests/test_integration_route_status.py")
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(list(tmp_path.glob("*/report.json"))) == 2
    assert path.read_bytes() == saved


def test_existing_opt_in_browser_tests_are_not_reported_as_passed(tmp_path):
    process, _, report = run_gate(tmp_path, "tests/test_live_browser_lifecycle.py")
    assert process.returncode == 2, process.stdout + process.stderr
    assert report["pytest_exit_code"] == 0
    assert report["status"] == "partial"
    assert report["counts"]["skipped"] == report["selected"] > 0
    assert report["counts"]["passed"] == 0
    assert all(case["phases"][0]["reason"] for case in report["cases"])


def test_reporter_does_not_count_successful_call_with_failed_teardown(tmp_path):
    # A reporter protocol fixture; this is not evidence of an application defect.
    sample = tmp_path / "test_teardown_contract.py"
    sample.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def cleanup():\n"
        "    yield\n"
        "    raise RuntimeError('reporter fixture cleanup failure')\n"
        "def test_call(cleanup):\n"
        "    assert True\n", encoding="utf-8",
    )
    process, _, report = run_gate(tmp_path / "reports", str(sample))
    assert process.returncode == 1, process.stdout + process.stderr
    assert report["status"] == "failed"
    assert report["counts"]["failed"] == 1
    assert report["counts"]["passed"] == 0
    assert report["cases"][0]["phases"][-1]["phase"] == "teardown"


def test_reporter_records_collection_failure_without_passing_empty_suite(tmp_path):
    sample = tmp_path / "test_collection_contract.py"
    sample.write_text("raise RuntimeError('reporter fixture collection failure')\n", encoding="utf-8")
    process, _, report = run_gate(tmp_path / "reports", str(sample))
    assert process.returncode != 0, process.stdout + process.stderr
    assert report["status"] == "failed"
    assert report["collection_errors"]
    assert report["counts"]["passed"] == 0
