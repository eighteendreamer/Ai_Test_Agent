"""Project discovery must keep formal project identity through API selection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestingRequestState
from src.modes.api_testing_mode.project_locator import ApiProjectLocator
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.campaign_state import ApiTestingState, ProjectCandidate
from src.modes.api_testing_mode.contracts import PHASE_AWAITING_PROJECT_SELECTION


def document(doc_id, project_id, project_name):
    return SimpleNamespace(id=doc_id, project_id=project_id, project_name=project_name,
        title=doc_id, filename=doc_id + '.json', project_url='https://api.example.test',
        endpoint_count=1, updated_at=None)


@pytest.mark.asyncio
async def test_same_project_name_different_ids_are_not_merged():
    service = SimpleNamespace(list_documents=AsyncMock(return_value=[
        document('a', 'project-a', '同名服务'), document('b', 'project-b', '同名服务')]))
    result = await ApiProjectLocator(api_docs_service=service).locate(
        request=ApiTestingRequestState(objective='查询订单'))
    assert [item.project_id for item in result.candidates] == ['project-a', 'project-b']
    assert all(len(item.doc_ids) == 1 for item in result.candidates)
    assert result.needs_clarification
    assert service.list_documents.await_args.kwargs == {'project_id': None}


@pytest.mark.asyncio
async def test_bound_project_id_filters_catalog_before_scoring():
    service = SimpleNamespace(list_documents=AsyncMock(return_value=[document('a', 'project-a', '同名服务')]))
    result = await ApiProjectLocator(api_docs_service=service).locate(
        request=ApiTestingRequestState(project_id='project-a', objective='查询订单'))
    assert len(result.candidates) == 1 and result.candidates[0].project_id == 'project-a'
    assert service.list_documents.await_args.kwargs == {'project_id': 'project-a'}


def test_runtime_does_not_auto_select_the_only_unnamed_project():
    runtime = ApiTestingModeRuntime(api_docs_service=object())
    state = ApiTestingState(project_candidates=[ProjectCandidate(project_name='未命名项目', doc_ids=['doc'])])
    state = runtime._resolve_project(state)
    assert state.phase == PHASE_AWAITING_PROJECT_SELECTION
    assert state.pending_selection is not None
