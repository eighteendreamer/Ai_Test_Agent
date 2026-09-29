from __future__ import annotations

import pytest

from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTestingState,
    CampaignReport,
    CredentialSession,
)
from src.modes.api_testing_mode.contracts import (
    PHASE_CAMPAIGN_READY,
    PHASE_REPORT_READY,
    STATE_METADATA_KEY,
)
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime


def _context(session_id: str, state: ApiTestingState) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id=session_id,
        turn_id=f"{session_id}-turn",
        trace_id=f"{session_id}-trace",
        user_message="",
        normalized_input="",
        context_bundle={STATE_METADATA_KEY: state.model_dump(mode="json")},
    )


@pytest.mark.asyncio
async def test_api_runner_report_output_stays_in_its_own_session():
    service = ToolRuntimeService(api_docs_service=object())
    first = _context(
        "session-a",
        ApiTestingState(
            phase=PHASE_CAMPAIGN_READY,
            campaign=ApiTestCampaign(campaign_id="campaign-a", project_name="Project A", tasks=[]),
        ),
    )
    first_output = await service._run_api_test_runner({}, first)
    assert first_output["phase"] == PHASE_REPORT_READY
    assert "Project A" in first_output["report_markdown"]
    assert first_output["artifacts"]

    second = _context(
        "session-b",
        ApiTestingState(
            phase=PHASE_REPORT_READY,
            campaign=ApiTestCampaign(campaign_id="campaign-b", project_name="Project B", tasks=[]),
            report=CampaignReport(campaign_id="campaign-b", project_name="Project B", summary="B result"),
        ),
    )
    second_output = await service._run_api_test_runner({}, second)

    assert second_output["report"]["campaign_id"] == "campaign-b"
    assert "Project A" not in second_output["report_markdown"]
    assert "Project B" in second_output["report_markdown"]
    assert "artifacts" not in second_output


def test_api_runner_restores_credential_identity_from_session_state():
    runtime = ApiTestingModeRuntime(api_docs_service=object())
    state = ApiTestingState(
        credential_session=CredentialSession(
            credential_session_id="credential-a",
            auth_type="bearer",
            token="fixture-value",
            headers={"Authorization": "Bearer fixture-value"},
        )
    )

    restored = runtime._restore_state(_context("session-a", state))

    assert restored.credential_session.credential_session_id == "credential-a"
    assert runtime._credential_manager.build_request_headers("credential-a") == {
        "Authorization": "Bearer fixture-value"
    }
