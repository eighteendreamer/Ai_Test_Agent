from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.knowledge import router as knowledge_router
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService


class LegacyGraphProvider:
    def __init__(self) -> None:
        self.read_queries: list[str] = []
        self.write_queries: list[str] = []

    def execute(self, query: str, _parameters: dict | None = None) -> list[dict]:
        self.read_queries.append(query)
        if "count(n) AS total" in query:
            return [
                {"total": 1, "count_field": "page_count"},
                {"total": 0, "count_field": "element_count"},
                {"total": 0, "count_field": "entity_count"},
                {"total": 0, "count_field": "edge_count"},
            ]
        if "RETURN n.id" in query:
            return [{"id": "legacy-page", "label": "Legacy page"}] if "MATCH (n:Page)" in query else []
        if "RETURN n ORDER BY" in query:
            return [{"n": {"id": "legacy-page", "label": "Legacy page"}}] if (
                "MATCH (n:Page)" in query and "n.project_id IS NULL" in query
            ) else []
        return []

    def execute_write(self, query: str, _parameters: dict | None = None) -> list[dict]:
        self.write_queries.append(query)
        return []


class Projects:
    async def require_active(self, project_id: str):
        return await self.get(project_id)

    async def get(self, project_id: str):
        assert project_id == "project-1"
        return SimpleNamespace(id=project_id, graph_scope_key="legacy-scope", status="active")


def _service(provider: LegacyGraphProvider) -> KnowledgeGraphService:
    service = object.__new__(KnowledgeGraphService)
    service._provider = provider
    service._projects = Projects()
    service._last_failure = None
    service._project_cache = None
    return service


@pytest.mark.asyncio
async def test_graph_get_reads_legacy_nodes_without_writing() -> None:
    provider = LegacyGraphProvider()
    service = _service(provider)
    app = FastAPI()
    app.state.knowledge_graph_service = service
    app.include_router(knowledge_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/knowledge/graph", params={"project_id": "project-1"})

    assert response.status_code == 200
    assert [node["id"] for node in response.json()["nodes"]] == ["legacy-page"]
    assert provider.write_queries == []
    assert any("n.project_id IS NULL" in query for query in provider.read_queries)


@pytest.mark.asyncio
async def test_summary_generation_read_only_and_explicit_delete_covers_legacy_scope() -> None:
    provider = LegacyGraphProvider()
    service = _service(provider)

    summary = await service.get_project_summary("project-1")
    context = await service.get_generation_context("project-1")

    assert summary.page_count == 1
    assert context["project_id"] == "project-1"
    assert provider.write_queries == []
    assert all(
        "project_id IS NULL" in query and "project_scope = $project_scope" in query
        for query in provider.read_queries
    )

    await service.delete_project("project-1")
    assert len(provider.write_queries) == 1
    assert "DETACH DELETE" in provider.write_queries[0]
    assert "n.project_id IS NULL" in provider.write_queries[0]
    assert "n.project_scope = $project_scope" in provider.write_queries[0]
