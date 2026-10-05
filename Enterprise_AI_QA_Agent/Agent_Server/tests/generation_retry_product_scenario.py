"""Built retry controls, real DOCX scope, production API/CLI and actual stores."""
import asyncio
import hashlib
import json

from playwright.async_api import expect
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect


async def retry_from_product(page, *, base, state, get, client, worker, queue, processes, objects, evidence, settings, sends):
    panel = page.get_by_label('需求增量生成', exact=True)
    root = base.split('/documents/')[0]
    normal_policy = state['mysql_initial_policy']['payload']
    rows = []

    async def save_policy(input_bytes, response_bytes):
        details = panel.get_by_label('项目增量生成设置', exact=True)
        if not await details.evaluate('(element)=>element.open'):
            await details.locator('summary').click()
        await details.get_by_label('生成完整输入字节上限', exact=True).fill(str(input_bytes))
        await details.get_by_label('生成原响应字节上限', exact=True).fill(str(response_bytes))
        async with page.expect_response(lambda response: response.request.method == 'PUT' and response.url.endswith('/case-generation-settings')) as saved:
            await details.get_by_role('button', name='保存生成设置', exact=True).click()
        response = await saved.value
        assert response.status == 200, await response.text()
        policy = await response.json()
        assert policy['payload']['max_input_bytes'] == input_bytes and policy['payload']['max_response_bytes'] == response_bytes
        assert MySQLApplicationConfigStore(settings).get('test_case.incremental_generation_settings', scope_key=policy['scope_key']).payload == policy['payload']
        return policy

    async def download(button, name, expected):
        async with page.expect_download() as event:
            await button.click()
        path = evidence / name
        await (await event.value).save_as(path)
        assert json.loads(path.read_text(encoding='utf-8')) == expected

    for strategy in ['frozen_request', 'saved_response']:
        if strategy == 'frozen_request':
            original = state['failed']
            await panel.get_by_role('button', name='查看生成组 '+original['group']['id'], exact=True).click()
            await expect(panel.get_by_label('固定增量生成结果', exact=True)).to_contain_text(original['group']['id'])
        else:
            await save_policy(normal_policy['max_input_bytes'], 1)
            await panel.get_by_label('生成依据需求版本', exact=True).select_option(state['scope']['review']['id'])
            await panel.get_by_label('生成基线 '+state['scope']['request']['target_case_version_ids'][0], exact=True).check()
            assert not await panel.get_by_label('增量新建用例', exact=True).is_checked()
            await panel.get_by_label('生成模型配置编号', exact=True).fill(state['scope']['request']['model_key'])
            async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/case-generations')) as submitted:
                await panel.get_by_role('button', name='提交固定增量生成', exact=True).click()
            response = await submitted.value
            assert response.status == 202, await response.text()
            group = await response.json()
            await expect(panel).to_contain_text('生成提交已确认：'+group['id'])
            await worker('case_generation', 'product-retry-response-parent')
            await panel.get_by_role('button', name='刷新当前生成结果', exact=True).click()
            original = await get(base+'/case-generations/'+group['id'])
        parent = original['modules'][0]
        assert parent['status'] == 'failed'
        fixed = await get(base+f"/case-generations/{original['group']['id']}/scope")
        retry_panel = panel.get_by_label('模块重试 '+parent['id'], exact=True)
        await expect(retry_panel).to_contain_text(parent['id'])
        if strategy == 'frozen_request':
            assert parent['checkpoint'] is None
            fresh_policy = await save_policy(normal_policy['max_input_bytes'], normal_policy['max_response_bytes'])
        else:
            assert parent['checkpoint']['phase'] == 'response_saved' and parent['checkpoint']['response']
            fresh_policy = await save_policy(1, normal_policy['max_response_bytes'])
            await expect(retry_panel).to_contain_text('不再次调用模型')
        submissions = []
        pattern = '**/case-generation-tasks/'+parent['id']+'/retries'
        async def observe(route):
            if route.request.method != 'POST':
                return await route.continue_()
            response = await route.fetch()
            assert response.status == 202, await response.text()
            submissions.append({'payload': route.request.post_data_json, 'accepted': await response.json()})
            if strategy == 'frozen_request':
                await route.abort('failed')
            else:
                await route.fulfill(response=response)
        await page.route(pattern, observe)
        await retry_panel.get_by_role('button', name='为失败模块创建新任务 '+parent['id'], exact=True).click()
        async with asyncio.timeout(30):
            while not submissions:
                await asyncio.sleep(.05)
        assert len(submissions) == 1 and set(submissions[0]['payload']) == {'creation_key'}
        accepted = submissions[0]['accepted']
        assert accepted['retry']['config'] == fresh_policy and accepted['retry']['strategy'] == strategy
        if strategy == 'frozen_request':
            await expect(retry_panel.get_by_label('原模块重试请求', exact=True)).to_contain_text('提交结果尚未确认')
            cached = await page.evaluate('(key)=>JSON.parse(sessionStorage.getItem(key))',
                f"qa-agent:generation-retry:{parent['project_id']}:{parent['document_id']}:{parent['id']}")
            assert cached['pending'] == submissions[0]['payload']
            await page.unroute(pattern, observe)
            await page.reload()
            await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
            await page.get_by_role('button', name='打开需求增量生成', exact=True).click()
            await expect(retry_panel).to_contain_text('重试提交已确认：'+accepted['task']['id'])
            await expect(retry_panel.get_by_label('原模块重试请求', exact=True)).to_have_count(0)
            assert len(submissions) == 1
            await save_policy(1, normal_policy['max_response_bytes'])
        else:
            await expect(retry_panel).to_contain_text('重试提交已确认：'+accepted['task']['id'])
            await page.unroute(pattern, observe)
        count = len(sends)
        await worker('case_generation', 'product-explicit-retry-'+strategy)
        await retry_panel.get_by_role('button', name='刷新重试任务 '+accepted['task']['id'], exact=True).click()
        task_panel = retry_panel.get_by_label('重试任务 '+accepted['task']['id'], exact=True)
        await expect(task_panel).to_contain_text('重试草稿或候选已保存')
        path = base+f"/case-generation-tasks/{parent['id']}/retries"
        view = await get(path+'/by-request?creation_key='+submissions[0]['payload']['creation_key'])
        assert view['retry'] == accepted['retry'] and view['task']['status'] == 'completed'
        assert len(sends) == count+(strategy == 'frozen_request')
        assert await get(base+f"/case-generations/{original['group']['id']}") == original
        assert await get(base+f"/case-generations/{original['group']['id']}/scope") == fixed
        reference = view['task']['checkpoint']['receipt']['versions'][0]
        draft = next(item for item in await get('/test-cases/'+reference['case_id']+'/versions') if item['id'] == reference['version_id'])
        assert len(draft['content_json']['steps']) == 12 and draft['content_json']['深层扩展'] == state['draft']['content_json']['深层扩展']
        assert draft['generation_metadata']['incremental_generation']['retry'] == {'id': view['retry']['id'], 'sha256': view['retry']['sha256']}
        current = await get('/test-cases/'+reference['case_id'])
        assert current['active_version_id'] == state['scope']['request']['target_case_version_ids'][0]
        assert current['version_reviews'][reference['version_id']]['status'] == 'draft'
        await task_panel.get_by_role('button', name='读取完整重试草稿 '+reference['version_id'], exact=True).click()
        await expect(task_panel).to_contain_text('完整步骤11')
        await expect(task_panel).to_contain_text('"布尔": false')
        await download(task_panel.get_by_role('button', name='下载重试草稿 '+reference['version_id'], exact=True),
            'downloaded-retry-draft-'+strategy+'.json', draft)
        response = await get(base+f"/case-generation-tasks/{view['task']['id']}/response")
        await task_panel.get_by_role('button', name='读取重试原响应 '+view['task']['id'], exact=True).click()
        await download(task_panel.get_by_role('button', name='下载重试原响应 '+view['task']['id'], exact=True),
            'downloaded-retry-response-'+strategy+'.json', response)
        await download(task_panel.get_by_role('button', name='下载重试关系与结果 '+view['task']['id'], exact=True),
            'downloaded-retry-'+strategy+'.json', view)
        await download(panel.get_by_role('button', name='下载完整生成范围 JSON', exact=True),
            'downloaded-retry-scope-'+strategy+'.json', fixed)
        if strategy == 'saved_response':
            assert view['task']['checkpoint']['response'] == parent['checkpoint']['response']
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            table = settings.database.postgres_api_doc_table
            cur.execute(f'SELECT record FROM {table}_case_gen_retries WHERE id=%s', (view['retry']['id'],))
            pg_retry = cur.fetchone()['record']
            cur.execute(f'SELECT id,status,seed,checkpoint FROM {table}_case_gen_tasks WHERE id=%s', (view['task']['id'],))
            row = dict(cur.fetchone()); row['id'] = str(row['id'])
        assert pg_retry == view['retry'] and row['status'] == 'completed' and row['checkpoint'] == view['task']['checkpoint']
        assert row['seed']['retry'] == {'id': view['retry']['id'], 'sha256': view['retry']['sha256']}
        for ref in [original['group']['scope'], view['task']['checkpoint']['response']]:
            raw = (await objects.read_object_uri(ref['storage_uri']))['content']
            assert len(raw) == ref['size_bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256']
            (evidence/(ref['sha256']+'.object')).write_bytes(raw)
        for task_id in [parent['id'], view['task']['id']]:
            await queue.enqueue({'schema_version': 1, 'task_type': 'case_generation', 'project_id': parent['project_id'], 'task_id': task_id})
            child = processes.start('product-retry-terminal-'+task_id, '-m', 'src.cli.run_case_generation_worker', '--once', '--worker-id', 'retry-terminal')
            assert await processes.wait(child) == 0
        assert len(sends) == count+(strategy == 'frozen_request')
        rows.append({'parent_report': original, 'scope': fixed, 'payload': submissions[0]['payload'], 'accepted': accepted,
            'view': view, 'response': response, 'draft': draft, 'pg_retry': pg_retry, 'pg_task': row,
            'mysql_retry_policy': fresh_policy, 'lost_reply_recovered': strategy == 'frozen_request', 'terminal_redelivery_no_send': True})
        (evidence/'generation-retry-product.json').write_text(json.dumps({'items': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        await page.screenshot(path=str(evidence/('retry-result-'+strategy+'.png')), full_page=True)
    assert len(sends) == 6
    for index, item in enumerate(rows):
        invocation = item['scope']['modules'][0]['generation']['invocation']
        assert sends[4+index]['messages'] == [{'role': 'system', 'content': invocation['system_prompt']}, *invocation['messages']]
    output = {'items': rows, 'actual_built_retry_app': True, 'independent_generation_cli': True,
        'additional_physical_calls': 2, 'user_business_review': False}
    (evidence/'generation-retry-product.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    return output


async def retry_product_after_restart(page, *, base, state, client, evidence):
    panel = page.get_by_label('需求增量生成', exact=True)
    for item in state['items']:
        group = item['parent_report']['group']; parent = item['parent_report']['modules'][0]
        await page.evaluate('(data)=>sessionStorage.setItem(data.key,JSON.stringify(data.value))', {
            'key': f"qa-agent:case-generation:{group['project_id']}:{group['document_id']}",
            'value': {'pending': None, 'confirmedFailure': False, 'groupId': group['id']}})
        await page.evaluate('(data)=>sessionStorage.setItem(data.key,JSON.stringify(data.value))', {
            'key': f"qa-agent:generation-retry:{group['project_id']}:{group['document_id']}:{parent['id']}",
            'value': {'pending': item['payload'], 'confirmedFailure': False, 'expanded': []}})
        await page.reload()
        await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
        await page.get_by_role('button', name='打开需求增量生成', exact=True).click()
        retry_panel = panel.get_by_label('模块重试 '+parent['id'], exact=True)
        await expect(retry_panel).to_contain_text('重试提交已确认：'+item['view']['task']['id'])
        await expect(retry_panel.get_by_label('原模块重试请求', exact=True)).to_have_count(0)
        await expect(retry_panel.get_by_role('button', name='为失败模块创建新任务 '+parent['id'], exact=True)).to_be_disabled()
        path = base+f"/case-generation-tasks/{parent['id']}/retries"
        restored = await client.get(path+'/by-request', params=item['payload'])
        assert restored.status_code == 200 and restored.json() == item['view']
        replay = await client.post(path, json=item['payload'])
        assert replay.status_code == 202 and replay.json() == item['view']
        assert (await client.post(path, json={'creation_key': 'archived-retry-new-'+parent['id']})).status_code == 503
        task_panel = retry_panel.get_by_label('重试任务 '+item['view']['task']['id'], exact=True)
        async with page.expect_download() as event:
            await task_panel.get_by_role('button', name='下载重试关系与结果 '+item['view']['task']['id'], exact=True).click()
        target = evidence/('downloaded-retry-after-restart-'+item['view']['retry']['strategy']+'.json')
        await (await event.value).save_as(target)
        assert json.loads(target.read_text(encoding='utf-8')) == item['view']
    state['api_restart_disabled_dispatch_archived_recovery'] = True
    (evidence/'generation-retry-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
