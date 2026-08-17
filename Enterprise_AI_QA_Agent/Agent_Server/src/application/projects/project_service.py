from __future__ import annotations

import logging
from uuid import uuid4

from src.application.projects.project_store import ProjectStore, utc_now
from src.schemas.project import (
    ProjectCreateRequest,
    ProjectPage,
    ProjectRecord,
    ProjectStatus,
    ProjectUpdateRequest,
)


logger = logging.getLogger(__name__)


class ProjectService:
    def __init__(self, *, store: ProjectStore) -> None:
        self._store = store

    async def initialize(self) -> None:
        await self._store.initialize()

    async def create(self, payload: ProjectCreateRequest, *, created_by: str | None = None) -> ProjectRecord:
        now = utc_now()
        project = ProjectRecord(
            id=str(uuid4()),
            project_key=payload.project_key,
            name=payload.name,
            description=payload.description,
            base_url=payload.base_url,
            graph_scope_key=payload.graph_scope_key,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        created = await self._store.create(project)
        logger.info(
            "project_created",
            extra={"project_id": created.id, "project_key": created.project_key},
        )
        return created

    async def get(self, project_id: str) -> ProjectRecord:
        project = await self._store.get(project_id)
        if not project:
            raise KeyError(f"Project not found: {project_id}")
        return project

    async def require_active(self, project_id: str) -> ProjectRecord:
        project = await self.get(project_id)
        if project.status != "active":
            raise ValueError(f"Project is archived: {project_id}")
        return project

    async def list(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> ProjectPage:
        items, has_more = await self._store.list(
            status=status,
            query=(query or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        return ProjectPage(items=items, limit=limit, offset=offset, has_more=has_more)

    async def update(self, project_id: str, payload: ProjectUpdateRequest) -> ProjectRecord:
        project = await self.get(project_id)
        if project.status != "active":
            raise ValueError(f"Project is archived: {project_id}")
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes and not changes["name"]:
            raise ValueError("Project name cannot be empty")
        updated = project.model_copy(update={**changes, "updated_at": utc_now()})
        saved = await self._store.update(updated)
        logger.info("project_updated", extra={"project_id": saved.id})
        return saved

    async def archive(self, project_id: str) -> ProjectRecord:
        project = await self.get(project_id)
        if project.status == "archived":
            return project
        now = utc_now()
        archived = project.model_copy(
            update={"status": "archived", "archived_at": now, "updated_at": now}
        )
        saved = await self._store.update(archived)
        logger.info("project_archived", extra={"project_id": saved.id})
        return saved
