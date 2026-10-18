"""Append-only encrypted API credentials, frozen inputs and original results."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from src.infrastructure.channel_config_store import ChannelCredentialCodec
from src.infrastructure.postgres_runtime import postgres_connect


class ApiCredentialReferenceError(ValueError):
    """Public reason codes contain no decrypted content or configured keys."""


class PostgresApiCredentialStore:
    def __init__(self, settings):
        self._settings = settings
        self._codec = ChannelCredentialCodec(settings.database.channel_credential_encryption_key)
        self._sessions = settings.database.postgres_session_table
        self._projects = settings.database.postgres_project_table
        # Existing counterpart: PostgresBusinessEnvironmentStore derived names.
        def derived(suffix):
            candidate = self._sessions + "_" + suffix
            return candidate if len(candidate) <= 63 else "ac_" + hashlib.sha256(self._sessions.encode()).hexdigest()[:16] + "_" + suffix
        self._credentials = derived("api_credentials")
        self._dispatches = derived("api_dispatch_inputs")
        self._results = derived("api_worker_results")
        for name in (self._sessions, self._projects, *self.tables):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid API credential table identifier")

    @property
    def tables(self):
        return [self._dispatches, self._credentials, self._results]

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            for table in self.tables:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY,
                    owner_session_id TEXT NOT NULL REFERENCES {self._sessions}(id) ON DELETE CASCADE,
                    project_id UUID NULL REFERENCES {self._projects}(id),
                    ciphertext TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")

    async def save_credential(self, payload, *, owner_session_id, project_id):
        ref = payload["credential_session_id"]
        await self._put(self._credentials, ref, payload, owner_session_id, project_id)
        return ref

    async def load_credential(self, ref, *, owner_session_id, project_id):
        return await self._get(self._credentials, ref, owner_session_id, project_id)

    async def save_dispatch(self, payload, *, owner_session_id, project_id):
        ref = str(uuid5(NAMESPACE_URL, json.dumps(["api-dispatch-input-v1", owner_session_id,
            payload["parent_turn_id"], payload["dispatch_task_id"]])))
        await self._put(self._dispatches, ref, payload, owner_session_id, project_id)
        return ref

    async def load_dispatch(self, ref, *, owner_session_id, project_id):
        return await self._get(self._dispatches, ref, owner_session_id, project_id)

    async def save_worker_result(self, payload, *, owner_session_id, project_id):
        ref = str(uuid5(NAMESPACE_URL, json.dumps(["api-worker-result-v1", owner_session_id,
            payload["child_session_id"], payload["tool_job_id"]])))
        await self._put(self._results, ref, payload, owner_session_id, project_id)
        return ref

    async def load_worker_result(self, ref, *, owner_session_id, project_id):
        return await self._get(self._results, ref, owner_session_id, project_id)

    def _encode(self, payload):
        if not self._codec.available:
            raise ApiCredentialReferenceError("api_credential_key_unavailable")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return self._codec.encrypt({"payload": raw}), hashlib.sha256(raw.encode()).hexdigest()

    def _decode(self, ciphertext, digest):
        if not self._codec.available:
            raise ApiCredentialReferenceError("api_credential_key_unavailable")
        try:
            raw = self._codec.decrypt(ciphertext)["payload"]
            if hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("digest mismatch")
            return json.loads(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiCredentialReferenceError("api_credential_decryption_failed") from exc

    async def _put(self, table, ref, payload, owner, project):
        ciphertext, digest = self._encode(payload)
        def put():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""INSERT INTO {table} (id, owner_session_id, project_id, ciphertext, content_sha256)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                    (ref, owner, project, ciphertext, digest))
                cur.execute(f"SELECT * FROM {table} WHERE id=%s", (ref,))
                row = cur.fetchone()
                if (row["owner_session_id"] != owner or str(row["project_id"] or "") != str(project or "")
                        or row["content_sha256"] != digest):
                    raise ApiCredentialReferenceError("api_credential_identity_conflict")
                # Confirm old immutable ciphertext is readable under the current key.
                self._decode(row["ciphertext"], row["content_sha256"])
        await asyncio.to_thread(put)

    async def _get(self, table, ref, owner, project):
        def get():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""SELECT ciphertext,content_sha256 FROM {table}
                    WHERE id=%s AND owner_session_id=%s AND project_id IS NOT DISTINCT FROM %s::uuid""",
                    (ref, owner, project))
                row = cur.fetchone()
                if not row:
                    raise ApiCredentialReferenceError("api_credential_reference_unavailable")
                return self._decode(row["ciphertext"], row["content_sha256"])
        return await asyncio.to_thread(get)
