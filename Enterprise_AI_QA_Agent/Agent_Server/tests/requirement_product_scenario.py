"""Real requirement controls against the built App and production storage."""
import asyncio
import json
from uuid import uuid4

from playwright.async_api import expect

from src.infrastructure.postgres_runtime import postgres_connect


async def review_from_product(page, *, base, view, case, get, post, evidence, settings):
    await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    panel = page.get_by_label('需求评审与追踪', exact=True)
    await expect(panel.get_by_role('button', name='保存需求评审', exact=True)).to_be_enabled()
    await panel.get_by_label('需求标题', exact=True).fill('正式订单保留期限')
    await panel.get_by_label('需求正文', exact=True).fill('订单记录保留9天。')
    for number, key, description in [(1, 'retention', '确认订单记录9天保留'), (2, 'boundary', '确认保留期边界')]:
        await panel.get_by_role('button', name='添加验证点', exact=True).click()
        await panel.get_by_label(f'验证点编号 {number}', exact=True).fill(key)
        await panel.get_by_label(f'验证点说明 {number}', exact=True).fill(description)
    await panel.get_by_label(f'验证点 1 关联版本 {case.version.id}', exact=True).check()
    await panel.get_by_label('评审理由', exact=True).fill('实际产品验证：依据原文确认，设计关系不表示执行已通过。')
    submissions = []; lost = asyncio.Event()
    async def drop_reply(route):
        if route.request.method != 'POST': return await route.continue_()
        response = await route.fetch()
        assert response.status == 201, await response.text()
        submissions.append({'request': route.request.post_data_json, 'saved': await response.json()})
        await route.abort('failed'); lost.set()
    pattern = '**/impact-snapshots/*/reviews'
    await page.route(pattern, drop_reply)
    await panel.get_by_role('button', name='保存需求评审', exact=True).click()
    await asyncio.wait_for(lost.wait(), timeout=30)
    await expect(panel.get_by_label('原需求评审请求', exact=True)).to_contain_text('提交结果尚未确认')
    assert len(submissions) == 1
    first = submissions[0]['saved']
    (evidence / 'requirement-lost-response.json').write_text(json.dumps(submissions, ensure_ascii=False, indent=2), encoding='utf-8')
    await page.unroute(pattern, drop_reply); await page.reload()
    await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    await expect(panel).to_contain_text('评审已保存：' + first['id'])
    await expect(panel.get_by_label('原需求评审请求', exact=True)).to_have_count(0)
    matrix = panel.get_by_label('需求追踪矩阵', exact=True)
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已有设计覆盖')
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('未覆盖')
    await expect(matrix).to_contain_text('执行结果尚未关联')
    await page.screenshot(path=str(evidence / 'requirement-v1-recovered.png'), full_page=True)
    requirement_id = first['requirement_version']['requirement_id']
    v1_id = first['requirement_version']['id']
    matrix_v1 = await get(base + f'/requirements/{requirement_id}/matrix?version_id={v1_id}')
    assert (await get(base + '/requirement-reviews/by-request?request_id=' + first['request']['request_id'])) == first

    await panel.get_by_label('关联需求', exact=True).select_option(requirement_id)
    await expect(panel.get_by_label(f'验证点 1 关联版本 {case.version.id}', exact=True)).not_to_be_checked()
    await panel.get_by_label('需求正文', exact=True).fill('订单记录保留9天；边界需要重新核对。')
    await panel.get_by_label('评审理由', exact=True).fill('本版明确不继承旧设计覆盖。')
    await panel.get_by_role('button', name='保存需求评审', exact=True).click()
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('关联用例待复核')
    history = (await get(base + '/impact-snapshots/' + view['snapshot']['id'] + '/reviews'))['items']
    assert len(history) == 2
    second = history[-1]
    assert second['request']['expected_review_id'] == first['id']
    assert second['requirement_version']['parent_version_id'] == v1_id and second['requirement_version']['case_links'] == []
    assert second['requirement_version']['points'] == first['requirement_version']['points']
    await matrix.get_by_label('需求历史版本', exact=True).select_option(v1_id)
    await expect(matrix).to_contain_text('正在查看历史版本，当前版本为 V2')
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已有设计覆盖')
    historical = await get(base + f'/requirements/{requirement_id}/matrix?version_id={v1_id}')
    assert historical['version'] == matrix_v1['version'] and historical['points'] == matrix_v1['points']
    assert historical['requirement']['current_version_id'] == second['requirement_version']['id']
    await page.screenshot(path=str(evidence / 'requirement-v1-history-after-v2.png'), full_page=True)

    # A competing actual API review leaves the open form stale. No automatic rebase/new send is allowed.
    external = await post(base + '/impact-snapshots/' + view['snapshot']['id'] + '/reviews', {
        'request_id': 'external-' + uuid4().hex, 'rule_id': first['request']['rule_id'], 'decision': 'reject',
        'note': '实际并发评审：保留正式需求，拒绝再次变更', 'expected_review_id': second['id']}, 201)
    await panel.get_by_label('评审理由', exact=True).fill('实际冲突验证：不自动重定父版本或重发')
    await panel.get_by_role('button', name='保存需求评审', exact=True).click()
    pending = panel.get_by_label('原需求评审请求', exact=True)
    await expect(pending).to_contain_text('原请求已明确拒绝')
    conflict_request = json.loads(await pending.locator('pre').text_content())
    assert conflict_request['expected_review_id'] == second['id']
    assert len(await get(base + f'/requirements/{requirement_id}/versions')) == 2
    await page.screenshot(path=str(evidence / 'requirement-conflict.png'), full_page=True)
    await panel.get_by_role('button', name='重新读取依据并评审', exact=True).click()
    await expect(panel).to_contain_text('已重新读取依据，请核对表单后重新评审')
    await expect(panel).to_contain_text(external['id'])

    for decision, note in [('reject', '保留已确认需求，不接受再次改变'), ('defer', '后续业务评审，保留现有版本')]:
        await panel.get_by_label('评审决定', exact=True).select_option(decision)
        await panel.get_by_label('评审理由', exact=True).fill(note)
        await panel.get_by_role('button', name='保存需求评审', exact=True).click()
        await expect(panel).to_contain_text('评审已保存')
        async with asyncio.timeout(15):
            while True:
                history = (await get(base + '/impact-snapshots/' + view['snapshot']['id'] + '/reviews'))['items']
                if history[-1]['request']['decision'] == decision: break
                await asyncio.sleep(.05)
        assert history[-1]['requirement_version'] is None
        await expect(panel.get_by_role('button', name='保存需求评审', exact=True)).to_be_enabled()

    await panel.get_by_label('评审决定', exact=True).select_option('accept')
    await panel.get_by_label('需求变化分类', exact=True).select_option('removed')
    await panel.get_by_label('评审理由', exact=True).fill('产品验证移除版本，只保留历史。')
    await panel.get_by_role('button', name='保存需求评审', exact=True).click()
    await expect(matrix).to_contain_text('该需求版本已移除')
    history = (await get(base + '/impact-snapshots/' + view['snapshot']['id'] + '/reviews'))['items']
    assert len(history) == 6
    removed = history[-1]
    assert removed['request']['expected_review_id'] == history[-2]['id']
    assert removed['requirement_version']['points'] == removed['requirement_version']['case_links'] == []
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_reviews ORDER BY created_at,id')
        records = [row['record'] for row in cur.fetchall()]
        cur.execute(f'SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_versions ORDER BY version')
        versions = [row['record'] for row in cur.fetchall()]
    assert records == history and len(versions) == 3
    assert [review['review_number'] for review in history] == [1, 2, 3, 4, 5, 6]
    assert (await get(base + '/impact-snapshots/' + view['snapshot']['id'])) == view
    state = {'submissions_with_lost_reply': submissions, 'reviews': history, 'pg_reviews': records, 'pg_versions': versions,
        'matrix_v1_before': matrix_v1, 'matrix_v1_after_v2': historical, 'requirement_id': requirement_id,
        'conflict_request': conflict_request, 'competing_api_review': external,
        'full_browser': True, 'user_business_review': False, 'new_model_sends': 0}
    (evidence / 'requirement-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return state


async def review_after_restart(page, *, base, state, client, evidence):
    await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    panel = page.get_by_label('需求评审与追踪', exact=True)
    await expect(panel.get_by_role('button', name='保存需求评审', exact=True)).to_be_enabled()
    await panel.get_by_label('查看需求矩阵', exact=True).select_option(state['requirement_id'])
    matrix = panel.get_by_label('需求追踪矩阵', exact=True)
    await expect(matrix).to_contain_text('该需求版本已移除')
    await matrix.get_by_label('需求历史版本', exact=True).select_option(state['reviews'][0]['requirement_version']['id'])
    await expect(matrix).to_contain_text('正在查看历史版本，当前版本为 V3')
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已有设计覆盖')
    # Archive via the actual production endpoint, then prove read/replay remain available from the built App.
    archived = await client.post(base.split('/documents/')[0] + '/archive')
    assert archived.status_code == 200, archived.text
    original = state['submissions_with_lost_reply'][0]['request']
    key = f'qa-agent:requirement-review:{state["reviews"][0]["project_id"]}:{state["reviews"][0]["document_id"]}:{state["reviews"][0]["source"]["snapshot_id"]}'
    await page.evaluate('(data) => sessionStorage.setItem(data.key, JSON.stringify(data.payload))', {'key': key, 'payload': original})
    await page.reload(); await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    await expect(panel).to_contain_text('当前项目不可提交新评审')
    await expect(panel).to_contain_text('评审已保存：' + state['reviews'][0]['id'])
    await expect(panel.get_by_role('button', name='保存需求评审', exact=True)).to_be_disabled()
    await expect(panel.get_by_label('原需求评审请求', exact=True)).to_have_count(0)
    await expect(panel.get_by_label('需求追踪矩阵', exact=True)).to_contain_text('该需求版本已移除')
    state['api_restart_history'] = state['archived_original_request_recovered'] = True
    (evidence / 'requirement-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    await page.screenshot(path=str(evidence / 'requirement-archived-history.png'), full_page=True)
