"""Public comparison projection over real document inputs and PostgreSQL tasks."""
from __future__ import annotations

import json
import os
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.task_pool import router
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.project import ProjectCreateRequest
from test_document_difference_tasks import inputs
from test_task_pool_entries import pool_environment, session

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_LIVE_TASK_POOL') != '1',
    reason='isolated real MySQL/PostgreSQL/RustFS and actual document subprocesses')]


async def test_comparison_pool_paging_filter_status_public_fields_and_rebound_title(tmp_path):
    async with pool_environment(tmp_path) as env:
        project, doc, _, _, request = await inputs(env, env.parsing, env.parse_store, tmp_path, 'docx')
        queued = await env.differences.submit(project.id, doc.id, request)
        completed_request = request.model_copy(update={'creation_key': 'completed-history'})
        completed = await env.differences.submit(project.id, doc.id, completed_request)
        completed = await env.differences.execute(await env.difference_store.claim(project.id, completed.id, worker_id='pool-comparison'))
        assert completed.status == 'completed'
        failed = await env.differences.submit(project.id, doc.id, request.model_copy(update={'creation_key': 'failed-history'}))
        claim = await env.difference_store.claim(project.id, failed.id, worker_id='pool-failure')
        failed = await env.difference_store.finish(claim, error_code='controlled_failure', message='保留失败历史')
        other = await env.projects.create(ProjectCreateRequest(project_key='other-pool', name='其他项目'))
        for index in range(25):
            await session(env, project, f'diff-review-{index:02}', updated_at=failed.updated_at + timedelta(seconds=index + 1))
        await session(env, other, 'foreign-pool-review')
        app = FastAPI(); app.state.task_pool_service = env.pool; app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            async def page(**params):
                response = await client.get('/api/v1/task-pool/entries', params=params)
                assert response.status_code == 200, response.text
                return response.json()
            first = await page(project_id=project.id, limit=24)
            second = await page(project_id=project.id, limit=24, offset=24)
            assert first['has_more'] and not second['has_more']
            combined = first['items'] + second['items']
            assert len(combined) == 28
            comparisons = [item for item in combined if item['kind'] == 'document_difference']
            assert {item['task']['id'] for item in comparisons} == {queued.id, completed.id, failed.id}
            for item in comparisons:
                original = {task.id: task for task in (queued, completed, failed)}[item['task']['id']]
                assert item['task'] == original.model_dump(mode='json')
                assert item['document_title'] == doc.title and item['project_name'] == project.name
            for state, task in [('active', queued), ('completed', completed), ('failed', failed)]:
                filtered = await page(project_id=project.id, kind='document_difference', state=state)
                assert [item['task']['id'] for item in filtered['items']] == [task.id]
            for query in (completed.id, 'completed-history', doc.title):
                filtered = await page(project_id=project.id, kind='document_difference', query=query)
                assert completed.id in [item['task']['id'] for item in filtered['items']]
            assert (await page(project_id=other.id, kind='document_difference'))['items'] == []
            assert (await page(query="%' OR 1=1 --"))['items'] == []
            public = json.dumps(comparisons)
            for private in ('seed', 'checkpoint', 'lease_token', 'request_hash', 'worker_id', 'config', 'original_asset'):
                assert f'"{private}"' not in public
            # A document rebound to another project must not leak its new title into old tasks/search.
            await env.docs.update_document(doc.id, project_id=other.id, title='新项目私有标题')
            rebound = await page(project_id=project.id, kind='document_difference')
            assert len(rebound['items']) == 3
            assert {item['document_title'] for item in rebound['items']} == {'文档比较'}
            assert (await page(project_id=project.id, query='新项目私有标题'))['items'] == []
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in (env.settings.database.postgres_session_table, env.difference_store._tasks):
                    cur.execute(f'UPDATE {table} SET updated_at=%s WHERE project_id=%s', (completed.updated_at, project.id))
            tied = (await page(project_id=project.id, limit=100))['items']
            keys = [(item['kind'], item['session']['id'] if item['kind'] == 'session' else item['task']['id']) for item in tied]
            assert keys == sorted(keys)
            # Projection is read-only: no new tasks/outbox or attempt changes.
            assert (await env.difference_store.get(project.id, completed.id)).attempt_no == 1
