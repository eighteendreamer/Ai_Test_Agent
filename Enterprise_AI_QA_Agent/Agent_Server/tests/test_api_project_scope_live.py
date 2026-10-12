"""Actual uploaded documents, including restored and re-bound catalogs."""
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_api_document_dependency_http import _MemoryObjects
from test_execution_worker_cli import isolated_runtime
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.documents.api_doc_store import InMemoryApiDocStore, PostgresApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore, PostgresProjectStore
from src.modes.api_testing_mode.campaign_state import ApiTestingState, DocumentCandidate, ProjectCandidate
from src.modes.api_testing_mode.contracts import PHASE_DOCUMENT_SELECTED, STATE_METADATA_KEY
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.schemas.project import ProjectCreateRequest


@asynccontextmanager
async def stores(tmp_path, live):
    if live:
        async with isolated_runtime(tmp_path) as (settings, *_):
            objects = ArtifactStorageService(settings)
            try:
                yield settings, PostgresProjectStore(settings), PostgresApiDocStore(settings), objects
            finally:
                await objects.close()
    else:
        yield SimpleNamespace(data_dir=str(tmp_path)), InMemoryProjectStore(), InMemoryApiDocStore(), _MemoryObjects()


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, True])
async def test_uploaded_same_name_projects_and_restored_scope(tmp_path, live):
    if live and os.getenv('RUN_LIVE_API_PROJECT_SCOPE') != '1':
        pytest.skip('owned isolated real PG/RustFS project catalogs')
    output = Path(os.environ['QA_API_PROJECT_SCOPE_DIR']) / ('postgres' if live else 'memory')
    output.mkdir(parents=True, exist_ok=False)
    async with stores(tmp_path, live) as (settings, project_store, catalog, objects):
        projects = ProjectService(store=project_store)
        await projects.initialize()
        docs = ApiDocsService(settings=settings, artifact_storage_service=objects,
            catalog_store=catalog, project_service=projects)
        await docs.initialize()
        first = await projects.create(ProjectCreateRequest(project_key='one', name='同名订单系统'))
        second = await projects.create(ProjectCreateRequest(project_key='two', name='同名订单系统'))
        source = '# Own API\n\n## GET /own/health\n\nReturns health for the first project.\n'
        foreign = '# Foreign API\n\n## GET /foreign/internal\n\nFOREIGN-BODY-MUST-NOT-BE-READ\n'
        records = []
        for project, content in ((first, source), (second, foreign)):
            path = tmp_path / (project.project_key + '.md')
            path.write_text(content, encoding='utf8')
            records.append(await docs.upload_document(filename=path.name, title=project.project_key + ' original',
                project_id=project.id, project_name=project.name, project_url='https://api.example.test',
                content_base64=base64.b64encode(path.read_bytes()).decode()))
        own_doc, foreign_doc = records
        try:
            if live:
                docs = ApiDocsService(settings=settings, artifact_storage_service=objects,
                    catalog_store=PostgresApiDocStore(settings), project_service=projects)
            body_reads = []
            original_read = docs.read_document_content
            async def observe(identity, **kwargs):
                body_reads.append(identity)
                return await original_read(identity, **kwargs)
            docs.read_document_content = observe
            runtime = ApiTestingModeRuntime(api_docs_service=docs)
            context = SimpleNamespace(session_id='bound-session', trace_id='bound-trace',
                selected_agent_key='api-testing-agent', user_message='Choose APIs manually',
                context_bundle={'project_id': first.id})
            new = await runtime.handle({'scope_preference': 'manual_pick', 'project_hint': second.name}, context)
            state = ApiTestingState.model_validate(new[STATE_METADATA_KEY])
            assert state.selected_project.project_id == first.id
            assert state.selected_project.doc_ids == [own_doc.id]
            assert state.selected_documents[0].title == own_doc.title
            assert [doc.doc_id for doc in state.document_candidates] == [own_doc.id]
            assert body_reads == [own_doc.id]
            assert all(endpoint.doc_id == own_doc.id and endpoint.path == '/own/health'
                for endpoint in state.endpoint_candidates)
            state.selected_endpoints = state.endpoint_candidates
            campaign_state = runtime._build_campaign(state)
            assert campaign_state.campaign.project_id == first.id
            before = deepcopy(campaign_state.model_dump(mode='json'))
            # Use a new runtime and server-bound state. Foreign declared project
            # IDs and foreign document IDs must fail before any content lookup.
            failures = []
            for defect in ('foreign_project', 'foreign_doc_with_own_label', 'legacy_foreign_doc', 'foreign_campaign'):
                restored = deepcopy(before)
                restored['phase'] = PHASE_DOCUMENT_SELECTED
                if defect == 'foreign_project':
                    restored['selected_project']['project_id'] = second.id
                elif defect in {'foreign_doc_with_own_label', 'legacy_foreign_doc'}:
                    restored['selected_project']['doc_ids'] = [foreign_doc.id]
                    restored['selected_documents'][0]['doc_id'] = foreign_doc.id
                    if defect == 'legacy_foreign_doc':
                        restored['selected_project'].pop('project_id')
                        restored['selected_documents'][0].pop('project_id')
                else:
                    restored['campaign']['project_id'] = second.id
                checked_context = SimpleNamespace(**{**vars(context), 'user_message': '',
                    'context_bundle': {'project_id': first.id, STATE_METADATA_KEY: restored}})
                body_reads.clear()
                with pytest.raises(ValueError, match='project scope'):
                    await ApiTestingModeRuntime(api_docs_service=docs).handle({}, checked_context)
                assert body_reads == []
                assert checked_context.context_bundle[STATE_METADATA_KEY] == restored
                failures.append(defect)
            # Legacy missing IDs remain readable when the original catalog
            # really belongs to the current project. No original rewrite.
            legacy = deepcopy(before)
            legacy['phase'] = PHASE_DOCUMENT_SELECTED
            legacy['selected_project'].pop('project_id')
            legacy['campaign'].pop('project_id')
            for row in legacy['selected_documents']:
                row.pop('project_id')
            legacy_context = SimpleNamespace(**{**vars(context), 'user_message': '',
                'context_bundle': {'project_id': first.id, STATE_METADATA_KEY: legacy}})
            body_reads.clear()
            legacy_output = await ApiTestingModeRuntime(api_docs_service=docs).handle(
                {'scope_preference': 'manual_pick'}, legacy_context)
            assert body_reads == [own_doc.id]
            legacy_state = ApiTestingState.model_validate(legacy_output[STATE_METADATA_KEY])
            legacy_state.selected_endpoints = legacy_state.endpoint_candidates
            rebuilt = runtime._build_campaign(legacy_state)
            assert rebuilt.campaign.project_id == first.id
            (output / 'facts.json').write_text(json.dumps({'external_ai_calls': 0,
                'real_postgres_rustfs': live, 'full_goal_complete': False,
                'project_ids': [first.id, second.id], 'same_name': first.name == second.name,
                'document_ids': [doc.id for doc in records], 'restored_scope_rejections': failures,
                'foreign_content_reads': 0, 'legacy_own_content_read': True,
                'campaign_project_id': rebuilt.campaign.project_id,
                'business_http_sent': 0}, ensure_ascii=False, indent=2), encoding='utf8')
            (output / 'discovered-state.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf8')
        finally:
            for record in records:
                await docs.delete_document(record.id)


def test_formal_id_cannot_be_donated_by_nested_model_request():
    runtime = ApiTestingModeRuntime(api_docs_service=object())
    context = SimpleNamespace(user_message='Find APIs', context_bundle={
        'api_testing_request': {'project_id': 'model-supplied-project'}})
    assert runtime._build_request({'project_id': 'argument-project'}, context).project_id == ''


def test_project_document_selection_uses_exact_document_membership():
    runtime = ApiTestingModeRuntime(api_docs_service=object())
    candidate = ProjectCandidate(project_id='own', project_name='Same', doc_ids=['selected'])
    state = ApiTestingState(document_candidates=[
        DocumentCandidate(doc_id='selected', project_id='own', title='Selected', project_name='Same'),
        DocumentCandidate(doc_id='not-selected', project_id='own', title='Other', project_name='Same')])
    selected = runtime._select_project_documents(state, candidate)
    assert [doc.doc_id for doc in selected.selected_documents] == ['selected']
