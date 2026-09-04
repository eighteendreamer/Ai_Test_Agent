from __future__ import annotations

import asyncio
import json
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_management import (
    TestCaseLifecycleStatus,
    TestCasePriority,
    TestCaseRecord,
    TestCaseVersionRecord,
)


class DuplicateCaseKeyError(ValueError):
    pass


class TestCaseStore(Protocol):
    async def initialize(self) -> None: ...
    async def create(
        self,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]: ...
    async def create_many(
        self,
        entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]],
    ) -> list[tuple[TestCaseRecord, TestCaseVersionRecord]]: ...
    async def get_case(self, case_id: str) -> TestCaseRecord | None: ...
    async def get_cases(self, case_ids: list[str]) -> dict[str, TestCaseRecord]: ...
    async def list_cases(
        self,
        *,
        project_id: str,
        status: TestCaseLifecycleStatus | None,
        mode_key: str | None,
        priority: TestCasePriority | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestCaseRecord], bool]: ...
    async def get_version(self, version_id: str) -> TestCaseVersionRecord | None: ...
    async def get_versions(
        self,
        version_ids: list[str],
    ) -> dict[str, TestCaseVersionRecord]: ...
    async def get_active_case_versions(
        self,
        case_ids: list[str],
    ) -> dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]]: ...
    async def list_versions(self, case_id: str) -> list[TestCaseVersionRecord]: ...
    async def append_version(
        self,
        case_id: str,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]: ...
    async def replace_case(
        self,
        case: TestCaseRecord,
        *,
        expected_statuses: set[TestCaseLifecycleStatus],
    ) -> TestCaseRecord: ...
    async def count_by_project(self, project_id: str) -> int: ...


class InMemoryTestCaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, TestCaseRecord] = {}
        self._versions: dict[str, TestCaseVersionRecord] = {}
        self._version_ids_by_case: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create(
        self,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        return (await self.create_many([(case, version)]))[0]

    async def create_many(
        self,
        entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]],
    ) -> list[tuple[TestCaseRecord, TestCaseVersionRecord]]:
        async with self._lock:
            requested_keys = [(case.project_id, case.case_key) for case, _ in entries]
            if len(set(requested_keys)) != len(requested_keys):
                raise DuplicateCaseKeyError("Generated batch contains duplicate case_key values")
            existing_keys = {
                (item.project_id, item.case_key) for item in self._cases.values()
            }
            conflict = next((key for key in requested_keys if key in existing_keys), None)
            if conflict is not None:
                raise DuplicateCaseKeyError(
                    f"case_key already exists in project: {conflict[1]}"
                )
            stored_entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]] = []
            for case, version in entries:
                stored_case = case.model_copy(deep=True, update={"latest_version": 1})
                stored_version = version.model_copy(deep=True, update={"version": 1})
                self._cases[case.id] = stored_case
                self._versions[version.id] = stored_version
                self._version_ids_by_case[case.id] = [version.id]
                stored_entries.append((stored_case, stored_version))
            return [
                (case.model_copy(deep=True), version.model_copy(deep=True))
                for case, version in stored_entries
            ]

    async def get_case(self, case_id: str) -> TestCaseRecord | None:
        case = self._cases.get(case_id)
        return case.model_copy(deep=True) if case else None

    async def get_cases(self, case_ids: list[str]) -> dict[str, TestCaseRecord]:
        return {
            case_id: self._cases[case_id].model_copy(deep=True)
            for case_id in dict.fromkeys(case_ids)
            if case_id in self._cases
        }

    async def list_cases(
        self,
        *,
        project_id: str,
        status: TestCaseLifecycleStatus | None,
        mode_key: str | None,
        priority: TestCasePriority | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestCaseRecord], bool]:
        cases = [item for item in self._cases.values() if item.project_id == project_id]
        if status:
            cases = [item for item in cases if item.lifecycle_status == status]
        if mode_key:
            cases = [item for item in cases if item.mode_key == mode_key]
        if priority:
            cases = [item for item in cases if item.priority == priority]
        if query:
            needle = query.casefold()
            cases = [
                item
                for item in cases
                if needle in item.case_key.casefold() or needle in item.title.casefold()
            ]
        cases.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        selected = cases[offset : offset + limit + 1]
        return [item.model_copy(deep=True) for item in selected[:limit]], len(selected) > limit

    async def get_version(self, version_id: str) -> TestCaseVersionRecord | None:
        version = self._versions.get(version_id)
        return version.model_copy(deep=True) if version else None

    async def get_versions(
        self,
        version_ids: list[str],
    ) -> dict[str, TestCaseVersionRecord]:
        return {
            version_id: self._versions[version_id].model_copy(deep=True)
            for version_id in dict.fromkeys(version_ids)
            if version_id in self._versions
        }

    async def get_active_case_versions(
        self,
        case_ids: list[str],
    ) -> dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]]:
        result: dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]] = {}
        for case_id in case_ids:
            case = self._cases.get(case_id)
            if case is None or case.active_version_id is None:
                continue
            version = self._versions.get(case.active_version_id)
            if version is not None:
                result[case_id] = (
                    case.model_copy(deep=True),
                    version.model_copy(deep=True),
                )
        return result

    async def list_versions(self, case_id: str) -> list[TestCaseVersionRecord]:
        return [
            self._versions[version_id].model_copy(deep=True)
            for version_id in self._version_ids_by_case.get(case_id, [])
        ]

    async def append_version(
        self,
        case_id: str,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        async with self._lock:
            current = self._cases.get(case_id)
            if current is None:
                raise KeyError(f"Test case not found: {case_id}")
            if current.lifecycle_status == "archived":
                raise ValueError(f"Test case is archived: {case_id}")
            next_number = current.latest_version + 1
            stored_version = version.model_copy(deep=True, update={"version": next_number})
            stored_case = current.model_copy(
                deep=True,
                update={
                    "latest_version": next_number,
                    "lifecycle_status": "draft",
                    "updated_at": version.created_at,
                },
            )
            self._versions[stored_version.id] = stored_version
            self._version_ids_by_case.setdefault(case_id, []).append(stored_version.id)
            self._cases[case_id] = stored_case
            return stored_case.model_copy(deep=True), stored_version.model_copy(deep=True)

    async def replace_case(
        self,
        case: TestCaseRecord,
        *,
        expected_statuses: set[TestCaseLifecycleStatus],
    ) -> TestCaseRecord:
        async with self._lock:
            current = self._cases.get(case.id)
            if current is None:
                raise KeyError(f"Test case not found: {case.id}")
            if current.lifecycle_status not in expected_statuses:
                raise ValueError(
                    f"Illegal test case transition from {current.lifecycle_status} to {case.lifecycle_status}"
                )
            stored = case.model_copy(deep=True)
            self._cases[case.id] = stored
            return stored.model_copy(deep=True)

    async def count_by_project(self, project_id: str) -> int:
        return sum(1 for case in self._cases.values() if case.project_id == project_id)


class PostgresTestCaseStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _case_table(self) -> str:
        return self._settings.database.postgres_test_case_table

    @property
    def _version_table(self) -> str:
        return self._settings.database.postgres_test_case_version_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create(
        self,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        return (await self.create_many([(case, version)]))[0]

    async def create_many(
        self,
        entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]],
    ) -> list[tuple[TestCaseRecord, TestCaseVersionRecord]]:
        return await asyncio.to_thread(self._create_many_sync, entries)

    async def get_case(self, case_id: str) -> TestCaseRecord | None:
        return await asyncio.to_thread(self._get_case_sync, case_id)

    async def get_cases(self, case_ids: list[str]) -> dict[str, TestCaseRecord]:
        return await asyncio.to_thread(self._get_cases_sync, case_ids)

    async def list_cases(
        self,
        *,
        project_id: str,
        status: TestCaseLifecycleStatus | None,
        mode_key: str | None,
        priority: TestCasePriority | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestCaseRecord], bool]:
        return await asyncio.to_thread(
            self._list_cases_sync,
            project_id,
            status,
            mode_key,
            priority,
            query,
            limit,
            offset,
        )

    async def get_version(self, version_id: str) -> TestCaseVersionRecord | None:
        return await asyncio.to_thread(self._get_version_sync, version_id)

    async def get_versions(
        self,
        version_ids: list[str],
    ) -> dict[str, TestCaseVersionRecord]:
        return await asyncio.to_thread(self._get_versions_sync, version_ids)

    async def get_active_case_versions(
        self,
        case_ids: list[str],
    ) -> dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]]:
        return await asyncio.to_thread(self._get_active_case_versions_sync, case_ids)

    async def list_versions(self, case_id: str) -> list[TestCaseVersionRecord]:
        return await asyncio.to_thread(self._list_versions_sync, case_id)

    async def append_version(
        self,
        case_id: str,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        return await asyncio.to_thread(self._append_version_sync, case_id, version)

    async def replace_case(
        self,
        case: TestCaseRecord,
        *,
        expected_statuses: set[TestCaseLifecycleStatus],
    ) -> TestCaseRecord:
        return await asyncio.to_thread(self._replace_case_sync, case, expected_statuses)

    async def count_by_project(self, project_id: str) -> int:
        return await asyncio.to_thread(self._count_by_project_sync, project_id)

    def _initialize_sync(self) -> None:
        case_table = self._case_table
        version_table = self._version_table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {case_table} (
                        id UUID PRIMARY KEY,
                        project_id UUID NOT NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        case_key TEXT NOT NULL,
                        lifecycle_status TEXT NOT NULL,
                        mode_key TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        active_version_id UUID,
                        latest_version INTEGER NOT NULL DEFAULT 1,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(project_id, case_key)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {version_table} (
                        id UUID PRIMARY KEY,
                        case_id UUID NOT NULL REFERENCES {case_table}(id),
                        version INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(case_id, version)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{case_table}_project_status_updated "
                    f"ON {case_table} (project_id, lifecycle_status, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{case_table}_project_mode_priority "
                    f"ON {case_table} (project_id, mode_key, priority, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{version_table}_case_version "
                    f"ON {version_table} (case_id, version ASC)"
                )

    def _create_many_sync(
        self,
        entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]],
    ) -> list[tuple[TestCaseRecord, TestCaseVersionRecord]]:
        try:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    if entries:
                        cur.executemany(
                            f"""
                            INSERT INTO {self._case_table} (
                                id, project_id, case_key, lifecycle_status, mode_key,
                                priority, active_version_id, latest_version, updated_at, record
                            ) VALUES (%s, %s, %s, %s, %s, %s, NULL, 1, %s, %s::jsonb)
                            """,
                            [
                                (
                                    case.id,
                                    case.project_id,
                                    case.case_key,
                                    case.lifecycle_status,
                                    case.mode_key,
                                    case.priority,
                                    case.updated_at,
                                    self._json(case),
                                )
                                for case, _ in entries
                            ],
                        )
                        cur.executemany(
                            f"""
                            INSERT INTO {self._version_table} (
                                id, case_id, version, content_hash, created_at, record
                            ) VALUES (%s, %s, 1, %s, %s, %s::jsonb)
                            """,
                            [
                                (
                                    version.id,
                                    version.case_id,
                                    version.content_hash,
                                    version.created_at,
                                    self._json(version),
                                )
                                for _, version in entries
                            ],
                        )
        except Exception as exc:
            message = str(exc).lower()
            if "case_key" in message or "project_id" in message and "unique" in message:
                case_key = entries[-1][0].case_key if entries else "unknown"
                raise DuplicateCaseKeyError(
                    f"case_key already exists in project: {case_key}"
                ) from exc
            raise
        return entries

    def _get_case_sync(self, case_id: str) -> TestCaseRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._case_table} WHERE id = %s", (case_id,))
                row = cur.fetchone()
        return self._case_from_value(row["record"]) if row else None

    def _get_cases_sync(self, case_ids: list[str]) -> dict[str, TestCaseRecord]:
        if not case_ids:
            return {}
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._case_table} WHERE id = ANY(%s::uuid[])",
                    (list(dict.fromkeys(case_ids)),),
                )
                rows = cur.fetchall() or []
        cases = [self._case_from_value(row["record"]) for row in rows]
        return {case.id: case for case in cases}

    def _list_cases_sync(
        self,
        project_id: str,
        status: TestCaseLifecycleStatus | None,
        mode_key: str | None,
        priority: TestCasePriority | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestCaseRecord], bool]:
        clauses = ["project_id = %s"]
        parameters: list[object] = [project_id]
        if status:
            clauses.append("lifecycle_status = %s")
            parameters.append(status)
        if mode_key:
            clauses.append("mode_key = %s")
            parameters.append(mode_key)
        if priority:
            clauses.append("priority = %s")
            parameters.append(priority)
        if query:
            clauses.append("(case_key ILIKE %s OR record->>'title' ILIKE %s)")
            pattern = f"%{query}%"
            parameters.extend([pattern, pattern])
        parameters.extend([limit + 1, offset])
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._case_table} WHERE {' AND '.join(clauses)} "
                    "ORDER BY updated_at DESC, id ASC LIMIT %s OFFSET %s",
                    tuple(parameters),
                )
                rows = cur.fetchall() or []
        return [self._case_from_value(row["record"]) for row in rows[:limit]], len(rows) > limit

    def _get_version_sync(self, version_id: str) -> TestCaseVersionRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._version_table} WHERE id = %s", (version_id,))
                row = cur.fetchone()
        return self._version_from_value(row["record"]) if row else None

    def _get_versions_sync(
        self,
        version_ids: list[str],
    ) -> dict[str, TestCaseVersionRecord]:
        if not version_ids:
            return {}
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._version_table} WHERE id = ANY(%s::uuid[])",
                    (list(dict.fromkeys(version_ids)),),
                )
                rows = cur.fetchall() or []
        versions = [self._version_from_value(row["record"]) for row in rows]
        return {version.id: version for version in versions}

    def _get_active_case_versions_sync(
        self,
        case_ids: list[str],
    ) -> dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]]:
        if not case_ids:
            return {}
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT c.record AS case_record, v.record AS version_record "
                    f"FROM {self._case_table} c "
                    f"JOIN {self._version_table} v ON v.id = c.active_version_id "
                    "WHERE c.id = ANY(%s::uuid[])",
                    (case_ids,),
                )
                rows = cur.fetchall() or []
        result: dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]] = {}
        for row in rows:
            case = self._case_from_value(row["case_record"])
            version = self._version_from_value(row["version_record"])
            result[case.id] = (case, version)
        return result

    def _list_versions_sync(self, case_id: str) -> list[TestCaseVersionRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._version_table} WHERE case_id = %s ORDER BY version ASC",
                    (case_id,),
                )
                rows = cur.fetchall() or []
        return [self._version_from_value(row["record"]) for row in rows]

    def _append_version_sync(
        self,
        case_id: str,
        version: TestCaseVersionRecord,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record, latest_version FROM {self._case_table} WHERE id = %s FOR UPDATE",
                    (case_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise KeyError(f"Test case not found: {case_id}")
                current = self._case_from_value(row["record"])
                if current.lifecycle_status == "archived":
                    raise ValueError(f"Test case is archived: {case_id}")
                next_number = int(row["latest_version"]) + 1
                stored_version = version.model_copy(update={"version": next_number})
                stored_case = current.model_copy(
                    update={
                        "latest_version": next_number,
                        "lifecycle_status": "draft",
                        "updated_at": version.created_at,
                    }
                )
                cur.execute(
                    f"""
                    INSERT INTO {self._version_table} (
                        id, case_id, version, content_hash, created_at, record
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        stored_version.id,
                        case_id,
                        next_number,
                        stored_version.content_hash,
                        stored_version.created_at,
                        self._json(stored_version),
                    ),
                )
                self._write_case(cur, stored_case)
        return stored_case, stored_version

    def _replace_case_sync(
        self,
        case: TestCaseRecord,
        expected_statuses: set[TestCaseLifecycleStatus],
    ) -> TestCaseRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT lifecycle_status FROM {self._case_table} WHERE id = %s FOR UPDATE",
                    (case.id,),
                )
                row = cur.fetchone()
                if not row:
                    raise KeyError(f"Test case not found: {case.id}")
                current_status = str(row["lifecycle_status"])
                if current_status not in expected_statuses:
                    raise ValueError(
                        f"Illegal test case transition from {current_status} to {case.lifecycle_status}"
                    )
                self._write_case(cur, case)
        return case

    def _write_case(self, cur, case: TestCaseRecord) -> None:
        cur.execute(
            f"""
            UPDATE {self._case_table}
            SET lifecycle_status = %s, active_version_id = %s,
                latest_version = %s, updated_at = %s, record = %s::jsonb
            WHERE id = %s
            """,
            (
                case.lifecycle_status,
                case.active_version_id,
                case.latest_version,
                case.updated_at,
                self._json(case),
                case.id,
            ),
        )

    def _count_by_project_sync(self, project_id: str) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM {self._case_table} WHERE project_id = %s",
                    (project_id,),
                )
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    @staticmethod
    def _json(record) -> str:
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _case_from_value(value) -> TestCaseRecord:
        return TestCaseRecord.model_validate(json.loads(value) if isinstance(value, str) else value)

    @staticmethod
    def _version_from_value(value) -> TestCaseVersionRecord:
        return TestCaseVersionRecord.model_validate(json.loads(value) if isinstance(value, str) else value)
