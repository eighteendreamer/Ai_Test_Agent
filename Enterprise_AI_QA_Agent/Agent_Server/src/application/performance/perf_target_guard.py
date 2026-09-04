"""Performance target safety guard.

Validates that load test targets are within the configured allowlist,
load parameters don't exceed hard limits, and the system doesn't target itself.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from src.core.config import Settings
from src.modes.performance_testing_mode.plan_state import PerfPlan


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]

_SELF_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class PerfTargetGuard:
    """Safety guard that validates targets before load execution."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._allowlist = self._parse_allowlist(settings.orchestration.performance_target_allowlist)
        self._max_vus = settings.orchestration.performance_max_vus
        self._max_rate_rps = settings.orchestration.performance_max_rate_rps
        self._max_duration = settings.orchestration.performance_max_duration_seconds

    def validate(self, plan: PerfPlan) -> GuardResult:
        for target in plan.targets:
            result = self._check_target(target.url)
            if not result.ok:
                return result

        result = self._check_limits(plan)
        if not result.ok:
            return result

        return GuardResult(ok=True)

    def _check_target(self, url: str) -> GuardResult:
        parsed = urlparse(url)
        host = parsed.hostname or ""

        if self._is_self_target(host, parsed.port):
            return GuardResult(
                ok=False,
                reason=f"目标 {host} 指向系统自身，禁止压测自身服务（自我保护）。",
            )

        if self._allowlist:
            if not self._host_in_allowlist(host):
                return GuardResult(
                    ok=False,
                    reason=f"目标 {host} 不在允许列表中。允许列表：{', '.join(self._allowlist)}",
                )
        else:
            if not self._is_private_host(host):
                return GuardResult(
                    ok=False,
                    reason=f"未配置目标允许列表，且目标 {host} 为公网地址。请配置 PERFORMANCE_TARGET_ALLOWLIST 或使用内网目标。",
                )

        return GuardResult(ok=True)

    def _check_limits(self, plan: PerfPlan) -> GuardResult:
        wl = plan.workload

        if wl.virtual_users and wl.virtual_users > self._max_vus:
            return GuardResult(
                ok=False,
                reason=f"虚拟用户数 {wl.virtual_users} 超过硬上限 {self._max_vus}。",
            )

        if wl.target_rate_rps and wl.target_rate_rps > self._max_rate_rps:
            return GuardResult(
                ok=False,
                reason=f"目标到达率 {wl.target_rate_rps} rps 超过硬上限 {self._max_rate_rps} rps。",
            )

        if wl.hold_seconds > self._max_duration:
            return GuardResult(
                ok=False,
                reason=f"持续时长 {wl.hold_seconds}s 超过硬上限 {self._max_duration}s。",
            )

        for stage in wl.ramp_stages:
            if stage.target > self._max_rate_rps:
                return GuardResult(
                    ok=False,
                    reason=f"阶梯目标 {stage.target} 超过到达率硬上限 {self._max_rate_rps}。",
                )

        return GuardResult(ok=True)

    def _is_self_target(self, host: str, port: int | None) -> bool:
        if host.lower() in _SELF_HOSTS:
            agent_port = 1032
            if port and port == agent_port:
                return True
        return False

    def _is_private_host(self, host: str) -> bool:
        if host.lower() in ("localhost", "host.docker.internal"):
            return True
        try:
            addr = ipaddress.ip_address(host)
            return any(addr in net for net in _PRIVATE_NETWORKS)
        except ValueError:
            return False

    def _host_in_allowlist(self, host: str) -> bool:
        host_lower = host.lower()
        for entry in self._allowlist:
            if host_lower == entry.lower():
                return True
            if entry.startswith("*.") and host_lower.endswith(entry[1:].lower()):
                return True
            try:
                net = ipaddress.ip_network(entry, strict=False)
                addr = ipaddress.ip_address(host)
                if addr in net:
                    return True
            except ValueError:
                pass
        return False

    @staticmethod
    def _parse_allowlist(raw: str) -> list[str]:
        if not raw.strip():
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]
