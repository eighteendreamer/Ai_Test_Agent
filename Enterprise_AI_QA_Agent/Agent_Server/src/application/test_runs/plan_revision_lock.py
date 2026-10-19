"""Coordinate short plan-projection transactions independently of Run totals."""
from __future__ import annotations

import logging
from uuid import UUID

from src.application.test_runs.plan_mapping import json_digest


logger = logging.getLogger(__name__)


def lock_plan_projection(cursor, *, run_table: str, run_id: str, exclusive: bool = False) -> None:
    # Same transaction-scoped advisory locking as alembic/env.py. A dedicated
    # resource avoids SHARE -> UPDATE promotion on the Run row when claimants
    # refresh totals; the existing item SKIP LOCKED remains the claim mechanism.
    # PostgreSQL functions-admin 9.28.10: shared readers coexist, writers exclude
    # them, and both modes are released by commit/rollback/connection loss.
    canonical_run_id = str(UUID(run_id))
    logger.info("test_run_plan_projection_lock_requested", extra={"run_id": canonical_run_id,
        "run_table": run_table, "lock_mode": "exclusive" if exclusive else "shared"})
    # Resolve aliases of the same physical relation, so schema qualification or
    # quoting cannot accidentally select a different coordination resource.
    cursor.execute("SELECT %s::regclass::oid AS table_oid", (run_table,))
    table_oid = int(cursor.fetchone()["table_oid"])
    digest = json_digest({"resource": "test_run_plan_projection_v1", "table_oid": table_oid,
        "run_id": canonical_run_id})
    key = int(digest[:16], 16)
    if key >= 1 << 63:
        key -= 1 << 64
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    cursor.execute(f"SELECT pg_catalog.{function}(%s::bigint)", (key,))
    logger.info("test_run_plan_projection_locked", extra={"run_id": canonical_run_id,
        "run_table": run_table, "table_oid": table_oid, "lock_mode": "exclusive" if exclusive else "shared"})
