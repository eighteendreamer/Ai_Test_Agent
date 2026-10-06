"""Real DOCX built-App execution confirmations and actual HTTP/ToolRuntime evidence."""
import os
import pytest

from test_document_impact_full_product import test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download as scenario
from test_requirement_execution import retention_http


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='requires built App/Chromium, independent production processes and isolated actual stores')
async def test_execution_app_actual_checks_lost_reply_history_conflict_and_archive(tmp_path, retention_http):
    await scenario(tmp_path, verify_requirements=True, verify_execution=retention_http)
