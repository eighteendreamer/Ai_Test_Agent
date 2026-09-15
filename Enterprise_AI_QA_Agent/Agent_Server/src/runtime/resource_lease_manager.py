"""Atomic Redis resource leases for Browser/Docker/account/environment isolation."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis

from src.runtime.resource_lease_scripts import CLAIM, RELEASE, RENEW


@dataclass(frozen=True)
class ResourceLease:
    resource_id: str
    resource_type: str
    project_id: str | None
    session_id: str | None
    run_id: str | None
    run_item_id: str | None
    attempt_id: str | None
    worker_id: str
    lease_token: str
    fencing_token: int
    lease_expire_at: datetime


class ResourceUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RedisResourceLeaseManager:
    """One Redis-backed ownership contract shared by every resource type."""

    def __init__(self, redis_url: str, *, socket_timeout_seconds: float = 5.0, client: Redis | Any | None = None) -> None:
        self._redis_url = redis_url
        self._client = client
        self._socket_timeout_seconds = socket_timeout_seconds
        if socket_timeout_seconds <= 0:
            raise ValueError("Resource lease Redis timeout must be positive")

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=True,
                                          socket_timeout=self._socket_timeout_seconds,
                                          socket_connect_timeout=self._socket_timeout_seconds)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def configure_quota(self, *, scope: str, identifier: str, limit: int) -> None:
        field = _quota_field(scope, identifier)
        await self._client.hset("qa:quota:limits", field, max(0, int(limit)))

    async def acquire(
        self,
        *,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        lease_seconds: int,
        project_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        run_item_id: str | None = None,
        attempt_id: str | None = None,
    ) -> ResourceLease:
        _validate_part(resource_type, "resource_type")
        _validate_part(resource_id, "resource_id")
        _validate_part(worker_id, "worker_id")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        quota_fields = [_quota_field("global", "all"), _quota_field("resource_type", resource_type)]
        if project_id:
            quota_fields.append(_quota_field("project", project_id))
        if run_id:
            quota_fields.append(_quota_field("run", run_id))
        lease_token = uuid.uuid4().hex
        payload = json.dumps({
            "resource_id": resource_id, "resource_type": resource_type,
            "project_id": project_id, "session_id": session_id, "run_id": run_id,
            "run_item_id": run_item_id, "attempt_id": attempt_id, "worker_id": worker_id,
            "lease_token": lease_token,
        }, ensure_ascii=False, separators=(",", ":"))
        result = await self._client.eval(
            CLAIM, 6, self._lease_key(resource_type, resource_id),
            "qa:fencing:" + resource_type, "qa:quota:usage", "qa:quota:limits",
            "qa:lease:registry", "qa:lease:metadata",
            payload, lease_token, int(lease_seconds * 1000), *quota_fields,
        )
        if not result or int(result[0]) == 0:
            reason = result[1] if len(result) > 1 else "resource_busy"
            if isinstance(reason, bytes):
                reason = reason.decode("utf-8", errors="replace")
            raise ResourceUnavailable(str(reason or "resource_busy"))
        fencing = int(result[0])
        return ResourceLease(
            resource_id=resource_id, resource_type=resource_type, project_id=project_id,
            session_id=session_id, run_id=run_id, run_item_id=run_item_id,
            attempt_id=attempt_id, worker_id=worker_id, lease_token=lease_token,
            fencing_token=fencing,
            lease_expire_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds),
        )

    async def renew(self, lease: ResourceLease, *, lease_seconds: int) -> bool:
        result = await self._client.eval(
            RENEW, 2, self._lease_key(lease.resource_type, lease.resource_id), "qa:lease:registry",
            lease.lease_token, int(lease_seconds * 1000),
        )
        return bool(result)

    async def acquire_first_available(self, *, resource_type: str, resource_ids: list[str], **kwargs) -> ResourceLease:
        """Atomically claim the first free slot; all slots share normal quotas."""
        if not resource_ids:
            raise ResourceUnavailable(f"{resource_type}_capacity")
        last_reason = f"{resource_type}_capacity"
        for resource_id in resource_ids:
            try:
                return await self.acquire(resource_type=resource_type, resource_id=resource_id, **kwargs)
            except ResourceUnavailable as exc:
                last_reason = exc.reason
        raise ResourceUnavailable(last_reason or f"{resource_type}_capacity")

    async def release(self, lease: ResourceLease) -> bool:
        quota_fields = [_quota_field("global", "all"), _quota_field("resource_type", lease.resource_type)]
        if lease.project_id:
            quota_fields.append(_quota_field("project", lease.project_id))
        if lease.run_id:
            quota_fields.append(_quota_field("run", lease.run_id))
        result = await self._client.eval(
            RELEASE, 4, self._lease_key(lease.resource_type, lease.resource_id),
            "qa:quota:usage", "qa:lease:registry", "qa:lease:metadata",
            lease.lease_token, *quota_fields,
        )
        return bool(result)

    async def get(self, *, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        raw = await self._client.get(self._lease_key(resource_type, resource_id))
        return json.loads(raw) if raw else None

    @staticmethod
    def _lease_key(resource_type: str, resource_id: str) -> str:
        return f"qa:lease:{resource_type}:{resource_id}"


def _quota_field(scope: str, identifier: str) -> str:
    _validate_part(scope, "quota_scope")
    _validate_part(identifier, "quota_identifier")
    return f"{scope}:{identifier}"


def _validate_part(value: str, label: str) -> None:
    if not value or not re.fullmatch(r"[A-Za-z0-9_.:-]+", str(value)):
        raise ValueError(f"{label} contains unsupported characters")
