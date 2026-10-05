"""Actual production API, separate Workers and requirement recovery after restart."""
import os

import pytest

from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as production_scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1",
    reason="requires production API process, independent Workers and isolated actual stores")
async def test_production_requirement_review_and_disabled_dispatch_recovery(tmp_path):
    await production_scenario(tmp_path, "md", False, False, verify_requirements=True)
