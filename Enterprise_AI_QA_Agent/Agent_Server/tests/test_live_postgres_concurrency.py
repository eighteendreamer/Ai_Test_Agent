from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import multiprocessing
import os
import queue as queue_module
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.core.config import Settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.registry.tools import ToolRegistry
from src.registry.agents import AgentRegistry
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.run_management import (
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
)
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus, ToolApprovalStatus
from src.schemas.memory import MemoryWriteRequest
from tests.live_postgres_config import LivePostgresTestConfig


LIVE_POSTGRES = LivePostgresTestConfig().run_live_postgres_tests
live_postgres = pytest.mark.skipif(
    not LIVE_POSTGRES,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use the local PostgreSQL instance",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _drop_tables(settings: Settings, table_names: list[str]) -> None:
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            for table_name in table_names:
                cur.execute(f"DROP TABLE IF EXISTS {table_name}")


def _settings_with_database_overrides(**overrides: object) -> Settings:
    """Apply live-test table overrides to the nested DatabaseConfig contract."""
    base = Settings()
    database = base.database.model_copy(update=overrides)
    return base.model_copy(update={"database": database})


def _create_claim_tables(settings: Settings) -> None:
    """Create isolated run tables with the same columns used by the live Store."""
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
                "case_version_id UUID NOT NULL, position INTEGER NOT NULL, "
                "status TEXT NOT NULL, attempt_no INTEGER NOT NULL DEFAULT 0, "
                "lease_owner TEXT NULL, lease_token TEXT NULL, lease_expires_at TIMESTAMPTZ NULL, "
                "result_id UUID NULL, regression_source_result_id UUID NULL, "
                "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL, "
                "UNIQUE(run_id, position), UNIQUE(run_id, case_id, case_version_id))"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_attempt_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL, "
                "attempt_no INTEGER NOT NULL, lease_token TEXT NOT NULL, status TEXT NOT NULL, "
                "record JSONB NOT NULL, UNIQUE(run_item_id, attempt_no), UNIQUE(lease_token))"
            )


def _worker_settings(overrides: dict[str, object]) -> Settings:
    return _settings_with_database_overrides(**overrides)


def _claim_tool_job_in_process(
    overrides: dict[str, object], job_id: str, result_queue
) -> None:
    """Claim through an independent interpreter and PostgreSQL connection."""
    try:
        store = PostgresToolJobStore(_worker_settings(overrides))
        claimed = asyncio.run(store.claim_job_execution(job_id))
        result_queue.put({"claimed": claimed is not None})
    except Exception as exc:
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})


def _claim_checkpoint_then_exit(
    overrides: dict[str, object],
    run_id: str,
    checkpoint_payload: dict[str, object],
    claimed_at_iso: str,
) -> None:
    """First isolated worker: persist a checkpoint, then exit without cleanup code."""
    settings = _worker_settings(overrides)
    now = datetime.fromisoformat(claimed_at_iso)

    async def run_worker() -> None:
        store = PostgresTestRunStore(settings)
        claims = await store.claim_items(
            run_id=run_id,
            worker_id="process-worker-a",
            limit=1,
            lease_seconds=1,
            now=now,
        )
        if len(claims) != 1:
            raise RuntimeError(f"expected one claim, got {len(claims)}")
        item, attempt = claims[0]
        await store.save_checkpoint(
            item.id,
            attempt.lease_token,
            "process_checkpoint",
            checkpoint_payload,
            1,
            now,
        )

    asyncio.run(run_worker())
    os._exit(0)


