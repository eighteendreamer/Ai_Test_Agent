"""A real project planning task must remain visible beyond the first session page."""
from __future__ import annotations

import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from test_case_planning_tasks import tasks_environment, active_case, item, finish, delivery
from src.api.routes.task_pool import router
from src.application.task_pool_service import TaskPoolService
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.run_revision_planning import RunRevisionPlanningInput, RunRevisionPlanningOutcome
from src.schemas.project import ProjectCreateRequest
from src.schemas.session import SessionStatus, SessionMode, RuntimeMode
from src.application.documents.parser_config_service import DocumentParserConfigService
from src.application.documents.parsing_service import DocumentParsingService
from src.application.documents.difference_config_service import DocumentDifferenceConfigService
from src.application.documents.difference_service import DocumentDifferenceService
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
from src.runtime.postgres_document_difference_store import PostgresDocumentDifferenceStore
from src.runtime.postgres_document_impact_store import PostgresDocumentImpactStore
from src.runtime.postgres_generation_task_store import PostgresGenerationTaskStore


@asynccontextmanager
async def pool_environment(tmp_path):
    async with tasks_environment(tmp_path) as env:
        fields = ['postgres_session_table', 'postgres_message_table', 'postgres_event_table',
                  'postgres_snapshot_table', 'postgres_approval_table']
        tables = ['up_pool_' + uuid4().hex[:12] for _ in fields]
        for field, table in zip(fields, tables):
            setattr(env.settings.database, field, table)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute('SELECT to_regclass(%s) AS existing', (table,))
                assert cur.fetchone()['existing'] is None
        env.sessions = PostgresSessionStore(env.settings)
        await env.sessions.initialize()
        env.parse_store = PostgresDocumentParseStore(env.settings, lease_seconds=30)
        env.difference_store = PostgresDocumentDifferenceStore(env.settings, lease_seconds=30)
        env.impact_store = PostgresDocumentImpactStore(env.settings, lease_seconds=30)
        env.generation_store = PostgresGenerationTaskStore(env.settings, env.cases._store)
        document_tables = [*env.generation_store.tables, *env.impact_store.tables, *env.difference_store.tables, *env.parse_store.tables]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            for table in document_tables:
                cur.execute('SELECT to_regclass(%s) AS existing', (table,))
                assert cur.fetchone()['existing'] is None
        env.parsing = DocumentParsingService(store=env.parse_store, documents=env.docs, projects=env.projects,
            configs=DocumentParserConfigService(config_store=env.configs, project_service=env.projects),
            objects=env.objects, stream='isolated:pool:parsing')
        env.differences = DocumentDifferenceService(store=env.difference_store, documents=env.docs, projects=env.projects,
            configs=DocumentDifferenceConfigService(config_store=env.configs, project_service=env.projects),
            objects=env.objects, parsing=env.parsing, stream='isolated:pool:differences')
        await env.parsing.initialize()
        await env.differences.initialize()
        await env.impact_store.initialize()
        await env.generation_store.initialize()
        try:
            # Keep this fixture import lazy so the pre-implementation API failure is observable.
            from src.runtime.postgres_task_pool_store import PostgresTaskPoolStore
            env.pool = TaskPoolService(store=env.sessions, entries_store=PostgresTaskPoolStore(env.settings))
            yield env
        finally:
            owned = set()
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for store in (env.impact_store, env.difference_store, env.parse_store):
                    fields = ('checkpoint', 'failure_artifact', 'input_checkpoint') if store is env.impact_store else ('checkpoint', 'failure_artifact')
                    cur.execute(f'SELECT {",".join(fields)} FROM {store._tasks}')
                    for row in cur.fetchall():
                        for field in fields:
                            if row[field]: owned.add(row[field]['storage_uri'])
            for uri in owned:
                await env.objects.delete_object_uri(uri)
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in document_tables:
                    cur.execute(f'DROP TABLE "{table}"')
                for table in reversed(tables):
                    cur.execute(f'DROP TABLE "{table}"')


async def session(env, project, key, status=SessionStatus.completed, mode='code_review', updated_at=None):
    stamp = updated_at or datetime.now(timezone.utc)
    record = SessionRecord(id=key, title=f'代码检查 {key}', status=status,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
        mode_key=mode, project_id=project.id, created_at=stamp, updated_at=stamp,
        selected_agent='review-agent', metadata={'worker_dispatches': [{'task_id': 'child', 'status': 'completed'}],
                                               'private_marker': 'PRIVATE-SESSION-INPUT'})
    # save_session intentionally restamps updates. Use the existing insert contract
    # to seed fixed historical timestamps for deterministic mixed-page assertions.
    with postgres_connect(env.settings) as conn, conn.cursor() as cur:
        assert env.sessions.create_session_in_transaction(cur, record)
    return record


