"""Durable native-browser intent, independent of the owning API process."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict

from psycopg import sql

from src.infrastructure.postgres_runtime import postgres_connect

LOGGER = logging.getLogger(__name__)


class PostgresBrowserAllocationStore:
    def __init__(self, settings):
        self._settings = settings
        self._table = sql.Identifier(settings.database.postgres_browser_allocation_table)

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    allocation_id TEXT PRIMARY KEY,
                    generation BIGSERIAL NOT NULL,
                    lease_namespace TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    lease_record JSONB NOT NULL,
                    processes JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """).format(self._table))
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS generation BIGSERIAL").format(self._table))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (lease_namespace, host_id, session_name)").format(
                sql.Identifier(self._settings.database.postgres_browser_allocation_table + "_scope_idx"), self._table))

    async def prepare(self, allocation_id, lease, *, namespace, host_id, session_name, recording_id=None):
        if not re.fullmatch(r"[a-f0-9]{32}", allocation_id) or lease.resource_type != "browser":
            raise ValueError("Invalid browser allocation")
        record = asdict(lease)
        record["lease_expire_at"] = lease.lease_expire_at.isoformat()
        record.update(allocation_id=allocation_id, external_resource_id=allocation_id)
        if recording_id is not None:
            record["recording_id"] = recording_id
        return await asyncio.to_thread(self._prepare_sync, allocation_id, namespace, host_id, session_name, record)

    def _prepare_sync(self, allocation_id, namespace, host_id, session_name, record):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {} (allocation_id, lease_namespace, host_id, session_name, lease_record)
                VALUES (%s, %s, %s, %s, %s::jsonb) ON CONFLICT (allocation_id) DO NOTHING
            """).format(self._table), (allocation_id, namespace, host_id, session_name, json.dumps(record)))
            cur.execute(sql.SQL("SELECT * FROM {} WHERE allocation_id = %s").format(self._table), (allocation_id,))
            row = cur.fetchone()
            if (row["lease_record"] != record or row["lease_namespace"] != namespace
                    or row["host_id"] != host_id or row["session_name"] != session_name):
                raise ValueError("Browser allocation intent cannot be reassigned")
            if record.get("recording_id"):
                # Lock first, then check real time: NOW() is transaction-start
                # time and an UPDATE predicate can be evaluated before a wait.
                cur.execute(sql.SQL("SELECT id FROM {} WHERE id = %s FOR UPDATE").format(
                    sql.Identifier(self._settings.database.postgres_recording_table)), (record["recording_id"],))
                cur.execute(sql.SQL("""
                    UPDATE {} SET metadata = metadata || %s::jsonb, updated_at = NOW()
                    WHERE id = %s AND project_id = %s AND driver_kind = 'playwright-managed'
                        AND status = 'launching'
                        AND launch_expires_at > clock_timestamp()
                        AND metadata->>'browser_lease_namespace' = %s AND metadata->>'browser_host_id' = %s
                        AND (metadata->>'browser_allocation_id' IS NULL OR metadata->>'browser_allocation_id' = %s)
                """).format(sql.Identifier(self._settings.database.postgres_recording_table)),
                    (json.dumps({"browser_allocation_id": allocation_id}), record["recording_id"],
                     record["project_id"], namespace, host_id, allocation_id))
                if cur.rowcount != 1:
                    raise ValueError("Recording launch is expired, terminal, missing or owned by another browser allocation")
            return int(row["generation"])

    async def bind(self, allocation_id, processes):
        await asyncio.to_thread(self._bind_sync, allocation_id, processes)

    def _bind_sync(self, allocation_id, processes):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                UPDATE {} SET processes = %s::jsonb WHERE allocation_id = %s
                AND (processes = '[]'::jsonb OR processes = %s::jsonb)
            """).format(self._table), (json.dumps(processes), allocation_id, json.dumps(processes)))
            if cur.rowcount != 1:
                raise ValueError("Browser allocation intent is missing or already bound")

    async def get_many(self, allocation_ids, *, namespace):
        if not allocation_ids:
            return {}
        return await asyncio.to_thread(self._get_many_sync, allocation_ids, namespace)

    def _get_many_sync(self, allocation_ids, namespace):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT * FROM {} WHERE allocation_id = ANY(%s) AND lease_namespace = %s").format(self._table),
                        (allocation_ids, namespace))
            return {row["allocation_id"]: row for row in cur.fetchall()}

    async def get_for_scope(self, session_name, *, namespace):
        return await asyncio.to_thread(self._get_for_scope_sync, session_name, namespace)

    def _get_for_scope_sync(self, session_name, namespace):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT * FROM {} WHERE lease_namespace = %s AND session_name = %s
                ORDER BY created_at DESC LIMIT 1
            """).format(self._table), (namespace, session_name))
            return cur.fetchone()

    async def mark_session_released(self, allocation):
        await asyncio.to_thread(self._mark_session_released_sync, allocation)

    def _mark_session_released_sync(self, allocation):
        recording_changed = False
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                UPDATE {} SET status = 'released', released_at = NOW(), updated_at = NOW(),
                    metadata = metadata || %s::jsonb
                WHERE session_id = %s AND kind = 'browser_session' AND resource_key = %s
                    AND metadata->>'allocation_id' = %s AND status <> 'released'
            """).format(sql.Identifier(self._settings.database.postgres_session_resource_table)),
                (json.dumps({"release_reason": "browser_process_cleanup"}), allocation["lease_record"]["session_id"],
                 allocation["session_name"], allocation["allocation_id"]))
            recording_id = allocation["lease_record"].get("recording_id")
            if recording_id:
                cur.execute(sql.SQL("""
                    UPDATE {} SET status = 'failed', ended_at = NOW(), updated_at = NOW(),
                        finalize_metrics = finalize_metrics || %s::jsonb
                    WHERE id = %s AND metadata->>'browser_allocation_id' = %s
                        AND status NOT IN ('completed', 'failed', 'discarded')
                """).format(sql.Identifier(self._settings.database.postgres_recording_table)),
                    (json.dumps({"failure_reason": "browser_resource_recovered",
                                 "browser_allocation_id": allocation["allocation_id"]}),
                     recording_id, allocation["allocation_id"]))
                recording_changed = cur.rowcount == 1
        if recording_changed:
            LOGGER.warning("recording_resource_recovered", extra={
                "recording_id": recording_id, "allocation_id": allocation["allocation_id"],
                "session_id": allocation["lease_record"]["session_id"],
                "project_id": allocation["lease_record"].get("project_id"),
                "resource_id": allocation["lease_record"]["resource_id"],
            })

    async def pending_recordings(self, *, namespace, host_id, after_generation, limit):
        return await asyncio.to_thread(self._pending_recordings_sync, namespace, host_id, after_generation, limit)

    def _pending_recordings_sync(self, namespace, host_id, after_generation, limit):
        # Include pre-launch intents with no PID: a killed owner may never have
        # reached Redis allocation binding or OS process creation.
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT a.* FROM {} a JOIN {} r ON r.id = a.lease_record->>'recording_id'
                WHERE a.lease_namespace = %s AND a.host_id = %s AND a.generation > %s
                    AND r.metadata->>'browser_allocation_id' = a.allocation_id
                    AND r.status NOT IN ('completed', 'failed', 'discarded')
                ORDER BY a.generation LIMIT %s
            """).format(self._table, sql.Identifier(self._settings.database.postgres_recording_table)),
                (namespace, host_id, after_generation, limit))
            return cur.fetchall()
