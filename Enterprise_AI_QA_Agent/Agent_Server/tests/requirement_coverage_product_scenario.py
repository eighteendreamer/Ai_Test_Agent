"""Explicit coverage through the built App, actual version reviews and PG."""
import asyncio
import json

from playwright.async_api import expect

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_coverage_store import fixed_coverage


async def open_coverage(page, requirement):
    panel = page.get_by_label('需求评审与追踪', exact=True)
    await panel.get_by_label('查看需求矩阵', exact=True).select_option(requirement['requirement_id'])
    matrix = panel.get_by_label('需求追踪矩阵', exact=True)
    await expect(matrix.get_by_label('需求历史版本', exact=True)).to_be_enabled()
    await matrix.get_by_label('需求历史版本', exact=True).select_option(requirement['id'])
    await expect(matrix.get_by_role('button', name='管理用例覆盖', exact=True)).to_be_enabled()
    await matrix.get_by_role('button', name='管理用例覆盖', exact=True).click()
    coverage = matrix.get_by_label('用例覆盖确认', exact=True)
    await expect(coverage.get_by_role('button', name='查询当前项目用例', exact=True)).to_be_enabled()
    return matrix, coverage


async def choose_version(panel, reference):
    await panel.get_by_label('覆盖候选用例', exact=True).select_option(reference['case_id'])
    await expect(panel.get_by_label('覆盖候选完整版本', exact=True)).to_be_enabled()
    await panel.get_by_label('覆盖候选完整版本', exact=True).select_option(reference['version_id'])
    await expect(panel).to_contain_text('完整步骤11')
    await expect(panel).to_contain_text('不能丢失')
    await expect(panel).to_contain_text('完整用例执行说明')


async def add_link(panel, reference, point, task_id):
    await choose_version(panel, reference)
    await panel.get_by_role('button', name='加入所选固定版本', exact=True).click()
    await panel.get_by_label(f"覆盖 {reference['version_id']} 验证点 {point}", exact=True).check()
    await panel.get_by_label(f"覆盖 {reference['version_id']} 生成任务", exact=True).fill(task_id)


