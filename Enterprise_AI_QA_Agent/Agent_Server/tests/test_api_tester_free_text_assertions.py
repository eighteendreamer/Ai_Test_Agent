from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.application.testing.verification_service import VerificationService
from src.main import app
from src.schemas.session import VerificationStatus


def test_api_tester_does_not_pass_unverified_free_text_assertion() -> None:
    response = TestClient(app).get("/api/v1/oauth/providers")
    assert response.status_code == 200
    assert response.json()["providers"]
    runtime = ToolRuntimeService()
    context = ToolExecutionContext(
        session_id="session-1", turn_id="turn-1", trace_id="trace-1",
        user_message="Check the API response", normalized_input="Check the API response",
        context_bundle={},
    )
    result = asyncio.run(runtime._run_api_tester({
        "endpoint": "/api/v1/oauth/providers",
        "method": "GET",
        "response_status": response.status_code,
        "response_body": response.json(),
        "expected_status": 200,
        "assertions": ["providers must be empty"],
    }, context))

    assert result["status"] == "partial"
    assert result["metrics"] == {"check_count": 2, "passed": 1, "failed": 0, "unverified": 1}
    assert result["checks"][0]["passed"] is True
    assert result["checks"][1]["passed"] is None
    assert result["checks"][1]["expected"] == "providers must be empty"

    verification = VerificationService().build_results(
        "session-1", "turn-1", "trace-1",
        [{"tool_key": "api-tester", "call_id": "call-1", "status": result["status"], "output": result}],
    )[0]
    assert verification.status == VerificationStatus.partial
    assert verification.passed_count == 1
    assert verification.failed_count == 0
