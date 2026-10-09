"""Performance result parser.

Converts engine RawMetrics into unified ParsedMetrics with
arrival-rate/latency curve data for inflection point detection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.application.performance.engine_adapter import RawMetrics
from src.modes.performance_testing_mode.plan_state import PerfMetrics


@dataclass
class CurvePoint:
    rate_or_vus: float
    p95_ms: float
    error_rate: float


@dataclass
class ParsedMetrics:
    metrics: PerfMetrics
    curve: list[CurvePoint] = field(default_factory=list)
    inflection_point: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PerfResultParser:
    """Parses RawMetrics into structured ParsedMetrics."""

    def parse(self, raw: RawMetrics) -> ParsedMetrics:
        metrics = PerfMetrics(
            samples=raw.samples,
            throughput_tps=raw.throughput_tps,
            avg_ms=raw.avg_ms,
            min_ms=raw.min_ms,
            max_ms=raw.max_ms,
            p50_ms=raw.p50_ms,
            p90_ms=raw.p90_ms,
            p95_ms=raw.p95_ms,
            p99_ms=raw.p99_ms,
            error_rate=raw.error_rate,
        )

        curve = self._extract_curve(raw)
        inflection = self._detect_inflection(curve)

        return ParsedMetrics(
            metrics=metrics,
            curve=curve,
            inflection_point=inflection,
            raw=raw.raw_data,
        )

    def _extract_curve(self, raw: RawMetrics) -> list[CurvePoint]:
        """Extract rate-vs-latency curve from raw data if available."""
        raw_data = raw.raw_data
        if not raw_data:
            return []

        series = raw_data.get("perf_curve") or raw_data.get("curve") or raw_data.get("stages")
        if isinstance(series, list):
            points = self._extract_curve_points(series)
            if points:
                return points

        if (raw.throughput_tps is not None and raw.throughput_tps > 0
                and raw.p95_ms is not None and raw.error_rate is not None):
            return [CurvePoint(
                rate_or_vus=raw.throughput_tps,
                p95_ms=raw.p95_ms,
                error_rate=raw.error_rate,
            )]
        return []

    def _extract_curve_points(self, series: list[Any]) -> list[CurvePoint]:
        points: list[CurvePoint] = []
        for item in series:
            if not isinstance(item, dict):
                continue
            rate = item.get("rate_or_vus", item.get("rate", item.get("vus", item.get("target"))))
            p95 = item.get("p95_ms", item.get("p95", item.get("p(95)")))
            error_rate = item.get("error_rate", item.get("failed_rate", item.get("errors", 0)))
            try:
                points.append(CurvePoint(
                    rate_or_vus=float(rate),
                    p95_ms=float(p95),
                    error_rate=float(error_rate),
                ))
            except (TypeError, ValueError):
                continue
        points.sort(key=lambda p: p.rate_or_vus)
        return points

    def _detect_inflection(self, curve: list[CurvePoint]) -> float | None:
        """Detect the load level where latency sharply increases.

        Uses the strongest positive latency slope as the knee point.
        """
        if len(curve) < 3:
            return None

        max_slope = 0.0
        inflection = None
        for i in range(1, len(curve)):
            rate_delta = curve[i].rate_or_vus - curve[i - 1].rate_or_vus
            if rate_delta <= 0:
                continue
            latency_slope = (curve[i].p95_ms - curve[i - 1].p95_ms) / rate_delta
            if latency_slope > max_slope:
                max_slope = latency_slope
                inflection = curve[i - 1].rate_or_vus

        return inflection
