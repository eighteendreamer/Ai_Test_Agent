from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.api_docs import ApiDocRecord


class ApiDocStore(Protocol):
    async def initialize(self) -> None: ...
    async def list(
        self,
        *,
        project_id: str | None = None,
        unbound: bool = False,
    ) -> list[ApiDocRecord]: ...
    async def get(self, doc_id: str) -> ApiDocRecord | None: ...
    async def count_by_project(self, project_id: str) -> int: ...
    async def create(self, record: ApiDocRecord) -> ApiDocRecord: ...
    async def update(self, record: ApiDocRecord) -> ApiDocRecord: ...
    async def delete(self, doc_id: str) -> ApiDocRecord | None: ...


class InMemoryApiDocStore:
    def __init__(self) -> None:
        self._records: dict[str, ApiDocRecord] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def list(
        self,
        *,
        project_id: str | None = None,
        unbound: bool = False,
    ) -> list[ApiDocRecord]:
        records = list(self._records.values())
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        elif unbound:
            records = [record for record in records if record.project_id is None]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return [record.model_copy(deep=True) for record in records]

    async def get(self, doc_id: str) -> ApiDocRecord | None:
        record = self._records.get(doc_id)
        return record.model_copy(deep=True) if record else None

    async def count_by_project(self, project_id: str) -> int:
        return sum(1 for record in self._records.values() if record.project_id == project_id)

    async def create(self, record: ApiDocRecord) -> ApiDocRecord:
        async with self._lock:
            if record.id in self._records:
                raise ValueError(f"API document already exists: {record.id}")
            self._records[record.id] = record.model_copy(deep=True)
        return record.model_copy(deep=True)

    async def update(self, record: ApiDocRecord) -> ApiDocRecord:
        async with self._lock:
            if record.id not in self._records:
                raise KeyError(f"API document not found: {record.id}")
            self._records[record.id] = record.model_copy(deep=True)
        return record.model_copy(deep=True)

    async def delete(self, doc_id: str) -> ApiDocRecord | None:
        async with self._lock:
            record = self._records.pop(doc_id, None)
        return record.model_copy(deep=True) if record else None


