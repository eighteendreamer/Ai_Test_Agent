"""Built App requirement review, immutable history, lost reply and archive recovery."""
import os

import pytest

from test_document_impact_full_product import (
    test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as product_scenario,
)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='requires built App/Chromium, independent production processes and isolated actual stores')
async def test_requirement_review_matrix_and_recovery_in_actual_product(tmp_path):
    await product_scenario(tmp_path, verify_requirements=True)
