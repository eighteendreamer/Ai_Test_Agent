"""Built generation controls against actual immutable sources, storage and CLIs."""
import asyncio
import hashlib
import json
from playwright.async_api import expect
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect


async def generation_from_product(page, *, base, state, case, model_key, get, client, worker,
    queue, processes, objects, evidence, settings, sends):
    await page.get_by_role('button',name='打开需求增量生成',exact=True).click()
    panel = page.get_by_label('需求增量生成',exact=True)
    original_review = state['reviews'][0]
    await panel.get_by_label('生成依据需求版本',exact=True).select_option(original_review['id'])
    await panel.get_by_text('项目增量生成设置',exact=True).click()
    await panel.get_by_label('生成请求发送策略',exact=True).select_option('single_attempt')
    await panel.get_by_label('生成单次调用超时（秒）',exact=True).fill('30')
    await panel.get_by_role('button',name='保存生成设置',exact=True).click()
    await expect(panel).to_contain_text('项目生成设置已保存')
    root = base.split('/documents/')[0]
    policy = await get(root+'/case-generation-settings')
    assert MySQLApplicationConfigStore(settings).get('test_case.incremental_generation_settings',scope_key=case.case.project_id).payload == policy['payload']
    await panel.get_by_role('button',name='读取生成基线 retention',exact=True).click()
    await expect(panel).to_contain_text('完整步骤11')
    await expect(panel).to_contain_text('"布尔": false')
    await panel.get_by_label('生成基线 '+case.version.id,exact=True).check()
    await panel.get_by_label('增量新建用例',exact=True).check()
    await panel.get_by_label('新用例模式元数据',exact=True).select_option('api_testing')
    await panel.get_by_label('生成模型配置编号',exact=True).fill(model_key)
    await page.evaluate("()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true})")
    lost = asyncio.Event(); submissions = []
    async def lose_reply(route):
        if route.request.method != 'POST': return await route.continue_()
        response = await route.fetch(); assert response.status == 202,await response.text()
        submissions.append({'request':route.request.post_data_json,'group':await response.json()})
        await route.abort('failed'); lost.set()
    pattern = '**/case-generations'
    await page.route(pattern,lose_reply)
    await panel.get_by_role('button',name='提交固定增量生成',exact=True).click()
    await asyncio.wait_for(lost.wait(),timeout=30)
    await expect(panel.get_by_label('原增量生成请求',exact=True)).to_contain_text('提交结果尚未确认')
    assert len(submissions)==1 and len(sends)==2
    original_request = submissions[0]['request']; group = submissions[0]['group']
    assert original_request['creation_key'].startswith('generation-')
    assert original_request['requirement_version_id']==original_review['requirement_version']['id']
    assert original_request['target_case_version_ids']==[case.version.id]
    await page.unroute(pattern,lose_reply); await page.reload()
    await page.get_by_role('button',name='打开需求评审与追踪',exact=True).click()
    await expect(page.get_by_role('button',name='保存需求评审',exact=True)).to_be_enabled()
    await page.get_by_role('button',name='打开需求增量生成',exact=True).click()
    await expect(panel).to_contain_text('生成提交已确认：'+group['id'])
    await expect(panel.get_by_label('原增量生成请求',exact=True)).to_have_count(0)
    fixed = await get(base+f"/case-generations/{group['id']}/scope")
    assert fixed['requirement']==original_review['requirement_version'] and len(fixed['modules'])==2
    assert fixed['template'] and fixed['authoring']
    assert (await get(base+'/case-generations/by-request?creation_key='+original_request['creation_key']))==group
    # Changing current editable policy must not change the already submitted group's policy.
    await panel.get_by_text('项目增量生成设置',exact=True).click()
    await panel.get_by_label('生成完整输入字节上限',exact=True).fill('1')
    await panel.get_by_role('button',name='保存生成设置',exact=True).click()
    await expect(panel).to_contain_text('项目生成设置已保存')
    changed_policy=await get(root+'/case-generation-settings'); assert changed_policy['payload']['max_input_bytes']==1
    for number in range(2): await worker('case_generation','product-generation-'+str(number))
    await panel.get_by_role('button',name='刷新当前生成结果',exact=True).click()
    await expect(panel.get_by_label('固定增量生成结果',exact=True)).to_contain_text('草稿或候选已保存')
    result=await get(base+f"/case-generations/{group['id']}")
    assert all(task['status']=='completed' for task in result['modules']) and result['group']['config']==policy
    assert len(sends)==4
    update,new=result['modules']
    receipt=update['checkpoint']['receipt']['versions'][0]
    assert receipt['case_id']==case.case.id and receipt['version']==3
    versions=await get('/test-cases/'+case.case.id+'/versions')
    draft=next(version for version in versions if version['id']==receipt['version_id'])
    assert next(version for version in versions if version['id']==case.version.id)==case.version.model_dump(mode='json')
    current=await get('/test-cases/'+case.case.id)
    assert current['active_version_id']==case.version.id and current['version_reviews'][receipt['version_id']]['status']=='draft'
    new_case=await get('/test-cases/'+new['checkpoint']['receipt']['versions'][0]['case_id'])
    assert new_case['active_version_id'] is None and new_case['lifecycle_status']=='draft'
    assert len(draft['content_json']['steps'])==12 and draft['content_json']['深层扩展']==case.version.content_json['深层扩展']
    await panel.get_by_role('button',name='读取完整生成草稿 '+draft['id'],exact=True).click()
    await expect(panel).to_contain_text('完整步骤11')
    await expect(panel).to_contain_text('不能丢失')
    responses=[]
    for task in result['modules']:
        await panel.get_by_role('button',name='读取原生成响应 '+task['id'],exact=True).click()
        async with page.expect_download() as download:
            await panel.get_by_role('button',name='下载原生成响应 '+task['id'],exact=True).click()
        target=evidence/('downloaded-generation-response-'+task['id']+'.json')
        await (await download.value).save_as(target)
        value=await get(base+'/case-generation-tasks/'+task['id']+'/response')
        assert json.loads(target.read_text(encoding='utf-8'))==value; responses.append(value)
        await queue.enqueue({'schema_version':1,'task_type':'case_generation','project_id':case.case.project_id,'task_id':task['id']})
        terminal=processes.start('product-generation-terminal-'+task['id'],'-m','src.cli.run_case_generation_worker','--once','--worker-id','terminal-generation')
        assert await processes.wait(terminal)==0
    assert len(sends)==4 and await get(base+f"/case-generations/{group['id']}")==result
    async with page.expect_download() as download:
        await panel.get_by_role('button',name='下载完整生成范围 JSON',exact=True).click()
    scope_download=evidence/'downloaded-generation-scope.json'
    await (await download.value).save_as(scope_download)
    assert json.loads(scope_download.read_text(encoding='utf-8'))==fixed
    await page.screenshot(path=str(evidence/'generation-drafts.png'),full_page=True)
    # A new group independently captures the changed one-byte budget and fails without sending.
    await panel.get_by_label('生成依据需求版本',exact=True).select_option(original_review['id'])
    await panel.get_by_label('生成基线 '+case.version.id,exact=True).check()
    await expect(panel.get_by_label('增量新建用例',exact=True)).not_to_be_checked()
    failed_submitted=[]
    async def observe(route):
        if route.request.method!='POST': return await route.continue_()
        response=await route.fetch(); assert response.status==202,await response.text()
        failed_submitted.append({'request':route.request.post_data_json,'group':await response.json()}); await route.fulfill(response=response)
    await page.route(pattern,observe)
    await panel.get_by_role('button',name='提交固定增量生成',exact=True).click()
    await expect(panel).to_contain_text('生成提交已确认')
    async with asyncio.timeout(30):
        while not failed_submitted: await asyncio.sleep(.05)
    await page.unroute(pattern,observe)
    await worker('case_generation','product-generation-budget-failure')
    await panel.get_by_role('button',name='刷新当前生成结果',exact=True).click()
    await expect(panel.get_by_label('固定增量生成结果',exact=True)).to_contain_text('input_budget_or_configuration_rejected')
    failed=await get(base+"/case-generations/"+failed_submitted[0]['group']['id'])
    assert failed['modules'][0]['status']=='failed' and len(sends)==4
    await panel.get_by_label('生成依据需求版本',exact=True).select_option(state['reviews'][-1]['id'])
    await panel.get_by_label('生成基线 '+case.version.id,exact=True).check()
    await expect(panel.get_by_label('增量新建用例',exact=True)).to_have_count(0)
    retired_submitted=[]
    async def observe_removed(route):
        if route.request.method!='POST': return await route.continue_()
        response=await route.fetch(); assert response.status==202,await response.text()
        retired_submitted.append(await response.json()); await route.fulfill(response=response)
    await page.route(pattern,observe_removed)
    await panel.get_by_role('button',name='提交固定增量生成',exact=True).click()
    async with asyncio.timeout(30):
        while not retired_submitted: await asyncio.sleep(.05)
    await page.unroute(pattern,observe_removed)
    await worker('case_generation','product-generation-retirement')
    await panel.get_by_role('button',name='刷新当前生成结果',exact=True).click()
    await expect(panel).to_contain_text('原用例停用候选（待评审）')
    retired=await get(base+'/case-generations/'+retired_submitted[0]['id'])
    assert retired['modules'][0]['checkpoint']['receipt']['kind']=='retirement_candidate' and len(sends)==4
    assert (await get('/test-cases/'+case.case.id))==current
    with postgres_connect(settings) as conn,conn.cursor() as cur:
        table=settings.database.postgres_api_doc_table
        cur.execute(f"SELECT record FROM {table}_case_gen_groups ORDER BY (record->>'created_at')::timestamptz,id")
        pg_groups=[row['record'] for row in cur.fetchall()]
        cur.execute(f'SELECT id,status,checkpoint FROM {table}_case_gen_tasks ORDER BY created_at,id')
        pg_tasks=[{'id':str(row['id']),'status':row['status'],'checkpoint':row['checkpoint']} for row in cur.fetchall()]
    assert len(pg_groups)==3 and len(pg_tasks)==4
    for value in [result,failed,retired]:
        assert value['group'] in pg_groups
        for task in value['modules']:
            assert {'id':task['id'],'status':task['status'],'checkpoint':task['checkpoint']} in pg_tasks
    for value in [result,failed,retired]:
        references=[value['group']['scope'],*[task['checkpoint']['response'] for task in value['modules']
            if task['checkpoint'] and task['checkpoint']['response']]]
        for reference in references:
            raw=(await objects.read_object_uri(reference['storage_uri']))['content']
            assert len(raw)==reference['size_bytes'] and hashlib.sha256(raw).hexdigest()==reference['sha256']
            (evidence/(reference['sha256']+'.object')).write_bytes(raw)
    output={'normal':result,'scope':fixed,'original_request':original_request,'lost_response_submission':submissions,
        'responses':responses,'draft':draft,'current_case':current,'new_case':new_case,'failed':failed,'retired':retired,
        'pg_groups':pg_groups,'pg_tasks':pg_tasks,'mysql_initial_policy':policy,'mysql_changed_policy':changed_policy,
        'full_browser':True,'independent_generation_workers':True,'physical_generation_http_calls':2,
        'terminal_redelivery_no_new_send':True,'user_business_review':False}
    output['actual_request_identity_fallback']=True
    (evidence/'generation-product.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    return output


async def generation_after_restart(page, *, state, base, client, evidence):
    group=state['normal']['group']
    key=f"qa-agent:case-generation:{group['project_id']}:{group['document_id']}"
    await page.evaluate('(data)=>sessionStorage.setItem(data.key,JSON.stringify(data.value))',
        {'key':key,'value':{'pending':state['original_request'],'confirmedFailure':False,'groupId':None}})
    await page.reload(); await page.get_by_role('button',name='打开需求评审与追踪',exact=True).click()
    await expect(page.get_by_role('button',name='保存需求评审',exact=True)).to_be_disabled()
    await page.get_by_role('button',name='打开需求增量生成',exact=True).click()
    panel=page.get_by_label('需求增量生成',exact=True)
    await expect(panel).to_contain_text('当前项目不可提交新生成')
    await expect(panel).to_contain_text('生成提交已确认：'+group['id'])
    await expect(panel.get_by_role('button',name='提交固定增量生成',exact=True)).to_be_disabled()
    await expect(panel.get_by_label('原增量生成请求',exact=True)).to_have_count(0)
    current=await client.get(base+'/case-generations/'+group['id']); assert current.status_code==200
    assert current.json()==state['normal']
    replay=await client.post(base+'/case-generations',json=state['original_request'])
    assert replay.status_code==202 and replay.json()==group
    assert (await client.post(base+'/case-generations',json={**state['original_request'],'creation_key':'disabled-browser-new'})).status_code==503
    await panel.get_by_role('button',name='查看生成组 '+state['failed']['group']['id'],exact=True).click()
    await expect(panel).to_contain_text('input_budget_or_configuration_rejected')
    await panel.get_by_role('button',name='查看生成组 '+state['retired']['group']['id'],exact=True).click()
    await expect(panel).to_contain_text('原用例停用候选（待评审）')
    await panel.get_by_role('button',name='查看生成组 '+group['id'],exact=True).click()
    await expect(panel.get_by_label('固定增量生成结果',exact=True)).to_contain_text(group['id'])
    await expect(panel.get_by_label('固定增量生成结果',exact=True)).to_contain_text(state['scope']['sha256'])
    async with page.expect_download() as download:
        await panel.get_by_role('button',name='下载完整生成范围 JSON',exact=True).click()
    target=evidence/'downloaded-generation-scope-after-restart.json'
    await (await download.value).save_as(target)
    assert json.loads(target.read_text(encoding='utf-8'))==state['scope']
    state['api_restart_disabled_dispatch_history']=True; state['archived_original_request_recovered']=True
    (evidence/'generation-product.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
