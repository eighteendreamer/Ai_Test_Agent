"""Coverage editing and immutable histories in a real DOCX built-App workflow."""
import os
import pytest
from test_document_impact_full_product import test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='requires built App/Chromium, independent production Workers and actual isolated stores')
async def test_coverage_app_complete_versions_reviews_history_lost_reply_conflict_and_archive(tmp_path):
    await scenario(tmp_path, verify_requirements=True, verify_generation=True, verify_retry=True, verify_coverage=True)
