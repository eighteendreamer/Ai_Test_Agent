"""HTTP probe runner using curl image for connectivity checks.

Per design doc section 10.2.2: health probe, connectivity verification,
pre-check before load test.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from src.application.images.image_resolver import ImageResolver
from src.core.config import Settings, get_settings
from src.runtime.docker_resource_manager import DockerCommandTimeout, DockerResourceManager


@dataclass
class HttpProbeResult:
    ok: bool
    status_code: int = 0
    response_time_ms: float = 0.0
    target_url: str = ""
    selected_image_key: str = ""
    selected_image: str = ""
    scenario: str = "connectivity_check"
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    fallback_used: bool = False
    next_recommendation: str = ""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class HttpProbeRunner:
    """Run connectivity probes via the helper_http_probe catalog image."""

    def __init__(self, settings: Settings | None = None, *, docker_resources=None) -> None:
        self._settings = settings or get_settings()
        self._resolver = ImageResolver()
        self._docker_resources = docker_resources or DockerResourceManager(self._settings)

    async def probe(
        self,
        *,
        target_url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 10,
    ) -> HttpProbeResult:
        resolution = self._resolver.resolve_image(
            domain="helper",
            scenario="connectivity_check",
            helper_need="connectivity_check",
        )

        if not resolution.ok:
            return HttpProbeResult(
                ok=False,
                target_url=target_url,
                reason=f"image_unavailable: {resolution.reason}",
            )

        cmd = self._build_curl_command(
            target_url=target_url,
            method=method,
            headers=headers or {},
            timeout_seconds=timeout_seconds,
        )

        docker_cmd = [
            "--entrypoint=",
            "--add-host", "host.docker.internal:host-gateway",
            "--label", "performance-engine=helper",
            resolution.selected_image,
            *cmd,
        ]

        try:
            proc = await self._docker_resources.execute(
                docker_cmd, name_prefix="qa-perf-probe", timeout_seconds=timeout_seconds + 5,
            )
        except DockerCommandTimeout as exc:
            return HttpProbeResult(
                ok=False,
                target_url=target_url,
                selected_image_key=resolution.selected_image_key,
                selected_image=resolution.selected_image,
                reason="timeout: probe exceeded time limit",
                stdout=exc.stdout[:2000],
                stderr=exc.stderr[:2000],
                next_recommendation="Check if target is reachable or increase timeout.",
            )
        except FileNotFoundError:
            return HttpProbeResult(
                ok=False,
                target_url=target_url,
                selected_image_key=resolution.selected_image_key,
                selected_image=resolution.selected_image,
                reason="image_unavailable: docker not found or image pull failed",
                next_recommendation="Ensure Docker is running and helper image is available.",
            )

        # curl documents exit 28 as timeout: https://curl.se/docs/manpage.html
        if proc.timed_out or proc.exit_code == 28:
            return HttpProbeResult(
                ok=False, target_url=target_url,
                selected_image_key=resolution.selected_image_key,
                selected_image=resolution.selected_image,
                reason="timeout: probe exceeded time limit",
                stdout=proc.stdout[:2000], stderr=proc.stderr[:2000],
                next_recommendation="Check if target is reachable or increase timeout.",
            )

        status_code, response_time = self._parse_curl_output(proc.stdout)

        ok = proc.exit_code == 0 and 200 <= status_code < 400

        if not ok and status_code == 0:
            error_category = self._classify_probe_error(proc.stderr, proc.stdout)
            reason = f"{error_category}: target may be unreachable"
        elif not ok:
            reason = f"HTTP {status_code} from target"
        else:
            reason = f"HTTP {status_code} from target in {response_time:.0f}ms"

        next_rec = (
            "Target is reachable, proceed to smoke validation."
            if ok else
            "Target is not reachable, check service status and network."
        )

        return HttpProbeResult(
            ok=ok,
            status_code=status_code,
            response_time_ms=response_time,
            target_url=target_url,
            selected_image_key=resolution.selected_image_key,
            selected_image=resolution.selected_image,
            reason=reason,
            stdout=proc.stdout[:2000] if proc.stdout else "",
            stderr=proc.stderr[:2000] if proc.stderr else "",
            next_recommendation=next_rec,
        )

    @staticmethod
    def _build_curl_command(
        *,
        target_url: str,
        method: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> list[str]:
        cmd = [
            "curl",
            "-s",
            "--show-error",
            "-o", "/dev/null",
            "-w", "%{http_code} %{time_total}",
            "-X", method.upper(),
            "--max-time", str(timeout_seconds),
        ]
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(target_url)
        return cmd

    @staticmethod
    def _parse_curl_output(stdout: str) -> tuple[int, float]:
        parts = stdout.strip().split()
        status_code = 0
        response_time = 0.0
        if len(parts) >= 1:
            try:
                status_code = int(parts[0])
            except ValueError:
                pass
        if len(parts) >= 2:
            try:
                response_time = float(parts[1]) * 1000
            except ValueError:
                pass
        return status_code, response_time

    @staticmethod
    def _classify_probe_error(stderr: str, stdout: str) -> str:
        combined = (stderr + " " + stdout).lower()
        if "could not resolve host" in combined or "no such host" in combined:
            return "dns_resolve"
        if "connection refused" in combined:
            return "connection_refused"
        if "timed out" in combined or "timeout" in combined:
            return "timeout"
        if "ssl" in combined or "tls" in combined:
            return "tls_error"
        return "unknown"
