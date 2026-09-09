from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import math
import platform
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import PostgresTestRunStore
from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.run_management import (
    RunItemCompletion,
    TestRunItemRecord as _TestRunItemRecord,
    TestRunRecord as _TestRunRecord,
)
from tests.live_postgres_config import LivePostgresTestConfig


LIVE_CONFIG = LivePostgresTestConfig()
LIVE_CAPACITY = LIVE_CONFIG.run_live_postgres_capacity
capacity = pytest.mark.skipif(
    not LIVE_CAPACITY,
    reason="set RUN_LIVE_POSTGRES_CAPACITY=1 to run the PostgreSQL capacity benchmark",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _tables(settings: Settings, names: list[str]) -> None:
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            for name in names:
                cur.execute(f"DROP TABLE IF EXISTS {name}")


def _settings_with_database_overrides(**overrides: object) -> Settings:
    """Apply live-test table overrides to the nested DatabaseConfig contract."""
    base = Settings()
    database = base.database.model_copy(update=overrides)
    return base.model_copy(update={"database": database})


def _current_rss_bytes() -> int | None:
    """Read current-process RSS without adding a runtime dependency."""
    if platform.system() == "Windows":
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if ok else None
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, ImportError, OSError):
        return None
    return value * (1024 if platform.system() != "Darwin" else 1)


def _postgres_connection_count(settings: Settings) -> int | None:
    try:
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM pg_stat_activity "
                    "WHERE datname = current_database()"
                )
                row = cur.fetchone()
        return int(row["total"]) if row else None
    except Exception:
        return None


def _create_tables(settings: Settings) -> None:
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_table} ("
                "id UUID PRIMARY KEY, project_id UUID NOT NULL, suite_id UUID NOT NULL, "
                "status TEXT NOT NULL, mode_key TEXT NOT NULL, session_id TEXT NULL, "
                "parent_run_id UUID NULL, created_at TIMESTAMPTZ NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL)"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_item_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, case_id UUID NOT NULL, "
                "case_version_id UUID NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL, "
                "attempt_no INTEGER NOT NULL, lease_owner TEXT NULL, lease_token TEXT NULL, "
                "lease_expires_at TIMESTAMPTZ NULL, result_id UUID NULL, "
                "regression_source_result_id UUID NULL, updated_at TIMESTAMPTZ NOT NULL, "
                "record JSONB NOT NULL, UNIQUE(run_id, position), "
                "UNIQUE(run_id, case_id, case_version_id))"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_attempt_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL, "
                "attempt_no INTEGER NOT NULL, lease_token TEXT NOT NULL, status TEXT NOT NULL, "
                "record JSONB NOT NULL, UNIQUE(run_item_id, attempt_no), UNIQUE(lease_token))"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_case_result_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL, "
                "case_id UUID NOT NULL, case_version_id UUID NOT NULL, attempt_id UUID NOT NULL, "
                "status TEXT NOT NULL, payload_hash TEXT NOT NULL, "
                "regression_source_result_id UUID NULL, created_at TIMESTAMPTZ NOT NULL, "
                "record JSONB NOT NULL, UNIQUE(run_item_id))"
            )
            cur.execute(
                f"CREATE INDEX idx_{settings.database.postgres_test_run_item_table}_run_status_position "
                f"ON {settings.database.postgres_test_run_item_table} (run_id, status, position ASC)"
            )
            cur.execute(
                f"CREATE INDEX idx_{settings.database.postgres_test_run_attempt_table}_run_item "
                f"ON {settings.database.postgres_test_run_attempt_table} (run_id, run_item_id, attempt_no DESC)"
            )
            cur.execute(
                f"CREATE INDEX idx_{settings.database.postgres_test_case_result_table}_run_status "
                f"ON {settings.database.postgres_test_case_result_table} (run_id, status, created_at DESC)"
            )
            cur.execute(
                f"ANALYZE {settings.database.postgres_test_run_item_table}"
            )


