"""Short App rendering diagnosis; HTTP bodies are stored real comparison samples.

This supplements production acceptance and does not prove backend functionality.
"""
import json
import os
from pathlib import Path

import pytest

from test_document_full_product import product_browser
from test_execution_worker_cli import free_port


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_LAYOUT') != '1', reason='built App/installed Chromium; diagnostic HTTP samples')
async def test_sample_task_pool_to_history_layout_stays_inside_shell(tmp_path):
    docs = Path(__file__).resolve().parents[2] / 'docs/upgrade_2026-10-03'
    evidence = Path(os.getenv('QA_DOCUMENT_LAYOUT_EVIDENCE_DIR', str(tmp_path / 'evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    sample = json.loads((docs / 'p3-comparison-history-product-verified/summary.json').read_text(encoding='utf-8'))
    async with product_browser(tmp_path, free_port(), evidence) as (page, _, _):
        async def metrics():
            return await page.evaluate('''() => ({
                shellLeft: document.querySelector('.prototype-shell').scrollLeft,
                sidebarLeft: document.querySelector('.left-nav').getBoundingClientRect().left,
                overflowing: [...document.querySelectorAll('*')].map(e=>({tag:e.tagName,class:e.className,
                    width:e.getBoundingClientRect().width,client:e.clientWidth,scroll:e.scrollWidth,
                    scrollLeft:e.scrollLeft,left:e.getBoundingClientRect().left,overflow:getComputedStyle(e).overflowX}))
                    .filter(e=>e.scroll>e.client+1||e.left<0)
            })''')
        await page.route('**/sponsors', lambda route: route.fulfill(status=200, content_type='application/json',
            body=json.dumps([{'id': 1, 'name': 'E-API', 'logo_url': '/logo.svg', 'website_url': 'https://example.test'}])))
        # Reproduce the measured 264px -> 469px tooltip overflow with a masked
        # target in the existing health DTO. No secret or real user's address.
        await page.route('**/health', lambda route: route.fulfill(status=200, content_type='application/json',
            body=json.dumps({'status': 'ok', 'name': 'layout-probe', 'environment': 'test',
                'memory_backend': 'postgres', 'session_backend': 'postgres', 'tool_job_backend': 'postgres',
                'ui_graph_backend': 'postgres', 'knowledge_enabled': True,
                'memory_target': 'postgresql+psycopg://test:***@127.0.0.1:5432/qa_layout_' + 'a' * 40})))
        await page.route('**/difference-tasks/*', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(sample['chosen'])))
        await page.route('**/difference-snapshots/*', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(sample['snapshot'])))
        entries = {'items': [{'kind': 'document_difference', 'project_name': '完整产品比较历史', 'document_title': 'requirements-v1',
            'task': task} for task in (sample['chosen'], sample['newer'])], 'limit': 24, 'offset': 0, 'has_more': False}
        await page.route('**/task-pool/entries?*', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(entries)))
        await page.reload()
        await page.locator('.left-nav a[href="/taskpool"]').click()
        await page.get_by_label('任务类型', exact=True).select_option('document_difference')
        await page.locator(f'[data-task-id="{sample["chosen"]["id"]}"]').get_by_role('button', name='查看比较任务').wait_for()
        before = await metrics()
        await page.locator(f'[data-task-id="{sample["chosen"]["id"]}"]').get_by_role('button', name='查看比较任务').click()
        await page.get_by_label('比较结果详情').wait_for()
        await page.get_by_label('文本差异视图').select_option('normalized')
        await page.locator('.difference-structure').filter(has_text='表格变化').locator('summary').click()
        after = await metrics()
        (evidence / 'layout.json').write_text(json.dumps({'before': before, 'after': after}, ensure_ascii=False, indent=2), encoding='utf-8')
        assert after['shellLeft'] == 0 and after['sidebarLeft'] == 0, after
