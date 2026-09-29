from __future__ import annotations

import asyncio
import io
import urllib.request
from threading import Event

import httpx
import pytest
from fastapi import FastAPI
from src.api.routes import registry as registry_route
from src.application.skills.skill_management_service import SkillManagementService
from src.application.skills.skill_marketplace_service import SkillMarketplaceService
from src.registry.skills import SkillRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "response_bytes", "result_key", "expected"),
    [
        (
            "GET",
            "/api/v1/registry/skills/marketplaces/search",
            {"params": {"source": "skillsmp", "q": "sample"}},
            b'{"skills":[{"id":"sample-skill","name":"Sample Skill"}]}',
            "count",
            1,
        ),
        (
            "GET",
            "/api/v1/registry/skills/marketplaces/preview",
            {"params": {"source": "skillsmp", "skill_id": "sample-skill", "url": "https://example.test/SKILL.md"}},
            b"sample preview",
            "content",
            "sample preview",
        ),
        (
            "POST",
            "/api/v1/registry/skills/marketplaces/install",
            {"json": {"source": "skillsmp", "skill_id": "sample-skill", "url": "https://example.test/SKILL.md"}},
            b"---\nname: Sample Skill\ndescription: Sample.\n---\nBody\n",
            "key",
            "sample-skill",
        ),
        (
            "POST",
            "/api/v1/registry/skills/install",
            {"json": {"url": "https://example.test/SKILL.md", "key": "sample-skill"}},
            b"---\nname: Sample Skill\ndescription: Sample.\n---\nBody\n",
            "key",
            "sample-skill",
        ),
    ],
)
async def test_remote_skill_routes_keep_event_loop_responsive(
    monkeypatch,
    tmp_path,
    method: str,
    path: str,
    request_kwargs: dict,
    response_bytes: bytes,
    result_key: str,
    expected: object,
) -> None:
    entered = Event()
    release = Event()
    timed_out = False

    def slow_urlopen(request, timeout):
        nonlocal timed_out
        entered.set()
        timed_out = not release.wait(timeout=1.0)
        return io.BytesIO(response_bytes)

    monkeypatch.setattr(urllib.request, "urlopen", slow_urlopen)
    skill_registry = SkillRegistry(skills_root=tmp_path)
    skill_management_service = SkillManagementService(skill_registry)
    skill_management_service._skills_root = tmp_path
    app = FastAPI()
    app.state.skill_management_service = skill_management_service
    app.state.skill_marketplace_service = SkillMarketplaceService(skill_management_service)
    app.include_router(registry_route.router, prefix="/api/v1")

    async def release_after_download_starts() -> None:
        while not entered.is_set():
            await asyncio.sleep(0)
        release.set()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response, _ = await asyncio.gather(
            client.request(method, path, **request_kwargs),
            release_after_download_starts(),
        )

    assert response.status_code == 200
    assert response.json()[result_key] == expected
    assert timed_out is False


@pytest.mark.asyncio
async def test_local_skill_install_and_marketplace_errors_keep_http_contract(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(
        "---\nname: Local Skill\ndescription: Local sample.\n---\nBody\n",
        encoding="utf-8",
    )
    skills_root = tmp_path / "installed"
    skill_registry = SkillRegistry(skills_root=skills_root)
    skill_management_service = SkillManagementService(skill_registry)
    skill_management_service._skills_root = skills_root
    app = FastAPI()
    app.state.skill_management_service = skill_management_service
    app.state.skill_marketplace_service = SkillMarketplaceService(skill_management_service)
    app.include_router(registry_route.router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        installed = await client.post(
            "/api/v1/registry/skills/install",
            json={"source_path": str(source_dir), "key": "local-skill"},
        )
        invalid_marketplace = await client.get(
            "/api/v1/registry/skills/marketplaces/search",
            params={"source": "unknown"},
        )
        missing_install_source = await client.post(
            "/api/v1/registry/skills/install", json={},
        )

    assert installed.status_code == 200
    assert installed.json()["key"] == "local-skill"
    assert (skills_root / "local-skill" / "SKILL.md").exists()
    assert invalid_marketplace.status_code == 400
    assert "Unsupported marketplace source" in invalid_marketplace.json()["detail"]
    assert missing_install_source.status_code == 400
    assert missing_install_source.json()["detail"] == "Either source_path or url is required."
