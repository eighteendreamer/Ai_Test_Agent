"""Performance engine adapter protocol and shared data structures.

Defines the engine-agnostic interface that k6/JMeter adapters implement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ScriptArtifact:
    """Generated engine script ready for execution."""

    engine: str
    script_content: str
    filename: str
    data_files: dict[str, str] = field(default_factory=dict)


@dataclass
class RunOptions:
    """Execution parameters passed to the engine."""

    timeout_seconds: int = 1800
    summary_export_path: str = "/work/summary.json"
    environment: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    is_smoke: bool = False
    smoke_vus: int = 1
    smoke_iterations: int = 3


@dataclass
class EngineCommand:
    """Fully resolved command to execute inside the container."""

    image: str
    command: list[str]
    workdir: str = "/work"
    env: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, str] = field(default_factory=dict)
    cpus: str = ""
    memory: str = ""


@dataclass
class RawMetrics:
    """Engine-parsed raw metrics in a unified schema."""

    # None means unavailable; a measured zero remains a valid observation.
    samples: int | None = None
    throughput_tps: float | None = None
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    p50_ms: float | None = None
    p90_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    error_rate: float | None = None
    error_count: int | None = None
    status_codes: dict[str, int] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)


def metric_number(values: dict[str, Any], key: str) -> float | None:
    """Read a measured value without replacing missing data with zero."""
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid performance metric {key}: boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Invalid performance metric {key}: non-finite value")
    return number


@runtime_checkable
class PerfEngineAdapter(Protocol):
    """Engine-agnostic adapter interface.

    Implementations: K6EngineAdapter (default), JMeterEngineAdapter (stage 2).
    """

    engine_key: str
    default_image: str

    def build_script(self, plan: Any) -> ScriptArtifact:
        """PerfPlan -> engine script. Pure generation, no network."""
        ...

    def run_command(self, script: ScriptArtifact, run_opts: RunOptions) -> EngineCommand:
        """Construct the container execution command."""
        ...

    def parse_results(self, summary_json: str) -> RawMetrics:
        """Parse engine output into unified RawMetrics."""
        ...

    def build_smoke_options(self, plan: Any) -> RunOptions:
        """Build run options for single-VU smoke validation."""
        ...
