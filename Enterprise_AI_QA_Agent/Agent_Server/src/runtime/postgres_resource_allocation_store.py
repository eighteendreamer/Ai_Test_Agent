"""Durable intent written before an external resource can be created.

Keep intents after cleanup: a Docker create response can be lost or arrive
after a first inventory sweep. Labels plus this immutable record identify it.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict

from psycopg import sql

from src.infrastructure.postgres_runtime import postgres_connect


class PostgresResourceAllocationStore:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._table = sql.Identifier(settings.database.postgres_resource_allocation_table)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    allocation_id TEXT PRIMARY KEY,
                    lease_namespace TEXT NOT NULL,
                    lease_record JSONB NOT NULL,
                    container_name TEXT NOT NULL,
                    container_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    bound_at TIMESTAMPTZ
                )
            """).format(self._table))

    async def prepare(self, allocation_id, lease, *, namespace, container_name) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", allocation_id):
            raise ValueError("Invalid Docker allocation ID")
        record = asdict(lease)
        record["lease_expire_at"] = lease.lease_expire_at.isoformat()
        record["allocation_id"] = allocation_id
        await asyncio.to_thread(self._prepare_sync, allocation_id, record, namespace, container_name)

    def _prepare_sync(self, allocation_id, record, namespace, container_name) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {} (allocation_id, lease_namespace, lease_record, container_name)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (allocation_id) DO NOTHING
            """).format(self._table), (allocation_id, namespace, json.dumps(record), container_name))
            cur.execute(sql.SQL("SELECT * FROM {} WHERE allocation_id = %s").format(self._table), (allocation_id,))
            row = cur.fetchone()
            if row["lease_record"] != record or row["lease_namespace"] != namespace or row["container_name"] != container_name:
                raise ValueError("Docker allocation intent cannot be reassigned")

    async def bind(self, allocation_id: str, container_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("Docker allocation requires an immutable full container ID")
        await asyncio.to_thread(self._bind_sync, allocation_id, container_id)

    def _bind_sync(self, allocation_id, container_id) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                UPDATE {} SET container_id = %s, bound_at = COALESCE(bound_at, NOW())
                WHERE allocation_id = %s AND (container_id IS NULL OR container_id = %s)
            """).format(self._table), (container_id, allocation_id, container_id))
            if cur.rowcount != 1:
                raise ValueError("Docker allocation binding missing or already assigned")

    async def get_many(self, allocation_ids: list[str], *, namespace: str) -> dict:
        if not allocation_ids:
            return {}
        return await asyncio.to_thread(self._get_many_sync, allocation_ids, namespace)

    def _get_many_sync(self, allocation_ids, namespace) -> dict:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT * FROM {} WHERE allocation_id = ANY(%s) AND lease_namespace = %s").format(
                self._table), (allocation_ids, namespace))
            return {row["allocation_id"]: row for row in cur.fetchall()}
