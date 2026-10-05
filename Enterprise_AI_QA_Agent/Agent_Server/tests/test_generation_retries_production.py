"""Real DOCX, public API, separate generation CLI and disabled-dispatch recovery."""
import os

import pytest

from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION")!="1",reason="requires isolated production processes/stores")
async def test_production_retry_fixed_request_saved_response_and_original_failure(tmp_path):
    await scenario(tmp_path,"docx",False,False,verify_requirements=True,verify_generation=True,verify_retry=True)
