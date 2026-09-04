"""Performance engine container lifecycle manager."""
from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass, asdict

from src.core.config import Settings


@dataclass
class PerfContainerResult:
    ok: bool
    action: str
    engine: str
    container_name: str = ""
    image: str = ""
    status: str = ""
    summary: str = ""
    stdout: str = ""
    stderr: str = ""

    def model_dump(self) -> dict:
        return asdict(self)


class PerfContainerManager:
    """Starts and destroys warm k6/JMeter containers for orchestrated runs."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._prefix = settings.orchestration.performance_runner_docker_container_prefix

    async def start(self, engine: str, container_name: str = "") -> PerfContainerResult:
        engine = self._normalize_engine(engine)
        image = self._image_for(engine)
        name = container_name or f"{self._prefix}-{engine}-{uuid.uuid4().hex[:8]}"

        cmd = [
            "docker", "run", "-d",
            "--name", name,
            "--label", "enterprise-ai-qa-agent=performance",
            "--label", f"performance-engine={engine}",
            "--entrypoint=",
            image,
            "sh", "-lc", "sleep infinity",
        ]
        proc = await self._run(cmd)
        ok = proc.returncode == 0
        return PerfContainerResult(
            ok=ok,
            action="start",
            engine=engine,
            container_name=name,
            image=image,
            status="running" if ok else "failed",
            summary=f"{engine} 容器已启动: {name}" if ok else f"{engine} 容器启动失败",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    async def stop(self, engine: str, container_name: str) -> PerfContainerResult:
        engine = self._normalize_engine(engine)
        proc = await self._run(["docker", "rm", "-f", container_name])
        ok = proc.returncode == 0
        return PerfContainerResult(
            ok=ok,
            action="stop",
            engine=engine,
            container_name=container_name,
            image=self._image_for(engine),
            status="removed" if ok else "failed",
            summary=f"容器已销毁: {container_name}" if ok else f"容器销毁失败: {container_name}",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    async def status(self, engine: str, container_name: str) -> PerfContainerResult:
        engine = self._normalize_engine(engine)
        proc = await self._run([
            "docker", "inspect", "-f", "{{.State.Status}}", container_name,
        ])
        ok = proc.returncode == 0
        status = proc.stdout.strip() if ok else "not_found"
        return PerfContainerResult(
            ok=ok,
            action="status",
            engine=engine,
            container_name=container_name,
            image=self._image_for(engine),
            status=status,
            summary=f"容器状态: {status}" if ok else f"容器不存在或不可访问: {container_name}",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    async def cleanup(self, engine: str = "") -> PerfContainerResult:
        engine = self._normalize_engine(engine) if engine else ""
        filters = [
            "docker", "ps", "-aq",
            "--filter", "label=enterprise-ai-qa-agent=performance",
        ]
        if engine:
            filters.extend(["--filter", f"label=performance-engine={engine}"])
        listed = await self._run(filters)
        names = [item.strip() for item in listed.stdout.splitlines() if item.strip()]
        if not names:
            return PerfContainerResult(
                ok=True,
                action="cleanup",
                engine=engine,
                status="none",
                summary="没有需要清理的性能测试容器",
            )
        proc = await self._run(["docker", "rm", "-f", *names])
        ok = proc.returncode == 0
        return PerfContainerResult(
            ok=ok,
            action="cleanup",
            engine=engine,
            status="removed" if ok else "failed",
            summary=f"已清理 {len(names)} 个性能测试容器" if ok else "性能测试容器清理失败",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def _image_for(self, engine: str) -> str:
        from src.application.images.image_resolver import ImageResolver
        resolver = ImageResolver()
        image_key = (
            self._settings.orchestration.jmeter_docker_image_key
            if engine == "jmeter"
            else self._settings.orchestration.k6_docker_image_key
        )
        result = resolver.resolve_by_key(image_key) if image_key else resolver.resolve_for_engine(engine)
        if result.ok:
            return result.selected_image
        if engine == "jmeter":
            return self._settings.orchestration.jmeter_docker_image
        return self._settings.orchestration.k6_docker_image

    @staticmethod
    def _normalize_engine(engine: str) -> str:
        normalized = (engine or "k6").lower().strip()
        if normalized not in {"k6", "jmeter"}:
            raise ValueError(f"unsupported performance engine: {engine}")
        return normalized

    @staticmethod
    async def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
