"""Replay actual failed correction paths before starting another process test."""
import json
from pathlib import Path

import pytest

from test_execution_worker_cli import controlled_case_content
from src.application.test_runs.literal_evidence import literal_value


def test_captured_response_rejects_wrong_fixture_and_resolves_shared_original_case():
    evidence = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-receipt-process-verified/response"
    response = json.loads((evidence / "receipt-response.json").read_bytes())
    receipt = json.loads(response["text"])
    actual_result = json.loads((evidence / "completed.json").read_bytes())["results"][0]
    assert actual_result["status"] == "error" and 'expected_path_tokens' in actual_result["error_message"]
    case = controlled_case_content("http://127.0.0.1")
    wrong = {**case, "检查": case["期望"]}
    del wrong["期望"]
    checks = next(s for s in receipt["steps"] if s["item_key"] == "target")["checks"]
    assert len(checks) == 4
    resolved = []
    for check in checks:
        with pytest.raises(ValueError):
            literal_value(wrong, check["expected_path_tokens"])
        resolved.append(literal_value(case, check["expected_path_tokens"]))
    assert resolved == [200, False, 0, None]
