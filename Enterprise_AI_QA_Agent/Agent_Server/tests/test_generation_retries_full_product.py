"""Built retry App, real DOCX and independent production generation Workers."""
import os
import pytest
from test_document_impact_full_product import test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='requires built App/Chromium, independent production Workers and actual isolated stores')
async def test_retry_app_lost_reply_frozen_messages_saved_response_drafts_and_archived_history(tmp_path):
    await scenario(tmp_path, verify_requirements=True, verify_generation=True, verify_retry=True)
