"""Workload modeler.

Converts user scenario descriptions, API docs, or traffic logs
into structured WorkloadModel configurations.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.modes.performance_testing_mode.contracts import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DRIVER_SOURCE_API_DOC,
    DRIVER_SOURCE_SCENARIO,
    DRIVER_SOURCE_TRAFFIC,
    WORKLOAD_MODE_CONSTANT_ARRIVAL_RATE,
    WORKLOAD_MODE_CONSTANT_VUS,
    WORKLOAD_MODE_RAMPING_ARRIVAL_RATE,
    WORKLOAD_MODE_RAMPING_VUS,
    WORKLOAD_MODEL_CLOSED,
    WORKLOAD_MODEL_OPEN,
    DriverConfidence,
    DriverSource,
    WorkloadMode,
    WorkloadModelType,
)
from src.modes.performance_testing_mode.plan_state import (
    CriticalPath,
    PerfWorkloadConfig,
    RampStage,
    TrafficMixEntry,
    WorkloadModel,
)


logger = logging.getLogger(__name__)


def build_default_ramp_stages(target: int, duration_seconds: int) -> list[RampStage]:
    """Preserve the requested duration, including short runs and division remainders."""
    if duration_seconds <= 0:
        raise ValueError("默认性能阶梯的持续时长必须大于0秒")
    first_ms = duration_seconds * 1000 // 3
    durations = [first_ms, first_ms, duration_seconds * 1000 - 2 * first_ms]
    stages = [RampStage(target=stage_target, duration=f"{milliseconds // 1000}s" if milliseconds % 1000 == 0
        else f"{milliseconds}ms") for stage_target, milliseconds in zip((target // 2, target, target), durations)]
    logger.info("performance_default_ramp_built", extra={"duration_seconds": duration_seconds,
        "target": target, "stage_durations_ms": durations})
    return stages


class WorkloadModeler:
    """Builds WorkloadModel from various input sources."""

    def from_scenario(
        self,
        description: str,
        target_rate_rps: int | None = None,
        virtual_users: int | None = None,
        duration_seconds: int = 60,
        model_type: WorkloadModelType | None = None,
    ) -> WorkloadModel:
        """L3 driver: user-described scenario → WorkloadModel."""
        if model_type is None:
            model_type = WORKLOAD_MODEL_OPEN if target_rate_rps else WORKLOAD_MODEL_CLOSED

        if model_type == WORKLOAD_MODEL_OPEN:
            mode: WorkloadMode = WORKLOAD_MODE_CONSTANT_ARRIVAL_RATE
            rate = target_rate_rps or 50
            vus = virtual_users or max(rate * 2, 100)
        else:
            mode = WORKLOAD_MODE_CONSTANT_VUS
            rate = target_rate_rps
            vus = virtual_users or 50

        config = PerfWorkloadConfig(
            model=model_type,
            mode=mode,
            virtual_users=vus,
            target_rate_rps=rate,
            hold_seconds=duration_seconds,
            think_time_ms=self._estimate_think_time(description),
        )

        return WorkloadModel(
            source=DRIVER_SOURCE_SCENARIO,
            confidence=CONFIDENCE_MEDIUM,
            description=description,
            workload_config=config,
            critical_paths=[CriticalPath(name="default", weight=1.0)],
        )

    def from_api_docs(
        self,
        endpoints: list[dict[str, Any]],
        total_rate_rps: int = 100,
        duration_seconds: int = 120,
    ) -> WorkloadModel:
        """L2 driver: API doc endpoint list → WorkloadModel with traffic mix."""
        if not endpoints:
            return self.from_scenario("未提供端点", target_rate_rps=total_rate_rps)

        mix: list[TrafficMixEntry] = []
        weight_each = 1.0 / len(endpoints)
        for ep in endpoints:
            mix.append(TrafficMixEntry(
                path=ep.get("path", "/"),
                method=ep.get("method", "GET"),
                weight=ep.get("weight", weight_each),
            ))

        config = PerfWorkloadConfig(
            model=WORKLOAD_MODEL_OPEN,
            mode=WORKLOAD_MODE_RAMPING_ARRIVAL_RATE,
            virtual_users=max(total_rate_rps * 2, 100),
            target_rate_rps=total_rate_rps,
            hold_seconds=duration_seconds,
            ramp_stages=build_default_ramp_stages(total_rate_rps, duration_seconds),
        )

        return WorkloadModel(
            source=DRIVER_SOURCE_API_DOC,
            confidence=CONFIDENCE_MEDIUM,
            description=f"基于 {len(endpoints)} 个 API 端点的混合负载",
            workload_config=config,
            traffic_mix=mix,
            critical_paths=[
                CriticalPath(name=ep.get("path", "/"), weight=ep.get("weight", weight_each))
                for ep in endpoints[:5]
            ],
        )

    def from_traffic_log(self, log_data: dict[str, Any]) -> WorkloadModel:
        """L1 driver: traffic log analysis → WorkloadModel (interface only for MVP)."""
        return WorkloadModel(
            source=DRIVER_SOURCE_TRAFFIC,
            confidence=CONFIDENCE_HIGH,
            description="基于真实流量日志的负载建模（需提供流量数据）",
            workload_config=PerfWorkloadConfig(),
        )

    def _estimate_think_time(self, description: str) -> int:
        """Heuristic think-time estimation from scenario description."""
        desc = description.lower()
        if any(kw in desc for kw in ["秒杀", "spike", "突发", "瞬时"]):
            return 0
        if any(kw in desc for kw in ["浏览", "browse", "阅读", "查看"]):
            return 3000
        if any(kw in desc for kw in ["api", "接口", "自动化"]):
            return 0
        return 1000
