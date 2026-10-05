"""Built task pool and fixed details with real DOCX/production API and Workers."""
import os
import pytest
from test_document_impact_full_product import test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as scenario


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT")!="1",
    reason="actual built App/Chromium, independent API/Workers, MySQL/PG/Redis/RustFS")
async def test_generation_pool_fixed_details_origins_json_and_archive_after_actual_restart(tmp_path):
    await scenario(tmp_path,verify_requirements=True,verify_generation=True,verify_retry=True,verify_generation_pool=True)
