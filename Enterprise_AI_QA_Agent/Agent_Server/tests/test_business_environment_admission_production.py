"""Actual independent API, planning/execution CLIs and SDK/HTTP transport."""
import os
import pytest
from test_api_http_origin import fixed_target
from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1",reason="independent production API/CLIs and stores")
async def test_production_allowed_admission_agent_execution_and_terminal_replay(tmp_path):
    with fixed_target(7) as old,fixed_target(9) as new:
        await scenario(tmp_path,"md",False,False,verify_requirements=True,verify_applicability=[old,new],
            verify_environment_planning=True,verify_environment_admission=True)
