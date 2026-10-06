"""Actual production API/independent document processes and fixed run evidence."""
import os
import pytest

from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as scenario
from test_requirement_execution import retention_http


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1", reason="requires isolated actual stores and production processes")
async def test_production_api_association_failed_regression_and_restart(tmp_path, retention_http):
    await scenario(tmp_path, "md", False, False, verify_requirements=True, verify_execution=retention_http)
