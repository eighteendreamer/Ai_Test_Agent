"""Formal execution selection and immutable histories over actual production records."""
import asyncio
import json

from playwright.async_api import expect

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_execution_store import fixed_execution
from requirement_execution_production_scenario import prepare_execution_fixture
from test_requirement_execution import actual_run


async def open_execution(page, version):
    review = page.get_by_label('需求评审与追踪', exact=True)
    await review.get_by_label('查看需求矩阵', exact=True).select_option(version['requirement_id'])
    matrix = review.get_by_label('需求追踪矩阵', exact=True)
    await expect(matrix.get_by_label('需求历史版本', exact=True)).to_be_enabled()
    await matrix.get_by_label('需求历史版本', exact=True).select_option(version['id'])
    await expect(matrix.get_by_role('button', name='管理正文执行核验', exact=True)).to_be_enabled()
    await matrix.get_by_role('button', name='管理正文执行核验', exact=True).click()
    panel = matrix.get_by_label('正文执行核验关联', exact=True)
    await expect(panel.get_by_role('button', name='读取实际执行结果', exact=True)).to_be_enabled()
    return matrix, panel


async def choose_checks(panel, result, indexes):
    await panel.get_by_label('执行候选结果', exact=True).select_option(result.id)
    await expect(panel).to_contain_text('核对完整操作11')
    await expect(panel).to_contain_text('全部正文与False/0/null保留')
    await expect(panel).to_contain_text('原运行总体结果 failed')
    for number, index in enumerate(indexes, 1):
        await panel.get_by_role('button', name=f'选择正文核验 {index}', exact=True).click()
        await panel.get_by_label(f'核验 {number} 验证点 retention', exact=True).check()


async def save_confirmation(page, panel, note):
    await panel.get_by_label('执行确认说明', exact=True).fill(note)
    async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/execution-reviews')) as waiting:
        await panel.get_by_role('button', name='保存正文执行确认', exact=True).click()
    response = await waiting.value
    assert response.status == 201, await response.text()
    saved = await response.json()
    await expect(panel).to_contain_text('执行确认已保存：'+saved['id'])
    await expect(panel.get_by_role('button', name='重新读取执行确认与编辑依据', exact=True)).to_be_enabled()
    return saved


