from __future__ import annotations

import subprocess

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.docker import router as docker_router
from src.application import docker_management_service
from src.application.docker_management_service import (
    DockerCommandError,
    DockerManagementService,
    DockerUnavailableError,
)
from src.core.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AttributeError("private Docker service field"), 500),
        (DockerCommandError("private Docker command stderr", exit_code=1), 502),
        (RuntimeError("private service runtime error"), 500),
        (ValueError("invalid image reference"), 400),
        (DockerUnavailableError("Docker unavailable"), 503),
    ],
)
async def test_docker_pull_reports_internal_failures_as_server_errors(
    failure: Exception,
    expected_status: int,
) -> None:
    class DockerService:
        async def pull_image(self, image: str):
            raise failure

    app = FastAPI()
    app.state.docker_management_service = DockerService()
    app.include_router(docker_router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/docker/images/pull", json={"image": "example/image:latest"})

    assert response.status_code == expected_status
    if expected_status in {500, 502}:
        assert str(failure) not in response.text


@pytest.mark.asyncio
async def test_docker_cli_failure_has_explicit_command_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_management_service.shutil, "which", lambda _: "docker-test")
    monkeypatch.setattr(
        docker_management_service.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 23, "", "injected CLI failure"),
    )
    service = DockerManagementService(Settings())

    with pytest.raises(DockerCommandError) as caught:
        await service._run(["image", "pull", "example/image:latest"], timeout=1)

    assert caught.value.exit_code == 23


@pytest.mark.asyncio
@pytest.mark.parametrize("output,code,expected", [("", 0, False), ("a" * 64 + "\n", 0, True),
    ("a" * 12, 0, None), ("b" * 64, 0, None), ("a" * 64 + "\n" + "a" * 64, 0, None), ("", 1, None)])
async def test_container_absence_query_requires_success_and_exact_identity(monkeypatch, output, code, expected):
    calls = []
    monkeypatch.setattr(docker_management_service.shutil, "which", lambda _: "docker-test")
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, code, output, "private-query-error" if code else "")
    monkeypatch.setattr(docker_management_service.subprocess, "run", run)
    service = DockerManagementService(Settings())
    if expected is None:
        with pytest.raises(DockerCommandError):
            await service.container_exists("a" * 64)
    else:
        assert await service.container_exists("a" * 64) is expected
    assert calls == [["docker-test", "ps", "--all", "--no-trunc", "--filter", f"id={'a' * 64}", "--format", "{{.ID}}"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["", "a" * 12, "reusable-container-name"])
async def test_container_absence_query_never_uses_a_mutable_target(monkeypatch, target):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid identity must not reach Docker")
    monkeypatch.setattr(docker_management_service.subprocess, "run", unexpected)
    with pytest.raises(ValueError, match="full container ID"):
        await DockerManagementService(Settings()).container_exists(target)
