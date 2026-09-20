"""Periodic export of queue, pool and cache depths that have no business call site.

No upstream counterpart (checked: src-wide grep for an existing background poller of
pool/stream statistics, official FastAPI docs; the project metrics registry only
stores values, so external depths must be polled): this reads PostgreSQL pool
statistics, Redis INFO/XLEN/XPENDING/SLOWLOG and the resource quota hashes, and
writes them as gauges.

Every source is read independently.  A missing stream, consumer group or an
unreachable Redis must never stop the other exports.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis

from src.application.observability.business_metrics import (
    collector_errors_total,
    compaction_lag_events,
    pg_pool_checked_out,
    pg_pool_size,
    redis_slowlog_entries,
    redis_slowlog_max_duration_microseconds,
    redis_used_memory_bytes,
    resource_quota_limit,
    resource_quota_used,
    stream_consumer_lag,
    stream_length,
    stream_pending,
)
from src.core.config import Settings
from src.infrastructure.sqlalchemy_runtime import iter_engines

LOGGER = logging.getLogger(__name__)

# Settings has no observability-specific interval; 30s matches the scrape cadence
# of every other reaper in this project, so the value stays a module constant.
COLLECT_INTERVAL_SECONDS = 30.0
DEAD_LETTER_SUFFIX = ":dead_letter"
# SLOWLOG GET is O(N) on the server side; the worst-case depth is all occupancy
# reporting needs, and an idle Redis answers this in one round trip.
REDIS_SLOWLOG_SAMPLE_SIZE = 32
# Only resource_type quota fields are exported: project and run quota fields carry
# unbounded identifiers, which would blow up Prometheus label cardinality.
QUOTA_SCOPE_RESOURCE_TYPE = "resource_type"
_NOGROUP = "NOGROUP"

_task: asyncio.Task[None] | None = None
_stop: asyncio.Event | None = None
_compaction_store: Any | None = None
_lease_manager: Any | None = None


async def start(
    settings: Settings, *, compaction_store: Any | None = None, lease_manager: Any | None = None
) -> None:
    """Start the collector exactly once per process."""
    global _task, _stop, _compaction_store, _lease_manager
    if _task is not None and not _task.done():
        return
    _compaction_store = compaction_store
    _lease_manager = lease_manager
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(settings), name="metrics-collector")
    LOGGER.info(
        "metrics_collector_started",
        extra={
            "interval_seconds": COLLECT_INTERVAL_SECONDS,
            "compaction_lag_wired": compaction_store is not None,
            "resource_occupancy_wired": lease_manager is not None,
        },
    )


async def stop() -> None:
    """Stop the collector without leaving a background task behind."""
    global _task, _stop
    task, stop_event = _task, _stop
    _task = None
    _stop = None
    if task is None:
        return
    if stop_event is not None:
        stop_event.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    LOGGER.info("metrics_collector_stopped")


async def _loop(settings: Settings) -> None:
    stop = _stop
    while stop is not None and not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), COLLECT_INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        await collect_once(settings)


async def collect_once(
    settings: Settings,
    *,
    compaction_store: Any | None = None,
    lease_manager: Any | None = None,
) -> None:
    """Run one full export pass; each source guards its own failures."""
    _collect_postgres_pools()
    await _collect_compaction_lag(compaction_store if compaction_store is not None else _compaction_store)
    await _collect_resource_occupancy(lease_manager if lease_manager is not None else _lease_manager)
    await _collect_redis(settings)


async def _collect_resource_occupancy(manager: Any | None) -> None:
    if manager is None:
        # The lifespan wires the lease manager; without it no quota hashes exist.
        LOGGER.debug("metrics_collect_resource_occupancy_not_wired")
        return
    try:
        usage = await manager.usage_snapshot()
        limits = await manager.quota_limits()
    except Exception as exc:
        _record_collector_error("resource_occupancy", exc)
        return
    for field, used in usage.items():
        scope, separator, resource_type = str(field).partition(":")
        if not separator or scope != QUOTA_SCOPE_RESOURCE_TYPE:
            continue
        resource_quota_used().labels(resource_type=resource_type).set(int(used))
        if field in limits:
            resource_quota_limit().labels(resource_type=resource_type).set(int(limits[field]))


async def _collect_compaction_lag(store: Any | None) -> None:
    if store is None:
        # The compaction subsystem is not wired in this process; the watermark
        # table it owns does not exist here, so there is nothing to export.
        LOGGER.debug("metrics_collect_compaction_lag_not_wired")
        return
    try:
        compaction_lag_events().set(await store.uncompacted_event_count())
    except Exception as exc:
        _record_collector_error("compaction_lag", exc)


def _record_collector_error(item: str, error: Exception | str) -> None:
    collector_errors_total().labels(item=item).inc()
    LOGGER.warning(
        "metrics_collect_failed",
        extra={
            "collect_item": item,
            "error_type": type(error).__name__ if isinstance(error, Exception) else "",
            "error_message": str(error)[:200],
        },
    )


def _collect_postgres_pools() -> None:
    try:
        pools = [engine.pool for engine in iter_engines("postgres")]
        if not pools:
            return
        pg_pool_checked_out().set(sum(pool.checkedout() for pool in pools))
        pg_pool_size().set(sum(pool.size() for pool in pools))
    except Exception as exc:
        _record_collector_error("postgres_pool", exc)


async def _collect_redis(settings: Settings) -> None:
    config = settings.orchestration
    streams = list(config.task_stream_names)
    client = Redis.from_url(
        settings.database.redis_url,
        decode_responses=True,
        socket_timeout=config.redis_task_socket_timeout_seconds,
        socket_connect_timeout=config.redis_task_socket_timeout_seconds,
    )
    try:
        await _collect_redis_memory(client)
        await _collect_redis_slowlog(client)
        for stream in streams:
            await _collect_stream(client, stream, group=config.redis_task_consumer_group)
        # Dead letter entries have no consumer group: only their depth matters.
        for stream in streams:
            await _collect_stream(client, f"{stream}{DEAD_LETTER_SUFFIX}", group=None)
    finally:
        try:
            await client.aclose()
        except Exception as exc:
            LOGGER.warning(
                "metrics_collector_redis_close_failed",
                extra={"error_type": type(exc).__name__},
            )


async def _collect_redis_memory(client: Redis) -> None:
    try:
        info = await client.info("memory")
        used_memory = int(info["used_memory"])
    except Exception as exc:
        _record_collector_error("redis_memory", exc)
        return
    redis_used_memory_bytes().set(used_memory)


async def _collect_redis_slowlog(client: Redis) -> None:
    try:
        entries = int(await client.slowlog_len())
    except Exception as exc:
        _record_collector_error("redis_slowlog", exc)
        return
    redis_slowlog_entries().set(entries)
    if entries <= 0:
        redis_slowlog_max_duration_microseconds().set(0)
        return
    try:
        # SLOWLOG GET returns the newest entries first; only the worst matters here.
        slow = await client.slowlog_get(min(entries, REDIS_SLOWLOG_SAMPLE_SIZE))
    except Exception as exc:
        _record_collector_error("redis_slowlog_detail", exc)
        return
    durations = [
        int(entry.get("duration") or 0) for entry in slow if isinstance(entry, dict)
    ]
    redis_slowlog_max_duration_microseconds().set(max(durations) if durations else 0)


async def _collect_stream(client: Redis, stream: str, *, group: str | None) -> None:
    try:
        stream_length().labels(stream=stream).set(await client.xlen(stream))
    except Exception as exc:
        _record_collector_error("stream_length", exc)
    if group is None:
        return
    try:
        summary = await client.xpending(stream, group)
    except Exception as exc:
        if _NOGROUP in str(exc):
            # A stream nobody has grouped yet is an idle queue, not a fault.
            LOGGER.debug("metrics_collect_stream_group_absent", extra={"stream": stream})
            return
        _record_collector_error("stream_pending", exc)
        return
    pending = summary.get("pending") if isinstance(summary, dict) else None
    if pending is None:
        _record_collector_error("stream_pending", ValueError(f"XPENDING summary has no pending for {stream}"))
        return
    stream_pending().labels(stream=stream).set(int(pending))
    try:
        groups = await client.xinfo_groups(stream)
    except Exception as exc:
        if _NOGROUP not in str(exc):
            _record_collector_error("stream_consumer_lag", exc)
        return
    for entry in groups if isinstance(groups, list) else []:
        if not isinstance(entry, dict) or entry.get("name") != group:
            continue
        lag = entry.get("lag")
        # Redis < 7.0 omits lag and 7.0 reports None before a read cursor exists.
        if lag is not None:
            stream_consumer_lag().labels(stream=stream).set(int(lag))
        break