live = pytest.mark.skipif(os.getenv('RUN_LIVE_TASK_POOL') != '1',
    reason='requires isolated real PostgreSQL/MySQL/RustFS')


@pytest.mark.asyncio
@live
async def test_unconfigured_task_pool_projection_reports_unavailable_without_resubmitting(tmp_path):
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='pool-repro', name='规划任务池'))
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='pool-visible', objective='核对项目用例'))
        app = FastAPI()
        app.state.task_pool_service = TaskPoolService(store=None)
        app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            # The pre-implementation reproduction returned 404; preserve that XML separately.
            response = await client.get('/api/v1/task-pool/entries')
            assert response.status_code == 503, response.text
            assert (await env.tasks.get(project.id, task.id)).status == 'queued'
            assert env.model.requests == []


@pytest.mark.asyncio
@live
async def test_task_pool_projects_run_revision_outcome_without_exposing_checkpoint(tmp_path):
    async with pool_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='pool-revision', name='运行修订任务池'))
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(
            creation_key='revision-visible', objective='读取运行修订结果'))
        run_id = str(uuid4())
        revision_input = RunRevisionPlanningInput(run_id=run_id, source_plan_id=str(uuid4()),
            expected_revision=0, reason='执行发现缺少必要前置', required_case_versions={'case': 'v1'},
            read_case_versions={'case': 'v1'}, context={'private_marker': 'PRIVATE-REVISION-CONTEXT'})
        pending = RunRevisionPlanningOutcome(run_id=run_id, status='pending', message='等待候选规划及运行修订确认')
        outcomes = [
            None,
            RunRevisionPlanningOutcome(run_id=run_id, status='applied', revision=1, message='修订已生效'),
            RunRevisionPlanningOutcome(run_id=run_id, status='rejected', message='运行版本已变化'),
        ]
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT seed,checkpoint FROM {env.jobs._table} WHERE id=%s', (task.id,))
            row = cur.fetchone()
            seed, checkpoint = row['seed'], row['checkpoint']

        app = FastAPI(); app.state.task_pool_service = env.pool; app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            response = await client.get('/api/v1/task-pool/entries', params={
                'kind': 'case_planning', 'project_id': project.id})
            assert response.status_code == 200, response.text
            legacy_task = response.json()['items'][0]['task']
            assert legacy_task['id'] == task.id and 'run_revision' not in legacy_task

            seed['run_revision'] = revision_input.model_dump(mode='json')
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'UPDATE {env.jobs._table} SET seed=%s::jsonb WHERE id=%s',
                    (json.dumps(seed, ensure_ascii=False), task.id))
            for outcome in outcomes:
                expected = pending if outcome is None else outcome
                if outcome is None:
                    checkpoint.pop('run_revision_outcome', None)
                else:
                    checkpoint['run_revision_outcome'] = outcome.model_dump(mode='json')
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f'UPDATE {env.jobs._table} SET checkpoint=%s::jsonb WHERE id=%s',
                        (json.dumps(checkpoint, ensure_ascii=False), task.id))
                response = await client.get('/api/v1/task-pool/entries', params={
                    'kind': 'case_planning', 'project_id': project.id})
                assert response.status_code == 200, response.text
                projected = response.json()['items'][0]['task']
                assert projected['run_revision'] == expected.model_dump(mode='json')
                assert 'checkpoint' not in projected and 'seed' not in projected
                assert 'PRIVATE-REVISION-CONTEXT' not in response.text


