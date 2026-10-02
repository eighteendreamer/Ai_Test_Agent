"""Skill API error contracts; controlled faults are not provider incidents."""

from __future__ import annotations

import base64
import io
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.registry import router
from src.application.skills.skill_management_service import SkillManagementService
from src.application.skills.skill_marketplace_service import SkillMarketplaceService
from src.registry.skills import SkillRegistry

CONTENT = "---\nname: healthy\ndescription: Error contract fixture.\n---\nBody\n"


def _app(tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    path = root / "healthy" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(CONTENT, encoding="utf-8")
    service = SkillManagementService(SkillRegistry(skills_root=root))
    service._skills_root = root
    app = FastAPI()
    app.state.skill_management_service = service
    app.state.skill_marketplace_service = SkillMarketplaceService(service)
    app.include_router(router, prefix="/api/v1")
    return app, root


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete", "local_install", "upload"])
@pytest.mark.parametrize("error_type", [OSError, ValueError])
async def test_controlled_local_execution_failure_is_not_bad_input(tmp_path, monkeypatch, caplog, operation, error_type):
    app, root = _app(tmp_path)
    private = "private local execution diagnostic"
    if operation in {"update", "upload"}:
        original = Path.write_text

        def write_text(path, *args, **kwargs):
            if path == root / "healthy" / "SKILL.md":
                raise error_type(private)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write_text)
        method, url, payload = "PUT", "/api/v1/registry/skills/healthy", {"content": CONTENT}
        if operation == "upload":
            method, url = "POST", "/api/v1/registry/skills/upload"
            payload = {"filename": "fixture.md", "key": "healthy", "content_base64": base64.b64encode(CONTENT.encode()).decode()}
    elif operation == "delete":
        original = shutil.rmtree

        def rmtree(path, *args, **kwargs):
            if Path(path) == root / "healthy":
                raise error_type(private)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", rmtree)
        method, url, payload = "DELETE", "/api/v1/registry/skills/healthy", None
    else:
        source = tmp_path / "source"
        source.mkdir()
        (source / "SKILL.md").write_text(CONTENT, encoding="utf-8")
        original = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            if Path(src) == source:
                raise error_type(private)
            return original(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", copytree)
        method, url, payload = "POST", "/api/v1/registry/skills/install", {"source_path": str(source), "key": "new-skill"}
    async with _client(app) as client:
        response = await client.request(method, url, json=payload)
    assert response.status_code == 500, response.text
    assert private not in response.text
    assert private not in caplog.text
    assert any(record.message == "skill_management_operation_failed" for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["install", "search", "preview", "marketplace_install"])
@pytest.mark.parametrize("failure", ["http", "transport", "timeout"])
async def test_controlled_remote_download_failure_is_visible(tmp_path, monkeypatch, caplog, operation, failure):
    app, root = _app(tmp_path)
    private = "private upstream diagnostic"

    def urlopen(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        if failure == "http":
            raise urllib.error.HTTPError(url, 503, private, {}, None)
        if failure == "transport":
            raise urllib.error.URLError(private)
        raise TimeoutError(private)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    source_url = "https://example.test/SKILL.md"
    async with _client(app) as client:
        if operation == "install":
            response = await client.post("/api/v1/registry/skills/install", json={"url": source_url, "key": "new-skill"})
        elif operation == "search":
            response = await client.get("/api/v1/registry/skills/marketplaces/search", params={"source": "skillsmp"})
        elif operation == "preview":
            response = await client.get("/api/v1/registry/skills/marketplaces/preview", params={"source": "skillsmp", "skill_id": "fixture", "url": source_url})
        else:
            response = await client.post("/api/v1/registry/skills/marketplaces/install", json={"source": "skillsmp", "skill_id": "fixture", "url": source_url, "key": "new-skill"})
    assert response.status_code == 502, response.text
    assert private not in response.text
    assert private not in caplog.text
    assert not (root / "new-skill").exists()
    records = [record for record in caplog.records if record.message == "skill_download_failed"]
    assert len(records) == 1
    assert records[0].cause_type == {"http": "HTTPError", "transport": "URLError", "timeout": "TimeoutError"}[failure]
    if failure == "http":
        assert records[0].upstream_status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("source,operation", [("skillsmp", "search"), ("anthropic", "search"), ("anthropic", "install")])
async def test_controlled_marketplace_payload_error_is_not_input_validation(tmp_path, monkeypatch, source, operation):
    app, _ = _app(tmp_path)
    # Valid request; invalid response belongs to the upstream contract.
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"not-json-or-zip"))
    async with _client(app) as client:
        if operation == "search":
            response = await client.get("/api/v1/registry/skills/marketplaces/search", params={"source": source})
        else:
            response = await client.post("/api/v1/registry/skills/marketplaces/install", json={"source": source, "skill_id": "fixture"})
    assert response.status_code == 502, response.text


@pytest.mark.asyncio
async def test_explicit_validation_and_successful_local_install_keep_http_contract(tmp_path):
    app, root = _app(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(CONTENT, encoding="utf-8")
    async with _client(app) as client:
        installed = await client.post("/api/v1/registry/skills/install", json={"source_path": str(source), "key": "copied"})
        assert installed.status_code == 200
        assert (root / "copied" / "SKILL.md").read_text(encoding="utf-8") == CONTENT
        requests = [
            ("PUT", "/api/v1/registry/skills/healthy", {"content": ""}),
            ("POST", "/api/v1/registry/skills/install", {}),
            ("POST", "/api/v1/registry/skills/install", {"source_path": str(source), "key": "copied"}),
            ("POST", "/api/v1/registry/skills/upload", {"filename": "fixture.md", "content_base64": "x"}),
            ("POST", "/api/v1/registry/skills/upload", {"filename": "fixture.md", "content_base64": base64.b64encode(b"\xff").decode()}),
            ("POST", "/api/v1/registry/skills/upload", {"filename": "fixture.zip", "content_base64": base64.b64encode(b"not-zip").decode()}),
            ("POST", "/api/v1/registry/skills/marketplaces/install", {"source": "unknown", "skill_id": "fixture"}),
        ]
        for method, url, payload in requests:
            response = await client.request(method, url, json=payload)
            assert response.status_code == 400, response.text
        no_url_preview = await client.get("/api/v1/registry/skills/marketplaces/preview", params={"source": "skillsmp", "skill_id": "fixture"})
        assert no_url_preview.status_code == 200
        assert no_url_preview.json()["content"] == ""


@pytest.mark.asyncio
async def test_archive_install_keeps_partial_success_contract(tmp_path):
    app, root = _app(tmp_path)
    archive_path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("healthy/SKILL.md", CONTENT)
        archive.writestr("new-skill/SKILL.md", CONTENT.replace("name: healthy", "name: new-skill"))
    async with _client(app) as client:
        response = await client.post("/api/v1/registry/skills/install", json={"source_path": str(archive_path)})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial"
    assert result["ok"] is False
    assert result["installed_count"] == result["failed_count"] == 1
    assert result["items"][0]["key"] == "new-skill"
    assert result["failed"][0]["key"] == "healthy"
    assert (root / "healthy" / "SKILL.md").read_text(encoding="utf-8") == CONTENT
    assert (root / "new-skill" / "SKILL.md").exists()
