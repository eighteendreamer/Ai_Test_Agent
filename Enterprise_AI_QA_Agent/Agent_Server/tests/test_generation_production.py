"""Production API and separate generation CLIs over actual fixed document changes."""
import os

import pytest

from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as production_scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1",
    reason="requires production API/independent generation CLI and isolated actual stores")
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_production_incremental_generation_drafts_and_disabled_recovery(tmp_path, extension):
    await production_scenario(tmp_path,extension,False,False,verify_requirements=True,verify_generation=True)
