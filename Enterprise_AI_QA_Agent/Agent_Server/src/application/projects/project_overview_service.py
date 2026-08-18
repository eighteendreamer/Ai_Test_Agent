from __future__ import annotations

import asyncio

from src.application.documents.api_doc_store import ApiDocStore
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_store import TestCaseStore
from src.application.test_suites.suite_store import TestSuiteStore
from src.application.test_runs.run_store import TestRunStore
from src.runtime.store import SessionStore
from src.schemas.project import ProjectGraphOverview, ProjectOverview


class ProjectOverviewService:
    def __init__(
        self,
        *,
        project_service: ProjectService,
        api_doc_store: ApiDocStore,
        session_store: SessionStore,
        knowledge_graph_service: KnowledgeGraphService,
        test_case_store: TestCaseStore | None = None,
        test_suite_store: TestSuiteStore | None = None,
        test_run_store: TestRunStore | None = None,
    ) -> None:
        self._projects = project_service
        self._api_docs = api_doc_store
        self._sessions = session_store
        self._knowledge = knowledge_graph_service
        self._test_cases = test_case_store
        self._test_suites = test_suite_store
        self._test_runs = test_run_store

    async def get(self, project_id: str) -> ProjectOverview:
        project = await self._projects.get(project_id)
        api_doc_count, session_count, test_case_count, test_suite_count, test_run_count = await asyncio.gather(
            self._api_docs.count_by_project(project_id),
            self._sessions.count_project_sessions(project_id),
            self._test_cases.count_by_project(project_id) if self._test_cases else _zero(),
            self._test_suites.count_by_project(project_id) if self._test_suites else _zero(),
            self._test_runs.count_by_project(project_id) if self._test_runs else _zero(),
        )
        graph = ProjectGraphOverview(project_scope=project.graph_scope_key)
        if project.graph_scope_key:
            try:
                graph_identity = (
                    project.id
                    if getattr(self._knowledge, "_projects", None) is not None
                    else project.graph_scope_key
                )
                summary = await self._knowledge.get_project_summary(graph_identity)
                values = summary.model_dump() if hasattr(summary, "model_dump") else dict(summary)
                graph = ProjectGraphOverview(
                    project_scope=project.graph_scope_key,
                    page_count=int(values.get("page_count") or 0),
                    element_count=int(values.get("element_count") or 0),
                    entity_count=int(values.get("entity_count") or 0),
                    edge_count=int(values.get("edge_count") or 0),
                )
            except Exception as exc:
                graph = ProjectGraphOverview(
                    available=False,
                    project_scope=project.graph_scope_key,
                    error=str(exc),
                )
        return ProjectOverview(
            project=project,
            api_doc_count=api_doc_count,
            session_count=session_count,
            test_case_count=test_case_count,
            test_suite_count=test_suite_count,
            test_run_count=test_run_count,
            graph=graph,
        )


async def _zero() -> int:
    return 0
