"""Built App submits/replays fixed impacts over real docs, HTTP and independent CLIs."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from playwright.async_api import expect
import pytest

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.case_management import TestCaseVersionRecord as CaseVersion
from src.schemas.document_impact_calls import DocumentImpactUnit
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_differences import write_pair
from test_document_full_product import product_browser
from test_document_impact_calls import provider
from test_document_impact_production import collect_objects
from test_document_impact_sources import source
from test_document_impact_workflow import responder
from test_document_impact_grouped_workflow import grouped_responder
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_LIVE_DOCUMENT_IMPACT_FULL_PRODUCT') != '1',
    reason='built App, production API/independent CLI, isolated actual MySQL/PG/Redis/RustFS and installed Chromium')]


async def test_full_product_submit_lost_reply_exact_history_settings_failure_restart_download(tmp_path):
    evidence = Path(os.getenv('QA_DOCUMENT_IMPACT_FULL_PRODUCT_EVIDENCE_DIR', str(tmp_path / 'evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, 'docx')
    for path in paths: (evidence / path.name).write_bytes(path.read_bytes())
    holder, views = {}, []
    def respond(body):
        payload = json.loads(body['messages'][-1]['content'])
        holder.setdefault('first', DocumentImpactUnit.model_validate(payload['unit']))
        holder['manifest'] = SimpleNamespace(sources=[SimpleNamespace(**item) for item in payload['fixed_source_catalog']])
        factory = grouped_responder if payload['analysis_protocol']['version'] in {'impact-call-v6', 'impact-call-v7'} else responder
        return factory(holder, views, holder['case'])(body)
    try:
        with provider(responder=respond) as (endpoint, sends, _, _):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = ','.join([DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM])
                models = MySQLModelConfigStore(settings); models.initialize()
                model = models.upsert(ModelConfigUpdateRequest(model_name='controlled-impact-product', provider='openai',
                    transport='openai_chat_completions', base_url=endpoint, api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
                    context_window=128000, max_output_tokens=1024))
                processes.env = child_environment(settings); port = free_port()
                args = ('-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', str(port))
                api_process = processes.start('product-impact-api', *args)
                queues = {kind: RedisTaskQueue(settings.database.redis_url, stream=stream,
                    group=settings.orchestration.redis_task_consumer_group + ':' + kind)
                    for kind, stream in [('document_parsing', DOCUMENT_PARSE_STREAM), ('document_difference', DOCUMENT_DIFFERENCE_STREAM), ('document_impact', DOCUMENT_IMPACT_STREAM)]}
                for queue in queues.values(): await queue.connect()
                try:
                    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1', timeout=50) as client:
                        await wait_api(client, api_process)
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload); assert response.status_code == status, response.text
                            return response.json()
                        async def get(path):
                            response = await client.get(path); assert response.status_code == 200, response.text
                            return response.json()
                        async def worker(kind, name):
                            assert await PostgresTaskOutbox(settings).relay_once(queues[kind], limit=1) == 1
                            command = {'document_parsing': 'run_document_parse_worker', 'document_difference': 'run_document_difference_worker', 'document_impact': 'run_document_impact_worker'}[kind]
                            child = processes.start(name, '-m', 'src.cli.' + command, '--once', '--worker-id', name)
                            assert await processes.wait(child) == 0
                            assert '_processed=1' in (tmp_path / (name + '.stdout')).read_text(encoding='utf-8')
                        project = await post('/projects', {'project_key': 'impact-product-' + uuid4().hex, 'name': '影响分析产品实测'}, 201)
                        root = '/projects/' + project['id']
                        doc = await post('/registry/api-docs/upload', {'project_id': project['id'], 'filename': paths[0].name,
                            'request_id': 'product-v1', 'content_base64': base64.b64encode(paths[0].read_bytes()).decode()})
                        base = root + '/documents/' + doc['id']
                        for number, path in enumerate(paths, 1):
                            if number == 2:
                                saved = await post('/registry/api-docs/' + doc['id'] + '/versions', {'project_id': project['id'],
                                    'expected_parent_version_id': doc['current_version_id'], 'request_id': 'product-v2', 'filename': path.name,
                                    'content_base64': base64.b64encode(path.read_bytes()).decode()})
                                doc = saved['document']
                            parsed = await post(base + '/parse-tasks', {'creation_key': f'product-parse-{number}', 'version_id': doc['current_version_id']}, 202)
                            await worker('document_parsing', f'product-parser-{number}')
                            parsed = await get(base + '/parse-tasks/' + parsed['id']); assert parsed['status'] == 'completed'
                            view = DocumentParseSnapshotView.model_validate(await get(base + '/parse-snapshots/' + parsed['snapshot_id']))
                            views.append(view); (evidence / f'parse-{number}.json').write_text(view.model_dump_json(indent=2), encoding='utf-8')
                        compared = await post(base + '/difference-tasks', {'creation_key': 'product-compare', 'old_snapshot_id': views[0].snapshot.id, 'new_snapshot_id': views[1].snapshot.id}, 202)
                        await worker('document_difference', 'product-comparison')
                        compared = await get(base + '/difference-tasks/' + compared['id']); assert compared['status'] == 'completed'
                        case_payload = {'case_key': 'retention', 'title': '验证保留期限', 'case_type': 'json', 'mode_key': 'ui_testing',
                            'content_json': {'目标': '验证订单保留7天', 'steps': [{'操作': f'完整步骤{i}'} for i in range(12)],
                                '深层扩展': {'零': 0, '空': None, '布尔': False, '未知字段': '不能丢失'}},
                            'execution_notes': '完整用例执行说明', 'source_refs': [source(SimpleNamespace(id=doc['id']), views[0])],
                            'model_key': 'manual', 'prompt_version': 'manual-v1', 'skill_versions': {'manual': 'v1'}}
                        case = CaseBundle.model_validate(await post(root + '/test-cases', case_payload, 201)); holder['case'] = case
                        async with product_browser(tmp_path, port, evidence) as (page, page_errors, network):
                            browser_url = urlsplit(page.url)
                            origin = f'{browser_url.scheme}://{browser_url.netloc}'
                            comparison_route = root + '/documents/' + doc['id'] + '/differences/' + compared['id']
                            await page.goto(origin + comparison_route)
                            panel = page.get_by_label('文档影响分析入口', exact=True)
                            await expect(panel).to_contain_text(compared['snapshot_id'])
                            await expect(panel.get_by_role('button', name='提交固定影响分析')).to_be_enabled()
                            # Save each real project policy from the formal page, then verify its actual MySQL payload.
                            await panel.get_by_text('项目影响分析设置', exact=True).click()
                            await panel.get_by_label('任务总超时（秒）', exact=True).fill('300')
                            await panel.get_by_role('button', name='保存影响范围设置', exact=True).click()
                            await expect(panel.get_by_role('status')).to_contain_text('项目影响设置已保存')
                            await panel.get_by_label('影响默认模型配置编号', exact=True).fill(model.key)
                            await panel.get_by_label('影响分析指导', exact=True).fill('产品实测引用完整原文与用例')
                            await panel.get_by_role('button', name='保存影响模型指导', exact=True).click()
                            await expect(panel.get_by_role('status')).to_contain_text('项目影响设置已保存')
                            await panel.get_by_label('边界上下文字符', exact=True).fill('0')
                            await panel.get_by_role('button', name='保存影响分段设置', exact=True).click()
                            await expect(panel.get_by_role('status')).to_contain_text('项目影响设置已保存')
                            await panel.get_by_label('逻辑调用数量上限', exact=True).fill('250')
                            await panel.get_by_label('影响请求发送策略', exact=True).select_option('single_attempt')
                            await panel.get_by_role('button', name='保存影响调用预算', exact=True).click()
                            await expect(panel.get_by_role('status')).to_contain_text('项目影响设置已保存')
                            mysql = MySQLApplicationConfigStore(settings)
                            await panel.get_by_label('每组证据数量上限', exact=True).fill('8')
                            await panel.get_by_role('button', name='保存影响推理分组设置', exact=True).click()
                            await expect(panel.get_by_role('status')).to_contain_text('项目影响设置已保存')
                            configuration = {suffix: await get(root + '/' + suffix) for suffix in ['document-impact-settings', 'document-impact-analysis-settings', 'document-impact-batch-settings', 'document-impact-invocation-settings', 'document-impact-reasoning-settings']}
                            for suffix, key in [('document-impact-settings', 'document.impact_settings'), ('document-impact-analysis-settings', 'document.impact_analysis_settings'),
                                ('document-impact-batch-settings', 'document.impact_batch_settings'), ('document-impact-invocation-settings', 'document.impact_invocation_settings'),
                                ('document-impact-reasoning-settings', 'document.impact_reasoning_settings')]:
                                assert mysql.get(key, scope_key=project['id']).payload == configuration[suffix]['payload']
                            assert configuration['document-impact-settings']['payload']['timeout_seconds'] == 300
                            assert configuration['document-impact-invocation-settings']['payload']['transport_policy'] == 'single_attempt'
                            assert configuration['document-impact-analysis-settings']['payload']['model_key'] == model.key
                            assert configuration['document-impact-batch-settings']['payload']['boundary_context_chars'] == 0
                            assert configuration['document-impact-invocation-settings']['payload']['max_logical_calls'] == 250
                            assert configuration['document-impact-reasoning-settings']['payload']['max_group_units'] == 8
                            await panel.get_by_label('影响分析用例范围', exact=True).select_option('explicit')
                            await panel.get_by_role('button', name='读取 retention 的完整版本', exact=True).click()
                            await panel.get_by_label('retention 版本 1', exact=True).check()
                            await panel.get_by_label('影响环境声明', exact=True).fill('qa')
                            await panel.get_by_label('影响发布版本声明', exact=True).fill('future-release')
                            submitted_requests = []
                            reply_dropped = asyncio.Event()
                            async def drop_reply(route):
                                if route.request.method != 'POST': return await route.continue_()
                                response = await route.fetch(); assert response.status == 202
                                submitted_requests.append({'payload': route.request.post_data_json, 'task': await response.json()})
                                await route.abort('failed')
                                reply_dropped.set()
                            await page.route('**/impact-tasks', drop_reply)
                            await panel.get_by_role('button', name='提交固定影响分析', exact=True).click()
                            await asyncio.wait_for(reply_dropped.wait(), timeout=30)
                            await expect(panel).to_contain_text('提交结果尚未确认')
                            assert len(submitted_requests) == 1 and not sends
                            original_request = submitted_requests[0]['payload']; chosen = submitted_requests[0]['task']
                            assert original_request['case_version_ids'] == [case.version.id]
                            await page.unroute('**/impact-tasks', drop_reply); await page.reload()
                            await expect(page.locator('.impact-task-status')).to_contain_text('等待影响分析')
                            assert (await get(base + '/impact-tasks/by-request?creation_key=' + original_request['creation_key']))['id'] == chosen['id']
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f'SELECT seed FROM {settings.database.postgres_api_doc_table}_impact_tasks WHERE id=%s', (chosen['id'],))
                                frozen = cur.fetchone()['seed']
                            assert frozen['selection'] == 'explicit' and frozen['cases'][0]['version_id'] == case.version.id
                            assert frozen['analysis_context']['environment']['deployment_status'] == 'unknown'
                            assert frozen['analysis_context']['environment']['target_release'] == 'future-release'
                            # Change live guidance and append a draft after submit; old analysis retains fixed inputs.
                            changed = await client.put(root + '/document-impact-analysis-settings', json={**configuration['document-impact-analysis-settings']['payload'], 'analysis_guidance': '后续指导不替换旧提交'})
                            assert changed.status_code == 200
                            later = await post('/test-cases/' + case.case.id + '/versions',
                                {key: value for key, value in case_payload.items() if key in {'content_json', 'execution_notes', 'source_refs', 'model_key', 'prompt_version', 'skill_versions'}}, 201)
                            assert later['id'] != case.version.id
                            await worker('document_impact', 'product-impact-worker')
                            await expect(page.locator('.impact-task-status')).to_contain_text('影响结果已保存', timeout=15000)
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text('订单保留7天调整为9天')
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text('重大影响候选')
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text('语义结论待评审')
                            chosen = await get(base + '/impact-tasks/' + chosen['id'])
                            saved_view = await get(base + '/impact-snapshots/' + chosen['snapshot_id'])
                            assert saved_view['snapshot']['seed'] == frozen and saved_view['result']['status'] == 'partial'
                            assert all(unit['status'] == 'analyzed' for unit in saved_view['result']['units'])
                            assert saved_view['result']['units'][-1]['unit']['id'] == case.version.id
                            assert saved_view['result']['implementation_version'] == 'impact-coordinator-v2'
                            assert len(saved_view['result']['discovery_tasks']) == 1 and len(sends) == 2
                            await expect(page.get_by_label('固定推理任务', exact=True)).to_contain_text('1 个文档发现任务')
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text('处理说明：已完整对照第1项')
                            await page.screenshot(path=str(evidence / 'submitted-result.png'), full_page=True)
                            count = len(sends)
                            # A genuine preparation rejection is displayed as a separate terminal failure.
                            await page.get_by_text('项目影响分析设置', exact=True).click()
                            await page.get_by_label('任务总超时（秒）', exact=True).fill('300')
                            # Capture checks only the case bodies. Preparation also reads
                            # both real document snapshots and the full input envelope.
                            prepare_limit = len(case.version.model_dump_json()) + len(CaseVersion.model_validate(later).model_dump_json()) + 1
                            await page.get_by_label('固定依据字符上限', exact=True).fill(str(prepare_limit))
                            await page.get_by_role('button', name='保存影响范围设置', exact=True).click()
                            await expect(page.get_by_role('button', name='提交固定影响分析', exact=True)).to_be_enabled()
                            await page.get_by_role('button', name='提交固定影响分析', exact=True).click()
                            await expect(page.locator('.impact-task-status')).to_contain_text('等待影响分析')
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f'SELECT id FROM {settings.database.postgres_api_doc_table}_impact_tasks WHERE id<>%s', (chosen['id'],))
                                failed_id = str(cur.fetchone()['id'])
                            await worker('document_impact', 'product-impact-failed-worker')
                            await expect(page.locator('.impact-task-status')).to_contain_text('影响任务失败', timeout=15000)
                            failed = await get(base + '/impact-tasks/' + failed_id); assert failed['status'] == 'failed' and len(sends) == count
                            await page.screenshot(path=str(evidence / 'failure-result.png'), full_page=True)
                            await page.locator('.left-nav a[href="/taskpool"]').click()
                            await page.get_by_label('任务类型', exact=True).select_option('document_impact')
                            await expect(page.locator('.data-table tbody tr')).to_have_count(2)
                            await expect(page.locator(f'[data-task-id="{chosen["id"]}"]')).to_contain_text('影响结果已保存')
                            await page.locator(f'[data-task-id="{chosen["id"]}"]').get_by_role('button', name='查看影响任务').click()
                            history_route = root + '/documents/' + doc['id'] + '/impacts/' + chosen['id']
                            await expect(page).to_have_url(re.compile(re.escape(origin + history_route) + '$'))
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text(chosen['snapshot_id'])
                            await page.get_by_role('link', name='返回任务池').click()
                            await page.get_by_label('任务类型', exact=True).select_option('document_impact')
                            await page.get_by_role('button', name='失败', exact=True).click()
                            await expect(page.locator('.data-table tbody tr')).to_have_count(1)
                            await page.get_by_role('button', name='查看影响任务').click()
                            await expect(page.get_by_label('影响任务详情', exact=True)).to_contain_text(failed['error_code'])
                            # Production API restarts with dispatch disabled; exact history and download remain readable.
                            await processes.stop(api_process); settings.orchestration.redis_task_dispatch_enabled = False
                            processes.env = child_environment(settings); api_process = processes.start('product-impact-api-restarted', *args)
                            await wait_api(client, api_process); await page.goto(origin + history_route); await page.reload()
                            await expect(page.get_by_label('影响结果详情', exact=True)).to_contain_text(chosen['snapshot_id'])
                            async with page.expect_download() as downloaded:
                                await page.get_by_role('button', name='下载完整影响结果 JSON', exact=True).click()
                            target = evidence / 'downloaded-history.json'; await (await downloaded.value).save_as(target)
                            assert json.loads(target.read_text(encoding='utf-8')) == saved_view
                            assert len(sends) == count and not page_errors, page_errors
                            layout = await page.evaluate('''() => ({ viewport:innerWidth, bodyWidth:document.documentElement.scrollWidth,
                                sidebarLeft:document.querySelector('.left-nav').getBoundingClientRect().left,
                                panelLeft:document.querySelector('.impact-page').getBoundingClientRect().left })''')
                            assert layout['bodyWidth'] <= layout['viewport'] and layout['sidebarLeft'] >= 0 and layout['panelLeft'] >= 0, layout
                            (evidence / 'layout.json').write_text(json.dumps(layout, indent=2), encoding='utf-8')
                            assert (await get(base + '/impact-tasks/' + chosen['id'])) == chosen
                            pool = await get('/task-pool/entries?kind=document_impact'); assert len(pool['items']) == 2
                            assert all('input_checkpoint' not in entry['task'] and 'failure_artifact' not in entry['task'] for entry in pool['items'])
                            # Retain original PG/object/physical HTTP evidence, not only UI assertions.
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f'SELECT record FROM {settings.database.postgres_api_doc_table}_impact_calls WHERE task_id=%s ORDER BY record->>\'created_at\'', (chosen['id'],))
                                calls = [row['record'] for row in cur.fetchall()]
                            for call in calls:
                                raw = (await objects.read_object_uri(call['request']['storage_uri']))['content']
                                assert hashlib.sha256(raw).hexdigest() == call['request']['sha256']
                                request = json.loads(raw)
                                expected_messages = [{'role': 'system', 'content': request['request']['system_prompt']}, *request['request']['messages']]
                                assert any(body['messages'] == expected_messages for body in sends)
                            collect_objects(settings, owned)
                            for uri in owned:
                                raw = (await objects.read_object_uri(uri))['content']
                                (evidence / (hashlib.sha256(raw).hexdigest() + '.object')).write_bytes(raw)
                            for name, value in [('snapshot-view.json', saved_view), ('physical-requests.json', sends), ('calls.json', calls), ('pool.json', pool), ('configuration.json', configuration),
                                ('verification.json', {'task': chosen, 'failed': failed, 'lost_response_submission': submitted_requests,
                                    'frozen_seed': frozen, 'http_calls': count, 'default_model_judgement': False,
                                    'history_after_dispatch_disabled': True, 'actual_built_app': True})]:
                                (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                            await page.screenshot(path=str(evidence / 'history-after-restart.png'), full_page=True)
                finally:
                    for queue in queues.values(): await queue.close()
                    collect_objects(settings, owned)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {'.stdout', '.stderr'} or path.name == 'docker.json':
                (evidence / path.name).write_bytes(path.read_bytes())
