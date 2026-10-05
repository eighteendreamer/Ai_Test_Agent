"""Real MD/DOCX/PDF production API and independent generation process coverage."""
import os

import pytest

from test_document_impact_production import test_production_fixed_analysis_history_and_process_recovery as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1",reason="requires isolated production API/CLIs and actual stores")
@pytest.mark.parametrize("extension",["md","docx","pdf"])
async def test_production_reviewed_full_drafts_coverage_and_original_history(tmp_path,extension):
    await scenario(tmp_path,extension,False,False,verify_requirements=True,verify_generation=True,
        verify_retry=extension=="docx",verify_coverage=True)
