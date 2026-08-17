from __future__ import annotations

import asyncio

from src.application.documents.api_doc_store import InMemoryApiDocStore
from src.application.projects.project_overview_service import ProjectOverviewService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.domain.models import SessionRecord
from src.runtime.store import InMemorySessionStore
from src.schemas.project import ProjectCreateRequest
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus


class _Graph:
    def __init__(self):
        self.requested_scope = None

    async def get_project_summary(self, scope):
        self.requested_scope = scope
        return {
            "page_count": 2,
            "element_count": 3,
            "entity_count": 4,
            "edge_count": 5,
        }


class _CountStore:
    def __init__(self, count):
        self.count = count

    async def count_by_project(self, project_id):
        return self.count


def test_overview_uses_aggregate_sources_and_graph_scope_mapping():
    async def scenario():
        project_service = ProjectService(store=InMemoryProjectStore())
        await project_service.initialize()
        project = await project_service.create(
            ProjectCreateRequest(
                project_key="orders",
                name="Orders",
                graph_scope_key="legacy-orders-scope",
            )
        )
        sessions = InMemorySessionStore()
        now = project.created_at
        await sessions.save_session(
            SessionRecord(
                id="session-1",
                title="test",
                status=SessionStatus.completed,
                session_mode=SessionMode.normal,
                runtime_mode=RuntimeMode.interactive,
                mode_key="api_testing",
                project_id=project.id,
                created_at=now,
                updated_at=now,
            )
        )
        graph = _Graph()
        overview = ProjectOverviewService(
            project_service=project_service,
            api_doc_store=InMemoryApiDocStore(),
            session_store=sessions,
            knowledge_graph_service=graph,
            test_case_store=_CountStore(7),
            test_suite_store=_CountStore(3),
            test_run_store=_CountStore(11),
        )

        result = await overview.get(project.id)

        assert result.session_count == 1
        assert result.api_doc_count == 0
        assert result.test_case_count == 7
        assert result.test_suite_count == 3
        assert result.test_run_count == 11
        assert result.graph.page_count == 2
        assert result.graph.edge_count == 5
        assert graph.requested_scope == "legacy-orders-scope"

    asyncio.run(scenario())
