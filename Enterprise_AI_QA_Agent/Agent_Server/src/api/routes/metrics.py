"""Prometheus scrape endpoint.

The platform has no authentication middleware yet, so this route is unauthenticated
like every other route; it exposes only aggregate counters and latency buckets, never
payload, identity or correlation data.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from src.application.observability.metrics import render

router = APIRouter(tags=["metrics"])

PROMETHEUS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(render(), media_type=PROMETHEUS_MEDIA_TYPE)
