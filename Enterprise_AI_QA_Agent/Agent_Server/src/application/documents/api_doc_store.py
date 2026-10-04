from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError
from psycopg.errors import ForeignKeyViolation
from sqlalchemy.exc import IntegrityError

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.api_docs import ApiDocRecord, ApiDocumentAsset, ApiDocVersionRecord, ApiDocSubmission, ApiDocHistory

logger = logging.getLogger(__name__)


class ApiDocConflictError(ValueError):
    """The catalog changed while a new immutable asset was being prepared."""


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
    async def create(self, record: ApiDocRecord, *, version: ApiDocVersionRecord | None = None,
        submission: ApiDocSubmission | None = None) -> ApiDocRecord: ...
    async def update(self, record: ApiDocRecord, *, expected_revision: int | None = None) -> ApiDocRecord: ...
    async def delete(self, doc_id: str) -> ApiDocRecord | None: ...
    async def list_versions(self, doc_id: str, limit: int = 50, offset: int = 0) -> list[ApiDocVersionRecord]: ...
    async def get_version(self, doc_id: str, version_id: str) -> ApiDocVersionRecord | None: ...
    async def get_submission(self, doc_id: str, request_id: str) -> ApiDocSubmission | None: ...
    async def list_assets(self, doc_id: str) -> list[ApiDocumentAsset]: ...
    async def commit_version(self, record: ApiDocRecord, version: ApiDocVersionRecord | None,
        submission: ApiDocSubmission, *, expected_revision: int) -> ApiDocSubmission: ...