async def execution_from_product(page, *, base, root, review, settings, get, post, client, evidence, sends, business_http, source_ref):
    count = len(sends)
    f, path, stem, version = await prepare_execution_fixture(base=base, root=root, review=review, settings=settings,
        post=post, business_http=business_http, source_ref=source_ref)
    failed = await actual_run(f, days=7)
    regression = await actual_run(f, parent=failed, cleanup_fail=True)
    assert failed.status == regression.status == 'failed'
    initial = await get(stem+'/matrix?version_id='+version['id'])
    matrix, panel = await open_execution(page, version)
    requests = []
    def observe(request):
        if request.method == 'POST' and request.url.endswith('/execution-reviews'):
            requests.append(request.post_data_json)
    page.on('request', observe)
    await choose_checks(panel, failed, [1])
    await panel.get_by_label('执行确认说明', exact=True).fill('实际九天规则收到七天；关联原正文失败。')
    lost = asyncio.Event(); submissions = []
    async def lose_reply(route):
        if route.request.method != 'POST': return await route.continue_()
        response = await route.fetch(); assert response.status == 201, await response.text()
        submissions.append({'request': route.request.post_data_json, 'saved': await response.json()})
        await route.abort('failed'); lost.set()
    pattern = '**/versions/*/execution-reviews'
    await page.route(pattern, lose_reply)
    await panel.get_by_role('button', name='保存正文执行确认', exact=True).click()
    await asyncio.wait_for(lost.wait(), 30)
    await expect(panel.get_by_label('原执行确认请求', exact=True)).to_contain_text('提交结果尚未确认')
    assert len(submissions) == len(requests) == 1
    first = submissions[0]['saved']; assert first['checks'][0]['check']['status'] == 'failed'
    await page.screenshot(path=str(evidence/'execution-lost-reply.png'), full_page=True)
    await page.unroute(pattern, lose_reply); await page.reload()
    await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    matrix, panel = await open_execution(page, version)
    await expect(panel).to_contain_text('执行确认已保存：'+first['id'])
    await expect(panel.get_by_label('原执行确认请求', exact=True)).to_have_count(0)
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：失败')
    assert len(requests) == 1 and len(sends) == count
    first_matrix = await get(stem+'/matrix?version_id='+version['id'])
    # Fixed history reads do not replace the pending edit collection.
    await panel.get_by_label('执行确认历史', exact=True).select_option('initial')
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：未关联')
    await expect(panel.get_by_role('button', name='移除正文核验 1', exact=True)).to_be_enabled()
    await panel.get_by_role('button', name='移除正文核验 1', exact=True).click()
    clear = await save_confirmation(page, panel, '明确撤销选中核验，保留原失败及历史。')
    assert clear['request']['checks'] == [] and clear['previous_execution_id'] == first['id']
    await choose_checks(panel, regression, [1, 2, 3, 4])
    third = await save_confirmation(page, panel, '实际新回归正文九天及False/0/null通过，清理失败单列。')
    assert third['previous_execution_id'] == clear['id']
    assert len(third['checks']) == 4 and all(check['check']['status'] == 'passed' and check['result_status'] == 'failed'
        and check['regression_source_result_id'] == failed.id for check in third['checks'])
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：通过')
    await expect(matrix.get_by_label('验证点 boundary', exact=True)).to_contain_text('已关联核验：未关联')
    await expect(matrix).to_contain_text('部分检查通过不能证明整个需求通过')
    await panel.get_by_label('执行确认历史', exact=True).select_option(first['id'])
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：失败')
    assert await get(stem+'/matrix?version_id='+version['id']+'&coverage_id='+f.coverage['id']+'&execution_id='+first['id']) == first_matrix
    await panel.get_by_label('执行确认历史', exact=True).select_option('latest')
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：通过')
    # Actual API writer races with the page's frozen parent; page must not silently rebase.
    concurrent = await post(path, {**third['request'], 'request_id': 'concurrent-execution-page', 'expected_execution_id': third['id'],
        'note': '并发API明确撤销', 'checks': []}, 201)
    await panel.get_by_label('执行确认说明', exact=True).fill('旧父确认不能覆盖并发决定')
    async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/execution-reviews')) as waiting:
        await panel.get_by_role('button', name='保存正文执行确认', exact=True).click()
    assert (await waiting.value).status == 409
    await expect(panel.get_by_label('原执行确认请求', exact=True)).to_contain_text('原请求已明确拒绝')
    await panel.get_by_role('button', name='重读依据并确认执行', exact=True).click()
    await expect(panel.get_by_label('原执行确认请求', exact=True)).to_have_count(0)
    await expect(panel.get_by_role('button', name='读取实际执行结果', exact=True)).to_be_enabled()
    await choose_checks(panel, regression, [1, 2, 3, 4])
    final = await save_confirmation(page, panel, '重读并发决定后，明确选择新回归四项正文核验。')
    assert final['previous_execution_id'] == concurrent['id']
    final_matrix = await get(stem+'/matrix?version_id='+version['id'])
    assert final_matrix['deployment_status'] == 'unknown' and final_matrix['points'][0]['execution_scope'] == 'selected_checks'
    assert final_matrix['version'] == initial['version'] and len(sends) == count and len(business_http[1]) == 6
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_execution ORDER BY revision")
        records = [row['record'] for row in cur.fetchall()]
    assert records == [first, clear, third, concurrent, final]
    assert all(fixed_execution(record).model_dump(mode='json') == record for record in records)
    state = {'path': path, 'stem': stem, 'base': base, 'version': version, 'coverage': f.coverage,
        'first': first, 'clear': clear, 'third': third, 'concurrent': concurrent, 'final': final,
        'first_matrix': first_matrix, 'matrix': final_matrix, 'execution_sql': records,
        'lost_response_submissions': submissions, 'browser_submissions': requests,
        'selected_case': f.selected.model_dump(mode='json'), 'results': [r.model_dump(mode='json') for r in f.results],
        'run_details': [await get('/runs/'+r.run_id) for r in f.results],
        'tool_jobs': [(await f.jobs.get_job_detail(r.job_id)).model_dump(mode='json') for r in f.tool_records],
        'business_http': business_http[1], 'association_model_http_calls': len(sends)-count,
        'actual_built_App': True, 'autonomous_agent_model_execution': False, 'external_default_model': False}
    (evidence/'requirement-execution-product.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    (evidence/'execution-final-dom.txt').write_text(await panel.inner_text(), encoding='utf-8')
    await page.screenshot(path=str(evidence/'execution-final.png'), full_page=True)
    return state


async def execution_product_after_restart(page, *, base, state, get, post, client, settings, evidence, sends):
    count = len(sends); first = state['first']; version = state['version']
    key = f"qa-agent:requirement-execution:{first['project_id']}:{first['document_id']}:{first['requirement_id']}:{first['requirement_version_id']}:{state['coverage']['id']}"
    cached = {'requirement_sha256': first['requirement_version_sha256'], 'coverage_sha256': first['coverage_sha256'],
        'pending': first['request'], 'confirmedFailure': False}
    await page.evaluate('(data) => sessionStorage.setItem(data.key, JSON.stringify(data.value))', {'key': key, 'value': cached})
    await page.reload(); await page.get_by_role('button', name='打开需求评审与追踪', exact=True).click()
    matrix, panel = await open_execution(page, version)
    await expect(panel).to_contain_text('当前项目不可提交新执行确认')
    await expect(panel).to_contain_text('执行确认已保存：'+first['id'])
    await expect(panel.get_by_role('button', name='保存正文执行确认', exact=True)).to_be_disabled()
    await expect(panel.get_by_label('原执行确认请求', exact=True)).to_have_count(0)
    await panel.get_by_label('执行确认历史', exact=True).select_option(first['id'])
    await expect(matrix.get_by_label('验证点 retention', exact=True)).to_contain_text('已关联核验：失败')
    await panel.get_by_label('执行候选结果', exact=True).select_option(state['results'][1]['id'])
    await expect(panel).to_contain_text('核对完整操作11')
    await expect(panel).to_contain_text('回归源失败结果 '+state['results'][0]['id'])
    await expect(panel.get_by_role('button', name='选择正文核验 1', exact=True)).to_be_disabled()
    assert await get(state['stem']+'/matrix?version_id='+version['id']) == state['matrix']
    assert await post(state['path'], first['request'], 201) == first
    rejected = await client.post(state['path'], json={**first['request'], 'request_id': 'new-archived-execution-page'})
    assert rejected.status_code == 409
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_requirement_execution ORDER BY revision")
        records = [row['record'] for row in cur.fetchall()]
    assert records == state['execution_sql'] and len(sends) == count
    assert len(state['browser_submissions']) == 5
    (evidence/'requirement-execution-product-restart.json').write_text(json.dumps({'records': records,
        'built_App_recovery': True, 'API_restarted_dispatch_disabled': True, 'archived_full_reads': True,
        'new_confirmation_rejected': True, 'recovery_model_http_calls': len(sends)-count}, ensure_ascii=False, indent=2), encoding='utf-8')
    (evidence/'execution-archived-dom.txt').write_text(await panel.inner_text(), encoding='utf-8')
    await page.screenshot(path=str(evidence/'execution-archived.png'), full_page=True)
