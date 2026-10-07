"""Replay the real failed lookup and verify frozen navigation through PG."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path

import pytest

from test_case_effect_evidence import NEW_POLICY
from test_case_planning import active_case, item, finish
from test_case_planning_tasks import tasks_environment, delivery
from src.application.test_cases.planning_tool_contracts import (
    OPERATION_PROMPT_VERSION, query_tool_contracts, tool_catalog,
)
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.schemas.case_planning import CasePlanningPolicy, PlanningQuery
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.project import ProjectCreateRequest

ROOT = Path(__file__).resolve().parents[2]


def failure():
    return json.loads((ROOT / 'docs/upgrade_2026-10-03/p4-default-coverage-task-contract/PG-planning.json').read_bytes())


def test_captured_unique_lookup_loads_exact_contract_only_in_new_version():
    captured = failure()
    contracts = captured['seed']['tool_contracts']
    original = deepcopy(contracts)
    query = PlanningQuery.model_validate(captured['checkpoint']['plan']['rounds'][1]['decision']['queries'][0])
    old, old_result = query_tool_contracts(contracts, query, 20)
    assert not old and old_result == {'tools': [{'key': 'api-test-runner', 'name': 'API Test Runner'}], 'has_more': False, 'offset': 0}
    evidence, metadata = query_tool_contracts(contracts, query, 20, navigation=True)
    assert metadata['contract_loaded'] is True
    direct, _ = query_tool_contracts(contracts, PlanningQuery(kind='tool', source_id='api-test-runner'), 20)
    assert evidence == direct and evidence[0].payload == contracts['api-test-runner']
    assert evidence[0].payload['input_schema']['properties']['task']['required'] == ['task_id', 'method', 'path']
    assert evidence[0].payload['output_variants'][0]['wrapper'] == 'task_result'
    assert metadata['tools'][0]['detail_query'] == {'kind': 'tool', 'source_id': 'api-test-runner'}
    evidence[0].payload['description'] = 'changed local read copy'
    assert contracts == original


@pytest.mark.parametrize('query,offset', [('', 0), ('API', 0), ('API Test Runner', 1), ('absent-fixture-tool', 0)])
def test_ambiguous_blank_and_empty_pages_do_not_claim_contract_loaded(query, offset):
    evidence, metadata = query_tool_contracts(failure()['seed']['tool_contracts'], PlanningQuery(kind='tools', query=query, offset=offset), 20, navigation=True)
    assert not evidence and metadata['contract_loaded'] is False
    assert all(x['detail_query']['source_id'] == x['key'] for x in metadata['tools'])


def test_initial_catalog_navigation_is_not_read_evidence_and_legacy_shape_stays():
    contracts = failure()['seed']['tool_contracts']
    old = tool_catalog(contracts, 20)
    new = tool_catalog(contracts, 20, navigation=True)
    assert 'contract_loaded' not in old and all('detail_query' not in t for t in old['tools'])
    assert new['contract_loaded'] is False and new['has_more'] == old['has_more']
    assert [(x['key'], x['name']) for x in new['tools']] == [(x['key'], x['name']) for x in old['tools']]


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_CASE_PLANNING_TASKS') != '1', reason='actual MySQL/PG/RustFS frozen task')
@pytest.mark.parametrize('legacy', [False, True])
async def test_actual_frozen_navigation_and_old_seed_recovery(tmp_path, monkeypatch, legacy):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='tool-navigation', name='固定导航'))
        content = {'objective': '读取真实需求及完整原生契约', 'steps': [{'order': i, 'action': '完整步骤'} for i in range(8)], 'typed': [False, 0, None]}
        await active_case(env, project, 'navigation', content)
        source = tmp_path / 'navigation-requirements.md'
        source.write_text('# 固定工具来源\n按完整用例进行测试；查询原生API任务接口，完整保留八步和False/0/null。\n', encoding='utf-8')
        doc = await env.docs.upload_document(project_id=project.id, filename=source.name, content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content'] == source.read_bytes()
        await env.service.save_policy(project.id, NEW_POLICY.model_copy(update={'effect_coverage_protocol': 'case-effect-coverage-v1'}))
        old = failure()['seed']
        if legacy:
            prepare = env.service.prepare_task
            async def old_seed(*args, **kwargs):
                seed = await prepare(*args, **kwargs)
                seed.plan.prompt_version = old['plan']['prompt_version']
                seed.system_prompt = old['system_prompt']
                seed.tool_contracts = deepcopy(old['tool_contracts'])
                seed.tool_operations = None
                return seed
            monkeypatch.setattr(env.service, 'prepare_task', old_seed)
        request = CasePlanningTaskRequest(creation_key='nav-'+str(legacy), objective=content['objective'])
        task = await env.tasks.submit(project.id, request)
        def forbidden():
            raise AssertionError('Recovery must not capture current registry')
        monkeypatch.setattr('src.application.test_cases.planning_service.capture_tool_contracts', forbidden)
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        captured_query = failure()['checkpoint']['plan']['rounds'][1]['decision']['queries'][0]
        env.model.replies = [{'action': 'query', 'summary': '读取固定文件及真实失败查询', 'queries': [
            {'kind': 'document', 'source_id': doc.id}, captured_query]}]
        if legacy:
            env.model.replies.append({'action': 'query', 'summary': '旧目录未读契约', 'queries': [{'kind': 'tool', 'source_id': 'api-test-runner'}]})
        env.model.replies.append(lambda c: finish(item(c, 'navigation')))
        handler = PlanningTaskHandler(env.service, env.jobs, worker_id='nav-fixture')
        await handler(delivery(task))
        terminal = await env.jobs.get(project.id, task.id)
        assert terminal.status == 'completed' and terminal.round_count == (3 if legacy else 2)
        plan = await env.service.get(project.id, terminal.plan_id)
        assert plan.prompt_version == (old['plan']['prompt_version'] if legacy else OPERATION_PROMPT_VERSION)
        assert next(e for e in plan.evidence if e.kind == 'case').payload['content'] == content
        assert next(e for e in plan.evidence if e.kind == 'document').payload['content'] == source.read_text(encoding='utf-8').rstrip('\n')
        contract = next(e for e in plan.evidence if e.kind == 'tool')
        assert 'task_id' in contract.payload['input_schema']['properties']['task']['required']
        second = json.loads(env.model.requests[1].messages[0]['content'])
        assert bool([e for e in second['evidence'] if e['kind'] == 'tool']) is (not legacy)
        assert ('contract_loaded' in second['query_results'][1]) is (not legacy)
        assert ('detail_query' in second['tool_catalog']['tools'][0]) is (not legacy)
        count = len(env.model.requests)
        assert await env.tasks.submit(project.id, request) == terminal
        await handler(delivery(task))
        assert len(env.model.requests) == count
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint,status FROM {env.jobs._table} WHERE id=%s', (task.id,))
            persisted = dict(cur.fetchone())
        assert persisted['seed']['plan']['prompt_version'] == plan.prompt_version
        directory = Path(os.environ['QA_TOOL_NAVIGATION_DIR']) / ('legacy' if legacy else 'new')
        directory.mkdir(parents=True, exist_ok=False)
        (directory / source.name).write_bytes(source.read_bytes())
        (directory / 'verification.json').write_text(json.dumps({'actual_PG': persisted,
            'current_MySQL_policy': (await env.service.get_policy(project.id)).model_dump(mode='json'),
            'actual_requests': [r.model_dump(mode='json') for r in env.model.requests],
            'actual_plan': plan.model_dump(mode='json'), 'terminal_no_new_calls': True,
            'registry_recapture_forbidden': True, 'legacy': legacy, 'controlled_model': True,
            'default_autonomous_Agent_verified': False, 'full_P0_P5_complete': False}, ensure_ascii=False, indent=2), encoding='utf-8')
