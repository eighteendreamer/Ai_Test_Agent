"""Real DOCX, built App, production API and independent generation Workers."""
import os
import pytest
from test_document_impact_full_product import test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='requires built App/Chromium, independent production Workers and isolated actual stores')
async def test_incremental_generation_original_request_drafts_failure_and_archive_in_actual_product(tmp_path):
    await scenario(tmp_path,verify_requirements=True,verify_generation=True)
