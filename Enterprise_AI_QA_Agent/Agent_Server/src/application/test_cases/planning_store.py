from __future__ import annotations

import asyncio
import re
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_planning import CasePlanRecord


class CasePlanStore(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, record: CasePlanRecord) -> CasePlanRecord: ...
    async def get(self, project_id: str, plan_id: str) -> CasePlanRecord | None: ...


class InMemoryCasePlanStore:
    def __init__(self) -> None:
        self._records: dict[str, CasePlanRecord] = {}

    async def initialize(self) -> None:
        pass

    async def save(self, record: CasePlanRecord) -> CasePlanRecord:
        existing = self._records.get(record.id)
        if existing and existing.project_id != record.project_id:
            raise RuntimeError("无法保存项目用例计划")
        self._records.setdefault(record.id, record.model_copy(deep=True))
        return self._records[record.id].model_copy(deep=True)

    async def get(self, project_id: str, plan_id: str) -> CasePlanRecord | None:
        record = self._records.get(plan_id)
        return record.model_copy(deep=True) if record and record.project_id == project_id else None


class PostgresCasePlanStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = settings.database.postgres_case_plan_table
        for identifier in (self._table, settings.database.postgres_project_table):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", identifier):
                raise ValueError("Invalid case plan table identifier")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"""CREATE TABLE IF NOT EXISTS {self._table} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                    created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL
                )""")
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_project ON {self._table}(project_id,created_at DESC)")

    async def save(self, record: CasePlanRecord) -> CasePlanRecord:
        return await asyncio.to_thread(self._save, record)

    def _save(self, record: CasePlanRecord) -> CasePlanRecord:
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO {self._table}(id,project_id,created_at,record) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING",
                    (record.id, record.project_id, record.created_at, record.model_dump_json()))
                cursor.execute(f"SELECT record FROM {self._table} WHERE id=%s AND project_id=%s", (record.id, record.project_id))
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("无法保存项目用例计划")
        return CasePlanRecord.model_validate(row["record"])

    async def get(self, project_id: str, plan_id: str) -> CasePlanRecord | None:
        def read() -> CasePlanRecord | None:
            with postgres_connect(self._settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT record FROM {self._table} WHERE id=%s AND project_id=%s", (plan_id, project_id))
                    row = cursor.fetchone()
            return CasePlanRecord.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)