@capacity
@pytest.mark.asyncio
async def test_live_postgres_full_lifecycle_capacity():
    raw_sizes = LIVE_CONFIG.run_live_postgres_capacity_sizes
    sizes = [int(value.strip()) for value in raw_sizes.split(",") if value.strip()]
    workers = max(1, LIVE_CONFIG.run_live_postgres_capacity_workers)
    for size in sizes:
        suffix = f"{size}_{uuid4().hex[:8]}"
        settings = _settings_with_database_overrides(
            postgres_pool_size=min(max(workers, 4), 64),
            postgres_test_run_table=f"cap_run_{suffix}",
            postgres_test_run_item_table=f"cap_item_{suffix}",
            postgres_test_run_attempt_table=f"cap_attempt_{suffix}",
            postgres_test_case_result_table=f"cap_result_{suffix}",
        )
        store = PostgresTestRunStore(settings)
        now = datetime.now(timezone.utc)
        run = _TestRunRecord(
            id=str(uuid4()),
            project_id=str(uuid4()),
            suite_id=str(uuid4()),
            mode_key="api_testing",
            stats={"total": size, "queued": size},
            created_at=now,
            updated_at=now,
        )
        items = [
            _TestRunItemRecord(
                id=str(uuid4()),
                run_id=run.id,
                case_id=str(uuid4()),
                case_version_id=str(uuid4()),
                position=index,
                created_at=now,
                updated_at=now,
            )
            for index in range(1, size + 1)
        ]
        latencies: dict[str, list[float]] = {key: [] for key in ("claim", "start", "heartbeat", "complete")}
        claimed = 0
        started = perf_counter()
        try:
            _create_tables(settings)
            await store.create_run(run, items)
            lock = asyncio.Lock()

            async def worker(worker_number: int) -> None:
                nonlocal claimed
                while True:
                    begin = perf_counter()
                    claims = await store.claim_items(
                        run_id=run.id,
                        worker_id=f"capacity-worker-{worker_number}",
                        limit=32,
                        lease_seconds=300,
                        now=datetime.now(timezone.utc),
                    )
                    latencies["claim"].append((perf_counter() - begin) * 1000)
                    if not claims:
                        return
                    async with lock:
                        claimed += len(claims)
                    for item, attempt in claims:
                        begin = perf_counter()
                        await store.start_item(item.id, attempt.lease_token, datetime.now(timezone.utc))
                        latencies["start"].append((perf_counter() - begin) * 1000)
                        begin = perf_counter()
                        await store.heartbeat_item(
                            item.id,
                            attempt.lease_token,
                            300,
                            datetime.now(timezone.utc),
                        )
                        latencies["heartbeat"].append((perf_counter() - begin) * 1000)
                        completion = RunItemCompletion(
                            status="passed",
                            summary="capacity benchmark passed",
                            actual={"capacity": True},
                            payload_hash=hashlib.sha256(
                                json.dumps({"item_id": item.id}, sort_keys=True).encode()
                            ).hexdigest(),
                        )
                        begin = perf_counter()
                        await store.complete_item(
                            item.id,
                            attempt.lease_token,
                            completion,
                            datetime.now(timezone.utc),
                        )
                        latencies["complete"].append((perf_counter() - begin) * 1000)

            await asyncio.gather(*(worker(index) for index in range(workers)))
            elapsed = perf_counter() - started
            with postgres_connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) AS total FROM {settings.database.postgres_test_run_attempt_table}")
                    attempt_count = int(cur.fetchone()["total"])
                    cur.execute(f"SELECT COUNT(*) AS total FROM {settings.database.postgres_test_case_result_table}")
                    result_count = int(cur.fetchone()["total"])
                    cur.execute(f"SELECT status, COUNT(*) AS total FROM {settings.database.postgres_test_run_item_table} GROUP BY status")
                    status_counts = {row["status"]: int(row["total"]) for row in cur.fetchall()}
                    cur.execute(
                        f"SELECT status, record FROM {settings.database.postgres_test_run_table} WHERE id = %s",
                        (run.id,),
                    )
                    run_row = cur.fetchone()
            assert claimed == size
            assert attempt_count == size
            assert result_count == size
            assert status_counts == {"passed": size}
            assert run_row["status"] == "completed"
            assert run_row["record"]["status"] == "completed"
            assert run_row["record"]["stats"] == {
                "total": size,
                "queued": 0,
                "claimed": 0,
                "running": 0,
                "waiting_approval": 0,
                "passed": size,
                "failed": 0,
                "error": 0,
                "blocked": 0,
                "skipped": 0,
                "cancelled": 0,
            }
            print(
                f"capacity size={size} workers={workers} throughput={size / elapsed:.2f}/s "
                + " ".join(
                    f"{key}_p50={_percentile(values, 0.50):.2f}ms {key}_p95={_percentile(values, 0.95):.2f}ms "
                    f"{key}_p99={_percentile(values, 0.99):.2f}ms"
                    for key, values in latencies.items()
                )
            )
        finally:
            _tables(
                settings,
                [
                    settings.database.postgres_test_case_result_table,
                    settings.database.postgres_test_run_attempt_table,
                    settings.database.postgres_test_run_item_table,
                    settings.database.postgres_test_run_table,
                ],
            )


