"""Security target allowlist hard gate (S6).

Every security execution must be checked against a configured allowlist before
it runs. This mirrors the performance mode's ``PerfTargetGuard`` approach
(``application/performance/perf_target_guard.py``) but is scoped to the security
testing mode: it validates a task/command target host against
``settings.security.security_target_allowlist`` and refuses out-of-scope targets.

Design notes:
- Empty allowlist means "do not restrict", but the guard still reports whether
  the target is a public address so the caller can log a warning (authorization
  boundary visibility, per the security-mode iron rules).
- The command-level extractor is intentionally conservative: it only pulls
  *high-confidence* network targets (URLs and literal IP addresses) out of a
  rendered command. Ambiguous bare tokens (which may be file names or flags)
  are ignored so the defense-in-depth gate errs on the side of letting a valid
  command through rather than wrongly blocking it (``宁可漏过、不可误杀``). The
  precise gate on ``task.target`` in the coordinator is the primary chokepoint.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class TargetGuardResult:
    """Outcome of an allowlist evaluation."""

    ok: bool
    reason: str = ""
    # True when the target is public and no allowlist is configured. The caller
    # should log a warning but is allowed to proceed.
    warn_public: bool = False
    checked_hosts: list[str] = field(default_factory=list)


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

_LOCAL_HOSTS = {"localhost", "host.docker.internal"}

# A token is treated as a literal IPv4/IPv6 host only when it parses as one.
# URL detection relies on an explicit scheme separator.
_URL_LIKE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")


class SecurityTargetGuard:
    """Validate security testing targets against a configured allowlist."""

    def __init__(self, settings: object | None = None) -> None:
        raw = getattr(settings.security, "security_target_allowlist", "") if settings is not None else ""
        self._allowlist = self._parse_allowlist(str(raw or ""))

    @property
    def has_allowlist(self) -> bool:
        return bool(self._allowlist)

    @property
    def allowlist(self) -> list[str]:
        return list(self._allowlist)

    # ------------------------------------------------------------------
    # Primary precise gate (task.target)
    # ------------------------------------------------------------------

    def evaluate_target(self, target: str) -> TargetGuardResult:
        """Check a single task target string (URL / host / IP / CIDR)."""
        host = self._extract_host(target)
        if not host:
            # Nothing resolvable to check. Allow but surface for logging so the
            # coverage gap is visible rather than silently skipped.
            return TargetGuardResult(ok=True, reason="no_resolvable_host", checked_hosts=[])

        if not self._allowlist:
            if self._is_public_host(host):
                return TargetGuardResult(
                    ok=True,
                    reason=(
                        f"目标 {host} 为公网地址且未配置 security_target_allowlist；"
                        "已放行但记录警告，建议配置允许列表。"
                    ),
                    warn_public=True,
                    checked_hosts=[host],
                )
            return TargetGuardResult(ok=True, checked_hosts=[host])

        if self._host_in_allowlist(host):
            return TargetGuardResult(ok=True, checked_hosts=[host])
        return TargetGuardResult(
            ok=False,
            reason=(
                f"目标 {host} 不在安全测试允许列表内。允许列表：{', '.join(self._allowlist)}"
            ),
            checked_hosts=[host],
        )

    # ------------------------------------------------------------------
    # Defense-in-depth gate (rendered command args)
    # ------------------------------------------------------------------

    def evaluate_command(self, command_args: list[str]) -> TargetGuardResult:
        """Check high-confidence network targets embedded in a rendered command.

        Only URLs and literal IP addresses are extracted. When the allowlist is
        empty the command is always allowed (the coordinator gate handles the
        public-target warning on ``task.target``).
        """
        if not self._allowlist:
            return TargetGuardResult(ok=True)
        hosts = self.extract_confident_hosts(command_args)
        if not hosts:
            return TargetGuardResult(ok=True)
        for host in hosts:
            if not self._host_in_allowlist(host):
                return TargetGuardResult(
                    ok=False,
                    reason=(
                        f"命令目标 {host} 不在安全测试允许列表内。"
                        f"允许列表：{', '.join(self._allowlist)}"
                    ),
                    checked_hosts=hosts,
                )
        return TargetGuardResult(ok=True, checked_hosts=hosts)

    def extract_confident_hosts(self, command_args: list[str]) -> list[str]:
        """Extract only unambiguous network targets (URLs + literal IPs)."""
        hosts: list[str] = []
        for token in command_args or []:
            text = str(token or "").strip()
            if not text or text.startswith("-"):
                continue
            if _URL_LIKE.match(text):
                netloc_host = urlparse(text).hostname
                if netloc_host:
                    hosts.append(netloc_host)
                continue
            candidate = text.split("/", 1)[0]
            candidate = candidate.rsplit(":", 1)[0] if candidate.count(":") == 1 else candidate
            if self._is_ip_literal(candidate):
                hosts.append(candidate)
        # De-duplicate while preserving order.
        return list(dict.fromkeys(hosts))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_host(self, target: str) -> str:
        text = str(target or "").strip()
        if not text:
            return ""
        if _URL_LIKE.match(text):
            return (urlparse(text).hostname or "").strip()
        # Bare CIDR / IP / host[:port]
        head = text.split("/", 1)[0]
        # Only strip a trailing :port when there is exactly one colon (IPv4 or
        # hostname); keep IPv6 literals intact.
        if head.count(":") == 1:
            head = head.rsplit(":", 1)[0]
        return head.strip()

    def _host_in_allowlist(self, host: str) -> bool:
        host_lower = host.lower()
        for entry in self._allowlist:
            entry_lower = entry.lower()
            if host_lower == entry_lower:
                return True
            if entry_lower.startswith("*.") and host_lower.endswith(entry_lower[1:]):
                return True
            try:
                net = ipaddress.ip_network(entry, strict=False)
                addr = ipaddress.ip_address(host)
                if addr in net:
                    return True
            except ValueError:
                continue
        return False

    def _is_ip_literal(self, value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    def _is_public_host(self, host: str) -> bool:
        if host.lower() in _LOCAL_HOSTS:
            return False
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # A hostname (not an IP literal). Treat as public unless it is a
            # loopback-style name handled above.
            return True
        return not any(addr in net for net in _PRIVATE_NETWORKS)

    @staticmethod
    def _parse_allowlist(raw: str) -> list[str]:
        if not raw.strip():
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]


__all__ = ["SecurityTargetGuard", "TargetGuardResult"]
