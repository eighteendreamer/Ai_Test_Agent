"""Prometheus scrape endpoint.

It exposes only aggregate counters, gauges and latency buckets, never payload,
identity or correlation data. Shared-file aggregation runs outside the event loop.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from src.application.observability.metrics import render
from src.application.observability.metrics_multiprocess import MetricSnapshotError

router = APIRouter(tags=["metrics"])
logger = logging.getLogger(__name__)

PROMETHEUS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    try:
        body = await asyncio.to_thread(render)
    except (OSError, MetricSnapshotError) as exc:
        logger.error("metrics_scrape_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="Metrics aggregation is unavailable.") from exc
    return PlainTextResponse(body, media_type=PROMETHEUS_MEDIA_TYPE)
