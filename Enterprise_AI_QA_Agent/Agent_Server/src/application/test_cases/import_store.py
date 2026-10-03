from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from src.application.test_cases.case_store import InMemoryTestCaseStore, PostgresTestCaseStore
from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_import import CaseImportBatchRecord, CaseImportSourceRecord
from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord

DraftEntries = list[tuple[TestCaseRecord, TestCaseVersionRecord]]


def _validate_commit(batch: CaseImportBatchRecord, entries: DraftEntries):
    if not batch.preview.candidates or any(issue.severity == "error" for issue in batch.preview.issues):
        raise ValueError("预览包含错误或没有有效用例，请修正后重新预览")
    expected = [candidate.draft.case_key for candidate in batch.preview.candidates]
    if [case.case_key for case, _ in entries] != expected or any(case.project_id != batch.project_id for case, _ in entries):
        raise ValueError("导入条目与固定预览不一致")


class CaseImportStore(Protocol):
    async def initialize(self) -> None: ...
    async def find_source(self, project_id: str, file_hash: str) -> CaseImportSourceRecord | None: ...
    async def get_source(self, project_id: str, source_id: str) -> CaseImportSourceRecord | None: ...
    async def save_source(self, source: CaseImportSourceRecord) -> CaseImportSourceRecord: ...
    async def get_batch(self, project_id: str, batch_id: str) -> CaseImportBatchRecord | None: ...
    async def save_batch(self, batch: CaseImportBatchRecord) -> CaseImportBatchRecord: ...
    async def commit_batch(self, project_id: str, batch_id: str, entries: DraftEntries) -> CaseImportBatchRecord: ...


class InMemoryCaseImportStore:
    def __init__(self, case_store: InMemoryTestCaseStore):
        self._cases = case_store
        self.sources: dict[str, CaseImportSourceRecord] = {}
        self.batches: dict[str, CaseImportBatchRecord] = {}
        self._lock = asyncio.Lock()

    async def initialize(self):
        pass

    async def find_source(self, project_id, file_hash):
        return next((item.model_copy(deep=True) for item in self.sources.values() if item.project_id == project_id and item.file_hash == file_hash), None)

    async def get_source(self, project_id, source_id):
        source = self.sources.get(source_id)
        return source.model_copy(deep=True) if source and source.project_id == project_id else None

    async def save_source(self, source):
        async with self._lock:
            current = await self.find_source(source.project_id, source.file_hash)
            if current is None:
                self.sources[source.id] = source.model_copy(deep=True)
            return current or source.model_copy(deep=True)

    async def get_batch(self, project_id, batch_id):
        batch = self.batches.get(batch_id)
        return batch.model_copy(deep=True) if batch and batch.project_id == project_id else None

    async def save_batch(self, batch):
        async with self._lock:
            source = await self.get_source(batch.project_id, batch.source_id)
            if source is None:
                raise ValueError("导入来源不属于当前项目")
            self.batches.setdefault(batch.id, batch.model_copy(deep=True))
            return self.batches[batch.id].model_copy(deep=True)

    async def commit_batch(self, project_id, batch_id, entries):
        async with self._lock:
            batch = await self.get_batch(project_id, batch_id)
            if batch is None:
                raise KeyError("导入批次不存在")
            if batch.status == "committed":
                return batch
            _validate_commit(batch, entries)
            await self._cases.create_many(entries)
            stored = batch.model_copy(update={"status": "committed", "case_ids": [case.id for case, _ in entries], "committed_at": datetime.now(timezone.utc)})
            self.batches[batch_id] = stored
            return stored.model_copy(deep=True)