async def coverage_from_product(page, *, base, reviews, generation, retry_state, get, post, client, evidence, settings, sends):
    requirement = reviews['reviews'][0]['requirement_version']
    stem = base+f"/requirements/{requirement['requirement_id']}"
    path = stem+f"/versions/{requirement['id']}/coverage-reviews"
    count = len(sends)
    original = await get(stem+'/matrix?version_id='+requirement['id']+'&coverage_id=initial')
    matrix, panel = await open_coverage(page, requirement)
    references = []
    for task in generation['normal']['modules']:
        reference = task['checkpoint']['receipt']['versions'][0]
        await choose_version(panel, reference)
        # Reuse the formal case review dialog. The update's V3 is chosen even though retry V5 already exists.
        await panel.get_by_role('button', name='打开所选用例评审', exact=True).click()
        dialog = page.get_by_role('dialog', name='测试用例内容', exact=True)
        await expect(dialog.locator('select')).to_have_value(reference['version_id'])
        await dialog.get_by_role('button', name='提交当前版本评审', exact=True).click()
        await expect(dialog).to_contain_text('待评审')
        await expect(dialog.get_by_role('button', name='启用当前版本', exact=True)).to_be_enabled()
        await dialog.get_by_role('button', name='启用当前版本', exact=True).click()
        await expect(dialog).to_contain_text('当前启用')
        await dialog.get_by_role('button', name='关闭', exact=True).click()
        await expect(panel.get_by_label('覆盖候选完整版本', exact=True)).to_have_value(reference['version_id'])
        case = await get('/test-cases/'+reference['case_id'])
        version = next(item for item in await get('/test-cases/'+reference['case_id']+'/versions') if item['id'] == reference['version_id'])
        assert case['active_version_id'] == version['id'] and case['version_reviews'][version['id']]['status'] == 'approved'
        assert version['content_hash'] == reference['content_hash'] and len(version['content_json']['steps']) == 12
        assert version['content_json']['深层扩展'] == {'零':0, '空':None, '布尔':False, '未知字段':'不能丢失'}
        references.append({'reference':reference, 'task':task['id'], 'case':case, 'version':version})
    for link in requirement['case_links']:
        await panel.get_by_role('button', name='移除覆盖版本 '+link['case_version_id'], exact=True).click()
    for item, point in zip(references, ['retention', 'boundary']):
        await add_link(panel, item['reference'], point, item['task'])
    await panel.get_by_label('覆盖确认说明', exact=True).fill('真实DOCX页面：明确用例版本及验证点，设计覆盖不代替执行结果。')
    submissions = []; lost = asyncio.Event()
    async def lose_reply(route):
        if route.request.method != 'POST': return await route.continue_()
        response = await route.fetch(); assert response.status == 201, await response.text()
        submissions.append({'request':route.request.post_data_json, 'saved':await response.json()})
        await route.abort('failed'); lost.set()
    pattern = '**/versions/*/coverage-reviews'
    await page.route(pattern, lose_reply)
    await panel.get_by_role('button', name='保存用例覆盖确认', exact=True).click()
    await asyncio.wait_for(lost.wait(), 30)
    await expect(panel.get_by_label('原覆盖确认请求', exact=True)).to_contain_text('提交结果尚未确认')
    first = submissions[0]['saved']; assert len(submissions) == 1
    assert all(link['review_status'] == 'approved' for link in first['case_links'])
    await page.screenshot(path=str(evidence/'coverage-lost-reply.png'), full_page=True)
    await page.unroute(pattern, lose_reply); await page.reload()
    await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    matrix, panel = await open_coverage(page, requirement)
    await expect(panel).to_contain_text('覆盖确认已保存：'+first['id'])
    await expect(panel.get_by_label('原覆盖确认请求', exact=True)).to_have_count(0)
    assert len(submissions) == 1 and len(sends) == count
    for key in ['retention', 'boundary']:
        await expect(matrix.get_by_label('验证点 '+key, exact=True)).to_contain_text('已有设计覆盖')
    await expect(matrix).to_contain_text('执行结果尚未关联')
    first_matrix = await get(stem+'/matrix?version_id='+requirement['id'])
    assert first_matrix['coverage_review'] == first
    assert first_matrix['version'] == original['version'] and first_matrix['deployment_status'] == 'unknown'
    await page.screenshot(path=str(evidence/'coverage-recovered.png'), full_page=True)
    # The explicit initial view does not replace the edit baseline or mutate an immutable requirement.
    await panel.get_by_label('覆盖确认历史', exact=True).select_option('initial')
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('未覆盖')
    assert (await get(stem+'/matrix?version_id='+requirement['id']+'&coverage_id=initial')) == original
    await panel.get_by_label('覆盖确认历史', exact=True).select_option(first['id'])
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('已有设计覆盖')
    for link in first['case_links']:
        await panel.get_by_role('button', name='移除覆盖版本 '+link['case_version_id'], exact=True).click()
    await panel.get_by_label('覆盖确认说明', exact=True).fill('明确撤销完整关联集合，保留原确认及历史。')
    await panel.get_by_role('button', name='保存用例覆盖确认', exact=True).click()
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('未覆盖')
    cleared = (await get(path))['items'][0]
    assert cleared['request']['expected_coverage_id'] == first['id'] and cleared['case_links'] == []
    for item, point in zip(references, ['retention', 'boundary']):
        await add_link(panel, item['reference'], point, item['task'])
    await panel.get_by_label('覆盖确认说明', exact=True).fill('复核后明确恢复两条固定用例关联。')
    await panel.get_by_role('button', name='保存用例覆盖确认', exact=True).click()
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('已有设计覆盖')
    restored = (await get(path))['items'][0]
    assert restored['previous_coverage_id'] == cleared['id'] and restored['revision'] == 3
    external = await post(path, {**restored['request'], 'request_id':'coverage-browser-external',
        'expected_coverage_id':restored['id'], 'note':'实际API并发确认，页面仍绑定旧父确认'}, 201)
    await panel.get_by_label('覆盖确认说明', exact=True).fill('实际页面冲突：不能静默更换父确认或重发')
    await panel.get_by_role('button', name='保存用例覆盖确认', exact=True).click()
    await expect(panel.get_by_label('原覆盖确认请求', exact=True)).to_contain_text('原请求已明确拒绝')
    conflict = json.loads(await panel.get_by_label('原覆盖确认请求', exact=True).locator('pre').text_content())
    assert conflict['expected_coverage_id'] == restored['id'] and len((await get(path))['items']) == 4
    await page.screenshot(path=str(evidence/'coverage-conflict.png'), full_page=True)
    await panel.get_by_role('button', name='重新读取依据并确认覆盖', exact=True).click()
    await expect(panel).to_contain_text('已重新读取最新覆盖')
    final = external
    if retry_state:
        retry = retry_state['items'][-1]['view']['task']
        reference = retry['checkpoint']['receipt']['versions'][0]
        await panel.get_by_role('button', name='移除覆盖版本 '+references[0]['reference']['version_id'], exact=True).click()
        await choose_version(panel, reference)
        await panel.get_by_role('button', name='打开所选用例评审', exact=True).click()
        dialog = page.get_by_role('dialog', name='测试用例内容', exact=True)
        await expect(dialog.locator('select')).to_have_value(reference['version_id'])
        await dialog.get_by_role('button', name='提交当前版本评审', exact=True).click()
        await expect(dialog).to_contain_text('待评审')
        await dialog.get_by_role('button', name='关闭', exact=True).click()
        await add_link(panel, reference, 'retention', retry['id'])
        for row in [references[1]]:
            await panel.get_by_role('button', name='读取完整关联用例 '+row['reference']['version_id'], exact=True).click()
        await panel.get_by_label('覆盖确认说明', exact=True).fill('明确关联原响应重试草稿；待评审状态不冒充启用或测试通过。')
        await panel.get_by_role('button', name='保存用例覆盖确认', exact=True).click()
        async with asyncio.timeout(20):
            while True:
                final = (await get(path))['items'][0]
                if final['revision'] == 5: break
                await asyncio.sleep(.05)
        link = next(item for item in final['case_links'] if item['case_version_id'] == reference['version_id'])
        assert link['generation']['task_id'] == retry['id'] and link['review_status'] == 'pending_review'
        assert link['active_version_id'] == references[0]['reference']['version_id']
    final_matrix = await get(stem+'/matrix?version_id='+requirement['id'])
    assert final_matrix['coverage_review'] == final and final_matrix['version'] == original['version']
    assert all(point['design_status'] == 'design_covered' and point['execution_status'] == 'not_assessed' for point in final_matrix['points'])
    await panel.get_by_label('覆盖确认历史', exact=True).select_option(first['id'])
    await expect(panel).to_contain_text('第 1 次 · '+first['id'])
    assert (await get(stem+'/matrix?version_id='+requirement['id']+'&coverage_id='+first['id'])) == first_matrix
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_coverage ORDER BY revision')
        records = [row['record'] for row in cur.fetchall()]
    for record in records: fixed_coverage(record)
    assert records == [first, cleared, restored, external, *([final] if retry_state else [])]
    assert len(sends) == count and (await get(base+'/impact-snapshots/'+reviews['reviews'][0]['source']['snapshot_id']))['snapshot']['sha256'] == reviews['reviews'][0]['source']['snapshot_sha256']
    state = {'requirement':requirement, 'first':first, 'first_payload':submissions[0]['request'], 'first_matrix':first_matrix,
        'initial':original, 'cleared':cleared, 'restored':restored, 'external':external, 'conflict_request':conflict,
        'final':final, 'final_matrix':final_matrix, 'references':references, 'pg_coverage':records,
        'actual_built_app':True, 'actual_docx':True, 'independent_workers':True,
        'coverage_model_http_calls':0, 'lost_reply_recovered_without_post':True, 'user_business_review':False}
    (evidence/'coverage-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    await page.screenshot(path=str(evidence/'coverage-fixed-history.png'), full_page=True)
    return state


async def coverage_after_restart(page, *, base, state, client, evidence):
    first = state['first']; requirement = state['requirement']
    key = f"qa-agent:requirement-coverage:{first['project_id']}:{first['document_id']}:{first['requirement_id']}:{first['requirement_version_id']}"
    await page.evaluate('(data)=>sessionStorage.setItem(data.key,JSON.stringify(data.value))', {'key':key,
        'value':{'requirement_sha256':first['requirement_version_sha256'], 'pending':state['first_payload'], 'confirmedFailure':False}})
    await page.reload(); await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    matrix, panel = await open_coverage(page, requirement)
    await expect(panel).to_contain_text('覆盖确认已保存：'+first['id'])
    await expect(panel).to_contain_text('当前项目不可提交新覆盖')
    await expect(panel.get_by_role('button', name='保存用例覆盖确认', exact=True)).to_be_disabled()
    await expect(panel.get_by_label('原覆盖确认请求', exact=True)).to_have_count(0)
    reference = state['final']['case_links'][0]
    read = panel.get_by_role('button', name='读取完整关联用例 '+reference['case_version_id'], exact=True)
    await expect(read).to_be_enabled(); await read.click()
    await expect(panel).to_contain_text('实际读取 SHA256 '+reference['content_hash'])
    await expect(panel).to_contain_text('完整步骤11')
    await expect(panel.get_by_label(f"覆盖 {reference['case_version_id']} 验证点 {reference['point_keys'][0]}", exact=True)).to_be_disabled()
    await panel.get_by_label('覆盖确认历史', exact=True).select_option(first['id'])
    await expect(panel).to_contain_text('第 1 次 · '+first['id'])
    response = await client.get(base+f"/requirements/{first['requirement_id']}/matrix", params={'version_id':first['requirement_version_id'], 'coverage_id':first['id']})
    assert response.status_code == 200 and response.json() == state['first_matrix']
    path = base+f"/requirements/{first['requirement_id']}/versions/{first['requirement_version_id']}/coverage-reviews"
    replay = await client.post(path, json=state['first_payload']); assert replay.status_code == 201 and replay.json() == first
    rejected = await client.post(path, json={**state['first_payload'], 'request_id':'archived-coverage-new'})
    assert rejected.status_code == 409, rejected.text
    state['api_restart_dispatch_disabled_archived_original_recovery'] = True
    state['archived_full_association_version_read'] = reference['case_version_id']
    (evidence/'coverage-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    await page.screenshot(path=str(evidence/'coverage-archived-original-recovery.png'), full_page=True)
