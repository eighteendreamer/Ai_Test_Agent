"""Durable cleanup receipts; a successful Stream delivery is ACKed after commit."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from psycopg import sql

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.resource_lease_manager import ExpiredResourceLease
from src.schemas.resource_cleanup import ResourceCleanupReconciliation


LOGGER = logging.getLogger(__name__)

_RECEIPT_IDENTITY_FIELDS = (
    "resource_id",
    "resource_type",
    "external_resource_id",
    "project_id",
    "session_id",
    "turn_id",
    "run_id",
    "run_item_id",
    "attempt_id",
    "worker_id",
    "fencing_token",
    "request_id",
    "trace_id",
    "allocation_id",
)


def _lease_record(lease: ExpiredResourceLease) -> dict[str, Any]:
    record = asdict(lease)
    record.pop("lease_token", None)
    return record


def _receipt_identity_matches(lease: ExpiredResourceLease, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    expected = _lease_record(lease)
    return all(record.get(field) == expected.get(field) for field in _RECEIPT_IDENTITY_FIELDS)


def reconcile_cleanup_receipt(
    task_id: str,
    lease: ExpiredResourceLease,
    receipt: dict[str, Any] | None,
) -> ResourceCleanupReconciliation:
    """Classify a durable cleanup receipt without calling an external adapter.

    A missing, running, or failed receipt is deliberately ``unknown``.  The
    cleanup callback may have reached the external system before the process
    stopped, so this read-only view never turns those states into a retry grant.
    """
    base = {
        "task_id": task_id,
        "resource_id": lease.resource_id,
        "resource_type": lease.resource_type,
        "external_resource_id": lease.external_resource_id,
        "allocation_id": lease.allocation_id,
    }
    if receipt is None:
        return ResourceCleanupReconciliation(
            **base,
            status="unknown",
            attempts=0,
            reason_code="receipt_missing",
        )

    raw_status = receipt.get("status")
    attempts = receipt.get("attempts", 0)
    valid_attempts = type(attempts) is int and attempts >= 0
    identity_ok = (
        receipt.get("task_id") == task_id
        and _receipt_identity_matches(lease, receipt.get("record"))
        and valid_attempts
    )
    if not identity_ok:
        return ResourceCleanupReconciliation(
            **base,
            status="drifted",
            receipt_status=raw_status if raw_status in {"running", "completed", "skipped", "failed"} else None,
            attempts=attempts if valid_attempts else 0,
            worker_id=receipt.get("worker_id") if isinstance(receipt.get("worker_id"), str) else None,
            error_type=receipt.get("error_type") if isinstance(receipt.get("error_type"), str) else None,
            completed_at=receipt.get("completed_at") if isinstance(receipt.get("completed_at"), type(None)) else None,
            reason_code="receipt_identity_drift",
        )

    common = {
        "receipt_status": raw_status,
        "attempts": attempts,
        "worker_id": receipt.get("worker_id"),
        "error_type": receipt.get("error_type"),
        "completed_at": receipt.get("completed_at"),
    }
    if raw_status == "completed":
        return ResourceCleanupReconciliation(
            **base,
            **common,
            status="completed",
            terminal_status="completed",
            reason_code="receipt_completed",
        )
    if raw_status == "skipped":
        return ResourceCleanupReconciliation(
            **base,
            **common,
            status="completed",
            terminal_status="skipped",
            reason_code="receipt_skipped",
        )
    if raw_status == "running":
        return ResourceCleanupReconciliation(
            **base,
            **common,
            status="unknown",
            reason_code="receipt_running",
        )
    if raw_status == "failed":
        return ResourceCleanupReconciliation(
            **base,
            **common,
            status="unknown",
            reason_code="receipt_failed",
        )
    return ResourceCleanupReconciliation(
        **base,
        status="drifted",
        attempts=attempts,
        worker_id=receipt.get("worker_id") if isinstance(receipt.get("worker_id"), str) else None,
        error_type=receipt.get("error_type") if isinstance(receipt.get("error_type"), str) else None,
        reason_code="receipt_status_invalid",
    )


class PostgresResourceCleanupStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = sql.Identifier(settings.database.postgres_resource_cleanup_table)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    task_id TEXT PRIMARY KEY,
                    record JSONB NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    error_type TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """).format(self._table))

    async def begin(self, task_id: str, lease: ExpiredResourceLease, *, worker_id: str) -> bool:
        """False means an earlier delivery already committed its terminal receipt.

        Delivery ownership is provided by RedisTaskWorker. The external callback
        must also be idempotent: a crash can occur after deletion but before this
        receipt commits. Docker cleanup therefore requires an immutable full ID.
        """
        record = asdict(lease)
        record.pop("lease_token")
        return await asyncio.to_thread(self._begin_sync, task_id, record, worker_id)

    async def completed(self, task_id: str, *, allocation_id: str) -> bool:
        return await asyncio.to_thread(self._completed_sync, task_id, allocation_id)

    async def reconcile(
        self,
        task_id: str,
        lease: ExpiredResourceLease,
    ) -> ResourceCleanupReconciliation:
        """Read one receipt without taking an owner, lease, or row lock."""
        receipt = await asyncio.to_thread(self._read_sync, task_id)
        result = reconcile_cleanup_receipt(task_id, lease, receipt)
        LOGGER.log(
            logging.WARNING if result.status in {"unknown", "drifted"} else logging.INFO,
            "resource_cleanup_receipt_reconciled",
            extra={
                "task_id": result.task_id,
                "resource_id": result.resource_id,
                "resource_type": result.resource_type,
                "status": result.status,
                "reason_code": result.reason_code,
                "receipt_status": result.receipt_status,
                "attempts": result.attempts,
            },
        )
        return result

    def _read_sync(self, task_id: str) -> dict[str, Any] | None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT task_id, record, status, worker_id, attempts,
                       error_type, completed_at
                FROM {}
                WHERE task_id = %s
            """).format(self._table), (task_id,))
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def _completed_sync(self, task_id, allocation_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT 1 FROM {} WHERE task_id = %s AND status = 'completed'
                AND record->>'allocation_id' = %s
            """).format(self._table), (task_id, allocation_id))
            return cur.fetchone() is not None

    def _begin_sync(self, task_id: str, record: dict, worker_id: str) -> bool:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {table} AS job (task_id, record, status, worker_id)
                VALUES (%s, %s::jsonb, 'running', %s)
                ON CONFLICT (task_id) DO UPDATE
                SET status = 'running', worker_id = EXCLUDED.worker_id,
                    attempts = job.attempts + 1, error_type = NULL, updated_at = NOW()
                WHERE job.status NOT IN ('completed', 'skipped')
                RETURNING task_id
            """).format(table=self._table), (task_id, json.dumps(record), worker_id))
            return cur.fetchone() is not None

    async def finish(self, task_id: str, *, worker_id: str, status: str,
                     error_type: str | None = None) -> None:
        if status not in {"completed", "skipped", "failed"}:
            raise ValueError(f"Unsupported cleanup status: {status}")
        await asyncio.to_thread(self._finish_sync, task_id, worker_id, status, error_type)

    def _finish_sync(self, task_id, worker_id, status, error_type) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                UPDATE {} SET status = %s, error_type = %s, updated_at = NOW(),
                    completed_at = CASE WHEN %s IN ('completed', 'skipped') THEN NOW() ELSE NULL END
                WHERE task_id = %s AND worker_id = %s AND status = 'running'
            """).format(self._table), (status, error_type, status, task_id, worker_id))
            if cur.rowcount != 1:
                raise RuntimeError(f"Cleanup receipt ownership lost for task {task_id}")
