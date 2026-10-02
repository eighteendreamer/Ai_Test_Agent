"""Skill directory boundary contracts on exclusively owned temporary files."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.registry import router
from src.application.skills.skill_management_service import SkillManagementService
from src.registry.skills import SkillRegistry

CONTENT = "---\nname: healthy\ndescription: Boundary fixture.\n---\nBody\n"


def _app(tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    healthy = root / "healthy" / "SKILL.md"
    healthy.parent.mkdir()
    healthy.write_text(CONTENT, encoding="utf-8")
    registry = SkillRegistry(skills_root=root)
    service = SkillManagementService(registry)
    # Same isolation pattern as test_registry_marketplace_event_loop. The root
    # and its parent belong to this fixture; product skill files are never used.
    service._skills_root = root
    app = FastAPI()
    app.state.skill_management_service = service
    app.include_router(router, prefix="/api/v1")
    return app, root, healthy


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("key", [".", ".."])
async def test_root_and_parent_are_not_manageable_skill_directories(tmp_path, method, key):
    app, root, healthy = _app(tmp_path)
    # Verify all potential legacy filesystem targets before invoking the API.
    # Even the parent target is this test's own temporary directory.
    target = (root / key).resolve()
    assert target == tmp_path.resolve() or tmp_path.resolve() in target.parents
    kwargs = {"json": {"content": CONTENT}} if method == "PUT" else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method, "/api/v1/registry/skills/" + quote(key, safe="").replace(".", "%2E"), **kwargs,
        )
    assert response.status_code == 400, response.text
    assert root.is_dir()
    assert healthy.read_text(encoding="utf-8") == CONTENT
    assert not (root / "SKILL.md").exists()
    assert not (tmp_path / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_valid_skill_crud_and_idempotent_delete_keep_their_contract(tmp_path):
    app, root, healthy = _app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/v1/registry/skills/missing")
        assert missing.status_code == 404
        loaded = await client.get("/api/v1/registry/skills/healthy")
        assert loaded.status_code == 200
        assert loaded.json()["content"] == CONTENT
        updated = await client.put(
            "/api/v1/registry/skills/healthy", json={"content": CONTENT.replace("Body", "Updated")},
        )
        assert updated.status_code == 200
        assert "Updated" in healthy.read_text(encoding="utf-8")
        invalid = await client.put("/api/v1/registry/skills/healthy", json={"content": ""})
        assert invalid.status_code == 400
        deleted = await client.delete("/api/v1/registry/skills/healthy")
        assert deleted.status_code == 200
        repeated = await client.delete("/api/v1/registry/skills/healthy")
        assert repeated.status_code == 200
        assert repeated.json()["ok"] is True
    assert root.is_dir()
    assert not healthy.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["filesystem", "decoding"])
async def test_controlled_skill_read_failure_is_not_reported_as_missing(tmp_path, monkeypatch, caplog, failure):
    # Error-classification robustness check, not a real disk failure incident.
    app, _, healthy = _app(tmp_path)
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == healthy:
            if failure == "decoding":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private decoding diagnostic")
            raise OSError("private filesystem diagnostic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/registry/skills/healthy")
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "Skill read failed."
    assert "private filesystem diagnostic" not in caplog.text
    assert "private decoding diagnostic" not in caplog.text
    assert any(
        record.message == "skill_read_failed"
        and record.error_type == ("OSError" if failure == "filesystem" else "UnicodeDecodeError")
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("link_scope", ["inside", "outside"])
async def test_skill_file_symlink_follows_managed_root_boundary(tmp_path, method, link_scope):
    app, root, healthy = _app(tmp_path)
    target = (root if link_scope == "inside" else tmp_path) / "linked-content.md"
    target.write_text(CONTENT, encoding="utf-8")
    assert healthy.resolve().is_relative_to(tmp_path.resolve())
    assert healthy.read_text(encoding="utf-8") == CONTENT
    healthy.unlink()
    try:
        healthy.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")
    replacement = CONTENT.replace("Body", "Updated")
    kwargs = {"json": {"content": replacement}} if method == "PUT" else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/api/v1/registry/skills/healthy", **kwargs)
    assert response.status_code == (200 if link_scope == "inside" else 400), response.text
    expected = replacement if method == "PUT" and link_scope == "inside" else CONTENT
    assert target.read_text(encoding="utf-8") == expected
