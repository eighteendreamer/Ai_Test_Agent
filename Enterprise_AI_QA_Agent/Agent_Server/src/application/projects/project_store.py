from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.project import ProjectRecord, ProjectStatus


class DuplicateProjectKeyError(ValueError):
    pass


class DuplicateGraphScopeError(ValueError):
    pass


class ProjectStore(Protocol):
    async def initialize(self) -> None: ...
    async def create(self, project: ProjectRecord) -> ProjectRecord: ...
    async def get(self, project_id: str) -> ProjectRecord | None: ...
    async def list(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProjectRecord], bool]: ...
    async def count(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
    ) -> int: ...
    async def counts(self) -> dict[str, int]: ...
    async def update(self, project: ProjectRecord) -> ProjectRecord: ...


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create(self, project: ProjectRecord) -> ProjectRecord:
        async with self._lock:
            self._ensure_unique(project)
            stored = project.model_copy(deep=True)
            self._projects[stored.id] = stored
            return stored.model_copy(deep=True)

    async def get(self, project_id: str) -> ProjectRecord | None:
        item = self._projects.get(project_id)
        return item.model_copy(deep=True) if item else None

    async def list(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProjectRecord], bool]:
        items = self._matching(status, query)
        items.sort(key=lambda item: (item.created_at, item.id))
        selected = items[offset : offset + limit + 1]
        return [item.model_copy(deep=True) for item in selected[:limit]], len(selected) > limit

    async def count(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
    ) -> int:
        return len(self._matching(status, query))

    async def counts(self) -> dict[str, int]:
        values = list(self._projects.values())
        return {
            "all": len(values),
            "active": sum(item.status == "active" for item in values),
            "archived": sum(item.status == "archived" for item in values),
        }

    def _matching(
        self,
        status: ProjectStatus | None,
        query: str | None,
    ) -> list[ProjectRecord]:
        items = list(self._projects.values())
        if status:
            items = [item for item in items if item.status == status]
        if query:
            needle = query.casefold()
            items = [
                item
                for item in items
                if needle in item.project_key.casefold() or needle in item.name.casefold()
            ]
        return items

    async def update(self, project: ProjectRecord) -> ProjectRecord:
        async with self._lock:
            if project.id not in self._projects:
                raise KeyError(f"Project not found: {project.id}")
            self._ensure_unique(project, excluding_id=project.id)
            stored = project.model_copy(deep=True)
            self._projects[stored.id] = stored
            return stored.model_copy(deep=True)

    def _ensure_unique(self, project: ProjectRecord, *, excluding_id: str | None = None) -> None:
        for item in self._projects.values():
            if item.id == excluding_id:
                continue
            if item.project_key == project.project_key:
                raise DuplicateProjectKeyError(f"project_key already exists: {project.project_key}")
            if project.graph_scope_key and item.graph_scope_key == project.graph_scope_key:
                raise DuplicateGraphScopeError(
                    f"graph_scope_key already exists: {project.graph_scope_key}"
                )