@pytest.mark.skipif(
    not LIVE_CONFIG.run_live_postgres_soak,
    reason="set RUN_LIVE_POSTGRES_SOAK=1 to run the PostgreSQL soak test",
)
@pytest.mark.asyncio
async def test_live_postgres_lifecycle_soak():
    """Run repeated real Store lifecycles and report stability metrics."""
    duration_seconds = max(1, LIVE_CONFIG.run_live_postgres_soak_seconds)
    sample_interval_seconds = max(
        1,
        LIVE_CONFIG.run_live_postgres_soak_sample_interval_seconds,
    )
    iteration_interval_seconds = max(
        0.0,
        LIVE_CONFIG.run_live_postgres_soak_iteration_interval_seconds,
    )
    workers = max(1, LIVE_CONFIG.run_live_postgres_soak_workers)
    max_iterations = max(
        0,
        LIVE_CONFIG.run_live_postgres_soak_max_iterations,
    )
    suffix = uuid4().hex[:10]
    settings = _settings_with_database_overrides(
        postgres_pool_size=max(workers, 4),
        postgres_test_run_table=f"soak_run_{suffix}",
        postgres_test_run_item_table=f"soak_item_{suffix}",
        postgres_test_run_attempt_table=f"soak_attempt_{suffix}",
        postgres_test_case_result_table=f"soak_result_{suffix}",
    )
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iterations = 0
    completed = 0
    errors: list[str] = []
    latencies: list[float] = []
    rss_samples: list[int] = []
    connection_samples: list[int] = []
    next_sample_at = perf_counter()
    started = perf_counter()

    try:
        _create_tables(settings)
        while perf_counter() - started < duration_seconds:
            if max_iterations and iterations >= max_iterations:
                break
            iterations += 1
            run_now = datetime.now(timezone.utc)
            run = _TestRunRecord(
                id=str(uuid4()),
                project_id=str(uuid4()),
                suite_id=str(uuid4()),
                mode_key="api_testing",
                stats={"total": 1, "queued": 1},
                created_at=run_now,
                updated_at=run_now,
            )
            item = _TestRunItemRecord(
                id=str(uuid4()),
                run_id=run.id,
                case_id=str(uuid4()),
                case_version_id=str(uuid4()),
                position=1,
                created_at=run_now,
                updated_at=run_now,
            )
            await store.create_run(run, [item])

            async def worker(worker_number: int) -> None:
                nonlocal completed
                try:
                    claims = await store.claim_items(
                        run_id=run.id,
                        worker_id=f"soak-worker-{worker_number}",
                        limit=1,
                        lease_seconds=60,
                        now=datetime.now(timezone.utc),
                    )
                    if not claims:
                        return
                    claimed_item, attempt = claims[0]
                    await store.start_item(
                        claimed_item.id,
                        attempt.lease_token,
                        datetime.now(timezone.utc),
                    )
                    completion = RunItemCompletion(
                        status="passed",
                        summary="soak lifecycle passed",
                        actual={"soak_iteration": iterations},
                        payload_hash=hashlib.sha256(
                            json.dumps(
                                {"item_id": claimed_item.id, "iteration": iterations},
                                sort_keys=True,
                            ).encode()
                        ).hexdigest(),
                    )
                    begin = perf_counter()
                    await store.complete_item(
                        claimed_item.id,
                        attempt.lease_token,
                        completion,
                        datetime.now(timezone.utc),
                    )
                    latencies.append((perf_counter() - begin) * 1000)
                    completed += 1
                except Exception as exc:
                    errors.append(f"iteration={iterations}: {type(exc).__name__}: {exc}")

            await asyncio.gather(*(worker(index) for index in range(workers)))
            if perf_counter() >= next_sample_at:
                rss = _current_rss_bytes()
                connections = _postgres_connection_count(settings)
                if rss is not None:
                    rss_samples.append(rss)
                if connections is not None:
                    connection_samples.append(connections)
                next_sample_at = perf_counter() + sample_interval_seconds
            await asyncio.sleep(iteration_interval_seconds)

        elapsed = perf_counter() - started
        assert not errors, errors[:5]
        assert completed == iterations
        assert iterations > 0
        print(
            "postgres_soak "
            f"duration_seconds={elapsed:.2f} iterations={iterations} completed={completed} "
            f"error_rate={len(errors) / max(iterations, 1):.6f} "
            f"complete_p50={_percentile(latencies, 0.50):.2f}ms "
            f"complete_p95={_percentile(latencies, 0.95):.2f}ms "
            f"complete_p99={_percentile(latencies, 0.99):.2f}ms "
            f"rss_min={min(rss_samples) if rss_samples else 'unavailable'} "
            f"rss_max={max(rss_samples) if rss_samples else 'unavailable'} "
            f"connections_max={max(connection_samples) if connection_samples else 'unavailable'}"
        )
    finally:
        _tables(
            settings,
            [
                settings.database.postgres_test_case_result_table,
                settings.database.postgres_test_run_attempt_table,
                settings.database.postgres_test_run_item_table,
                settings.database.postgres_test_run_table,
            ],
        )