class JsonApiDocStore(InMemoryApiDocStore):
    """Compatibility store for tests and local legacy operation only."""

    def __init__(self, catalog_path: Path) -> None:
        super().__init__()
        self._catalog_path = catalog_path

    async def initialize(self) -> None:
        if not self._catalog_path.exists():
            return
        try:
            raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_legacy_record(item, force_unbound=False)
            try:
                record = ApiDocRecord.model_validate(normalized)
            except Exception:
                continue
            self._records[record.id] = record

    async def create(self, record: ApiDocRecord) -> ApiDocRecord:
        result = await super().create(record)
        await self._flush()
        return result

    async def update(self, record: ApiDocRecord) -> ApiDocRecord:
        result = await super().update(record)
        await self._flush()
        return result

    async def delete(self, doc_id: str) -> ApiDocRecord | None:
        result = await super().delete(doc_id)
        await self._flush()
        return result

    async def _flush(self) -> None:
        self._catalog_path.parent.mkdir(parents=True, exist_ok=True)
        records = await super().list()
        self._catalog_path.write_text(
            json.dumps(
                [record.model_dump(mode="json") for record in records],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class PostgresApiDocStore:
    """PostgreSQL metadata catalog; document bodies remain in object storage."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _table(self) -> str:
        return self._settings.database.postgres_api_doc_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def list(
        self,
        *,
        project_id: str | None = None,
        unbound: bool = False,
    ) -> list[ApiDocRecord]:
        return await asyncio.to_thread(self._list_sync, project_id, unbound)

    async def get(self, doc_id: str) -> ApiDocRecord | None:
        return await asyncio.to_thread(self._get_sync, doc_id)

    async def count_by_project(self, project_id: str) -> int:
        return await asyncio.to_thread(self._count_by_project_sync, project_id)

    async def create(self, record: ApiDocRecord) -> ApiDocRecord:
        return await asyncio.to_thread(self._create_sync, record)

    async def update(self, record: ApiDocRecord) -> ApiDocRecord:
        return await asyncio.to_thread(self._update_sync, record)

    async def delete(self, doc_id: str) -> ApiDocRecord | None:
        return await asyncio.to_thread(self._delete_sync, doc_id)

    async def migrate_legacy_catalog(self, catalog_path: Path) -> dict[str, int]:
        """Explicit, idempotent migration. It never infers or creates projects."""
        return await asyncio.to_thread(self._migrate_legacy_catalog_sync, catalog_path)

    def _initialize_sync(self) -> None:
        table = self._table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id UUID PRIMARY KEY,
                        project_id UUID REFERENCES {self._settings.database.postgres_project_table}(id),
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_project_updated "
                    f"ON {table} (project_id, updated_at DESC)"
                )

    def _list_sync(self, project_id: str | None, unbound: bool) -> list[ApiDocRecord]:
        where = ""
        parameters: tuple[object, ...] = ()
        if project_id is not None:
            where = " WHERE project_id = %s"
            parameters = (project_id,)
        elif unbound:
            where = " WHERE project_id IS NULL"
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._table}{where} ORDER BY updated_at DESC, id ASC",
                    parameters,
                )
                rows = cur.fetchall() or []
        return [self._from_value(row["record"]) for row in rows]

    def _get_sync(self, doc_id: str) -> ApiDocRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._table} WHERE id = %s", (doc_id,))
                row = cur.fetchone()
        return self._from_value(row["record"]) if row else None

    def _count_by_project_sync(self, project_id: str) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM {self._table} WHERE project_id = %s",
                    (project_id,),
                )
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    def _create_sync(self, record: ApiDocRecord) -> ApiDocRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._table} (id, project_id, updated_at, record) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (record.id, record.project_id, record.updated_at, self._json(record)),
                )
        return record

    def _update_sync(self, record: ApiDocRecord) -> ApiDocRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._table} SET project_id = %s, updated_at = %s, record = %s::jsonb "
                    "WHERE id = %s",
                    (record.project_id, record.updated_at, self._json(record), record.id),
                )
                if cur.rowcount != 1:
                    raise KeyError(f"API document not found: {record.id}")
        return record

    def _delete_sync(self, doc_id: str) -> ApiDocRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._table} WHERE id = %s RETURNING record",
                    (doc_id,),
                )
                row = cur.fetchone()
        return self._from_value(row["record"]) if row else None

    def _migrate_legacy_catalog_sync(self, catalog_path: Path) -> dict[str, int]:
        if not catalog_path.exists():
            return {"read": 0, "inserted": 0, "skipped": 0, "invalid": 0}
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Legacy API document catalog must contain a JSON array")
        result = {"read": len(raw), "inserted": 0, "skipped": 0, "invalid": 0}
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                for item in raw:
                    if not isinstance(item, dict):
                        result["invalid"] += 1
                        continue
                    try:
                        record = ApiDocRecord.model_validate(_normalize_legacy_record(item, force_unbound=True))
                    except Exception:
                        result["invalid"] += 1
                        continue
                    cur.execute(
                        f"""
                        INSERT INTO {self._table} (id, project_id, updated_at, record)
                        VALUES (%s, NULL, %s, %s::jsonb)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (record.id, record.updated_at, self._json(record)),
                    )
                    if cur.rowcount == 1:
                        result["inserted"] += 1
                    else:
                        result["skipped"] += 1
        return result

    @staticmethod
    def _json(record: ApiDocRecord) -> str:
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _from_value(value) -> ApiDocRecord:
        if isinstance(value, str):
            value = json.loads(value)
        return ApiDocRecord.model_validate(value)


def _normalize_legacy_record(item: dict, *, force_unbound: bool) -> dict:
    normalized = dict(item)
    metadata = normalized.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    legacy_name = normalized.get("legacy_project_name") or normalized.get("project_name") or metadata.get("project_name")
    normalized["project_id"] = None if force_unbound else normalized.get("project_id")
    normalized["legacy_project_name"] = str(legacy_name).strip() if legacy_name else None
    normalized["project_name"] = normalized["legacy_project_name"]
    normalized["metadata"] = metadata
    return normalized
