"""Built App/task-pool/route reads actual independently computed comparisons."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
from playwright.async_api import expect
import pytest

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_differences import write_pair
from test_document_full_product import product_browser
from test_execution_worker_cli import child_environment, controlled_http, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_DIFFERENCE_FULL_PRODUCT') != '1',
    reason='built App/router, real MySQL/PG/RustFS/Redis, independent CLIs and installed Chromium')]


async def test_full_product_pool_exact_history_poll_restart_download_and_failure(tmp_path):
    evidence = Path(os.getenv('QA_DOCUMENT_DIFFERENCE_FULL_PRODUCT_EVIDENCE_DIR', str(tmp_path / 'evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, 'docx')
    try:
        async with controlled_http(tmp_path) as (business, calls, _, _, _, model_errors):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = ','.join([DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM])
                models = MySQLModelConfigStore(settings); models.initialize()
                models.upsert(ModelConfigUpdateRequest(model_name='controlled-comparison-history', provider='openai',
                    transport='openai_chat_completions', base_url=business + '/v1', api_key=uuid4().hex,
                    is_active=True, use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False)))
                processes.env = child_environment(settings)
                port = free_port()
                api_args = ('-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', str(port))
                api_process = processes.start('product-history-api', *api_args)
                queues = {key: RedisTaskQueue(settings.database.redis_url, stream=stream,
                    group=settings.orchestration.redis_task_consumer_group + ':' + suffix)
                    for key, stream, suffix in [('parse', DOCUMENT_PARSE_STREAM, 'document_parsing'),
                                               ('difference', DOCUMENT_DIFFERENCE_STREAM, 'document_difference')]}
                for queue in queues.values(): await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1', timeout=50) as client:
                        await wait_api(client, api_process)
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, response.text
                            return response.json()
                        async def get(path):
                            response = await client.get(path)
                            assert response.status_code == 200, response.text
                            return response.json()
                        async def worker(kind, name):
                            assert await PostgresTaskOutbox(settings).relay_once(queues[kind], limit=1) == 1
                            module = 'run_document_parse_worker' if kind == 'parse' else 'run_document_difference_worker'
                            process = processes.start(name, '-m', 'src.cli.' + module, '--once', '--worker-id', name)
                            assert await processes.wait(process) == 0
                            assert '_processed=1' in (tmp_path / (name + '.stdout')).read_text(encoding='utf-8')

                        project = await post('/projects', {'project_key': 'history-' + uuid4().hex, 'name': '完整产品比较历史'}, 201)
                        project_path = '/projects/' + project['id']
                        document = await post('/registry/api-docs/upload', {'project_id': project['id'], 'filename': paths[0].name,
                            'request_id': 'history-source-1', 'content_base64': base64.b64encode(paths[0].read_bytes()).decode()})
                        base = project_path + '/documents/' + document['id']
                        sources = []
                        for number, path in enumerate(paths, 1):
                            if number == 2:
                                saved = await post('/registry/api-docs/' + document['id'] + '/versions', {'project_id': project['id'],
                                    'expected_parent_version_id': document['current_version_id'], 'request_id': 'history-source-2',
                                    'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode()})
                                document = saved['document']
                            original = document['original_asset']; owned.append(original['storage_uri'])
                            assert (await objects.read_object_uri(original['storage_uri']))['content'] == path.read_bytes()
                            task = await post(base + '/parse-tasks', {'creation_key': f'history-parse-{number}',
                                'version_id': document['current_version_id']}, 202)
                            await worker('parse', f'product-history-parser-{number}')
                            task = await get(base + '/parse-tasks/' + task['id']); assert task['status'] == 'completed'
                            source = await get(base + '/parse-snapshots/' + task['snapshot_id'])
                            sources.append(source); owned.append(source['snapshot']['storage_uri'])
                        request = {'creation_key': 'chosen-historical-comparison', 'old_snapshot_id': sources[0]['snapshot']['id'],
                            'new_snapshot_id': sources[1]['snapshot']['id']}
                        chosen = await post(base + '/difference-tasks', request, 202)
                        await worker('difference', 'product-history-chosen-worker')
                        chosen = await get(base + '/difference-tasks/' + chosen['id']); assert chosen['status'] == 'completed'
                        chosen_view = await get(base + '/difference-snapshots/' + chosen['snapshot_id'])
                        owned.append(chosen_view['snapshot']['storage_uri'])
                        newer = await post(base + '/difference-tasks', {'creation_key': 'newer-reverse-comparison',
                            'old_snapshot_id': request['new_snapshot_id'], 'new_snapshot_id': request['old_snapshot_id']}, 202)
                        browser_path = f"/projects/{project['id']}/documents/{document['id']}/differences/"
                        async with product_browser(tmp_path, port, evidence) as (page, page_errors, network):
                            await page.locator('.left-nav a[href="/taskpool"]').click()
                            await page.get_by_label('任务类型', exact=True).select_option('document_difference')
                            await expect(page.locator(f'[data-task-id="{chosen["id"]}"]')).to_be_visible()
                            await page.locator(f'[data-task-id="{newer["id"]}"]').get_by_role('button', name='查看比较任务').click()
                            await expect(page).to_have_url(re.compile(re.escape(browser_path + newer['id']) + '$'))
                            await expect(page.locator('.difference-task-status')).to_contain_text('等待比较')
                            await worker('difference', 'product-history-newer-worker')
                            await expect(page.locator('.difference-task-status')).to_contain_text('比较结果已保存', timeout=15000)
                            newer = await get(base + '/difference-tasks/' + newer['id'])
                            newer_view = await get(base + '/difference-snapshots/' + newer['snapshot_id'])
                            owned.append(newer_view['snapshot']['storage_uri'])
                            await expect(page.get_by_label('旧侧固定来源')).to_contain_text(sources[1]['snapshot']['id'])
                            await page.get_by_role('link', name='返回任务池').click()
                            await page.get_by_label('任务类型', exact=True).select_option('document_difference')
                            await page.locator(f'[data-task-id="{chosen["id"]}"]').get_by_role('button', name='查看比较任务').click()
                            await expect(page).to_have_url(re.compile(re.escape(browser_path + chosen['id']) + '$'))
                            await expect(page.get_by_label('旧侧固定来源')).to_contain_text(sources[0]['snapshot']['id'])
                            await expect(page.locator('.difference-change')).to_contain_text('7 天')
                            await expect(page.locator('.difference-change')).to_contain_text('9 天')
                            await page.get_by_label('文本差异视图').select_option('normalized')
                            await page.locator('.difference-structure').filter(has_text='表格变化').locator('summary').click()
                            await expect(page.locator('.difference-structure').filter(has_text='表格变化')).to_contain_text('word/document.xml')
                            assert not page_errors, page_errors
                            await page.locator('.difference-page').evaluate('(element) => { element.scrollTop = 0; }')
                            layout = await page.evaluate('''() => ({
                                scrollX: window.scrollX, viewport: window.innerWidth,
                                bodyWidth: document.documentElement.scrollWidth,
                                shellScrollLeft: document.querySelector('.prototype-shell').scrollLeft,
                                shellWidth: document.querySelector('.prototype-shell').scrollWidth,
                                mainWidth: document.querySelector('.prototype-main').getBoundingClientRect().width,
                                contentScrollLeft: document.querySelector('.prototype-content').scrollLeft,
                                sidebarLeft: document.querySelector('.left-nav').getBoundingClientRect().left,
                                panelLeft: document.querySelector('.difference-page').getBoundingClientRect().left,
                                contentOverflow: [...document.querySelectorAll('*')]
                                    .map(element => ({tag: element.tagName, class: element.className,
                                        client: element.clientWidth, scroll: element.scrollWidth,
                                        visibility: getComputedStyle(element).visibility,
                                        whiteSpace: getComputedStyle(element).whiteSpace,
                                        overflowX: getComputedStyle(element).overflowX}))
                                    .filter(element => element.scroll > element.client + 1).slice(0,30),
                                overflow: [...document.querySelector('.prototype-shell').querySelectorAll('*')]
                                    .map(element => ({ tag: element.tagName, class: element.className,
                                        right: element.getBoundingClientRect().right,
                                        width: element.getBoundingClientRect().width,
                                        scrollWidth: element.scrollWidth,
                                        overflowX: getComputedStyle(element).overflowX,
                                        parent: element.parentElement?.className }))
                                    .filter(element => element.right > window.innerWidth - document.querySelector('.prototype-shell').scrollLeft + 1)
                                    .sort((a,b) => b.right-a.right).slice(0,20)
                            })''')
                            (evidence / 'layout.json').write_text(json.dumps(layout, indent=2), encoding='utf-8')
                            assert layout['sidebarLeft'] >= 0 and layout['panelLeft'] >= 0, layout
                            await page.screenshot(path=str(evidence / 'chosen-history.png'), full_page=True)

                            # True process failure is visible in the product, distinct from worker exit 0.
                            settings_path = project_path + '/document-difference-settings'
                            config = await get(settings_path)
                            cpu_policy = {**config['payload']['policy'], 'max_cpu_seconds': 0.000001}
                            changed = await client.put(settings_path, json={'policy': cpu_policy}); changed.raise_for_status()
                            failed = await post(base + '/difference-tasks', {**request, 'creation_key': 'product-cpu-failure'}, 202)
                            await worker('difference', 'product-history-failure-worker')
                            failed = await get(base + '/difference-tasks/' + failed['id']); assert failed['status'] == 'failed'
                            owned.append(failed['failure_artifact']['storage_uri'])
                            trace = await get(base + '/difference-tasks/' + failed['id'] + '/failure')
                            await page.get_by_role('link', name='返回任务池').click()
                            await page.get_by_label('任务类型', exact=True).select_option('document_difference')
                            await page.get_by_role('button', name='失败', exact=True).click()
                            await expect(page.locator('.data-table tbody tr')).to_have_count(1)
                            await page.get_by_role('button', name='查看比较任务').click()
                            await expect(page.get_by_label('比较失败诊断')).to_contain_text(trace['error'])
                            await expect(page.get_by_label('比较失败诊断')).to_contain_text(str(trace['exit_code']))
                            await page.screenshot(path=str(evidence / 'failure-history.png'), full_page=True)

                            # Close dispatch and restart production API; route refresh must still read exact history.
                            await processes.stop(api_process)
                            settings.orchestration.redis_task_outbox_streams = DOCUMENT_PARSE_STREAM
                            processes.env = child_environment(settings)
                            api_process = processes.start('product-history-api-restarted', *api_args)
                            await wait_api(client, api_process)
                            origin = page.url.split('/projects/')[0]
                            await page.goto(origin + browser_path + chosen['id']); await page.reload()
                            await expect(page.get_by_label('比较结果详情')).to_contain_text(chosen['snapshot_id'])
                            await expect(page.get_by_label('旧侧固定来源')).to_contain_text(sources[0]['snapshot']['id'])
                            async with page.expect_download() as download:
                                await page.get_by_role('button', name='下载完整比较结果 JSON').click()
                            downloaded = await download.value; target = evidence / 'downloaded-history.json'
                            await downloaded.save_as(target)
                            assert json.loads(target.read_text(encoding='utf-8')) == chosen_view
                            assert (await get(base + '/difference-tasks/' + chosen['id'])) == chosen
                            rejected = await client.post(base + '/difference-tasks', json={**request, 'creation_key': 'disabled-new'})
                            assert rejected.status_code == 503
                            other = await post('/projects', {'project_key': 'other-history-' + uuid4().hex, 'name': '错误项目'}, 201)
                            await page.goto(origin + browser_path.replace(project['id'], other['id']) + chosen['id'])
                            await expect(page.get_by_role('alert')).to_contain_text('当前项目')
                            await expect(page.get_by_label('比较结果详情')).to_have_count(0)
                            await page.goto(origin + browser_path + chosen['id'])
                            await expect(page.get_by_label('比较结果详情')).to_contain_text(chosen['snapshot_id'])
                            assert not page_errors, page_errors
                            assert not calls and not model_errors, (calls, model_errors)
                            difference_reads = [event for event in network if '/difference-' in event['url']]
                            assert any(event['url'].endswith('/difference-tasks/' + chosen['id']) for event in difference_reads)
                            assert any(event['url'].endswith('/difference-snapshots/' + chosen['snapshot_id']) for event in difference_reads)
                            assert not any('/document-difference-settings' in event['url'] for event in network)
                            assert not any(event['method'] == 'POST' for event in difference_reads)
                            (evidence / 'summary.json').write_text(json.dumps({'scope': 'built App/task-pool/formal route + production API + independent CLIs + real stores',
                                'chosen': chosen, 'newer': newer, 'failed': failed, 'snapshot': chosen_view,
                                'api_restarted': True, 'dispatch_disabled_history_readable': True, 'exact_download': True,
                                'wrong_project_hidden': True, 'browser_difference_reads': difference_reads, 'model_calls': len(calls)},
                                ensure_ascii=False, indent=2), encoding='utf-8')
                finally:
                    for queue in queues.values(): await queue.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        for suffix in ('parse', 'diff'):
                            table = settings.database.postgres_api_doc_table + '_' + suffix + '_tasks'
                            cur.execute('SELECT to_regclass(%s) AS existing', (table,))
                            if cur.fetchone()['existing']:
                                cur.execute(f'SELECT checkpoint,failure_artifact FROM {table}')
                                for row in cur.fetchall():
                                    for field in ('checkpoint', 'failure_artifact'):
                                        if row[field] and row[field]['storage_uri'] not in owned: owned.append(row[field]['storage_uri'])
                        cur.execute('SELECT to_regclass(%s) AS existing', (settings.database.postgres_api_doc_table + '_assets',))
                        if cur.fetchone()['existing']:
                            cur.execute(f'SELECT record FROM {settings.database.postgres_api_doc_table}_assets')
                            for row in cur.fetchall():
                                if row['record']['storage_uri'] not in owned: owned.append(row['record']['storage_uri'])
    finally:
        for path in tmp_path.glob('product-*.std*'): shutil.copyfile(path, evidence / path.name)
        for path in paths:
            if path.exists(): shutil.copyfile(path, evidence / path.name)
        (evidence / '.gitattributes').write_text('* -text\n*.docx -diff -merge\n', encoding='utf-8')
