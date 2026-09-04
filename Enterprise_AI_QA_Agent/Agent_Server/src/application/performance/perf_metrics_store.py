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
            logger.warning(f"Failed to initialize perf metrics table: {e}")
            return False

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        run_id TEXT PRIMARY KEY,
                        plan_id TEXT NOT NULL,
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
    ) -> bool:
        if not await self.initialize():
            return False

        try:
            await asyncio.to_thread(
                self._save_run_sync,
                run,
                metrics.model_dump() if metrics else None,
                verdict,
                target_url,
                run_intent,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to save perf run: {e}")
            return False

    def _save_run_sync(
        self,
        run: PerfRun,
        metrics_data: dict[str, Any] | None,
        verdict: str | None,
        target_url: str,
        run_intent: str,
    ) -> None:
        metrics_json = json.dumps(metrics_data) if metrics_data else None
        started_at = datetime.fromisoformat(run.started_at) if run.started_at else None
        completed_at = datetime.fromisoformat(run.completed_at) if run.completed_at else None
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table}
                        (run_id, plan_id, engine, backend, status, run_intent, target_url,
                         metrics_json, verdict, started_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        metrics_json = EXCLUDED.metrics_json,
                        verdict = EXCLUDED.verdict,
                        completed_at = EXCLUDED.completed_at
                    """,
                    (
                        run.run_id,
                        run.plan_id,
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

    async def get_baseline(self, target_url: str) -> PerfMetrics | None:
        """Get the most recent successful baseline metrics for a target URL."""
        if not await self.initialize():
            return None

        try:
            data = await asyncio.to_thread(self._get_baseline_sync, target_url)
            return PerfMetrics(**data) if data else None
        except Exception as e:
            logger.warning(f"Failed to get baseline: {e}")
            return None

    def _get_baseline_sync(self, target_url: str) -> dict[str, Any] | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT metrics_json FROM {self._table}
                    WHERE target_url = %s
                      AND status = 'completed'
                      AND verdict IN ('baseline', 'pass')
                      AND metrics_json IS NOT NULL
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (target_url,),
                )
                row = cur.fetchone()
        if not row or not row["metrics_json"]:
            return None
        return json.loads(row["metrics_json"]) if isinstance(row["metrics_json"], str) else row["metrics_json"]

    async def list_runs(
        self,
        target_url: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not await self.initialize():
            return []

        try:
            return await asyncio.to_thread(self._list_runs_sync, target_url, limit)
        except Exception as e:
            logger.warning(f"Failed to list runs: {e}")
            return []

    def _list_runs_sync(self, target_url: str | None, limit: int) -> list[dict[str, Any]]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if target_url:
                    cur.execute(
                        f"""
                        SELECT run_id, plan_id, engine, status, run_intent, target_url,
                               verdict, started_at, completed_at
                        FROM {self._table}
                        WHERE target_url = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (target_url, limit),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT run_id, plan_id, engine, status, run_intent, target_url,
                               verdict, started_at, completed_at
                        FROM {self._table}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
        return [dict(row) for row in rows]
