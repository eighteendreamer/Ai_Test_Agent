from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.run_management import (
    RunItemCompletion,
    TestCaseResultRecord,
    TestRunAttemptRecord,
    TestRunDetail,
    TestRunItemRecord,
    TestRunRecord,
    TestRunStats,
    TestRunStatus,
)


TERMINAL_ITEM_STATUSES = {
    "passed",
    "failed",
    "error",
    "blocked",
    "skipped",
    "cancelled",
}
ACTIVE_ITEM_STATUSES = {"claimed", "running"}


class TestRunStore(Protocol):
    async def initialize(self) -> None: ...
    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail: ...
    async def get_run(self, run_id: str) -> TestRunDetail | None: ...
    async def get_run_record(self, run_id: str) -> TestRunRecord | None: ...
    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]: ...
    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]: ...
    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord: ...
    async def recover_expired(self, run_id: str, now: datetime) -> int: ...
    async def recover_all_expired(self, now: datetime) -> int: ...
    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail: ...
    async def count_by_project(self, project_id: str) -> int: ...


class InMemoryTestRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, TestRunRecord] = {}
        self._items: dict[str, TestRunItemRecord] = {}
        self._item_ids_by_run: dict[str, list[str]] = {}
        self._attempts: dict[str, TestRunAttemptRecord] = {}
        self._attempt_ids_by_run: dict[str, list[str]] = {}
        self._results: dict[str, TestCaseResultRecord] = {}
        self._result_ids_by_run: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        async with self._lock:
            if run.id in self._runs:
                raise ValueError(f"Test run already exists: {run.id}")
            stored_run = run.model_copy(deep=True)
            stored_items = [item.model_copy(deep=True) for item in items]
            self._runs[run.id] = stored_run
            self._item_ids_by_run[run.id] = [item.id for item in stored_items]
            self._attempt_ids_by_run[run.id] = []
            self._result_ids_by_run[run.id] = []
            for item in stored_items:
                self._items[item.id] = item
            self._refresh_run(run.id, run.created_at)
            return self._detail(run.id)

    async def get_run(self, run_id: str) -> TestRunDetail | None:
        if run_id not in self._runs:
            return None
        return self._detail(run_id)

    async def get_run_record(self, run_id: str) -> TestRunRecord | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        runs = [run for run in self._runs.values() if run.project_id == project_id]
        if status:
            runs = [run for run in runs if run.status == status]
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        selected = runs[offset : offset + limit + 1]
        return [run.model_copy(deep=True) for run in selected[:limit]], len(selected) > limit

    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status in {"completed", "cancelled"}:
                return []
            queued = sorted(
                (
                    self._items[item_id]
                    for item_id in self._item_ids_by_run[run_id]
                    if self._items[item_id].status == "queued"
                ),
                key=lambda item: (item.position, item.id),
            )[:limit]
            claimed: list[tuple[TestRunItemRecord, TestRunAttemptRecord]] = []
            for item in queued:
                token = str(uuid4())
                attempt_no = item.attempt_no + 1
                attempt = TestRunAttemptRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    run_item_id=item.id,
                    attempt_no=attempt_no,
                    worker_id=worker_id,
                    lease_token=token,
                    claimed_at=now,
                )
                stored_item = item.model_copy(
                    deep=True,
                    update={
                        "status": "claimed",
                        "attempt_no": attempt_no,
                        "lease_owner": worker_id,
                        "lease_token": token,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "heartbeat_at": now,
                        "updated_at": now,
                    },
                )
                self._items[item.id] = stored_item
                self._attempts[attempt.id] = attempt
                self._attempt_ids_by_run[run_id].append(attempt.id)
                claimed.append((stored_item.model_copy(deep=True), attempt.model_copy(deep=True)))
            self._refresh_run(run_id, now)
            return claimed

    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(item_id, lease_token, now, {"claimed"})
            updated = item.model_copy(
                deep=True,
                update={"status": "running", "started_at": now, "updated_at": now},
            )
            self._items[item_id] = updated
            attempt = self._active_attempt(item_id, lease_token)
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={"status": "running", "started_at": now, "heartbeat_at": now},
            )
            self._refresh_run(item.run_id, now)
            return updated.model_copy(deep=True)

    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(
                item_id,
                lease_token,
                now,
                {"claimed", "running"},
            )
            updated = item.model_copy(
                deep=True,
                update={
                    "heartbeat_at": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
            )
            self._items[item_id] = updated
            attempt = self._active_attempt(item_id, lease_token)
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={"heartbeat_at": now},
            )
            return updated.model_copy(deep=True)

    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Test run item not found: {item_id}")
            if item.result_id:
                existing = self._results[item.result_id]
                attempt = self._attempts[existing.attempt_id]
                if attempt.lease_token == lease_token and existing.payload_hash == completion.payload_hash:
                    return existing.model_copy(deep=True)
                raise ValueError(f"Test run item already completed: {item_id}")
            item = self._require_active_lease(item_id, lease_token, now, {"running"})
            attempt = self._active_attempt(item_id, lease_token)
            result = TestCaseResultRecord(
                id=str(uuid4()),
                run_id=item.run_id,
                run_item_id=item.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                regression_source_result_id=item.regression_source_result_id,
                attempt_id=attempt.id,
                attempt_no=attempt.attempt_no,
                created_at=now,
                **completion.model_dump(mode="python"),
            )
            self._results[result.id] = result
            self._result_ids_by_run[item.run_id].append(result.id)
            self._items[item_id] = item.model_copy(
                deep=True,
                update={
                    "status": completion.status,
                    "result_id": result.id,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={"status": completion.status, "completed_at": now},
            )
            self._refresh_run(item.run_id, now)
            return result.model_copy(deep=True)

    async def recover_expired(self, run_id: str, now: datetime) -> int:
        async with self._lock:
            self._require_run(run_id)
            return self._recover_expired_unlocked(run_id, now)

    async def recover_all_expired(self, now: datetime) -> int:
        async with self._lock:
            return sum(
                self._recover_expired_unlocked(run_id, now)
                for run_id in list(self._runs)
            )

    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status == "completed":
                raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
            if run.status == "cancelled":
                return self._detail(run_id)
            for item_id in self._item_ids_by_run[run_id]:
                item = self._items[item_id]
                if item.status in TERMINAL_ITEM_STATUSES:
                    continue
                if item.lease_token and item.status in ACTIVE_ITEM_STATUSES:
                    attempt = self._active_attempt(item_id, item.lease_token)
                    self._attempts[attempt.id] = attempt.model_copy(
                        deep=True,
                        update={"status": "cancelled", "completed_at": now},
                    )
                self._items[item_id] = item.model_copy(
                    deep=True,
                    update={"status": "cancelled", "completed_at": now, "updated_at": now},
                )
            self._runs[run_id] = run.model_copy(
                deep=True,
                update={
                    "status": "cancelled",
                    "cancel_reason": reason,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            self._refresh_run(run_id, now, preserve_cancelled=True)
            return self._detail(run_id)

    async def count_by_project(self, project_id: str) -> int:
        return sum(1 for run in self._runs.values() if run.project_id == project_id)

    def _require_run(self, run_id: str) -> TestRunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Test run not found: {run_id}")
        return run

    def _recover_expired_unlocked(self, run_id: str, now: datetime) -> int:
        recovered = 0
        for item_id in self._item_ids_by_run[run_id]:
            item = self._items[item_id]
            if (
                item.status in ACTIVE_ITEM_STATUSES
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                if item.lease_token:
                    attempt = self._active_attempt(item_id, item.lease_token)
                    self._attempts[attempt.id] = attempt.model_copy(
                        deep=True,
                        update={"status": "expired", "completed_at": now},
                    )
                self._items[item_id] = item.model_copy(
                    deep=True,
                    update={
                        "status": "queued",
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                        "heartbeat_at": None,
                        "updated_at": now,
                        "started_at": None,
                    },
                )
                recovered += 1
        if recovered:
            self._refresh_run(run_id, now)
        return recovered

    def _require_active_lease(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
        allowed_statuses: set[str],
    ) -> TestRunItemRecord:
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"Test run item not found: {item_id}")
        if item.status not in allowed_statuses:
            raise ValueError(
                f"Illegal run item transition from {item.status}: {item_id}"
            )
        if item.lease_token != lease_token:
            raise ValueError(f"Invalid lease token for run item: {item_id}")
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            raise ValueError(f"Lease expired for run item: {item_id}")
        return item

    def _active_attempt(
        self,
        item_id: str,
        lease_token: str,
    ) -> TestRunAttemptRecord:
        attempt = next(
            (
                self._attempts[attempt_id]
                for attempt_id in reversed(
                    self._attempt_ids_by_run[self._items[item_id].run_id]
                )
                if self._attempts[attempt_id].run_item_id == item_id
                and self._attempts[attempt_id].lease_token == lease_token
            ),
            None,
        )
        if attempt is None:
            raise ValueError(f"Active attempt not found for run item: {item_id}")
        return attempt

    def _refresh_run(
        self,
        run_id: str,
        now: datetime,
        *,
        preserve_cancelled: bool = False,
    ) -> None:
        run = self._runs[run_id]
        items = [self._items[item_id] for item_id in self._item_ids_by_run[run_id]]
        counts = {status: 0 for status in TestRunStats.model_fields if status != "total"}
        for item in items:
            counts[item.status] += 1
        stats = TestRunStats(total=len(items), **counts)
        all_terminal = bool(items) and all(
            item.status in TERMINAL_ITEM_STATUSES for item in items
        )
        if preserve_cancelled or run.status == "cancelled":
            status = "cancelled"
        elif all_terminal:
            status = "completed"
        elif any(item.status != "queued" for item in items):
            status = "running"
        else:
            status = "queued"
        started_at = run.started_at
        if status == "running" and started_at is None:
            started_at = now
        completed_at = run.completed_at
        if status == "completed" and completed_at is None:
            completed_at = now
        self._runs[run_id] = run.model_copy(
            deep=True,
            update={
                "status": status,
                "stats": stats,
                "started_at": started_at,
                "completed_at": completed_at,
                "updated_at": now,
            },
        )

    def _detail(self, run_id: str) -> TestRunDetail:
        return TestRunDetail(
            run=self._runs[run_id].model_copy(deep=True),
            items=[
                self._items[item_id].model_copy(deep=True)
                for item_id in self._item_ids_by_run.get(run_id, [])
            ],
            attempts=[
                self._attempts[attempt_id].model_copy(deep=True)
                for attempt_id in self._attempt_ids_by_run.get(run_id, [])
            ],
            results=[
                self._results[result_id].model_copy(deep=True)
                for result_id in self._result_ids_by_run.get(run_id, [])
            ],
        )


class PostgresTestRunStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _run_table(self) -> str:
        return self._settings.postgres_test_run_table

    @property
    def _item_table(self) -> str:
        return self._settings.postgres_test_run_item_table

    @property
    def _attempt_table(self) -> str:
        return self._settings.postgres_test_run_attempt_table

    @property
    def _result_table(self) -> str:
        return self._settings.postgres_test_case_result_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        return await asyncio.to_thread(self._create_run_sync, run, items)

    async def get_run(self, run_id: str) -> TestRunDetail | None:
        return await asyncio.to_thread(self._get_run_sync, run_id)

    async def get_run_record(self, run_id: str) -> TestRunRecord | None:
        return await asyncio.to_thread(self._get_run_record_sync, run_id)

    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        return await asyncio.to_thread(
            self._list_runs_sync,
            project_id,
            status,
            limit,
            offset,
        )

    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        return await asyncio.to_thread(
            self._claim_items_sync,
            run_id,
            worker_id,
            limit,
            lease_seconds,
            now,
        )

    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._start_item_sync,
            item_id,
            lease_token,
            now,
        )

    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._heartbeat_item_sync,
            item_id,
            lease_token,
            lease_seconds,
            now,
        )

    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        return await asyncio.to_thread(
            self._complete_item_sync,
            item_id,
            lease_token,
            completion,
            now,
        )

    async def recover_expired(self, run_id: str, now: datetime) -> int:
        return await asyncio.to_thread(self._recover_expired_sync, run_id, now)

    async def recover_all_expired(self, now: datetime) -> int:
        return await asyncio.to_thread(self._recover_all_expired_sync, now)

    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        return await asyncio.to_thread(self._cancel_run_sync, run_id, reason, now)

    async def count_by_project(self, project_id: str) -> int:
        return await asyncio.to_thread(self._count_by_project_sync, project_id)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._run_table} (
                        id UUID PRIMARY KEY,
                        project_id UUID NOT NULL REFERENCES {self._settings.postgres_project_table}(id),
                        suite_id UUID NOT NULL REFERENCES {self._settings.postgres_test_suite_table}(id),
                        status TEXT NOT NULL,
                        mode_key TEXT NOT NULL,
                        session_id TEXT NULL,
                        parent_run_id UUID NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._item_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        case_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_table}(id),
                        case_version_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_version_table}(id),
                        position INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        attempt_no INTEGER NOT NULL DEFAULT 0,
                        lease_owner TEXT NULL,
                        lease_token TEXT NULL,
                        lease_expires_at TIMESTAMPTZ NULL,
                        result_id UUID NULL,
                        regression_source_result_id UUID NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_id, position),
                        UNIQUE(run_id, case_id, case_version_id)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._attempt_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        run_item_id UUID NOT NULL REFERENCES {self._item_table}(id),
                        attempt_no INTEGER NOT NULL,
                        lease_token TEXT NOT NULL,
                        status TEXT NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_item_id, attempt_no),
                        UNIQUE(lease_token)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._result_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        run_item_id UUID NOT NULL REFERENCES {self._item_table}(id),
                        case_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_table}(id),
                        case_version_id UUID NOT NULL REFERENCES {self._settings.postgres_test_case_version_table}(id),
                        attempt_id UUID NOT NULL REFERENCES {self._attempt_table}(id),
                        status TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        regression_source_result_id UUID NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_item_id)
                    )
                    """
                )
                cur.execute(
                    f"ALTER TABLE {self._run_table} "
                    "ADD COLUMN IF NOT EXISTS parent_run_id UUID NULL"
                )
                cur.execute(
                    f"ALTER TABLE {self._item_table} "
                    "ADD COLUMN IF NOT EXISTS regression_source_result_id UUID NULL"
                )
                cur.execute(
                    f"ALTER TABLE {self._result_table} "
                    "ADD COLUMN IF NOT EXISTS regression_source_result_id UUID NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._run_table}_project_created "
                    f"ON {self._run_table} (project_id, created_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._run_table}_parent_run "
                    f"ON {self._run_table} (parent_run_id) WHERE parent_run_id IS NOT NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_run_status_position "
                    f"ON {self._item_table} (run_id, status, position ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_lease_expiry "
                    f"ON {self._item_table} (run_id, lease_expires_at) "
                    "WHERE status IN ('claimed', 'running')"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_regression_source "
                    f"ON {self._item_table} (regression_source_result_id) "
                    "WHERE regression_source_result_id IS NOT NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._attempt_table}_run_item "
                    f"ON {self._attempt_table} (run_id, run_item_id, attempt_no ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._result_table}_regression_source "
                    f"ON {self._result_table} (regression_source_result_id) "
                    "WHERE regression_source_result_id IS NOT NULL"
                )

    def _create_run_sync(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._run_table} "
                    "(id, project_id, suite_id, status, mode_key, session_id, parent_run_id, "
                    "created_at, updated_at, record) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        run.id,
                        run.project_id,
                        run.suite_id,
                        run.status,
                        run.mode_key,
                        run.session_id,
                        run.parent_run_id,
                        run.created_at,
                        run.updated_at,
                        self._json(run),
                    ),
                )
                if items:
                    cur.executemany(
                        f"INSERT INTO {self._item_table} "
                        "(id, run_id, case_id, case_version_id, position, status, attempt_no, "
                        "lease_owner, lease_token, lease_expires_at, result_id, "
                        "regression_source_result_id, updated_at, record) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, "
                        "%s, %s, %s::jsonb)",
                        [
                            (
                                item.id,
                                item.run_id,
                                item.case_id,
                                item.case_version_id,
                                item.position,
                                item.status,
                                item.attempt_no,
                                item.regression_source_result_id,
                                item.updated_at,
                                self._json(item),
                            )
                            for item in items
                        ],
                    )
        return TestRunDetail(run=run, items=items)

    def _get_run_sync(self, run_id: str) -> TestRunDetail | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                return self._detail_from_cursor(cur, run_id)

    def _get_run_record_sync(self, run_id: str) -> TestRunRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._run_table} WHERE id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        return self._run_from_value(row["record"]) if row else None

    def _list_runs_sync(
        self,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        clauses = ["project_id = %s"]
        params: list[object] = [project_id]
        if status:
            clauses.append("status = %s")
            params.append(status)
        params.extend([limit + 1, offset])
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._run_table} "
                    f"WHERE {' AND '.join(clauses)} "
                    "ORDER BY created_at DESC, id ASC LIMIT %s OFFSET %s",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        runs = [self._run_from_value(row["record"]) for row in rows]
        return runs[:limit], len(runs) > limit

    def _claim_items_sync(
        self,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                run = self._read_run(cur, run_id)
                if run.status in {"completed", "cancelled"}:
                    return []
                cur.execute(
                    f"SELECT record FROM {self._item_table} "
                    "WHERE run_id = %s AND status = 'queued' "
                    "ORDER BY position ASC, id ASC "
                    "FOR UPDATE SKIP LOCKED LIMIT %s",
                    (run_id, limit),
                )
                candidates = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                claimed: list[tuple[TestRunItemRecord, TestRunAttemptRecord]] = []
                for item in candidates:
                    token = str(uuid4())
                    attempt_no = item.attempt_no + 1
                    attempt = TestRunAttemptRecord(
                        id=str(uuid4()),
                        run_id=run_id,
                        run_item_id=item.id,
                        attempt_no=attempt_no,
                        worker_id=worker_id,
                        lease_token=token,
                        claimed_at=now,
                    )
                    updated = item.model_copy(
                        update={
                            "status": "claimed",
                            "attempt_no": attempt_no,
                            "lease_owner": worker_id,
                            "lease_token": token,
                            "lease_expires_at": now + timedelta(seconds=lease_seconds),
                            "heartbeat_at": now,
                            "updated_at": now,
                        }
                    )
                    claimed.append((updated, attempt))
                if claimed:
                    cur.executemany(
                        f"UPDATE {self._item_table} SET status = %s, attempt_no = %s, "
                        "lease_owner = %s, lease_token = %s, lease_expires_at = %s, "
                        "updated_at = %s, record = %s::jsonb WHERE id = %s",
                        [
                            (
                                item.status,
                                item.attempt_no,
                                item.lease_owner,
                                item.lease_token,
                                item.lease_expires_at,
                                item.updated_at,
                                self._json(item),
                                item.id,
                            )
                            for item, _ in claimed
                        ],
                    )
                    cur.executemany(
                        f"INSERT INTO {self._attempt_table} "
                        "(id, run_id, run_item_id, attempt_no, lease_token, status, record) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                        [
                            (
                                attempt.id,
                                attempt.run_id,
                                attempt.run_item_id,
                                attempt.attempt_no,
                                attempt.lease_token,
                                attempt.status,
                                self._json(attempt),
                            )
                            for _, attempt in claimed
                        ],
                    )
                    self._refresh_run_in_cursor(cur, run_id, now)
        return claimed

    def _start_item_sync(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                self._validate_lease(item, lease_token, now, {"claimed"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                item = item.model_copy(
                    update={"status": "running", "started_at": now, "updated_at": now}
                )
                attempt = attempt.model_copy(
                    update={"status": "running", "started_at": now, "heartbeat_at": now}
                )
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
                self._refresh_run_in_cursor(cur, item.run_id, now)
        return item

    def _heartbeat_item_sync(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                self._validate_lease(item, lease_token, now, {"claimed", "running"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                item = item.model_copy(
                    update={
                        "heartbeat_at": now,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "updated_at": now,
                    }
                )
                attempt = attempt.model_copy(update={"heartbeat_at": now})
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
        return item

    def _complete_item_sync(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                if item.result_id:
                    result = self._get_result_in_cursor(cur, item.result_id)
                    attempt = self._get_attempt_by_id(cur, result.attempt_id)
                    if (
                        attempt.lease_token == lease_token
                        and result.payload_hash == completion.payload_hash
                    ):
                        return result
                    raise ValueError(f"Test run item already completed: {item_id}")
                self._validate_lease(item, lease_token, now, {"running"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                result = TestCaseResultRecord(
                    id=str(uuid4()),
                    run_id=item.run_id,
                    run_item_id=item.id,
                    case_id=item.case_id,
                    case_version_id=item.case_version_id,
                    regression_source_result_id=item.regression_source_result_id,
                    attempt_id=attempt.id,
                    attempt_no=attempt.attempt_no,
                    created_at=now,
                    **completion.model_dump(mode="python"),
                )
                cur.execute(
                    f"INSERT INTO {self._result_table} "
                    "(id, run_id, run_item_id, case_id, case_version_id, "
                    "regression_source_result_id, attempt_id, status, payload_hash, "
                    "created_at, record) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        result.id,
                        result.run_id,
                        result.run_item_id,
                        result.case_id,
                        result.case_version_id,
                        result.regression_source_result_id,
                        result.attempt_id,
                        result.status,
                        result.payload_hash,
                        result.created_at,
                        self._json(result),
                    ),
                )
                item = item.model_copy(
                    update={
                        "status": completion.status,
                        "result_id": result.id,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
                attempt = attempt.model_copy(
                    update={"status": completion.status, "completed_at": now}
                )
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
                self._refresh_run_in_cursor(cur, item.run_id, now)
        return result

    def _recover_expired_sync(self, run_id: str, now: datetime) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._read_run(cur, run_id)
                cur.execute(
                    f"SELECT record FROM {self._item_table} "
                    "WHERE run_id = %s AND status IN ('claimed', 'running') "
                    "AND lease_expires_at <= %s FOR UPDATE SKIP LOCKED",
                    (run_id, now),
                )
                expired = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                attempts: list[TestRunAttemptRecord] = []
                queued: list[TestRunItemRecord] = []
                for item in expired:
                    if item.lease_token:
                        attempt = self._lock_attempt(cur, item.id, item.lease_token)
                        attempts.append(
                            attempt.model_copy(
                                update={"status": "expired", "completed_at": now}
                            )
                        )
                    queued.append(
                        item.model_copy(
                            update={
                                "status": "queued",
                                "lease_owner": None,
                                "lease_token": None,
                                "lease_expires_at": None,
                                "heartbeat_at": None,
                                "started_at": None,
                                "updated_at": now,
                            }
                        )
                    )
                for item in queued:
                    self._write_item(cur, item)
                for attempt in attempts:
                    self._write_attempt(cur, attempt)
                if expired:
                    self._refresh_run_in_cursor(cur, run_id, now)
        return len(expired)

    def _recover_all_expired_sync(self, now: datetime) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT run_id FROM {self._item_table} "
                    "WHERE status IN ('claimed', 'running') AND lease_expires_at <= %s",
                    (now,),
                )
                run_ids = [str(row["run_id"]) for row in (cur.fetchall() or [])]
        return sum(self._recover_expired_sync(run_id, now) for run_id in run_ids)

    def _cancel_run_sync(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                run = self._read_run(cur, run_id)
                if run.status == "completed":
                    raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
                if run.status == "cancelled":
                    detail = self._detail_from_cursor(cur, run_id)
                    if detail is None:
                        raise KeyError(f"Test run not found: {run_id}")
                    return detail
                cur.execute(
                    f"SELECT record FROM {self._item_table} "
                    "WHERE run_id = %s AND status NOT IN "
                    "('passed', 'failed', 'error', 'blocked', 'skipped', 'cancelled') "
                    "FOR UPDATE",
                    (run_id,),
                )
                items = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                run = self._lock_run(cur, run_id)
                if run.status == "completed":
                    raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
                if run.status == "cancelled":
                    detail = self._detail_from_cursor(cur, run_id)
                    if detail is None:
                        raise KeyError(f"Test run not found: {run_id}")
                    return detail
                for item in items:
                    if item.lease_token and item.status in ACTIVE_ITEM_STATUSES:
                        attempt = self._lock_attempt(cur, item.id, item.lease_token)
                        self._write_attempt(
                            cur,
                            attempt.model_copy(
                                update={"status": "cancelled", "completed_at": now}
                            ),
                        )
                    self._write_item(
                        cur,
                        item.model_copy(
                            update={
                                "status": "cancelled",
                                "completed_at": now,
                                "updated_at": now,
                            }
                        ),
                    )
                cancelled = run.model_copy(
                    update={
                        "status": "cancelled",
                        "cancel_reason": reason,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
                self._write_run(cur, cancelled)
                self._refresh_run_in_cursor(
                    cur,
                    run_id,
                    now,
                    preserve_cancelled=True,
                )
                detail = self._detail_from_cursor(cur, run_id)
                if detail is None:
                    raise KeyError(f"Test run not found: {run_id}")
                return detail

    def _count_by_project_sync(self, project_id: str) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM {self._run_table} WHERE project_id = %s",
                    (project_id,),
                )
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    def _detail_from_cursor(self, cur, run_id: str) -> TestRunDetail | None:
        cur.execute(f"SELECT record FROM {self._run_table} WHERE id = %s", (run_id,))
        run_row = cur.fetchone()
        if not run_row:
            return None
        cur.execute(
            f"SELECT record FROM {self._item_table} WHERE run_id = %s ORDER BY position ASC",
            (run_id,),
        )
        item_rows = cur.fetchall() or []
        cur.execute(
            f"SELECT record FROM {self._attempt_table} "
            "WHERE run_id = %s ORDER BY run_item_id, attempt_no ASC",
            (run_id,),
        )
        attempt_rows = cur.fetchall() or []
        cur.execute(
            f"SELECT record FROM {self._result_table} WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
        result_rows = cur.fetchall() or []
        return TestRunDetail(
            run=self._run_from_value(run_row["record"]),
            items=[self._item_from_value(row["record"]) for row in item_rows],
            attempts=[self._attempt_from_value(row["record"]) for row in attempt_rows],
            results=[self._result_from_value(row["record"]) for row in result_rows],
        )

    def _lock_run(self, cur, run_id: str) -> TestRunRecord:
        cur.execute(
            f"SELECT record FROM {self._run_table} WHERE id = %s FOR UPDATE",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run not found: {run_id}")
        return self._run_from_value(row["record"])

    def _read_run(self, cur, run_id: str) -> TestRunRecord:
        cur.execute(
            f"SELECT record FROM {self._run_table} WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run not found: {run_id}")
        return self._run_from_value(row["record"])

    def _lock_item(self, cur, item_id: str) -> TestRunItemRecord:
        cur.execute(
            f"SELECT record FROM {self._item_table} WHERE id = %s FOR UPDATE",
            (item_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run item not found: {item_id}")
        return self._item_from_value(row["record"])

    def _lock_attempt(
        self,
        cur,
        item_id: str,
        lease_token: str,
    ) -> TestRunAttemptRecord:
        cur.execute(
            f"SELECT record FROM {self._attempt_table} "
            "WHERE run_item_id = %s AND lease_token = %s "
            "ORDER BY attempt_no DESC LIMIT 1 FOR UPDATE",
            (item_id, lease_token),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Active attempt not found for run item: {item_id}")
        return self._attempt_from_value(row["record"])

    def _get_attempt_by_id(self, cur, attempt_id: str) -> TestRunAttemptRecord:
        cur.execute(
            f"SELECT record FROM {self._attempt_table} WHERE id = %s",
            (attempt_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run attempt not found: {attempt_id}")
        return self._attempt_from_value(row["record"])

    def _get_result_in_cursor(self, cur, result_id: str) -> TestCaseResultRecord:
        cur.execute(
            f"SELECT record FROM {self._result_table} WHERE id = %s",
            (result_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test case result not found: {result_id}")
        return self._result_from_value(row["record"])

    @staticmethod
    def _validate_lease(
        item: TestRunItemRecord,
        lease_token: str,
        now: datetime,
        allowed_statuses: set[str],
    ) -> None:
        if item.status not in allowed_statuses:
            raise ValueError(
                f"Illegal run item transition from {item.status}: {item.id}"
            )
        if item.lease_token != lease_token:
            raise ValueError(f"Invalid lease token for run item: {item.id}")
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            raise ValueError(f"Lease expired for run item: {item.id}")

    def _refresh_run_in_cursor(
        self,
        cur,
        run_id: str,
        now: datetime,
        *,
        preserve_cancelled: bool = False,
    ) -> TestRunRecord:
        run = self._lock_run(cur, run_id)
        cur.execute(
            f"SELECT status, count(*) AS total FROM {self._item_table} "
            "WHERE run_id = %s GROUP BY status",
            (run_id,),
        )
        rows = cur.fetchall() or []
        counts = {status: 0 for status in TestRunStats.model_fields if status != "total"}
        total = 0
        for row in rows:
            status = str(row["status"])
            count = int(row["total"])
            if status in counts:
                counts[status] = count
            total += count
        stats = TestRunStats(total=total, **counts)
        terminal_count = sum(counts[status] for status in TERMINAL_ITEM_STATUSES)
        if preserve_cancelled or run.status == "cancelled":
            status: TestRunStatus = "cancelled"
        elif total > 0 and terminal_count == total:
            status = "completed"
        elif total > counts["queued"]:
            status = "running"
        else:
            status = "queued"
        started_at = run.started_at
        if status == "running" and started_at is None:
            started_at = now
        completed_at = run.completed_at
        if status == "completed" and completed_at is None:
            completed_at = now
        updated = run.model_copy(
            update={
                "status": status,
                "stats": stats,
                "started_at": started_at,
                "completed_at": completed_at,
                "updated_at": now,
            }
        )
        self._write_run(cur, updated)
        return updated

    def _write_run(self, cur, run: TestRunRecord) -> None:
        cur.execute(
            f"UPDATE {self._run_table} SET status = %s, parent_run_id = %s, "
            "updated_at = %s, record = %s::jsonb WHERE id = %s",
            (
                run.status,
                run.parent_run_id,
                run.updated_at,
                self._json(run),
                run.id,
            ),
        )

    def _write_item(self, cur, item: TestRunItemRecord) -> None:
        cur.execute(
            f"UPDATE {self._item_table} SET status = %s, attempt_no = %s, "
            "lease_owner = %s, lease_token = %s, lease_expires_at = %s, "
            "result_id = %s, regression_source_result_id = %s, updated_at = %s, "
            "record = %s::jsonb WHERE id = %s",
            (
                item.status,
                item.attempt_no,
                item.lease_owner,
                item.lease_token,
                item.lease_expires_at,
                item.result_id,
                item.regression_source_result_id,
                item.updated_at,
                self._json(item),
                item.id,
            ),
        )

    def _write_attempt(self, cur, attempt: TestRunAttemptRecord) -> None:
        cur.execute(
            f"UPDATE {self._attempt_table} SET status = %s, record = %s::jsonb WHERE id = %s",
            (attempt.status, self._json(attempt), attempt.id),
        )

    @staticmethod
    def _json(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _value(value):
        return json.loads(value) if isinstance(value, str) else value

    @classmethod
    def _run_from_value(cls, value) -> TestRunRecord:
        return TestRunRecord.model_validate(cls._value(value))

    @classmethod
    def _item_from_value(cls, value) -> TestRunItemRecord:
        return TestRunItemRecord.model_validate(cls._value(value))

    @classmethod
    def _attempt_from_value(cls, value) -> TestRunAttemptRecord:
        return TestRunAttemptRecord.model_validate(cls._value(value))

    @classmethod
    def _result_from_value(cls, value) -> TestCaseResultRecord:
        return TestCaseResultRecord.model_validate(cls._value(value))