class InMemoryApiDocStore:
    def __init__(self) -> None:
        self._records: dict[str, ApiDocRecord] = {}
        self._histories: dict[str, ApiDocHistory] = {}
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

    async def create(self, record: ApiDocRecord, *, version: ApiDocVersionRecord | None = None,
        submission: ApiDocSubmission | None = None) -> ApiDocRecord:
        async with self._lock:
            if submission:
                existing = self._submission(record.id, submission.request_id)
                if existing:
                    _check_submission(existing, submission)
                    return existing.result.document.model_copy(deep=True)
            if record.id in self._records:
                raise ValueError(f"API document already exists: {record.id}")
            self._commit_with_history(record, version, submission)
        return record.model_copy(deep=True)

    def _commit_records(self, records: dict[str, ApiDocRecord], *, operation: str, doc_id: str) -> None:
        self._records = records

    def _commit_with_history(self, record, version=None, submission=None) -> None:
        _check_history_shape(record, version, submission)
        previous = self._histories.get(record.id)
        history = previous.model_copy(deep=True) if previous else ApiDocHistory()
        if version:
            if any(version.id in item.versions for item in self._histories.values()) or any(
                value.version == version.version for value in history.versions.values()):
                raise ValueError("文档版本身份或序号已经存在，不可覆盖")
            history.versions[version.id] = version.model_copy(deep=True)
        if submission:
            history.submissions[submission.request_id] = submission.model_copy(deep=True)
        for asset in _record_assets(record):
            if any(asset.id in item.assets for doc_id, item in self._histories.items() if doc_id != record.id):
                raise ValueError("文档资产身份已经属于其他文档")
            _check_asset(history.assets.get(asset.id), asset)
            history.assets[asset.id] = asset.model_copy(deep=True)
        self._histories[record.id] = history
        try:
            self._commit_records({**self._records, record.id: record.model_copy(deep=True)},
                operation="update" if record.id in self._records else "create", doc_id=record.id)
        except Exception:
            if previous is None:
                self._histories.pop(record.id, None)
            else:
                self._histories[record.id] = previous
            raise

    def _submission(self, doc_id, request_id):
        history = self._histories.get(doc_id)
        return history.submissions.get(request_id) if history else None

    async def get_submission(self, doc_id, request_id):
        async with self._lock:
            entry = self._submission(doc_id, request_id)
            return entry.model_copy(deep=True) if entry else None

    async def list_versions(self, doc_id, limit=50, offset=0):
        async with self._lock:
            history = self._histories.get(doc_id, ApiDocHistory())
            values = sorted(history.versions.values(), key=lambda value: value.version, reverse=True)
            return [value.model_copy(deep=True) for value in values[offset:offset + limit]]

    async def get_version(self, doc_id, version_id):
        async with self._lock:
            value = self._histories.get(doc_id, ApiDocHistory()).versions.get(version_id)
            return value.model_copy(deep=True) if value else None

    async def list_assets(self, doc_id):
        async with self._lock:
            assets = dict(self._histories.get(doc_id, ApiDocHistory()).assets)
            for asset in _record_assets(self._records.get(doc_id)):
                assets.setdefault(asset.id, asset)
            return [asset.model_copy(deep=True) for asset in assets.values()]

    async def commit_version(self, record, version, submission, *, expected_revision):
        async with self._lock:
            saved = self._submission(record.id, submission.request_id)
            if saved:
                _check_submission(saved, submission)
                return saved.model_copy(deep=True)
            current = self._records.get(record.id)
            if current is None:
                raise KeyError(f"API document not found: {record.id}")
            if current.catalog_revision != expected_revision:
                raise ApiDocConflictError("文档已被其他请求修改，请刷新后重试")
            _check_version_parent(current, version)
            self._commit_with_history(record, version, submission)
            return submission.model_copy(deep=True)

    async def update(self, record: ApiDocRecord, *, expected_revision: int | None = None) -> ApiDocRecord:
        async with self._lock:
            if record.id not in self._records:
                raise KeyError(f"API document not found: {record.id}")
            if expected_revision is not None and self._records[record.id].catalog_revision != expected_revision:
                raise ApiDocConflictError("文档已被其他请求修改，请刷新后重试")
            self._commit_with_history(record)
        return record.model_copy(deep=True)

    async def delete(self, doc_id: str) -> ApiDocRecord | None:
        async with self._lock:
            record = self._records.get(doc_id)
            if record:
                self._commit_records({key: value for key, value in self._records.items() if key != doc_id},
                    operation="delete", doc_id=doc_id)
                self._histories.pop(doc_id, None)
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
        except Exception as exc:
            logger.exception("api_doc_catalog_load_failed path=%s", self._catalog_path)
            raise ValueError(f"Cannot read API document catalog: {self._catalog_path}") from exc
        if not isinstance(raw, list):
            logger.error("api_doc_catalog_invalid_shape path=%s", self._catalog_path)
            raise TypeError(f"API document catalog must contain a list: {self._catalog_path}")
        records: dict[str, ApiDocRecord] = {}
        histories: dict[str, ApiDocHistory] = {}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                logger.error("api_doc_catalog_invalid_item path=%s index=%d", self._catalog_path, index)
                raise TypeError(f"Invalid API document catalog item {index}: {self._catalog_path}")
            try:
                normalized = _normalize_legacy_record(item, force_unbound=False)
                record = ApiDocRecord.model_validate(normalized)
                histories[record.id] = ApiDocHistory.model_validate(item.get("history") or {})
            except ValidationError as exc:
                logger.error(
                    "api_doc_catalog_invalid_item path=%s index=%d error_type=%s",
                    self._catalog_path,
                    index,
                    type(exc).__name__,
                )
                raise ValueError(f"Invalid API document catalog item {index}: {self._catalog_path}") from None
            records[record.id] = record
        async with self._lock:
            self._records = records
            self._histories = histories

    def _commit_records(self, records, *, operation, doc_id):
        self._commit(records, operation=operation, doc_id=doc_id)

    def _commit(self, records: dict[str, ApiDocRecord], *, operation: str, doc_id: str) -> None:
        try:
            self._write_catalog(records)
        except Exception:
            logger.exception(
                "api_doc_catalog_write_failed path=%s operation=%s document_id=%s",
                self._catalog_path,
                operation,
                doc_id,
            )
            raise
        self._records = records
        logger.debug(
            "api_doc_catalog_committed path=%s operation=%s document_id=%s count=%d",
            self._catalog_path,
            operation,
            doc_id,
            len(records),
        )

    def _write_catalog(self, records: dict[str, ApiDocRecord]) -> None:
        self._catalog_path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(records.values(), key=lambda record: record.updated_at, reverse=True)
        payload = json.dumps(
            [{**record.model_dump(mode="json"), "history": self._histories.get(record.id, ApiDocHistory()).model_dump(mode="json")}
                for record in ordered],
            ensure_ascii=False,
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._catalog_path.parent,
                prefix=f".{self._catalog_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self._catalog_path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("api_doc_catalog_temp_cleanup_failed path=%s", temporary_path, exc_info=True)


class PostgresApiDocStore:
    """PostgreSQL metadata catalog; document bodies remain in object storage."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        for name in [self._table, *self.history_tables, settings.database.postgres_project_table]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid API document table identifier")

    @property
    def _table(self) -> str:
        return self._settings.database.postgres_api_doc_table

    @property
    def history_tables(self) -> list[str]:
        return [self._table + "_submissions", self._table + "_assets", self._table + "_versions"]

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

    async def create(self, record: ApiDocRecord, *, version=None, submission=None) -> ApiDocRecord:
        return await asyncio.to_thread(self._create_sync, record, version, submission)

    async def update(self, record: ApiDocRecord, *, expected_revision: int | None = None) -> ApiDocRecord:
        return await asyncio.to_thread(self._update_sync, record, expected_revision)

    async def delete(self, doc_id: str) -> ApiDocRecord | None:
        return await asyncio.to_thread(self._delete_sync, doc_id)

    async def list_versions(self, doc_id, limit=50, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._table}_versions WHERE document_id=%s ORDER BY version DESC LIMIT %s OFFSET %s",
                    (doc_id, limit, offset))
                return [ApiDocVersionRecord.model_validate(row["record"]) for row in cur.fetchall()]
        return await asyncio.to_thread(read)

    async def get_version(self, doc_id, version_id):
        return await asyncio.to_thread(self._read_history, "versions", ApiDocVersionRecord, doc_id, version_id)

    async def get_submission(self, doc_id, request_id):
        return await asyncio.to_thread(self._read_history, "submissions", ApiDocSubmission, doc_id, request_id)

    def _read_history(self, kind, model, doc_id, identity):
        column = "request_id" if kind == "submissions" else "id"
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {self._table}_{kind} WHERE document_id=%s AND {column}=%s", (doc_id, identity))
            row = cur.fetchone()
        return model.model_validate(row["record"]) if row else None

    async def list_assets(self, doc_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._table}_assets WHERE document_id=%s", (doc_id,))
                assets = {row["record"]["id"]: ApiDocumentAsset.model_validate(row["record"]) for row in cur.fetchall()}
                cur.execute(f"SELECT record FROM {self._table} WHERE id=%s", (doc_id,))
                current = cur.fetchone()
                for asset in _record_assets(self._from_value(current["record"]) if current else None):
                    assets.setdefault(asset.id, asset)
            return list(assets.values())
        return await asyncio.to_thread(read)

    async def commit_version(self, record, version, submission, *, expected_revision):
        return await asyncio.to_thread(self._commit_version, record, version, submission, expected_revision)

    def _commit_version(self, record, version, submission, expected_revision):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {self._table} WHERE id=%s FOR UPDATE", (record.id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"API document not found: {record.id}")
            cur.execute(f"SELECT record FROM {self._table}_submissions WHERE document_id=%s AND request_id=%s",
                (record.id, submission.request_id))
            existing = cur.fetchone()
            if existing:
                saved = ApiDocSubmission.model_validate(existing["record"])
                _check_submission(saved, submission)
                return saved
            current = self._from_value(row["record"])
            if current.catalog_revision != expected_revision:
                raise ApiDocConflictError("文档已被其他请求修改，请刷新后重试")
            _check_version_parent(current, version)
            self._write_history(cur, record, version, submission)
            cur.execute(f"UPDATE {self._table} SET record=%s::jsonb,updated_at=%s WHERE id=%s",
                (self._json(record), record.updated_at, record.id))
        return submission

    def _write_history(self, cur, record, version=None, submission=None):
        _check_history_shape(record, version, submission)
        if version:
            cur.execute(f"SELECT id FROM {self._table}_versions WHERE id=%s OR (document_id=%s AND version=%s)",
                (version.id, record.id, version.version))
            if cur.fetchone():
                raise ValueError("文档版本身份或序号已经存在，不可覆盖")
            cur.execute(f"INSERT INTO {self._table}_versions(id,document_id,version,record) VALUES(%s,%s,%s,%s::jsonb)",
                (version.id, record.id, version.version, version.model_dump_json()))
        for asset in _record_assets(record):
            cur.execute(f"SELECT document_id,record FROM {self._table}_assets WHERE id=%s", (asset.id,))
            old = cur.fetchone()
            if old and str(old["document_id"]) != record.id:
                raise ValueError("文档资产身份已经属于其他文档")
            _check_asset(ApiDocumentAsset.model_validate(old["record"]) if old else None, asset)
            cur.execute(f"INSERT INTO {self._table}_assets(id,document_id,record) VALUES(%s,%s,%s::jsonb) "
                "ON CONFLICT(id) DO UPDATE SET record=EXCLUDED.record WHERE "
                f"{self._table}_assets.document_id=EXCLUDED.document_id", (asset.id, record.id, asset.model_dump_json()))
            if cur.rowcount != 1:
                raise ValueError("文档资产身份已经属于其他文档")
        if submission:
            cur.execute(f"INSERT INTO {self._table}_submissions(document_id,request_id,record) VALUES(%s,%s,%s::jsonb)",
                (record.id, submission.request_id, submission.model_dump_json()))

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
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table}_versions (id UUID PRIMARY KEY, "
                    f"document_id UUID NOT NULL REFERENCES {table}(id) ON DELETE CASCADE, version INTEGER NOT NULL, "
                    "record JSONB NOT NULL, UNIQUE(document_id,version))")
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table}_assets (id UUID PRIMARY KEY, "
                    f"document_id UUID NOT NULL REFERENCES {table}(id) ON DELETE CASCADE, record JSONB NOT NULL)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_assets_document ON {table}_assets(document_id)")
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table}_submissions (document_id UUID NOT NULL "
                    f"REFERENCES {table}(id) ON DELETE CASCADE, request_id VARCHAR(120) NOT NULL, "
                    "record JSONB NOT NULL, PRIMARY KEY(document_id,request_id))")

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

    def _create_sync(self, record: ApiDocRecord, version=None, submission=None) -> ApiDocRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._table} (id, project_id, updated_at, record) "
                    "VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT(id) DO NOTHING",
                    (record.id, record.project_id, record.updated_at, self._json(record)),
                )
                if cur.rowcount != 1:
                    if submission:
                        cur.execute(f"SELECT record FROM {self._table}_submissions WHERE document_id=%s AND request_id=%s",
                            (record.id, submission.request_id))
                        existing = cur.fetchone()
                        if existing:
                            saved = ApiDocSubmission.model_validate(existing["record"])
                            _check_submission(saved, submission)
                            return saved.result.document
                    raise ValueError(f"API document already exists: {record.id}")
                self._write_history(cur, record, version, submission)
        return record

    def _update_sync(self, record: ApiDocRecord, expected_revision: int | None = None) -> ApiDocRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._table} SET project_id = %s, updated_at = %s, record = %s::jsonb "
                    "WHERE id = %s AND (%s::bigint IS NULL OR COALESCE((record->>'catalog_revision')::bigint, 0) = %s::bigint)",
                    (record.project_id, record.updated_at, self._json(record), record.id, expected_revision, expected_revision),
                )
                if cur.rowcount != 1:
                    cur.execute(f"SELECT id FROM {self._table} WHERE id = %s", (record.id,))
                    if cur.fetchone():
                        raise ApiDocConflictError("文档已被其他请求修改，请刷新后重试")
                    raise KeyError(f"API document not found: {record.id}")
                self._write_history(cur, record)
        return record

    def _delete_sync(self, doc_id: str) -> ApiDocRecord | None:
        try:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {self._table} WHERE id = %s RETURNING record",
                        (doc_id,),
                    )
                    row = cur.fetchone()
        except IntegrityError as exc:
            if not isinstance(exc.orig, ForeignKeyViolation):
                raise
            logger.warning("api_doc_delete_referenced", extra={"api_doc_id": doc_id})
            raise ApiDocConflictError("文档已有持久业务引用，不能删除原件或历史版本") from exc
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
                for index, item in enumerate(raw):
                    if not isinstance(item, dict):
                        result["invalid"] += 1
                        continue
                    try:
                        record = ApiDocRecord.model_validate(_normalize_legacy_record(item, force_unbound=True))
                        history = ApiDocHistory.model_validate(item.get("history") or {})
                        _check_catalog_history(record, history)
                    except (ValueError, TypeError) as exc:
                        result["invalid"] += 1
                        logger.warning("api_doc_catalog_migration_invalid", extra={
                            "item_index": index, "error_type": type(exc).__name__,
                            "reason": str(exc) if type(exc) is ValueError else "Legacy catalog record validation failed"})
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
                        self._write_history(cur, record)
                        for value in sorted(history.versions.values(), key=lambda value: value.version):
                            self._write_history(cur, value.snapshot, version=value)
                        for asset in history.assets.values():
                            self._write_history(cur, record.model_copy(update={"original_asset": None, "derived_assets": [asset]}))
                        for value in history.submissions.values():
                            self._write_history(cur, value.result.document, submission=value)
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


def _record_assets(record: ApiDocRecord | None) -> list[ApiDocumentAsset]:
    if record is None:
        return []
    return [*record.derived_assets, *([record.original_asset] if record.original_asset else [])]


def _check_asset(previous: ApiDocumentAsset | None, asset: ApiDocumentAsset) -> None:
    if previous is None:
        return
    if previous.model_dump(exclude={"version_id"}) != asset.model_dump(exclude={"version_id"}):
        raise ValueError("文档资产身份已存在且内容不一致")
    if previous.version_id is not None and previous.version_id != asset.version_id:
        raise ValueError("文档资产已经关联其他版本")


def _check_submission(previous: ApiDocSubmission, proposed: ApiDocSubmission) -> None:
    if previous.request_hash != proposed.request_hash:
        raise ApiDocConflictError("同一提交身份不能使用不同的文档内容或参数")


def _check_version_parent(current: ApiDocRecord, version: ApiDocVersionRecord | None) -> None:
    if version and (version.document_id != current.id or version.parent_version_id != current.current_version_id
        or version.version != current.version_number + 1):
        raise ApiDocConflictError("文档父版本已变化，请刷新后重新选择")


def _check_history_shape(record, version=None, submission=None) -> None:
    if version and (version.document_id != record.id or version.id != record.current_version_id
        or version.version != record.version_number or version.snapshot != record):
        raise ValueError("文档版本与目录快照不一致")
    if submission and (submission.document_id != record.id or submission.result.document != record
        or submission.result.version.document_id != record.id
        or submission.result.version.id != record.current_version_id):
        raise ValueError("文档提交与固定版本不一致")


def _check_catalog_history(record: ApiDocRecord, history: ApiDocHistory) -> None:
    """Validate persisted references before importing or skipping an existing document."""
    versions = sorted(history.versions.values(), key=lambda value: value.version)
    parent_id = None
    for ordinal, value in enumerate(versions, start=1):
        if history.versions.get(value.id) != value:
            raise ValueError("Legacy version key differs from its identity")
        _check_history_shape(value.snapshot, value)
        if value.document_id != record.id or value.version != ordinal or value.parent_version_id != parent_id:
            raise ValueError("Legacy version chain is incomplete or inconsistent")
        parent_id = value.id
    if record.current_version_id != parent_id or record.version_number != len(versions):
        raise ValueError("Legacy current version differs from the latest history version")
    for key, asset in history.assets.items():
        if key != asset.id or (asset.version_id is not None and asset.version_id not in history.versions):
            raise ValueError("Legacy asset references an invalid identity or version")
    snapshots = [record, *(value.snapshot for value in versions)]
    for key, value in history.submissions.items():
        if key != value.request_id or value.document_id != record.id:
            raise ValueError("Legacy submission identity or document differs")
        _check_history_shape(value.result.document, submission=value)
        if history.versions.get(value.result.version.id) != value.result.version:
            raise ValueError("Legacy submission references a missing or different version")
        snapshots.append(value.result.document)
    for snapshot in snapshots:
        for asset in _record_assets(snapshot):
            saved = history.assets.get(asset.id)
            if saved is None:
                if not history.versions and not history.assets:
                    continue  # Older catalogs stored assets only in the current record.
                raise ValueError("Legacy snapshot asset is missing from history")
            _check_asset(asset, saved)
