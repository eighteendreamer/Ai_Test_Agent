from __future__ import annotations

from src.runtime.store import SessionStore
from src.schemas.task_pool import TaskPoolPage
from src.schemas.task_pool import TaskPoolEntriesPage, TaskPoolKind, TaskPoolState
from src.runtime.postgres_task_pool_store import TaskPoolEntriesStore


class TaskPoolService:
    def __init__(self, *, store: SessionStore, entries_store: TaskPoolEntriesStore | None = None) -> None:
        self._store = store
        self._entries = entries_store

    async def list_entries_page(self, *, limit: int = 24, offset: int = 0, kind: TaskPoolKind = "all",
                                state: TaskPoolState = "all", query: str = "", project_id: str | None = None) -> TaskPoolEntriesPage:
        if self._entries is None:
            raise RuntimeError("Task-pool entries store is not configured")
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        entries = await self._entries.list_entries(limit=limit + 1, offset=offset, kind=kind,
                                                   state=state, query=query.strip(), project_id=project_id)
        return TaskPoolEntriesPage(items=entries[:limit], limit=limit, offset=offset, has_more=len(entries) > limit)

    async def list_tasks_page(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> TaskPoolPage:
        normalized_limit = max(1, min(int(limit or 24), 100))
        normalized_offset = max(int(offset or 0), 0)
        sessions = await self._store.list_task_pool_sessions(
            limit=normalized_limit + 1,
            offset=normalized_offset,
        )
        return TaskPoolPage(
            items=sessions[:normalized_limit],
            limit=normalized_limit,
            offset=normalized_offset,
            has_more=len(sessions) > normalized_limit,
        )