def _recover_and_claim_in_new_process(
    overrides: dict[str, object],
    run_id: str,
    recover_at_iso: str,
    result_queue,
) -> None:
    """Second isolated worker: recover expired work and claim it with a new Attempt."""
    settings = _worker_settings(overrides)
    recover_at = datetime.fromisoformat(recover_at_iso)

    async def run_worker() -> dict[str, object]:
        store = PostgresTestRunStore(settings)
        recovered = await store.recover_expired(run_id, recover_at)
        claims = await store.claim_items(
            run_id=run_id,
            worker_id="process-worker-b",
            limit=1,
            lease_seconds=60,
            now=recover_at,
        )
        if len(claims) != 1:
            raise RuntimeError(f"expected one recovered claim, got {len(claims)}")
        item, attempt = claims[0]
        return {
            "recovered": recovered,
            "item_id": item.id,
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "recovered_from_attempt_id": attempt.recovered_from_attempt_id,
            "checkpoint_version": attempt.checkpoint_version,
            "checkpoint_key": attempt.checkpoint_key,
            "checkpoint_payload": attempt.checkpoint_payload,
        }

    try:
        result_queue.put(asyncio.run(run_worker()))
    except Exception as exc:
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})
        raise


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_claims_each_item_once_without_deadlock():
    suffix = uuid4().hex[:10]
    run_table = f"live_run_{suffix}"
    item_table = f"live_item_{suffix}"
    attempt_table = f"live_attempt_{suffix}"
    result_table = f"live_result_{suffix}"
    settings = _settings_with_database_overrides(
        postgres_pool_size=12,
        postgres_test_run_table=run_table,
        postgres_test_run_item_table=item_table,
        postgres_test_run_attempt_table=attempt_table,
        postgres_test_case_result_table=result_table,
    )
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc)
    run = _RunRecord(
        id=str(uuid4()),
        project_id=str(uuid4()),
        suite_id=str(uuid4()),
        mode_key="api_testing",
        stats={"total": 480, "queued": 480},
        created_at=now,
        updated_at=now,
    )
    items = [
        _RunItemRecord(
            id=str(uuid4()),
            run_id=run.id,
            case_id=str(uuid4()),
            case_version_id=str(uuid4()),
            position=position,
            created_at=now,
            updated_at=now,
        )
        for position in range(1, 481)
    ]
    claimed_item_ids: list[str] = []
    claim_latencies_ms: list[float] = []

    try:
        _create_claim_tables(settings)
        await store.create_run(run, items)

        async def claim_until_empty(worker_number: int) -> None:
            while True:
                started = perf_counter()
                claims = await store.claim_items(
                    run_id=run.id,
                    worker_id=f"worker-{worker_number}",
                    limit=4,
                    lease_seconds=120,
                    now=datetime.now(timezone.utc),
                )
                claim_latencies_ms.append((perf_counter() - started) * 1000)
                if not claims:
                    return
                claimed_item_ids.extend(item.id for item, _ in claims)

        started = perf_counter()
        await asyncio.gather(*(claim_until_empty(index) for index in range(12)))
        elapsed_seconds = perf_counter() - started

        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM {attempt_table}")
                attempt_count = int(cur.fetchone()["total"])
                cur.execute(
                    f"SELECT COUNT(DISTINCT run_item_id) AS items, "
                    f"COUNT(DISTINCT lease_token) AS leases FROM {attempt_table}"
                )
                distinct_counts = cur.fetchone()
                cur.execute(
                    f"SELECT status, COUNT(*) AS total FROM {item_table} GROUP BY status"
                )
                status_counts = {
                    row["status"]: int(row["total"]) for row in cur.fetchall()
                }

        assert len(claimed_item_ids) == 480
        assert len(set(claimed_item_ids)) == 480
        assert attempt_count == 480
        assert int(distinct_counts["items"]) == 480
        assert int(distinct_counts["leases"]) == 480
        assert status_counts == {"claimed": 480}
        print(
            "live_postgres_claim "
            f"items=480 workers=12 throughput={480 / elapsed_seconds:.2f}/s "
            f"p50={_percentile(claim_latencies_ms, 0.50):.2f}ms "
            f"p95={_percentile(claim_latencies_ms, 0.95):.2f}ms "
            f"p99={_percentile(claim_latencies_ms, 0.99):.2f}ms"
        )
    finally:
        _drop_tables(settings, [attempt_table, item_table, run_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_process_worker_takeover_restores_checkpoint():
    suffix = uuid4().hex[:10]
    run_table = f"live_takeover_run_{suffix}"
    item_table = f"live_takeover_item_{suffix}"
    attempt_table = f"live_takeover_attempt_{suffix}"
    settings_overrides = {
        "postgres_pool_size": 4,
        "postgres_test_run_table": run_table,
        "postgres_test_run_item_table": item_table,
        "postgres_test_run_attempt_table": attempt_table,
        "postgres_test_case_result_table": f"live_takeover_result_{suffix}",
    }
    settings = _settings_with_database_overrides(**settings_overrides)
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    run = _RunRecord(
        id=str(uuid4()),
        project_id=str(uuid4()),
        suite_id=str(uuid4()),
        mode_key="api_testing",
        stats={"total": 1, "queued": 1},
        created_at=now,
        updated_at=now,
    )
    item = _RunItemRecord(
        id=str(uuid4()),
        run_id=run.id,
        case_id=str(uuid4()),
        case_version_id=str(uuid4()),
        position=1,
        created_at=now,
        updated_at=now,
    )
    result_table = settings.database.postgres_test_case_result_table
    process_a = None
    process_b = None
    result_queue = None
    try:
        _create_claim_tables(settings)
        await store.create_run(run, [item])
        context = multiprocessing.get_context("spawn")
        process_a = context.Process(
            target=_claim_checkpoint_then_exit,
            args=(settings_overrides, run.id, {"step": 7, "request_id": "req-7"}, now.isoformat()),
            name="live-worker-a",
        )
        process_a.start()
        process_a.join(timeout=30)
        assert not process_a.is_alive()
        assert process_a.exitcode == 0

        first_attempt = await store.get_latest_attempt(item.id)
        assert first_attempt is not None
        assert first_attempt.attempt_no == 1
        assert first_attempt.checkpoint_payload == {"step": 7, "request_id": "req-7"}

        result_queue = context.Queue()
        recover_at = now.replace(microsecond=0) + timedelta(seconds=2)
        process_b = context.Process(
            target=_recover_and_claim_in_new_process,
            args=(settings_overrides, run.id, recover_at.isoformat(), result_queue),
            name="live-worker-b",
        )
        process_b.start()
        process_b.join(timeout=30)
        assert not process_b.is_alive()
        assert process_b.exitcode == 0
        try:
            takeover = result_queue.get(timeout=5)
        except queue_module.Empty as exc:
            raise AssertionError("recovery worker returned no result") from exc

        assert "error" not in takeover
        assert takeover["recovered"] == 1
        assert takeover["item_id"] == item.id
        assert takeover["attempt_no"] == 2
        assert takeover["attempt_id"] != first_attempt.id
        assert takeover["recovered_from_attempt_id"] == first_attempt.id
        assert takeover["checkpoint_version"] == 1
        assert takeover["checkpoint_key"] == "process_checkpoint"
        assert takeover["checkpoint_payload"] == {"step": 7, "request_id": "req-7"}
    finally:
        if process_a is not None and process_a.is_alive():
            process_a.terminate()
            process_a.join(timeout=5)
        if process_b is not None and process_b.is_alive():
            process_b.terminate()
            process_b.join(timeout=5)
        if result_queue is not None:
            result_queue.close()
        _drop_tables(settings, [result_table, attempt_table, item_table, run_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_tool_job_has_one_process_owner_and_terminal_is_not_reclaimed():
    suffix = uuid4().hex[:10]
    job_table = f"live_tool_job_{suffix}"
    artifact_table = f"live_tool_artifact_{suffix}"
    settings_overrides = {
        "postgres_pool_size": 4,
        "postgres_tool_job_table": job_table,
        "postgres_tool_artifact_table": artifact_table,
    }
    settings = _settings_with_database_overrides(**settings_overrides)
    store = PostgresToolJobStore(settings)
    jobs = ToolJobService(store, heartbeat_timeout_seconds=0)
    descriptor = ToolRegistry().get("knowledge-rag")
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = []

    try:
        await jobs.initialize()
        job = await jobs.create_job(
            tool=descriptor,
            call_id=f"process-call-{suffix}",
            session_id=f"process-session-{suffix}",
            turn_id=f"process-turn-{suffix}",
            trace_id=f"process-trace-{suffix}",
            input_payload={"query": "durable evidence"},
            once_per_call=True,
        )
        for worker_number in range(2):
            process = context.Process(
                target=_claim_tool_job_in_process,
                args=(settings_overrides, job.id, result_queue),
                name=f"live-tool-worker-{worker_number}",
            )
            processes.append(process)
            process.start()
        for process in processes:
            process.join(timeout=30)
            assert not process.is_alive()
            assert process.exitcode == 0
        outcomes = []
        for _ in processes:
            try:
                outcomes.append(result_queue.get(timeout=5))
            except queue_module.Empty as exc:
                raise AssertionError("tool worker returned no claim result") from exc
        assert all("error" not in item for item in outcomes), outcomes
        assert sorted(item["claimed"] for item in outcomes) == [False, True]

        await jobs.mark_completed(
            job.id,
            summary="persisted process result",
            output_payload={"status": "completed", "value": 42},
        )
        duplicate = await jobs.create_job(
            tool=descriptor,
            call_id=f"process-call-{suffix}",
            session_id=f"process-session-{suffix}",
            turn_id=f"process-turn-{suffix}",
            trace_id=f"process-trace-{suffix}",
            input_payload={"query": "durable evidence"},
            once_per_call=True,
        )
        assert duplicate.id == job.id
        assert duplicate.status.value == "completed"
        assert duplicate.output_payload == {"status": "completed", "value": 42}
        assert await jobs.claim_execution(job.id) is None

        unknown = await jobs.create_job(
            tool=descriptor,
            call_id=f"unknown-call-{suffix}",
            session_id=f"process-session-{suffix}",
            turn_id=f"process-turn-{suffix}",
            trace_id=f"process-trace-{suffix}",
            input_payload={"query": "unknown side effect"},
            once_per_call=True,
        )
        claimant = context.Process(
            target=_claim_tool_job_in_process,
            args=(settings_overrides, unknown.id, result_queue),
            name="live-tool-crash-owner",
        )
        processes.append(claimant)
        claimant.start()
        claimant.join(timeout=30)
        assert not claimant.is_alive()
        assert claimant.exitcode == 0
        assert result_queue.get(timeout=5) == {"claimed": True}
        stale = await jobs.initialize()
        assert [item.id for item in stale] == [unknown.id]
        assert (await jobs.get_job(unknown.id)).status.value == "resume_requested"
        assert await jobs.claim_execution(unknown.id) is None
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        _drop_tables(settings, [artifact_table, job_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_approval_cas_has_one_final_decision():
    suffix = uuid4().hex[:10]
    session_table = f"live_session_{suffix}"
    approval_table = f"live_approval_{suffix}"
    settings = _settings_with_database_overrides(
        postgres_pool_size=12,
        postgres_session_table=session_table,
        postgres_approval_table=approval_table,
    )
    store = PostgresSessionStore(settings)
    session_id = f"session-{suffix}"
    approval_id = f"approval-{suffix}"
    now = datetime.now(timezone.utc)

    try:
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE {session_table} ("
                    "id TEXT PRIMARY KEY, updated_at TIMESTAMPTZ NOT NULL)"
                )
                cur.execute(
                    f"CREATE TABLE {approval_table} ("
                    "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, tool_key TEXT NOT NULL, "
                    "tool_name TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, "
                    "created_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ NULL, "
                    "decision_note TEXT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb)"
                )
                cur.execute(
                    f"INSERT INTO {session_table} (id, updated_at) VALUES (%s, %s)",
                    (session_id, now),
                )
                cur.execute(
                    f"INSERT INTO {approval_table} "
                    "(id, session_id, tool_key, tool_name, reason, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
                    (
                        approval_id,
                        session_id,
                        "security-scan-runner",
                        "Security Scan Runner",
                        "live CAS validation",
                        now,
                    ),
                )

        decisions = [
            ToolApprovalStatus.approved if index % 2 == 0 else ToolApprovalStatus.denied
            for index in range(32)
        ]

        async def decide(status: ToolApprovalStatus):
            try:
                approval = await store.resolve_approval(
                    session_id,
                    approval_id,
                    status,
                    f"decision:{status.value}",
                )
                return approval.status, None
            except Exception as exc:
                return None, exc

        started = perf_counter()
        outcomes = await asyncio.gather(*(decide(status) for status in decisions))
        elapsed_seconds = perf_counter() - started
        successes = [status for status, error in outcomes if error is None]
        errors = [error for status, error in outcomes if error is not None]
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT status FROM {approval_table} WHERE id = %s",
                    (approval_id,),
                )
                final_status = ToolApprovalStatus(cur.fetchone()["status"])

        assert len(successes) == 16
        assert set(successes) == {final_status}
        assert len(errors) == 16
        assert all(isinstance(error, ValueError) for error in errors)

        continuation_stores = [PostgresSessionStore(settings) for _ in range(8)]
        claim_results = await asyncio.gather(*(
            candidate.claim_approval_continuation(
                session_id,
                approval_id,
                owner_id=f"continuation-worker-{index}",
                lease_token=f"continuation-token-{index}",
                lease_seconds=30,
            )
            for index, candidate in enumerate(continuation_stores)
        ))
        assert claim_results.count(True) == 1
        winning_index = claim_results.index(True)
        winning_token = f"continuation-token-{winning_index}"
        assert not await store.renew_approval_continuation(
            session_id, approval_id, "wrong-token", 30
        )
        assert await store.renew_approval_continuation(
            session_id, approval_id, winning_token, 30
        )
        assert not await store.complete_approval_continuation(
            session_id, approval_id, "wrong-token"
        )
        assert await store.complete_approval_continuation(
            session_id, approval_id, winning_token
        )
        assert not await store.claim_approval_continuation(
            session_id,
            approval_id,
            owner_id="late-worker",
            lease_token="late-token",
            lease_seconds=30,
        )

        expiring_approval_id = f"expiring-{suffix}"
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {approval_table} "
                    "(id, session_id, tool_key, tool_name, reason, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'approved', %s)",
                    (
                        expiring_approval_id,
                        session_id,
                        "knowledge-rag",
                        "Knowledge RAG",
                        "lease expiry validation",
                        now,
                    ),
                )
        assert await store.claim_approval_continuation(
            session_id,
            expiring_approval_id,
            owner_id="crashed-worker",
            lease_token="expired-token",
            lease_seconds=0,
        )
        assert await continuation_stores[0].claim_approval_continuation(
            session_id,
            expiring_approval_id,
            owner_id="recovery-worker",
            lease_token="recovery-token",
            lease_seconds=30,
        )
        print(
            "live_postgres_approval_cas "
            f"requests=32 final_status={final_status.value} "
            f"throughput={32 / elapsed_seconds:.2f}/s "
            f"elapsed={elapsed_seconds * 1000:.2f}ms"
        )
    finally:
        _drop_tables(settings, [approval_table, session_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_turn_owner_fences_stale_worker_writes():
    suffix = uuid4().hex[:10]
    table_names = {
        "postgres_session_table": f"live_turn_session_{suffix}",
        "postgres_message_table": f"live_turn_message_{suffix}",
        "postgres_event_table": f"live_turn_event_{suffix}",
        "postgres_snapshot_table": f"live_turn_snapshot_{suffix}",
        "postgres_approval_table": f"live_turn_approval_{suffix}",
        "postgres_memory_table": f"live_turn_memory_{suffix}",
    }
    settings = _settings_with_database_overrides(**table_names)
    store = PostgresSessionStore(settings)
    memory_store = PostgresVectorMemoryStore(settings)
    session_id = f"turn-session-{suffix}"
    turn_id = f"turn-{suffix}"
    now = datetime.now(timezone.utc)
    session = SessionRecord(
        id=session_id,
        title="live turn owner fencing",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
    )

    try:
        await store.initialize()
        await memory_store.initialize()
        await store.save_session(session)
        stores = [PostgresSessionStore(settings) for _ in range(8)]
        claims = await asyncio.gather(*(
            candidate.claim_turn_execution(
                session_id,
                turn_id,
                owner_id=f"turn-worker-{index}",
                lease_token=f"turn-token-{index}",
                lease_seconds=30,
            )
            for index, candidate in enumerate(stores)
        ))
        assert sum(item is not None for item in claims) == 1
        winning_index = next(index for index, item in enumerate(claims) if item is not None)
        winning_token = f"turn-token-{winning_index}"
        assert not await store.renew_turn_execution(session_id, "wrong-token", 30)
        assert await store.renew_turn_execution(session_id, winning_token, 0)
        takeover = await store.claim_turn_execution(
            session_id,
            turn_id,
            owner_id="recovery-worker",
            lease_token="recovery-token",
            lease_seconds=30,
        )
        assert takeover is not None

        stale = deepcopy(takeover)
        stale.metadata["turn_result"] = "stale-worker"
        with pytest.raises(ContinuationLeaseLostError):
            await store.save_session(stale, turn_lease_token=winning_token)
        recovered = deepcopy(takeover)
        recovered.metadata["turn_result"] = "recovery-worker"
        await store.save_session(recovered, turn_lease_token="recovery-token")
        assert await store.complete_turn_execution(session_id, "recovery-token")
        persisted = await store.get_session(session_id)
        assert persisted is not None
        assert persisted.metadata["turn_result"] == "recovery-worker"
        assert "turn_lease_token" not in persisted.metadata

        dispatch_claims = await asyncio.gather(*(
            candidate.claim_coordinator_dispatch(
                session_id,
                turn_id,
                "worker-task-1",
                owner_id=f"coordinator-worker-{index}",
            )
            for index, candidate in enumerate(stores)
        ))
        assert sum(dispatch_claims) == 1

        crashed = SessionRecord(
            id=f"crashed-{suffix}",
            title="startup recovery",
            status=SessionStatus.running,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=now,
            updated_at=now,
            metadata={
                "turn_lease_turn_id": "crashed-turn",
                "turn_lease_owner": "crashed-worker",
                "turn_lease_token": "crashed-token",
                "turn_lease_expires_at": (now - timedelta(seconds=10)).isoformat(),
                "pending_turn": {"turn_id": "crashed-turn"},
                "control": {"control_state": "active_turn"},
            },
        )
        await store.save_session(crashed)
        assert await store.recover_expired_turn_executions() == [crashed.id]
        recovered_crashed = await store.get_session(crashed.id)
        assert recovered_crashed is not None
        assert recovered_crashed.status == SessionStatus.interrupted
        assert recovered_crashed.metadata["control"]["is_resumable"] is True

        memory_session = SessionRecord(
            id=f"memory-{suffix}",
            title="memory fencing",
            status=SessionStatus.idle,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=now,
            updated_at=now,
        )
        await store.save_session(memory_session)
        assert await store.claim_turn_execution(
            memory_session.id, "memory-turn", "memory-worker", "memory-token", 30
        )
        request = MemoryWriteRequest(
            scope="session",
            kind="episodic",
            content="fenced memory",
            session_id=memory_session.id,
            turn_id="memory-turn",
            turn_lease_token="memory-token",
        )
        await memory_store.write(request)
        assert await store.renew_turn_execution(memory_session.id, "memory-token", 0) is True
        assert await store.claim_turn_execution(
            memory_session.id, "memory-turn", "memory-recovery", "memory-recovery-token", 30
        )
        with pytest.raises(ContinuationLeaseLostError):
            await memory_store.write(request)
    finally:
        _drop_tables(
            settings,
            [
                table_names["postgres_approval_table"],
                table_names["postgres_snapshot_table"],
                table_names["postgres_event_table"],
                table_names["postgres_message_table"],
                table_names["postgres_session_table"],
                table_names["postgres_memory_table"],
            ],
        )


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_coordinator_recovery_reconciles_interrupted_child():
    suffix = uuid4().hex[:10]
    table_names = {
        "postgres_session_table": f"live_coord_session_{suffix}",
        "postgres_message_table": f"live_coord_message_{suffix}",
        "postgres_event_table": f"live_coord_event_{suffix}",
        "postgres_snapshot_table": f"live_coord_snapshot_{suffix}",
        "postgres_approval_table": f"live_coord_approval_{suffix}",
        "postgres_memory_table": f"live_coord_memory_{suffix}",
    }
    settings = _settings_with_database_overrides(**table_names)
    store = PostgresSessionStore(settings)
    now = datetime.now(timezone.utc)
    parent = SessionRecord(
        id=f"coord-parent-{suffix}",
        title="coordinator recovery parent",
        status=SessionStatus.running,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
        metadata={
            "worker_dispatches": [
                {
                    "task_id": "coord-task-1",
                    "child_session_id": f"coord-child-{suffix}",
                    "status": "running",
                }
            ]
        },
    )
    child = SessionRecord(
        id=f"coord-child-{suffix}",
        title="interrupted coordinator child",
        status=SessionStatus.interrupted,
        session_mode=SessionMode.background_task,
        runtime_mode=RuntimeMode.background,
        mode_key="default",
        created_at=now,
        updated_at=now,
    )
    try:
        await store.initialize()
        await store.save_session(parent)
        await store.save_session(child)
        service = CoordinatorRuntimeService(
            settings=settings,
            store=store,
            session_service=None,
            agent_registry=AgentRegistry(),
        )
        assert await service.recover_orphaned_dispatches() == 1
        recovered = await store.get_session(parent.id)
        assert recovered is not None
        dispatch = recovered.metadata["worker_dispatches"][0]
        assert dispatch["status"] == "recovery_required"
        assert dispatch["recovery_reason"] == "child_session_interrupted_without_safe_replay"
        events = await store.list_events(parent.id)
        assert [event.type for event in events] == ["worker.dispatches_reconciled"]
    finally:
        _drop_tables(settings, list(table_names.values()))
