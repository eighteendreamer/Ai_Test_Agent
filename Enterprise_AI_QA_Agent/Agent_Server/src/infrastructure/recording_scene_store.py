"""Project-owned scene heads and append-only versions; recording rows remain immutable."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.recording_scene import SceneBundle, SceneVersion

logger = logging.getLogger(__name__)


def scene_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class PostgresRecordingSceneStore:
    def __init__(self, settings):
        self._settings = settings
        self._heads = settings.database.postgres_recording_scene_table
        self._versions = settings.database.postgres_recording_scene_version_table

    async def initialize(self):
        await asyncio.to_thread(self._initialize)

    def _initialize(self):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._heads} (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, current_version_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(project_id, request_id))""")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._versions} (
                id TEXT PRIMARY KEY, scene_id TEXT NOT NULL REFERENCES {self._heads}(id),
                project_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
                request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL,
                UNIQUE(scene_id, version), UNIQUE(scene_id, request_id))""")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._heads}_project ON {self._heads}(project_id, updated_at DESC, id)")
        logger.info("recording_scene_store_initialized")

    def _bundle(self, head, version):
        value = SceneVersion.model_validate(version["payload"])
        if (value.id != version["id"] or value.scene_id != head["id"] or value.project_id != head["project_id"]
            or value.version != version["version"] or value.request_id != version["request_id"]
            or value.request_hash != version["request_hash"]
            or scene_hash({"content":value.content.model_dump(mode="json"),"source":value.source}) != value.content_hash):
            logger.error("recording_scene_snapshot_invalid", extra={"scene_id":head["id"],"version_id":version["id"]})
            raise ValueError("scene snapshot identity or hash mismatch")
        return SceneBundle(scene_id=head["id"], project_id=head["project_id"],
            current_version_id=head["current_version_id"], version=value)

    async def get(self, project_id, scene_id, version_id=None):
        return await asyncio.to_thread(self._get, project_id, scene_id, version_id)

    def _get(self, project_id, scene_id, version_id=None):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._heads} WHERE id=%s AND project_id=%s", (scene_id, project_id))
            head = cur.fetchone()
            if not head: raise KeyError("scene not found in project")
            cur.execute(f"SELECT * FROM {self._versions} WHERE id=%s AND scene_id=%s AND project_id=%s",
                (version_id or head["current_version_id"], scene_id, project_id))
            version = cur.fetchone()
            if not version: raise KeyError("scene version not found")
            return self._bundle(head, version)

    async def by_request(self, project_id, request_id):
        return await asyncio.to_thread(self._by_request, project_id, request_id)

    def _by_request(self, project_id, request_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._heads} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
            head = cur.fetchone()
            if not head: return None
            cur.execute(f"SELECT * FROM {self._versions} WHERE scene_id=%s AND version=1", (head["id"],))
            return self._bundle(head, cur.fetchone())

    async def list(self, project_id, limit, offset):
        return await asyncio.to_thread(self._list, project_id, limit, offset)

    def _list(self, project_id, limit, offset):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT h.*, v.payload FROM {self._heads} h JOIN {self._versions} v ON v.id=h.current_version_id WHERE h.project_id=%s ORDER BY h.updated_at DESC,h.id LIMIT %s OFFSET %s", (project_id,limit+1,offset))
            rows = cur.fetchall()
        return {"items":[{"scene_id":r["id"],"project_id":r["project_id"],"current_version_id":r["current_version_id"],
            "name":r["payload"]["content"]["name"],"status":r["payload"]["content"]["status"],
            "version":r["payload"]["version"],"step_count":len(r["payload"]["content"]["steps"])} for r in rows[:limit]],
            "has_more":len(rows)>limit,"limit":limit,"offset":offset}

    async def versions(self, project_id, scene_id):
        return await asyncio.to_thread(self._history, project_id, scene_id)

    async def version_by_request(self, project_id, scene_id, request_id):
        return await asyncio.to_thread(self._version_by_request, project_id, scene_id, request_id)

    def _version_by_request(self, project_id, scene_id, request_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._heads} WHERE id=%s AND project_id=%s", (scene_id,project_id))
            head = cur.fetchone()
            if not head: raise KeyError("scene not found in project")
            cur.execute(f"SELECT * FROM {self._versions} WHERE scene_id=%s AND request_id=%s", (scene_id,request_id))
            value = cur.fetchone()
            return self._bundle(head,value) if value else None

    def _history(self, project_id, scene_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._heads} WHERE id=%s AND project_id=%s", (scene_id,project_id))
            if not cur.fetchone(): raise KeyError("scene not found in project")
            cur.execute(f"SELECT id,version,created_at FROM {self._versions} WHERE scene_id=%s AND project_id=%s ORDER BY version DESC", (scene_id,project_id))
            return [dict(r) for r in cur.fetchall()]

    async def save(self, version, *, create=False):
        return await asyncio.to_thread(self._save, version, create)

    def _save(self, version, create):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            if create:
                cur.execute(f"""INSERT INTO {self._heads}(id,project_id,request_id,request_hash,current_version_id,created_at,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(project_id,request_id) DO NOTHING""",
                    (version.scene_id,version.project_id,version.request_id,version.request_hash,version.id,version.created_at,version.created_at))
                cur.execute(f"SELECT * FROM {self._heads} WHERE project_id=%s AND request_id=%s FOR UPDATE", (version.project_id,version.request_id))
            else:
                cur.execute(f"SELECT * FROM {self._heads} WHERE id=%s AND project_id=%s FOR UPDATE", (version.scene_id,version.project_id))
            head = cur.fetchone()
            if not head: raise KeyError("scene not found in project")
            cur.execute(f"SELECT * FROM {self._versions} WHERE scene_id=%s AND request_id=%s", (head["id"],version.request_id))
            original = cur.fetchone()
            if original:
                if original["request_hash"] != version.request_hash: raise ValueError("scene request identity reused with different content")
                return self._bundle(head, original)
            if not create and head["current_version_id"] != version.parent_version_id:
                logger.warning("recording_scene_parent_conflict", extra={"scene_id":version.scene_id,"parent_version_id":version.parent_version_id})
                raise ValueError("scene has a newer version; reload before saving")
            if create and head["id"] != version.scene_id: raise ValueError("incomplete scene creation")
            cur.execute(f"INSERT INTO {self._versions}(id,scene_id,project_id,version,request_id,request_hash,created_at,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (version.id,version.scene_id,version.project_id,version.version,version.request_id,version.request_hash,version.created_at,json.dumps(version.model_dump(mode="json"),ensure_ascii=False)))
            cur.execute(f"UPDATE {self._heads} SET current_version_id=%s,updated_at=%s WHERE id=%s", (version.id,version.created_at,version.scene_id))
            head = {**head,"current_version_id":version.id}
            result = self._bundle(head,{"id":version.id,"version":version.version,"request_id":version.request_id,"request_hash":version.request_hash,"payload":version.model_dump(mode="json")})
        logger.info("recording_scene_version_saved", extra={"scene_id":version.scene_id,"project_id":version.project_id,"version_id":version.id,"version":version.version})
        return result
