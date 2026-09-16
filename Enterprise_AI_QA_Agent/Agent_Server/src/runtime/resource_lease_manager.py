"""Atomic Redis resource leases for Browser/Docker/account/environment isolation."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis

from src.core.request_context import get_request_context, new_id
from src.runtime.resource_lease_scripts import (
    BIND,
    CLAIM,
    REAP,
    RECONCILE_QUOTA,
    RELEASE,
    RENEW,
)


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
    request_id: str | None = None
    trace_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ExpiredResourceLease:
    resource_id: str
    resource_type: str
    external_resource_id: str | None
    project_id: str | None
    session_id: str | None
    turn_id: str | None
    run_id: str | None
    run_item_id: str | None
    attempt_id: str | None
    worker_id: str
    lease_token: str
    fencing_token: int
    request_id: str
    trace_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ExpiredResourceLease":
        required = ("resource_id", "resource_type", "worker_id", "lease_token")
        missing = [field for field in required if not str(payload.get(field) or "").strip()]
        if missing:
            raise ValueError(f"Expired resource lease is missing: {', '.join(missing)}")
        lease_token = str(payload["lease_token"])
        return cls(
            resource_id=str(payload["resource_id"]),
            resource_type=str(payload["resource_type"]),
            external_resource_id=_optional_text(payload.get("external_resource_id")),
            project_id=_optional_text(payload.get("project_id")),
            session_id=_optional_text(payload.get("session_id")),
            turn_id=_optional_text(payload.get("turn_id")),
            run_id=_optional_text(payload.get("run_id")),
            run_item_id=_optional_text(payload.get("run_item_id")),
            attempt_id=_optional_text(payload.get("attempt_id")),
            worker_id=str(payload["worker_id"]),
            lease_token=lease_token,
            fencing_token=int(payload.get("fencing_token") or 0),
            request_id=str(payload.get("request_id") or f"req_cleanup_{lease_token}"),
            trace_id=str(payload.get("trace_id") or f"trace_cleanup_{lease_token}"),
        )


class ResourceUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RedisResourceLeaseManager:
    """One Redis-backed ownership contract shared by every resource type."""

    def __init__(
        self,
        redis_url: str,
        *,
        socket_timeout_seconds: float = 5.0,
        cleanup_stream: str = "qa:tasks:cleanup",
        client: Redis | Any | None = None,
        key_prefix: str = "qa",
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        self._socket_timeout_seconds = socket_timeout_seconds
        self._cleanup_stream = cleanup_stream
        _validate_part(key_prefix, "key_prefix")
        self._prefix = key_prefix
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
        await self._client.hset(self._key("quota:limits"), field, max(0, int(limit)))

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
            quota_fields.append(_quota_field("project_resource_type", f"{project_id}:{resource_type}"))
        if run_id:
            quota_fields.append(_quota_field("run", run_id))
            quota_fields.append(_quota_field("run_resource_type", f"{run_id}:{resource_type}"))
        lease_token = uuid.uuid4().hex
        context = get_request_context()
        request_id = context.request_id if context is not None else new_id("req")
        trace_id = context.trace_id if context is not None else new_id("trace")
        turn_id = context.turn_id if context is not None else None
        payload = json.dumps({
            "resource_id": resource_id, "resource_type": resource_type,
            "project_id": project_id, "session_id": session_id, "run_id": run_id,
            "run_item_id": run_item_id, "attempt_id": attempt_id, "worker_id": worker_id,
            "lease_token": lease_token, "request_id": request_id,
            "trace_id": trace_id, "turn_id": turn_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, separators=(",", ":"))
        result = await self._client.eval(
            CLAIM, 7, self._lease_key(resource_type, resource_id),
            self._key("fencing:" + resource_type), self._key("quota:usage"), self._key("quota:limits"),
            self._key("lease:registry"), self._key("lease:metadata"), self._cleanup_stream,
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
            request_id=request_id, trace_id=trace_id, turn_id=turn_id,
        )

    async def renew(self, lease: ResourceLease, *, lease_seconds: int) -> bool:
        result = await self._client.eval(
            RENEW, 2, self._lease_key(lease.resource_type, lease.resource_id), self._key("lease:registry"),
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
            quota_fields.append(_quota_field("project_resource_type", f"{lease.project_id}:{lease.resource_type}"))
        if lease.run_id:
            quota_fields.append(_quota_field("run", lease.run_id))
            quota_fields.append(_quota_field("run_resource_type", f"{lease.run_id}:{lease.resource_type}"))
        result = await self._client.eval(
            RELEASE, 4, self._lease_key(lease.resource_type, lease.resource_id),
            self._key("quota:usage"), self._key("lease:registry"), self._key("lease:metadata"),
            lease.lease_token, *quota_fields,
        )
        return bool(result)

    async def bind(self, lease: ResourceLease, external_resource_id: str) -> bool:
        if not external_resource_id or len(str(external_resource_id)) > 240:
            raise ValueError("external_resource_id must be a nonempty bounded string")
        return bool(await self._client.eval(
            BIND, 2, self._lease_key(lease.resource_type, lease.resource_id),
            self._key("lease:metadata"),
            lease.lease_token, str(external_resource_id),
        ))

    async def get(self, *, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        raw = await self._client.get(self._lease_key(resource_type, resource_id))
        return json.loads(raw) if raw else None

    async def reap_expired(self, *, limit: int = 100) -> list[ExpiredResourceLease]:
        members = await self._client.eval(
            REAP, 4, self._key("lease:registry"), self._key("quota:usage"), self._key("lease:metadata"),
            self._cleanup_stream,
            max(1, min(int(limit), 1000)),
        )
        results: list[ExpiredResourceLease] = []
        for member in members or []:
            raw = member.decode() if isinstance(member, bytes) else str(member)
            payload = json.loads(raw)
            if payload:
                results.append(ExpiredResourceLease.from_payload(payload))
        return results

    async def reconcile_quota_usage(self) -> int:
        """Rebuild usage from authoritative active-lease metadata."""
        return int(await self._client.eval(
            RECONCILE_QUOTA,
            2,
            self._key("lease:metadata"),
            self._key("quota:usage"),
        ))

    async def list_active(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        async for key in self._client.scan_iter(match=self._key("lease:*"), count=100):
            key_text = key.decode() if isinstance(key, bytes) else str(key)
            if key_text in {self._key("lease:registry"), self._key("lease:metadata")}:
                continue
            try:
                raw = await self._client.get(key)
            except Exception:
                continue
            if not raw:
                continue
            payload = json.loads(raw)
            if project_id and payload.get("project_id") != project_id:
                continue
            payload["lease_key"] = key
            payload["ttl_ms"] = await self._client.pttl(key)
            active.append(payload)
        active.sort(key=lambda item: (str(item.get("resource_type")), str(item.get("resource_id"))))
        return active

    async def usage_snapshot(self) -> dict[str, int]:
        values = await self._client.hgetall(self._key("quota:usage"))
        return {str(key): int(value) for key, value in values.items()}

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def _lease_key(self, resource_type: str, resource_id: str) -> str:
        return self._key(f"lease:{resource_type}:{resource_id}")


def _quota_field(scope: str, identifier: str) -> str:
    _validate_part(scope, "quota_scope")
    _validate_part(identifier, "quota_identifier")
    return f"{scope}:{identifier}"


def _validate_part(value: str, label: str) -> None:
    if not value or not re.fullmatch(r"[A-Za-z0-9_.:-]+", str(value)):
        raise ValueError(f"{label} contains unsupported characters")


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