class PostgresProjectStore:
    """PostgreSQL project registry; table name is trusted configuration only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _table(self) -> str:
        return self._settings.database.postgres_project_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create(self, project: ProjectRecord) -> ProjectRecord:
        return await asyncio.to_thread(self._create_sync, project)

    async def get(self, project_id: str) -> ProjectRecord | None:
        return await asyncio.to_thread(self._get_sync, project_id)

    async def list(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProjectRecord], bool]:
        return await asyncio.to_thread(self._list_sync, status, query, limit, offset)

    async def count(
        self,
        *,
        status: ProjectStatus | None,
        query: str | None,
    ) -> int:
        return await asyncio.to_thread(self._count_sync, status, query)

    async def counts(self) -> dict[str, int]:
        return await asyncio.to_thread(self._counts_sync)

    async def update(self, project: ProjectRecord) -> ProjectRecord:
        return await asyncio.to_thread(self._update_sync, project)

    def _initialize_sync(self) -> None:
        table = self._table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id UUID PRIMARY KEY,
                        project_key TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        description TEXT,
                        base_url TEXT,
                        graph_scope_key TEXT UNIQUE,
                        status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
                        created_by TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        archived_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_status_updated "
                    f"ON {table} (status, updated_at DESC)"
                )

    def _create_sync(self, project: ProjectRecord) -> ProjectRecord:
        try:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self._table} (
                            id, project_key, name, description, base_url, graph_scope_key,
                            status, created_by, created_at, updated_at, archived_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        self._parameters(project),
                    )
                    row = cur.fetchone()
        except Exception as exc:
            self._translate_integrity_error(exc, project)
            raise
        return self._from_row(row)

    def _get_sync(self, project_id: str) -> ProjectRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} WHERE id = %s", (project_id,))
                row = cur.fetchone()
        return self._from_row(row) if row else None

    def _where(
        self,
        status: ProjectStatus | None,
        query: str | None,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status:
            clauses.append("status = %s")
            parameters.append(status)
        if query:
            clauses.append("(project_key ILIKE %s OR name ILIKE %s)")
            pattern = f"%{query}%"
            parameters.extend([pattern, pattern])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, parameters

    def _count_sync(self, status: ProjectStatus | None, query: str | None) -> int:
        where, parameters = self._where(status, query)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS total FROM {self._table}{where}", tuple(parameters))
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    def _counts_sync(self) -> dict[str, int]:
        counts = {"all": 0, "active": 0, "archived": 0}
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT status, count(*) AS total FROM {self._table} GROUP BY status")
                rows = cur.fetchall() or []
        for row in rows:
            total = int(row.get("total") or 0)
            counts["all"] += total
            if row.get("status") in ("active", "archived"):
                counts[row["status"]] = total
        return counts

    def _list_sync(
        self,
        status: ProjectStatus | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProjectRecord], bool]:
        where, parameters = self._where(status, query)
        parameters.extend([limit + 1, offset])
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._table}{where} "
                    "ORDER BY created_at ASC, id ASC LIMIT %s OFFSET %s",
                    tuple(parameters),
                )
                rows = cur.fetchall() or []
        return [self._from_row(row) for row in rows[:limit]], len(rows) > limit

    def _update_sync(self, project: ProjectRecord) -> ProjectRecord:
        try:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {self._table}
                        SET name = %s, description = %s, base_url = %s,
                            graph_scope_key = %s, status = %s, updated_at = %s,
                            archived_at = %s
                        WHERE id = %s
                        RETURNING *
                        """,
                        (
                            project.name,
                            project.description,
                            project.base_url,
                            project.graph_scope_key,
                            project.status,
                            project.updated_at,
                            project.archived_at,
                            project.id,
                        ),
                    )
                    row = cur.fetchone()
        except Exception as exc:
            self._translate_integrity_error(exc, project)
            raise
        if not row:
            raise KeyError(f"Project not found: {project.id}")
        return self._from_row(row)

    @staticmethod
    def _parameters(project: ProjectRecord) -> tuple[object, ...]:
        return (
            project.id,
            project.project_key,
            project.name,
            project.description,
            project.base_url,
            project.graph_scope_key,
            project.status,
            project.created_by,
            project.created_at,
            project.updated_at,
            project.archived_at,
        )

    @staticmethod
    def _from_row(row) -> ProjectRecord:
        values = dict(row)
        # psycopg returns PostgreSQL UUID columns as uuid.UUID instances while
        # the public schema intentionally exposes stable string identifiers.
        if values.get("id") is not None:
            values["id"] = str(values["id"])
        return ProjectRecord.model_validate(values)

    @staticmethod
    def _translate_integrity_error(exc: Exception, project: ProjectRecord) -> None:
        constraint = str(getattr(getattr(exc, "diag", None), "constraint_name", "") or "")
        message = str(exc).lower()
        if "project_key" in constraint or "project_key" in message:
            raise DuplicateProjectKeyError(
                f"project_key already exists: {project.project_key}"
            ) from exc
        if "graph_scope_key" in constraint or "graph_scope_key" in message:
            raise DuplicateGraphScopeError(
                f"graph_scope_key already exists: {project.graph_scope_key}"
            ) from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
