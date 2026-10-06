"""Project-owned business environments and append-only target captures."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.business_environment import (
    BusinessEnvironmentRecord, BusinessEnvironmentSnapshot, BusinessTargetConfigurationRecord,
    BusinessEnvironmentPage, BusinessEnvironmentSnapshotPage,
)


def environment_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def environment_request_digest(project_id, kind, request, environment_id=None):
    return environment_digest({"version": "business-environment-v1", "project_id": project_id,
        "kind": kind, "environment_id": environment_id, "request": request.model_dump(mode="json")})


def environment_identity(project_id, kind, request_id):
    return str(uuid5(NAMESPACE_URL, json.dumps(["business-environment-v1", project_id, kind, request_id],
        ensure_ascii=False)))


def fixed_target_configuration(value):
    value = BusinessTargetConfigurationRecord.model_validate(value)
    if (value.scope_key != value.environment_id or value.sha256 !=
        environment_digest(value.model_dump(mode="json", exclude={"sha256"}))):
        raise ValueError("环境目标配置作用域或固定哈希不同")
    return value


def fixed_environment_snapshot(value):
    value = BusinessEnvironmentSnapshot.model_validate(value)
    config = fixed_target_configuration(value.configuration)
    if (config.project_id != value.project_id or config.environment_id != value.environment_id
        or value.request.expected_config_revision != config.revision
        or value.request.expected_config_sha256 != config.sha256
        or value.request_sha256 != environment_request_digest(value.project_id, "capture", value.request, value.environment_id)
        or value.id != environment_identity(value.project_id, "capture", value.request.request_id)
        or value.sha256 != environment_digest(value.model_dump(mode="json", exclude={"sha256"}))):
        raise ValueError("固定环境依据的项目、配置、请求或哈希不同")
    return value


class PostgresBusinessEnvironmentStore:
    def __init__(self, settings):
        self._settings = settings
        self._projects = settings.database.postgres_project_table
        def derived(suffix):
            candidate = self._projects + "_" + suffix
            # Existing project table names may already use all 63 bytes.
            # Match the short deterministic naming used by requirement indexes.
            return candidate if len(candidate) <= 63 else "be_" + hashlib.sha256(self._projects.encode()).hexdigest()[:16] + "_" + suffix
        self._environments = derived("environments")
        self._snapshots = derived("environment_snapshots")
        for name in [self._projects, *self.tables]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid business environment table identifier")

    @property
    def tables(self):
        return [self._snapshots, self._environments]

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._environments} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    environment_key TEXT NOT NULL, request_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
                    record JSONB NOT NULL, UNIQUE(project_id,environment_key),
                    UNIQUE(project_id,request_id), UNIQUE(id,project_id))""")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._snapshots} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL, environment_id UUID NOT NULL,
                    request_id TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    UNIQUE(project_id,request_id),
                    FOREIGN KEY(environment_id,project_id) REFERENCES {self._environments}(id,project_id))""")
                index = "idx_env_capture_" + hashlib.sha256(self._snapshots.encode()).hexdigest()[:16]
                cur.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {self._snapshots}(project_id,environment_id,captured_at,id)")
        await asyncio.to_thread(create)

    @staticmethod
    def _environment(row):
        value = BusinessEnvironmentRecord.model_validate(row["record"])
        if (value.id != str(row["id"]) or value.project_id != str(row["project_id"])
            or value.request.environment_key != row["environment_key"] or value.request.request_id != row["request_id"]
            or value.created_at != row["created_at"]
            or value.id != environment_identity(value.project_id, "register", value.request.request_id)
            or value.request_sha256 != environment_request_digest(value.project_id, "register", value.request)):
            raise ValueError("环境登记身份或原请求哈希不同")
        return value

    @staticmethod
    def _snapshot(row):
        value = fixed_environment_snapshot(row["record"])
        if (value.id != str(row["id"]) or value.project_id != str(row["project_id"])
            or value.environment_id != str(row["environment_id"]) or value.request.request_id != row["request_id"]
            or value.captured_at != row["captured_at"]):
            raise ValueError("固定环境依据与持久目录不同")
        return value

    def _get(self, cur, project_id, environment_id):
        cur.execute(f"SELECT * FROM {self._environments} WHERE project_id=%s AND id=%s", (project_id, environment_id))
        row = cur.fetchone()
        return self._environment(row) if row else None

    def _by_request(self, cur, table, project_id, request_id, digest=None):
        cur.execute(f"SELECT * FROM {table} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
        row = cur.fetchone()
        value = (self._environment(row) if table == self._environments else self._snapshot(row)) if row else None
        if value and digest is not None and value.request_sha256 != digest:
            raise ValueError("相同环境请求编号不能改变参数或所属环境")
        return value

    async def get(self, project_id, environment_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._get(cur, project_id, environment_id)
        return await asyncio.to_thread(read)

    async def get_by_request(self, project_id, request_id, *, capture=False, digest=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._by_request(cur, self._snapshots if capture else self._environments,
                    project_id, request_id, digest)
        return await asyncio.to_thread(read)

    def _project(self, cur, project_id):
        cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (project_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError("当前项目不存在")
        return row

    async def register(self, project_id, request):
        def save():
            digest = environment_request_digest(project_id, "register", request)
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                project = self._project(cur, project_id)
                prior = self._by_request(cur, self._environments, project_id, request.request_id, digest)
                if prior:
                    return prior
                if project["status"] != "active":
                    raise ValueError("已归档项目不能登记新环境")
                cur.execute(f"SELECT id FROM {self._environments} WHERE project_id=%s AND environment_key=%s",
                    (project_id, request.environment_key))
                if cur.fetchone():
                    raise ValueError("当前项目已有这个环境key，请使用原环境身份")
                now = datetime.now(timezone.utc)
                value = BusinessEnvironmentRecord(id=environment_identity(project_id, "register", request.request_id),
                    project_id=project_id, request=request, request_sha256=digest, created_at=now, updated_at=now)
                cur.execute(f"INSERT INTO {self._environments}(id,project_id,environment_key,request_id,created_at,record) "
                    "VALUES(%s,%s,%s,%s,%s,%s)", (value.id, project_id, request.environment_key, request.request_id,
                    now, Jsonb(value.model_dump(mode="json"))))
                return value
        return await asyncio.to_thread(save)

    async def archive(self, project_id, environment_id):
        def save():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                project = self._project(cur, project_id)
                value = self._get(cur, project_id, environment_id)
                if value is None:
                    raise KeyError("环境不属于当前项目")
                if value.status == "archived":
                    return value
                if project["status"] != "active":
                    raise ValueError("已归档项目不能变更环境状态")
                now = datetime.now(timezone.utc)
                value = value.model_copy(update={"status": "archived", "archived_at": now, "updated_at": now})
                cur.execute(f"UPDATE {self._environments} SET record=%s WHERE project_id=%s AND id=%s",
                    (Jsonb(value.model_dump(mode="json")), project_id, environment_id))
                return value
        return await asyncio.to_thread(save)

    async def capture(self, value):
        value = fixed_environment_snapshot(value)
        def save():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                project = self._project(cur, value.project_id)
                prior = self._by_request(cur, self._snapshots, value.project_id, value.request.request_id, value.request_sha256)
                if prior:
                    return prior
                registered = self._get(cur, value.project_id, value.environment_id)
                if registered is None:
                    raise KeyError("环境不属于当前项目")
                if registered.request.environment_key != value.environment_key or registered.request_sha256 != value.registration_request_sha256:
                    raise ValueError("固定环境依据与原登记不同")
                if project["status"] != "active" or registered.status != "active":
                    raise ValueError("已归档项目或环境不能捕获新依据")
                cur.execute(f"INSERT INTO {self._snapshots}(id,project_id,environment_id,request_id,captured_at,record) "
                    "VALUES(%s,%s,%s,%s,%s,%s)", (value.id, value.project_id, value.environment_id,
                    value.request.request_id, value.captured_at, Jsonb(value.model_dump(mode="json"))))
                return value
        return await asyncio.to_thread(save)

    async def get_snapshot(self, project_id, environment_id, snapshot_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._snapshots} WHERE project_id=%s AND environment_id=%s AND id=%s",
                    (project_id, environment_id, snapshot_id))
                row = cur.fetchone()
                return self._snapshot(row) if row else None
        return await asyncio.to_thread(read)

    async def list(self, project_id, *, environment_id=None, status=None, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("环境分页参数无效")
        def read():
            capture = environment_id is not None
            table, order = (self._snapshots, "captured_at") if capture else (self._environments, "created_at")
            clause, args = "project_id=%s", [project_id]
            if capture:
                clause += " AND environment_id=%s"
                args.append(environment_id)
            elif status:
                clause += " AND record->>'status'=%s"
                args.append(status)
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} WHERE {clause} ORDER BY {order},id LIMIT %s OFFSET %s",
                    (*args, limit+1, offset))
                rows = cur.fetchall()
            items = [(self._snapshot(row) if capture else self._environment(row)) for row in rows[:limit]]
            page = BusinessEnvironmentSnapshotPage if capture else BusinessEnvironmentPage
            return page(items=items, limit=limit, offset=offset, has_more=len(rows) > limit)
        return await asyncio.to_thread(read)