class PostgresCaseImportStore:
    def __init__(self, settings: Settings, case_store: PostgresTestCaseStore):
        self._settings = settings
        self._cases = case_store
        self._source_table = settings.database.postgres_case_import_source_table
        self._batch_table = settings.database.postgres_case_import_batch_table
        for table in (self._source_table, self._batch_table):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid case import table identifier")

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self):
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"""CREATE TABLE IF NOT EXISTS {self._source_table} (
                    id UUID PRIMARY KEY,
                    project_id UUID NOT NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                    file_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    UNIQUE (project_id, file_hash), UNIQUE (id, project_id)
                )""")
                cursor.execute(f"""CREATE TABLE IF NOT EXISTS {self._batch_table} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL, source_id UUID NOT NULL,
                    preview_hash TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('previewed','committed')),
                    created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    FOREIGN KEY (source_id, project_id) REFERENCES {self._source_table}(id, project_id),
                    UNIQUE (source_id, preview_hash)
                )""")
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._batch_table}_project ON {self._batch_table}(project_id, created_at DESC)")

    async def find_source(self, project_id, file_hash):
        return await asyncio.to_thread(self._read, self._source_table, "project_id = %s AND file_hash = %s", (project_id, file_hash), CaseImportSourceRecord)

    async def get_source(self, project_id, source_id):
        return await asyncio.to_thread(self._read, self._source_table, "project_id = %s AND id = %s", (project_id, source_id), CaseImportSourceRecord)

    async def get_batch(self, project_id, batch_id):
        return await asyncio.to_thread(self._read, self._batch_table, "project_id = %s AND id = %s", (project_id, batch_id), CaseImportBatchRecord)

    def _read(self, table, clause, parameters, model):
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT record FROM {table} WHERE {clause}", parameters)
                row = cursor.fetchone()
        return model.model_validate(row["record"]) if row else None

    async def save_source(self, source):
        return await asyncio.to_thread(self._save_source_sync, source)

    def _save_source_sync(self, source):
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO {self._source_table}(id,project_id,file_hash,created_at,record) VALUES(%s,%s,%s,%s,%s::jsonb) "
                               "ON CONFLICT(project_id,file_hash) DO NOTHING RETURNING record",
                               (source.id, source.project_id, source.file_hash, source.created_at, _json(source)))
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(f"SELECT record FROM {self._source_table} WHERE project_id=%s AND file_hash=%s", (source.project_id, source.file_hash))
                    row = cursor.fetchone()
        if row is None:
            raise RuntimeError("导入原件目录冲突无法解析")
        return CaseImportSourceRecord.model_validate(row["record"])

    async def save_batch(self, batch):
        return await asyncio.to_thread(self._save_batch_sync, batch)

    def _save_batch_sync(self, batch):
        with postgres_connect(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO {self._batch_table}(id,project_id,source_id,preview_hash,status,created_at,record) "
                               "VALUES(%s,%s,%s,%s,'previewed',%s,%s::jsonb) ON CONFLICT(source_id,preview_hash) DO NOTHING RETURNING record",
                               (batch.id, batch.project_id, batch.source_id, batch.preview_hash, batch.created_at, _json(batch)))
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(f"SELECT record FROM {self._batch_table} WHERE source_id=%s AND preview_hash=%s AND project_id=%s", (batch.source_id, batch.preview_hash, batch.project_id))
                    row = cursor.fetchone()
        if row is None:
            raise RuntimeError("导入预览冲突无法解析")
        return CaseImportBatchRecord.model_validate(row["record"])

    async def commit_batch(self, project_id, batch_id, entries):
        return await asyncio.to_thread(self._commit_sync, project_id, batch_id, entries)

    def _commit_sync(self, project_id, batch_id, entries):
        try:
            with postgres_connect(self._settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT record FROM {self._batch_table} WHERE project_id=%s AND id=%s FOR UPDATE", (project_id, batch_id))
                    row = cursor.fetchone()
                    if row is None:
                        raise KeyError("导入批次不存在")
                    batch = CaseImportBatchRecord.model_validate(row["record"])
                    if batch.status == "committed":
                        return batch
                    cursor.execute(f"SELECT status FROM {self._settings.database.postgres_project_table} WHERE id=%s FOR SHARE", (project_id,))
                    project = cursor.fetchone()
                    if not project or project["status"] != "active":
                        raise ValueError("项目已归档，不能提交导入")
                    _validate_commit(batch, entries)
                    self._cases.insert_drafts_in_transaction(cursor, entries)
                    stored = batch.model_copy(update={"status": "committed", "case_ids": [case.id for case, _ in entries], "committed_at": datetime.now(timezone.utc)})
                    cursor.execute(f"UPDATE {self._batch_table} SET status='committed', record=%s::jsonb WHERE id=%s", (_json(stored), batch_id))
            return stored
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise ValueError("用例编号已存在或导入提交冲突；未覆盖旧用例，请重新预览") from None
            raise


def _json(model):
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
