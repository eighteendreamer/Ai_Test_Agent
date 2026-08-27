"""Controlled Docker image and container management for system settings."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlparse

from src.application.images.image_catalog import ImageCatalog
from src.core.config import Settings
from src.schemas.docker_management import DockerContainerCreateRequest


_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VOLUME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DockerUnavailableError(RuntimeError):
    pass


class DockerManagementService:
    """Lists and manages Docker resources without invoking a host shell."""

    def __init__(self, settings: Settings, image_catalog: ImageCatalog | None = None) -> None:
        self._settings = settings
        self._image_catalog = image_catalog or ImageCatalog()
        root = Path(settings.docker_managed_volume_root).expanduser()
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        self._volume_root = root.resolve()

    async def overview(self) -> dict[str, Any]:
        environment = await self.environment()
        required_specs = self._required_image_specs()
        templates = self._public_templates()
        if not environment["daemon_available"]:
            return {
                "ok": True,
                "environment": environment,
                "required_images": [
                    {**item, "installed": False, "container_count": 0}
                    for item in required_specs
                ],
                "templates": templates,
                "images": [],
                "containers": [],
                "summary": {
                    "required_total": len(required_specs),
                    "required_installed": 0,
                    "image_count": 0,
                    "container_count": 0,
                    "running_count": 0,
                },
            }

        images, containers = await asyncio.gather(self.list_images(), self.list_containers())
        installed = {str(item.get("reference") or ""): item for item in images}
        required = []
        for spec in required_specs:
            image_info = installed.get(spec["image"])
            matching_containers = [
                item
                for item in containers
                if item.get("image") == spec["image"]
                or item.get("image_id") == (image_info or {}).get("id")
            ]
            required.append(
                {
                    **spec,
                    "installed": image_info is not None,
                    "image_id": (image_info or {}).get("id", ""),
                    "size": (image_info or {}).get("size", ""),
                    "container_count": len(matching_containers),
                }
            )

        return {
            "ok": True,
            "environment": environment,
            "required_images": required,
            "templates": templates,
            "images": images,
            "containers": containers,
            "summary": {
                "required_total": len(required),
                "required_installed": sum(1 for item in required if item["installed"]),
                "image_count": len(images),
                "container_count": len(containers),
                "running_count": sum(1 for item in containers if item["state"] == "running"),
            },
        }

    async def environment(self) -> dict[str, Any]:
        docker = shutil.which("docker")
        if not docker:
            return {
                "cli_available": False,
                "daemon_available": False,
                "docker_path": "",
                "client_version": "",
                "server_version": "",
                "error": "Docker CLI is not installed or is not available on PATH.",
            }

        client = await self._run(["--version"], timeout=15, check=False)
        info = await self._run(["info", "--format", "{{json .}}"], timeout=20, check=False)
        if info.returncode != 0:
            return {
                "cli_available": True,
                "daemon_available": False,
                "docker_path": docker,
                "client_version": (client.stdout or "").strip(),
                "server_version": "",
                "error": self._error_detail(info),
            }

        payload = self._parse_json(info.stdout) or {}
        return {
            "cli_available": True,
            "daemon_available": True,
            "docker_path": docker,
            "client_version": (client.stdout or "").strip(),
            "server_version": str(payload.get("ServerVersion") or ""),
            "operating_system": str(payload.get("OperatingSystem") or ""),
            "os_type": str(payload.get("OSType") or ""),
            "architecture": str(payload.get("Architecture") or ""),
            "name": str(payload.get("Name") or ""),
            "cpus": payload.get("NCPU") or 0,
            "memory_bytes": payload.get("MemTotal") or 0,
            "error": "",
        }

    async def list_images(self) -> list[dict[str, Any]]:
        proc = await self._run(
            ["image", "ls", "--no-trunc", "--format", "{{json .}}"],
            timeout=30,
        )
        images = []
        for payload in self._parse_json_lines(proc.stdout):
            repository = str(payload.get("Repository") or "")
            tag = str(payload.get("Tag") or "")
            reference = f"{repository}:{tag}" if repository and tag else repository
            images.append(
                {
                    "id": str(payload.get("ID") or ""),
                    "repository": repository,
                    "tag": tag,
                    "reference": reference,
                    "digest": str(payload.get("Digest") or ""),
                    "size": str(payload.get("Size") or ""),
                    "created_at": str(payload.get("CreatedAt") or payload.get("CreatedSince") or ""),
                }
            )
        return images

    async def list_containers(self) -> list[dict[str, Any]]:
        proc = await self._run(
            ["ps", "-a", "--no-trunc", "--format", "{{json .}}"],
            timeout=30,
        )
        containers = []
        for payload in self._parse_json_lines(proc.stdout):
            labels = str(payload.get("Labels") or "")
            containers.append(
                {
                    "id": str(payload.get("ID") or ""),
                    "name": str(payload.get("Names") or ""),
                    "image": str(payload.get("Image") or ""),
                    "image_id": str(payload.get("ImageID") or ""),
                    "command": str(payload.get("Command") or ""),
                    "state": str(payload.get("State") or "").lower(),
                    "status": str(payload.get("Status") or ""),
                    "ports": str(payload.get("Ports") or ""),
                    "created_at": str(payload.get("CreatedAt") or ""),
                    "size": str(payload.get("Size") or ""),
                    "labels": labels,
                    "managed": "enterprise-ai-qa-agent=managed" in labels,
                }
            )
        return containers

    async def pull_image(self, image: str) -> dict[str, Any]:
        normalized = self._validate_image(image)
        proc = await self._run(["pull", normalized], timeout=1800)
        return {
            "ok": True,
            "action": "pull",
            "image": normalized,
            "message": f"Docker image '{normalized}' is ready.",
            "output": (proc.stdout or "").strip()[-4000:],
        }

    async def remove_image(self, image: str, *, force: bool = False) -> dict[str, Any]:
        normalized = self._validate_image(image)
        args = ["image", "rm"]
        if force:
            args.append("--force")
        args.append(normalized)
        await self._run(args, timeout=120)
        return {"ok": True, "action": "remove_image", "image": normalized}

    async def create_container(self, payload: DockerContainerCreateRequest) -> dict[str, Any]:
        return await self._create_container(payload, extra_labels={})

    async def create_from_template(
        self,
        template_key: str,
        *,
        name: str = "",
        pull_if_missing: bool = True,
    ) -> dict[str, Any]:
        templates = self._template_specs()
        template = templates.get(template_key)
        if template is None:
            raise ValueError(f"Unknown Docker container template '{template_key}'.")

        existing = await self._run(
            [
                "ps",
                "-aq",
                "--filter",
                f"label=enterprise-ai-qa-agent.template={template_key}",
            ],
            timeout=20,
        )
        existing_id = next(
            (line.strip() for line in (existing.stdout or "").splitlines() if line.strip()),
            "",
        )
        if existing_id:
            return {
                "ok": True,
                "created": False,
                "container_id": existing_id,
                "message": "The system template container already exists.",
            }

        image = str(template["image"])
        if pull_if_missing and not await self._image_installed(image):
            await self.pull_image(image)

        request = DockerContainerCreateRequest(
            name=name.strip() or str(template["default_name"]),
            image=image,
            command=list(template.get("command") or []),
            entrypoint=template.get("entrypoint"),
            ports=list(template.get("ports") or []),
            volumes=list(template.get("volumes") or []),
            environment=dict(template.get("environment") or {}),
            restart_policy=str(template.get("restart_policy") or "unless-stopped"),
            start=True,
        )
        return await self._create_container(
            request,
            extra_labels={"enterprise-ai-qa-agent.template": template_key},
        )

    async def container_action(self, container_id: str, action: str) -> dict[str, Any]:
        target = self._validate_container_target(container_id)
        if action not in {"start", "stop", "restart", "pause", "unpause"}:
            raise ValueError(f"Unsupported Docker container action '{action}'.")
        args = [action]
        if action in {"stop", "restart"}:
            args.extend(["--time", "15"])
        args.append(target)
        await self._run(args, timeout=60)
        return {"ok": True, "action": action, "container_id": target}

    async def remove_container(self, container_id: str, *, force: bool = False) -> dict[str, Any]:
        target = self._validate_container_target(container_id)
        args = ["rm"]
        if force:
            args.append("--force")
        args.append(target)
        await self._run(args, timeout=60)
        return {"ok": True, "action": "remove_container", "container_id": target}

    async def container_logs(self, container_id: str, *, tail: int = 200) -> dict[str, Any]:
        target = self._validate_container_target(container_id)
        safe_tail = max(1, min(int(tail), 2000))
        proc = await self._run(
            ["logs", "--tail", str(safe_tail), "--timestamps", target],
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(self._error_detail(proc))
        combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        return {"ok": True, "container_id": target, "tail": safe_tail, "logs": combined}

    async def _create_container(
        self,
        payload: DockerContainerCreateRequest,
        *,
        extra_labels: dict[str, str],
    ) -> dict[str, Any]:
        name = self._validate_name(payload.name)
        image = self._validate_image(payload.image)
        args = [
            "create",
            "--name",
            name,
            "--label",
            "enterprise-ai-qa-agent=managed",
        ]
        for key, value in extra_labels.items():
            args.extend(["--label", f"{key}={value}"])
        if payload.restart_policy != "no":
            args.extend(["--restart", payload.restart_policy])
        for port in payload.ports:
            args.extend(
                ["--publish", f"{port.host_port}:{port.container_port}/{port.protocol}"]
            )
        for volume in payload.volumes:
            source = self._validate_volume_source(volume.source)
            target = self._validate_volume_target(volume.target)
            mount = f"{source}:{target}"
            if volume.read_only:
                mount += ":ro"
            args.extend(["--volume", mount])
        for key, value in payload.environment.items():
            if not _ENV_KEY_PATTERN.fullmatch(key):
                raise ValueError(f"Invalid environment variable name '{key}'.")
            args.extend(["--env", f"{key}={value}"])
        if payload.entrypoint:
            args.extend(["--entrypoint", str(payload.entrypoint)])
        if len(payload.command) > 64 or any(len(str(item)) > 4096 for item in payload.command):
            raise ValueError("Container command exceeds the allowed size.")
        args.append(image)
        args.extend(str(item) for item in payload.command)

        created = await self._run(args, timeout=120)
        container_id = (created.stdout or "").strip()
        if payload.start:
            started = await self._run(["start", container_id or name], timeout=60, check=False)
            if started.returncode != 0:
                raise RuntimeError(
                    f"Container was created but could not be started: {self._error_detail(started)}"
                )
        return {
            "ok": True,
            "created": True,
            "container_id": container_id,
            "name": name,
            "image": image,
            "state": "running" if payload.start else "created",
        }

    async def _image_installed(self, image: str) -> bool:
        proc = await self._run(["image", "inspect", image], timeout=20, check=False)
        return proc.returncode == 0

    async def _run(
        self,
        args: list[str],
        *,
        timeout: int,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        docker = shutil.which("docker")
        if not docker:
            raise DockerUnavailableError(
                "Docker CLI is not installed or is not available on PATH."
            )
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [docker, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Docker command timed out after {timeout}s.") from exc
        if check and proc.returncode != 0:
            raise RuntimeError(self._error_detail(proc))
        return proc

    def _required_image_specs(self) -> list[dict[str, Any]]:
        specs = [
            {
                "key": key,
                "image": item["image"],
                "category": item["category"],
                "purpose": item["purpose"],
                "template_key": key,
            }
            for key, item in self._template_specs().items()
        ]
        known_keys = {item["key"] for item in specs}
        image_overrides = {
            "perf_k6_default": self._settings.k6_docker_image,
            "perf_jmeter_default": self._settings.jmeter_docker_image,
        }
        for entry in self._image_catalog.list_entries():
            if entry.image_key in known_keys:
                continue
            specs.append(
                {
                    "key": entry.image_key,
                    "image": image_overrides.get(entry.image_key, entry.image),
                    "category": entry.domain,
                    "purpose": entry.description,
                    "template_key": "",
                }
            )
        return specs

    def _template_specs(self) -> dict[str, dict[str, Any]]:
        prefix = self._settings.docker_managed_container_prefix
        redis_port = urlparse(self._settings.redis_url).port or 6379
        rustfs_port = self._endpoint_port(self._settings.rustfs_endpoint, 9000)
        mysql_environment = {
            "MYSQL_DATABASE": self._settings.mysql_database,
            "MYSQL_USER": self._settings.mysql_user,
        }
        if self._settings.mysql_password:
            mysql_environment.update(
                {
                    "MYSQL_PASSWORD": self._settings.mysql_password,
                    "MYSQL_ROOT_PASSWORD": self._settings.mysql_password,
                }
            )
        else:
            mysql_environment["MYSQL_ALLOW_EMPTY_PASSWORD"] = "yes"
        return {
            "redis": {
                "image": self._settings.docker_redis_image,
                "category": "infrastructure",
                "purpose": "Redis distributed locks and shared runtime coordination.",
                "default_name": f"{prefix}-redis",
                "ports": [{"host_port": redis_port, "container_port": 6379}],
                "volumes": [{"source": f"{prefix}-redis-data", "target": "/data"}],
                "environment": {},
                "command": ["redis-server", "--appendonly", "yes"],
            },
            "rustfs": {
                "image": self._settings.docker_rustfs_image,
                "category": "infrastructure",
                "purpose": "Artifact, attachment, and report object storage.",
                "default_name": f"{prefix}-rustfs",
                "ports": [
                    {"host_port": rustfs_port, "container_port": 9000},
                    {"host_port": rustfs_port + 1, "container_port": 9001},
                ],
                "volumes": [{"source": f"{prefix}-rustfs-data", "target": "/data"}],
                "environment": {
                    "RUSTFS_VOLUMES": "/data",
                    "RUSTFS_ADDRESS": "0.0.0.0:9000",
                    "RUSTFS_CONSOLE_ADDRESS": "0.0.0.0:9001",
                    "RUSTFS_CONSOLE_ENABLE": "true",
                    "RUSTFS_ACCESS_KEY": self._settings.rustfs_access_key,
                    "RUSTFS_SECRET_KEY": self._settings.rustfs_secret_key,
                },
                "command": [],
            },
            "mysql": {
                "image": self._settings.docker_mysql_image,
                "category": "infrastructure",
                "purpose": "Model, mailbox, and system configuration database.",
                "default_name": f"{prefix}-mysql",
                "ports": [{"host_port": self._settings.mysql_port, "container_port": 3306}],
                "volumes": [{"source": f"{prefix}-mysql-data", "target": "/var/lib/mysql"}],
                "environment": mysql_environment,
                "command": [],
            },
            "postgres": {
                "image": self._settings.docker_postgres_image,
                "category": "infrastructure",
                "purpose": "Sessions, memories, jobs, and runtime state database.",
                "default_name": f"{prefix}-postgres",
                "ports": [{"host_port": self._settings.postgres_port, "container_port": 5432}],
                "volumes": [{"source": f"{prefix}-postgres-data", "target": "/var/lib/postgresql/data"}],
                "environment": {
                    "POSTGRES_USER": self._settings.postgres_user,
                    "POSTGRES_PASSWORD": self._settings.postgres_password,
                    "POSTGRES_DB": self._settings.postgres_database,
                },
                "command": [],
            },
            "memgraph": {
                "image": self._settings.docker_memgraph_image,
                "category": "infrastructure",
                "purpose": "Knowledge and UI graph storage.",
                "default_name": f"{prefix}-memgraph",
                "ports": [{"host_port": self._settings.memgraph_port, "container_port": 7687}],
                "volumes": [{"source": f"{prefix}-memgraph-data", "target": "/var/lib/memgraph"}],
                "environment": {},
                "command": [],
            },
            "security_runner": {
                "image": self._settings.security_runner_docker_image,
                "category": "security",
                "purpose": "Isolated security testing command environment.",
                "default_name": f"{prefix}-security-runner",
                "ports": [],
                "volumes": [],
                "environment": {},
                "entrypoint": "sh",
                "command": ["-lc", "sleep infinity"],
            },
            "perf_k6_default": {
                "image": self._settings.k6_docker_image,
                "category": "performance",
                "purpose": "k6 performance and smoke testing runtime.",
                "default_name": f"{prefix}-k6",
                "ports": [],
                "volumes": [],
                "environment": {},
                "entrypoint": "sh",
                "command": ["-lc", "sleep infinity"],
            },
            "perf_jmeter_default": {
                "image": self._settings.jmeter_docker_image,
                "category": "performance",
                "purpose": "JMeter performance testing runtime.",
                "default_name": f"{prefix}-jmeter",
                "ports": [],
                "volumes": [],
                "environment": {},
                "entrypoint": "sh",
                "command": ["-lc", "sleep infinity"],
            },
        }

    def _public_templates(self) -> list[dict[str, Any]]:
        result = []
        for key, item in self._template_specs().items():
            result.append(
                {
                    "key": key,
                    "image": item["image"],
                    "category": item["category"],
                    "purpose": item["purpose"],
                    "default_name": item["default_name"],
                    "ports": item.get("ports") or [],
                    "volumes": item.get("volumes") or [],
                    "environment_keys": sorted((item.get("environment") or {}).keys()),
                }
            )
        return result

    def _validate_volume_source(self, value: str) -> str:
        normalized = str(value or "").strip()
        if _VOLUME_NAME_PATTERN.fullmatch(normalized):
            return normalized
        path = Path(normalized).expanduser()
        if not path.is_absolute():
            path = self._volume_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self._volume_root)
        except ValueError as exc:
            raise ValueError(
                f"Bind mount source must stay under '{self._volume_root}'."
            ) from exc
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)

    @staticmethod
    def _validate_volume_target(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized.startswith("/") or ".." in Path(normalized).parts:
            raise ValueError(f"Invalid container volume target '{value}'.")
        return normalized

    @staticmethod
    def _validate_image(value: str) -> str:
        normalized = str(value or "").strip()
        if not _IMAGE_PATTERN.fullmatch(normalized):
            raise ValueError(f"Invalid Docker image reference '{value}'.")
        return normalized

    @staticmethod
    def _validate_name(value: str) -> str:
        normalized = str(value or "").strip()
        if not _NAME_PATTERN.fullmatch(normalized):
            raise ValueError(f"Invalid Docker container name '{value}'.")
        return normalized

    @staticmethod
    def _validate_container_target(value: str) -> str:
        return DockerManagementService._validate_name(value)

    @staticmethod
    def _endpoint_port(endpoint: str, default: int) -> int:
        text = str(endpoint or "").strip()
        if ":" not in text:
            return default
        try:
            return int(text.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_json(value: str | None) -> dict[str, Any] | None:
        try:
            parsed = json.loads(str(value or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _parse_json_lines(cls, value: str | None) -> list[dict[str, Any]]:
        return [
            payload
            for line in str(value or "").splitlines()
            if (payload := cls._parse_json(line)) is not None
        ]

    @staticmethod
    def _error_detail(proc: subprocess.CompletedProcess[str]) -> str:
        return (proc.stderr or proc.stdout or "Docker command failed.").strip()
