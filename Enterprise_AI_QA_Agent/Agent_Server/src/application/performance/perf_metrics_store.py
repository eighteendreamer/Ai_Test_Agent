"""Performance metrics store.

Persists PerfRun records and provides baseline lookup
for regression comparison.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.performance_testing_mode.plan_state import PerfMetrics, PerfRun

logger = logging.getLogger(__name__)


class PerfMetricsStore:
    """PostgreSQL-backed store for performance run records."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._table = settings.database.postgres_perf_runs_table
        self._initialized = False

    async def initialize(self) -> bool:
        if self._initialized:
            return True
        try:
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True
            return True
        except Exception as e:
            logger.warning("performance_metrics_initialize_failed", extra={
                "table": self._table, "error_type": type(e).__name__,
            })
            return False

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        run_id TEXT PRIMARY KEY,
                        plan_id TEXT NOT NULL,
                        project_id UUID NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        engine TEXT NOT NULL DEFAULT 'k6',
                        backend TEXT NOT NULL DEFAULT 'docker',
                        status TEXT NOT NULL DEFAULT 'pending',
                        run_intent TEXT NOT NULL DEFAULT 'probe',
                        target_url TEXT,
                        metrics_json JSONB,
                        verdict TEXT,
                        started_at TIMESTAMPTZ,
                        completed_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                # Match the existing session-store additive migration: unknown
                # historical ownership stays NULL, never inferred from URL.
                cur.execute(f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS project_id UUID NULL "
                            f"REFERENCES {self._settings.database.postgres_project_table}(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_project_target "
                            f"ON {self._table} (project_id, target_url, completed_at DESC)")
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_plan_id
                    ON {self._table} (plan_id)
                    """
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_target_url
                    ON {self._table} (target_url)
                    """
                )

    async def save_run(
        self,
        run: PerfRun,
        metrics: PerfMetrics | None = None,
        verdict: str | None = None,
        target_url: str = "",
        run_intent: str = "probe",
        *,
        project_id: str | None = None,
    ) -> bool:
        if not await self.initialize():
            return False

        try:
            owner = str(UUID(str(project_id))) if project_id else None
            saved = await asyncio.to_thread(
                self._save_run_sync,
                run,
                metrics.model_dump() if metrics else None,
                verdict,
                target_url,
                run_intent,
                owner,
            )
            if not saved:
                logger.warning("performance_metrics_identity_conflict", extra={
                    "run_id": run.run_id, "plan_id": run.plan_id, "project_id": owner,
                })
            elif owner is None:
                logger.info("performance_metrics_unscoped", extra={"run_id": run.run_id})
            return saved
        except Exception as e:
            # SQL exception strings can contain the full bound metrics/URL.
            logger.warning("performance_metrics_save_failed", extra={
                "run_id": run.run_id, "plan_id": run.plan_id,
                "engine": run.engine, "table": self._table,
                "error_type": type(e).__name__,
            })
            return False

    def _save_run_sync(
        self,
        run: PerfRun,
        metrics_data: dict[str, Any] | None,
        verdict: str | None,
        target_url: str,
        run_intent: str,
        project_id: str | None,
    ) -> bool:
        metrics_json = json.dumps(metrics_data) if metrics_data else None
        started_at = datetime.fromisoformat(run.started_at) if run.started_at else None
        completed_at = datetime.fromisoformat(run.completed_at) if run.completed_at else None
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} AS stored
                        (run_id, plan_id, project_id, engine, backend, status, run_intent, target_url,
                         metrics_json, verdict, started_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        metrics_json = EXCLUDED.metrics_json,
                        verdict = EXCLUDED.verdict,
                        completed_at = EXCLUDED.completed_at
                    WHERE stored.project_id IS NOT DISTINCT FROM EXCLUDED.project_id
                      AND stored.plan_id = EXCLUDED.plan_id
                      AND stored.engine = EXCLUDED.engine
                      AND stored.backend = EXCLUDED.backend
                      AND stored.run_intent = EXCLUDED.run_intent
                      AND stored.target_url IS NOT DISTINCT FROM EXCLUDED.target_url
                    """,
                    (
                        run.run_id,
                        run.plan_id,
                        project_id,
                        run.engine,
                        run.backend,
                        run.status,
                        run_intent,
                        target_url,
                        metrics_json,
                        verdict,
                        started_at,
                        completed_at,
                    ),
                )
                return cur.rowcount == 1

    async def get_baseline(self, target_url: str, *, project_id: str | None = None) -> PerfMetrics | None:
        """Get this project's latest successful baseline; no global fallback."""
        if not project_id:
            logger.info("performance_baseline_unavailable", extra={"reason": "missing_project"})
            return None
        if not await self.initialize():
            return None

        try:
            data = await asyncio.to_thread(self._get_baseline_sync, target_url, str(UUID(str(project_id))))
            return PerfMetrics(**data) if data else None
        except Exception as e:
            logger.warning("performance_baseline_lookup_failed", extra={
                "table": self._table, "error_type": type(e).__name__,
            })
            return None

    def _get_baseline_sync(self, target_url: str, project_id: str) -> dict[str, Any] | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT metrics_json FROM {self._table}
                    WHERE project_id = %s AND target_url = %s
                      AND status = 'completed'
                      AND verdict IN ('baseline', 'pass')
                      AND metrics_json IS NOT NULL
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (project_id, target_url),
                )
                row = cur.fetchone()
        if not row or not row["metrics_json"]:
            return None
        return json.loads(row["metrics_json"]) if isinstance(row["metrics_json"], str) else row["metrics_json"]

    async def list_runs(
        self,
        target_url: str | None = None,
        limit: int = 20,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not project_id:
            logger.info("performance_history_unavailable", extra={"reason": "missing_project"})
            return []
        if not await self.initialize():
            return []

        try:
            return await asyncio.to_thread(self._list_runs_sync, target_url, limit, str(UUID(str(project_id))))
        except Exception as e:
            logger.warning("performance_history_lookup_failed", extra={
                "table": self._table, "error_type": type(e).__name__,
            })
            return []

    def _list_runs_sync(self, target_url: str | None, limit: int, project_id: str) -> list[dict[str, Any]]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if target_url:
                    cur.execute(
                        f"""
                        SELECT run_id, plan_id, project_id, engine, status, run_intent, target_url,
                               verdict, started_at, completed_at
                        FROM {self._table}
                        WHERE project_id = %s AND target_url = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (project_id, target_url, limit),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT run_id, plan_id, project_id, engine, status, run_intent, target_url,
                               verdict, started_at, completed_at
                        FROM {self._table}
                        WHERE project_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (project_id, limit),
                    )
                rows = cur.fetchall()
        return [dict(row, project_id=str(row["project_id"])) for row in rows]
