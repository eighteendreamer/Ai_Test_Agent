from __future__ import annotations

import asyncio
import json
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.suite_management import TestSuiteBundle, TestSuiteItemRecord, TestSuiteRecord


class TestSuiteStore(Protocol):
    async def initialize(self) -> None: ...
    async def create(
        self,
        suite: TestSuiteRecord,
        items: list[TestSuiteItemRecord],
    ) -> TestSuiteBundle: ...
    async def get(self, suite_id: str) -> TestSuiteBundle | None: ...
    async def list(
        self,
        *,
        project_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[TestSuiteBundle], bool]: ...
    async def replace(self, suite: TestSuiteRecord) -> TestSuiteRecord: ...
    async def count_by_project(self, project_id: str) -> int: ...


class InMemoryTestSuiteStore:
    def __init__(self) -> None:
        self._suites: dict[str, TestSuiteRecord] = {}
        self._items_by_suite: dict[str, list[TestSuiteItemRecord]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create(
        self,
        suite: TestSuiteRecord,
        items: list[TestSuiteItemRecord],
    ) -> TestSuiteBundle:
        async with self._lock:
            self._suites[suite.id] = suite.model_copy(deep=True)
            self._items_by_suite[suite.id] = [item.model_copy(deep=True) for item in items]
        return TestSuiteBundle(suite=suite, items=items)

    async def get(self, suite_id: str) -> TestSuiteBundle | None:
        suite = self._suites.get(suite_id)
        if suite is None:
            return None
        return TestSuiteBundle(
            suite=suite.model_copy(deep=True),
            items=[item.model_copy(deep=True) for item in self._items_by_suite.get(suite_id, [])],
        )

    async def list(
        self,
        *,
        project_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[TestSuiteBundle], bool]:
        suites = [item for item in self._suites.values() if item.project_id == project_id]
        suites.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        selected = suites[offset : offset + limit + 1]
        bundles = [
            TestSuiteBundle(
                suite=suite.model_copy(deep=True),
                items=[
                    item.model_copy(deep=True)
                    for item in self._items_by_suite.get(suite.id, [])
                ],
            )
            for suite in selected[:limit]
        ]
        return bundles, len(selected) > limit

    async def replace(self, suite: TestSuiteRecord) -> TestSuiteRecord:
        async with self._lock:
            if suite.id not in self._suites:
                raise KeyError(f"Test suite not found: {suite.id}")
            self._suites[suite.id] = suite.model_copy(deep=True)
        return suite.model_copy(deep=True)

    async def count_by_project(self, project_id: str) -> int:
        return sum(1 for suite in self._suites.values() if suite.project_id == project_id)


class PostgresTestSuiteStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _suite_table(self) -> str:
        return self._settings.postgres_test_suite_table

    @property
    def _item_table(self) -> str:
        return self._settings.postgres_test_suite_item_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create(
        self,
        suite: TestSuiteRecord,
        items: list[TestSuiteItemRecord],
    ) -> TestSuiteBundle:
        return await asyncio.to_thread(self._create_sync, suite, items)

    async def get(self, suite_id: str) -> TestSuiteBundle | None:
        return await asyncio.to_thread(self._get_sync, suite_id)

    async def list(
        self,
        *,
        project_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[TestSuiteBundle], bool]:
        return await asyncio.to_thread(self._list_sync, project_id, limit, offset)

    async def replace(self, suite: TestSuiteRecord) -> TestSuiteRecord:
        return await asyncio.to_thread(self._replace_sync, suite)

    async def count_by_project(self, project_id: str) -> int:
        return await asyncio.to_thread(self._count_by_project_sync, project_id)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._suite_table} (
                        id UUID PRIMARY KEY,
                        project_id UUID NOT NULL REFERENCES {self._settings.postgres_project_table}(id),
                        status TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._item_table} (
                        id UUID PRIMARY KEY,
                        suite_id UUID NOT NULL REFERENCES {self._suite_table}(id),
                        case_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_table}(id),
                        case_version_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_version_table}(id),
                        position INTEGER NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(suite_id, case_id, case_version_id),
                        UNIQUE(suite_id, position)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._suite_table}_project_updated "
                    f"ON {self._suite_table} (project_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_suite_position "
                    f"ON {self._item_table} (suite_id, position ASC)"
                )

    def _create_sync(
        self,
        suite: TestSuiteRecord,
        items: list[TestSuiteItemRecord],
    ) -> TestSuiteBundle:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._suite_table} (id, project_id, status, updated_at, record) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb)",
                    (
                        suite.id,
                        suite.project_id,
                        suite.status,
                        suite.updated_at,
                        self._json(suite),
                    ),
                )
                if items:
                    cur.executemany(
                        f"""
                        INSERT INTO {self._item_table} (
                            id, suite_id, case_id, case_version_id, position, record
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        [
                            (
                            item.id,
                            item.suite_id,
                            item.case_id,
                            item.case_version_id,
                            item.position,
                            self._json(item),
                            )
                            for item in items
                        ],
                    )
        return TestSuiteBundle(suite=suite, items=items)

    def _get_sync(self, suite_id: str) -> TestSuiteBundle | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._suite_table} WHERE id = %s", (suite_id,))
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    f"SELECT record FROM {self._item_table} WHERE suite_id = %s ORDER BY position ASC",
                    (suite_id,),
                )
                item_rows = cur.fetchall() or []
        return TestSuiteBundle(
            suite=self._suite_from_value(row["record"]),
            items=[self._item_from_value(item["record"]) for item in item_rows],
        )

    def _list_sync(
        self,
        project_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[TestSuiteBundle], bool]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._suite_table} WHERE project_id = %s "
                    "ORDER BY updated_at DESC, id ASC LIMIT %s OFFSET %s",
                    (project_id, limit + 1, offset),
                )
                rows = cur.fetchall() or []
                suites = [self._suite_from_value(row["record"]) for row in rows[:limit]]
                items_by_suite: dict[str, list[TestSuiteItemRecord]] = {
                    suite.id: [] for suite in suites
                }
                if suites:
                    placeholders = ", ".join(["%s"] * len(suites))
                    cur.execute(
                        f"SELECT record FROM {self._item_table} "
                        f"WHERE suite_id IN ({placeholders}) ORDER BY suite_id, position ASC",
                        tuple(suite.id for suite in suites),
                    )
                    for item_row in cur.fetchall() or []:
                        item = self._item_from_value(item_row["record"])
                        items_by_suite[item.suite_id].append(item)
        return [
            TestSuiteBundle(suite=suite, items=items_by_suite[suite.id])
            for suite in suites
        ], len(rows) > limit

    def _replace_sync(self, suite: TestSuiteRecord) -> TestSuiteRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._suite_table} SET status = %s, updated_at = %s, record = %s::jsonb WHERE id = %s",
                    (suite.status, suite.updated_at, self._json(suite), suite.id),
                )
                if cur.rowcount != 1:
                    raise KeyError(f"Test suite not found: {suite.id}")
        return suite

    def _count_by_project_sync(self, project_id: str) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM {self._suite_table} WHERE project_id = %s",
                    (project_id,),
                )
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    @staticmethod
    def _json(record) -> str:
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _suite_from_value(value) -> TestSuiteRecord:
        return TestSuiteRecord.model_validate(json.loads(value) if isinstance(value, str) else value)

    @staticmethod
    def _item_from_value(value) -> TestSuiteItemRecord:
        return TestSuiteItemRecord.model_validate(json.loads(value) if isinstance(value, str) else value)
