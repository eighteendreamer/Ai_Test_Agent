"""Public impact catalog over actual document inputs and durable checkpoints."""
from __future__ import annotations

import json
import os
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.task_pool import router
from src.application.documents.impact_config_service import DocumentImpactConfigService
from src.application.documents.impact_sources import DocumentImpactSources
from src.application.documents.impact_task_service import DocumentImpactTaskService
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.document_impact import DocumentImpactTaskRequest
from src.schemas.project import ProjectCreateRequest
from test_document_difference_tasks import inputs
from test_document_impact_sources import draft, source
from test_task_pool_entries import pool_environment, session

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_LIVE_TASK_POOL') != '1',
    reason='isolated actual MySQL/PostgreSQL/RustFS and real document subprocesses')]


async def test_impact_pool_mixed_pages_filters_private_checkpoint_and_rebound_title(tmp_path):
    async with pool_environment(tmp_path) as env:
        project, doc, _, views, comparison_request = await inputs(env, env.parsing, env.parse_store, tmp_path, 'docx')
        comparison = await env.differences.submit(project.id, doc.id, comparison_request)
        compared = await env.differences.execute(await env.difference_store.claim(project.id, comparison.id, worker_id='pool-input'))
        assert compared.status == 'completed'
        await draft(env, project, 'retention', [source(doc, views[0])])
        configs = DocumentImpactConfigService(config_store=env.configs, project_service=env.projects)
        await configs.initialize()
        sources = DocumentImpactSources(documents=env.docs, parsing=env.parsing, differences=env.differences,
            cases=env.cases, configs=configs)
        tasks = DocumentImpactTaskService(store=env.impact_store, sources=sources, documents=env.docs,
            projects=env.projects, objects=env.objects, stream='isolated:pool:impact')
        # Submit the source-capture stage directly: this test exercises the catalog,
        # not model freezing/dispatch (covered by the separate production API tests).
        async def submit(key):
            request = DocumentImpactTaskRequest(creation_key=key, difference_snapshot_id=compared.snapshot_id)
            seed = await sources.capture(project.id, doc.id, compared.snapshot_id, request)
            return await env.impact_store.submit(seed, creation_key=key,
                request_hash=env.impact_store.request_hash(request), stream='isolated:pool:impact')
        queued = await submit('impact-queued-history')
        running = await submit('impact-input-history')
        running = await tasks.prepare_input(await env.impact_store.claim(project.id, running.id, worker_id='pool-impact'))
        assert running.phase == 'input_saved' and running.input_checkpoint
        failed = await submit('impact-failed-history')
        failed = await env.impact_store.finish(await env.impact_store.claim(project.id, failed.id, worker_id='pool-failed'),
            error_code='controlled_failure', message='保留影响失败历史')
        other = await env.projects.create(ProjectCreateRequest(project_key='other-impact-pool', name='隔离项目'))
        for index in range(25):
            await session(env, project, f'impact-review-{index:02}', updated_at=failed.updated_at + timedelta(seconds=index + 1))
        app = FastAPI(); app.state.task_pool_service = env.pool; app.include_router(router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            async def page(**params):
                response = await client.get('/api/v1/task-pool/entries', params=params)
                assert response.status_code == 200, response.text
                return response.json()
            first = await page(project_id=project.id, limit=24)
            second = await page(project_id=project.id, limit=24, offset=24)
            combined = first['items'] + second['items']
            assert first['has_more'] and not second['has_more'] and len(combined) == 29
            impacts = [item for item in combined if item['kind'] == 'document_impact']
            assert {item['task']['id'] for item in impacts} == {queued.id, running.id, failed.id}
            for entry in impacts:
                task = {value.id: value for value in (queued, running, failed)}[entry['task']['id']]
                assert entry['task'] == task.model_dump(mode='json', exclude={'input_checkpoint', 'failure_artifact'})
                assert entry['project_name'] == project.name and entry['document_title'] == doc.title
            assert {entry['task']['id'] for entry in (await page(project_id=project.id, kind='document_impact', state='active'))['items']} == {queued.id, running.id}
            assert [entry['task']['id'] for entry in (await page(kind='document_impact', state='failed'))['items']] == [failed.id]
            assert not (await page(kind='document_impact', state='completed'))['items']
            for query in (running.id, 'impact-input-history', doc.title, compared.snapshot_id):
                assert running.id in [entry['task']['id'] for entry in (await page(kind='document_impact', query=query))['items']]
            assert not (await page(project_id=other.id, kind='document_impact'))['items']
            assert not (await page(query="%' OR 1=1 --"))['items']
            public = json.dumps(impacts)
            for private in ('seed', 'input_checkpoint', 'failure_artifact', 'checkpoint', 'lease_token', 'request_hash', 'worker_id', 'config', 'original_asset'):
                assert f'"{private}"' not in public
            assert running.input_checkpoint.storage_uri not in public
            await env.docs.update_document(doc.id, project_id=other.id, title='重新绑定项目私有标题')
            assert {entry['document_title'] for entry in (await page(project_id=project.id, kind='document_impact'))['items']} == {'文档影响分析'}
            assert not (await page(project_id=project.id, query='重新绑定项目私有标题'))['items']
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                for table in (env.settings.database.postgres_session_table, env.difference_store._tasks, env.impact_store._tasks):
                    cur.execute(f'UPDATE {table} SET updated_at=%s WHERE project_id=%s', (failed.updated_at, project.id))
            tied = (await page(project_id=project.id, limit=100))['items']
            identities = [(entry['kind'], entry['session']['id'] if entry['kind'] == 'session' else entry['task']['id']) for entry in tied]
            assert identities == sorted(identities)
            assert (await env.impact_store.get(project.id, running.id)).attempt_no == 1
            assert env.model.requests == []