@pytest.mark.asyncio
@live
async def test_mixed_pool_pages_filters_public_projection_and_legacy_api(tmp_path):
    async with pool_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='pool-one', name='中文规划项目'))
        other = await env.projects.create(ProjectCreateRequest(project_key='pool-other', name='隔离项目'))
        doc_content = '# 需求\n查询订单时保留未登录拒绝。\n'
        source = tmp_path / 'pool.md'; source.write_text(doc_content, encoding='utf-8')
        document = await env.docs.upload_document(filename=source.name, project_id=project.id,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(document.storage_uri))['content'] == source.read_bytes()
        case = await active_case(env, project, 'pool-case', {'objective': '未登录查询订单', 'steps': [{'action': '查询', 'expected': '拒绝'}]})
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='chosen-history',
            objective='核对未登录查询', candidate_case_ids=[case.case.id], runtime_context={'private_marker': 'PRIVATE-PLANNING-INPUT'}))
        foreign = await env.tasks.submit(other.id, CasePlanningTaskRequest(creation_key='foreign', objective='另一个项目'))
        # PostgreSQL is authoritative; its clock need not equal the fixture host clock.
        now = task.updated_at + timedelta(seconds=1)
        for index in range(25):
            await session(env, project, f'review-{index:02}', updated_at=now + timedelta(seconds=index))
        await session(env, other, 'other-review', status=SessionStatus.running, updated_at=now + timedelta(seconds=30))
        await session(env, project, 'ordinary-chat', mode='default')
        app = FastAPI(); app.state.task_pool_service = env.pool; app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            root = '/api/v1/task-pool/entries'
            first = await client.get(root, params={'limit': 24, 'project_id': project.id})
            first.raise_for_status(); page = first.json()
            assert len(page['items']) == 24 and page['has_more']
            second = (await client.get(root, params={'limit': 24, 'offset': 24, 'project_id': project.id})).json()
            combined = page['items'] + second['items']
            ids = [e['session']['id'] if e['kind'] == 'session' else e['task']['id'] for e in combined]
            assert len(ids) == len(set(ids)) == 26 and task.id in ids
            assert foreign.id not in ids and 'ordinary-chat' not in ids
            assert ids[:2] == ['review-24', 'review-23']
            assert not second['has_more']
            for params in [{'kind': 'case_planning'}, {'state': 'active'}, {'query': task.id}, {'query': 'chosen-history'}]:
                result = await client.get(root, params={**params, 'project_id': project.id})
                result.raise_for_status()
                assert [e['task']['id'] for e in result.json()['items']] == [task.id]
            body = json.dumps(combined)
            for private in ['PRIVATE-PLANNING-INPUT', 'PRIVATE-SESSION-INPUT', 'seed', 'checkpoint', 'lease_token', 'response_text']:
                assert private not in body
            entry = combined[-1]
            assert entry['project_name'] == project.name and entry['task'] == task.model_dump(mode='json')
            legacy = await client.get('/api/v1/task-pool', params={'limit': 1})
            legacy.raise_for_status()
            assert legacy.json()['items'][0]['id'] == 'other-review'
            assert legacy.json()['items'][0]['worker_dispatches'][0]['task_id'] == 'child'
            assert 'kind' not in legacy.json()['items'][0]
            for params in [{'kind': 'invalid'}, {'state': 'invalid'}, {'project_id': 'bad'}, {'offset': -1}, {'query': 'x' * 501}]:
                assert (await client.get(root, params=params)).status_code == 422
            assert (await client.get(root, params={'query': "%' OR 1=1 --"})).json()['items'] == []
            env.model.replies = [lambda context: finish(item(context, 'pool-case'))]
            await PlanningTaskHandler(env.service, env.jobs, worker_id='pool-test')(delivery(task))
            completed = (await client.get(root, params={'kind': 'case_planning', 'state': 'completed', 'project_id': project.id})).json()['items']
            assert completed[0]['task']['status'] == 'completed' and completed[0]['task']['plan_id']
            assert completed[0]['task']['round_count'] == 1
            assert (await env.service.get(project.id, completed[0]['task']['plan_id'])).status == 'planned'
            env.model.replies = [RuntimeError('controlled model failure')]
            await PlanningTaskHandler(env.service, env.jobs, worker_id='pool-test')(delivery(foreign))
            failed = (await client.get(root, params={'kind': 'case_planning', 'state': 'failed'})).json()['items']
            assert len(failed) == 1 and failed[0]['task']['id'] == foreign.id
            assert failed[0]['task']['message']
            await env.projects.archive(project.id)
            assert (await client.get(root, params={'project_id': project.id, 'kind': 'case_planning'})).json()['items'][0]['task']['id'] == task.id
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'UPDATE {env.settings.database.postgres_session_table} SET updated_at=%s WHERE project_id=%s', (task.updated_at, project.id))
                cur.execute(f'UPDATE {env.settings.database.postgres_case_planning_task_table} SET updated_at=%s WHERE project_id=%s', (task.updated_at, project.id))
            tied = (await client.get(root, params={'project_id': project.id, 'limit': 100})).json()['items']
            identities = [(e['kind'], e['session']['id'] if e['kind'] == 'session' else e['task']['id']) for e in tied]
            assert identities == sorted(identities)
