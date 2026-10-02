from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.application.skills.errors import (
    SkillDownloadError,
    SkillManagementValidationError,
)
from src.application.skills.skill_management_service import SkillManagementService

logger = logging.getLogger(__name__)


class SkillMarketplaceService:
    """Search and install skills from supported remote marketplaces."""

    ANTHROPIC_REPO_ZIP = "https://github.com/anthropics/skills/archive/refs/heads/main.zip"
    SKILLSMP_SEARCH_URL = "https://skillsmp.com/api/v1/skills/search"
    CACHE_TTL_SECONDS = 600

    def __init__(self, skill_management_service: SkillManagementService) -> None:
        self._skill_management_service = skill_management_service
        self._anthropic_cache: tuple[float, list[dict[str, Any]]] | None = None

    def list_marketplaces(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "anthropic",
                "name": "Anthropic Official Skills",
                "description": "Official skills from https://github.com/anthropics/skills.",
                "supports_search": True,
                "requires_api_key": False,
            },
            {
                "key": "skillsmp",
                "name": "SkillsMP Marketplace",
                "description": "Community skill marketplace from https://skillsmp.com.",
                "supports_search": True,
                "requires_api_key": False,
                "ai_search_available": bool(os.getenv("SKILLSMP_API_KEY")),
            },
        ]

    def search(self, source: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        source_key = self._safe_source(source)
        if source_key == "anthropic":
            items = self._search_anthropic(query=query, limit=limit)
        elif source_key == "skillsmp":
            items = self._search_skillsmp(query=query, limit=limit)
        else:
            raise SkillManagementValidationError(f"Unsupported skill marketplace source: {source}")
        return {"source": source_key, "query": query, "items": items, "count": len(items)}

    def preview(self, source: str, skill_id: str, url: str | None = None) -> dict[str, Any]:
        source_key = self._safe_source(source)
        if source_key == "anthropic":
            item = self._find_anthropic_item(skill_id)
            return {**item, "content": item.get("content", "")}
        if source_key == "skillsmp":
            if url and self._is_http_url(url):
                content = self._download_text(url)
            else:
                content = ""
            return {"source": source_key, "id": skill_id, "url": url or "", "content": content}
        raise SkillManagementValidationError(f"Unsupported skill marketplace source: {source}")

    def install(
        self,
        source: str,
        skill_id: str,
        url: str | None = None,
        key: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        source_key = self._safe_source(source)
        if source_key == "anthropic":
            return self._install_anthropic(skill_id=skill_id, key=key, overwrite=overwrite)
        if source_key == "skillsmp":
            install_url = url or self._derive_skillsmp_install_url(skill_id)
            if not install_url or not self._is_http_url(install_url):
                raise SkillManagementValidationError(
                    "SkillsMP result does not include a downloadable SKILL.md or GitHub source URL. "
                    "Try installing the same skill from its GitHub repository URL."
                )
            return self._skill_management_service.install_from_url(
                url=install_url,
                key=key or self._key_from_skillsmp_id(skill_id),
                overwrite=overwrite,
            )
        raise SkillManagementValidationError(f"Unsupported skill marketplace source: {source}")

    def _search_anthropic(self, query: str, limit: int) -> list[dict[str, Any]]:
        items = self._load_anthropic_index()
        normalized_query = query.strip().lower()
        if normalized_query:
            tokens = [token for token in re.split(r"\s+", normalized_query) if token]
            items = [
                item
                for item in items
                if all(
                    token in " ".join(
                        [
                            str(item.get("id", "")),
                            str(item.get("name", "")),
                            str(item.get("description", "")),
                        ]
                    ).lower()
                    for token in tokens
                )
            ]
        return items[: max(1, min(limit, 100))]

    def _load_anthropic_index(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._anthropic_cache and now - self._anthropic_cache[0] < self.CACHE_TTL_SECONDS:
            return self._anthropic_cache[1]

        data = self._download_bytes(self.ANTHROPIC_REPO_ZIP)
        items: list[dict[str, Any]] = []
        with TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "anthropic-skills.zip"
            zip_path.write_bytes(data)
            with self._open_marketplace_archive(zip_path) as archive:
                for info in archive.infolist():
                    normalized = info.filename.replace("\\", "/")
                    if not normalized.endswith("/SKILL.md") or "/skills/" not in normalized:
                        continue
                    parts = normalized.split("/")
                    skill_index = parts.index("skills")
                    if len(parts) <= skill_index + 2:
                        continue
                    skill_id = parts[skill_index + 1]
                    content = archive.read(info).decode("utf-8", errors="replace")
                    frontmatter, body = self._parse_frontmatter(content)
                    name = str(frontmatter.get("name") or skill_id)
                    description = str(frontmatter.get("description") or self._first_sentence(body) or "")
                    items.append(
                        {
                            "source": "anthropic",
                            "id": skill_id,
                            "key": skill_id,
                            "name": name,
                            "description": description,
                            "tags": ["anthropic", "official"],
                            "url": self.ANTHROPIC_REPO_ZIP,
                            "content": content,
                        }
                    )
        items.sort(key=lambda item: str(item.get("name", item.get("id", ""))).lower())
        self._anthropic_cache = (now, items)
        return items

    def _find_anthropic_item(self, skill_id: str) -> dict[str, Any]:
        safe_id = self._safe_key(skill_id)
        for item in self._load_anthropic_index():
            if item.get("id") == safe_id or self._safe_key(str(item.get("id", ""))) == safe_id:
                return item
        raise SkillManagementValidationError(f"Anthropic skill not found: {skill_id}")

    def _install_anthropic(self, skill_id: str, key: str | None, overwrite: bool) -> dict[str, Any]:
        safe_id = self._safe_key(skill_id)
        data = self._download_bytes(self.ANTHROPIC_REPO_ZIP)
        with TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "anthropic-skills.zip"
            extract_root = Path(temp_dir) / "repo"
            zip_path.write_bytes(data)
            with self._open_marketplace_archive(zip_path) as archive:
                self._safe_extract(archive, extract_root)
            matches = [
                path
                for path in extract_root.rglob("SKILL.md")
                if path.parent.name == safe_id and path.parent.parent.name == "skills"
            ]
            if not matches:
                raise SkillManagementValidationError(f"Anthropic skill not found in downloaded archive: {skill_id}")
            install_key = key or safe_id
            return self._skill_management_service.install_from_path(
                source_path=str(matches[0].parent),
                key=install_key,
                overwrite=overwrite,
            )

    def _search_skillsmp(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"q": query or "skill", "limit": max(1, min(limit, 50))})
        headers = {"Accept": "application/json", "User-Agent": "Enterprise-AI-QA-Agent/0.1"}
        api_key = os.getenv("SKILLSMP_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = self._download_json(f"{self.SKILLSMP_SEARCH_URL}?{params}", headers=headers)
        raw_items = self._extract_items(data)
        return [self._normalize_skillsmp_item(item) for item in raw_items[: max(1, min(limit, 50))]]

    def _normalize_skillsmp_item(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            item = {"name": str(item)}
        skill_id = str(
            item.get("id")
            or item.get("slug")
            or item.get("key")
            or item.get("name")
            or item.get("title")
            or "skillsmp-skill"
        )
        name = str(item.get("name") or item.get("title") or skill_id)
        description = str(item.get("description") or item.get("summary") or "")
        install_url = self._first_url(
            item,
            [
                "skillFileUrl",
                "skill_file_url",
                "skillUrl",
                "skill_url",
                "install_url",
                "installUrl",
                "download_url",
                "downloadUrl",
                "source_url",
                "sourceUrl",
                "raw_url",
                "rawUrl",
                "github_url",
                "githubUrl",
                "repository_url",
                "repositoryUrl",
                "repo_url",
                "repoUrl",
                "html_url",
                "htmlUrl",
                "url",
            ],
        )
        return {
            "source": "skillsmp",
            "id": self._safe_key(skill_id),
            "key": self._safe_key(name),
            "name": name,
            "description": description,
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else ["skillsmp"],
            "url": install_url,
            "metadata": item,
        }

    def _download_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        request = urllib.request.Request(
            url,
            headers=headers or {"User-Agent": "Enterprise-AI-QA-Agent/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SkillDownloadError("Skill marketplace download failed.") from exc

    def _download_text(self, url: str) -> str:
        return self._download_bytes(url).decode("utf-8", errors="replace")

    def _download_json(self, url: str, headers: dict[str, str]) -> Any:
        data = self._download_bytes(url, headers=headers)
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillDownloadError("Skill marketplace returned invalid JSON.") from exc

    @contextmanager
    def _open_marketplace_archive(self, path: Path) -> Iterator[zipfile.ZipFile]:
        try:
            with zipfile.ZipFile(path) as archive:
                yield archive
        except zipfile.BadZipFile as exc:
            raise SkillDownloadError("Skill marketplace returned an invalid ZIP archive.") from exc

    def _extract_items(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("skills", "items", "results", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    nested = self._extract_items(value)
                    if nested:
                        return nested
        return []

    def _first_url(self, item: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and self._is_http_url(value):
                return value
        for value in item.values():
            if isinstance(value, dict):
                nested = self._first_url(value, keys)
                if nested:
                    return nested
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str) and self._is_http_url(entry):
                        return entry
                    if isinstance(entry, dict):
                        nested = self._first_url(entry, keys)
                        if nested:
                            return nested
        return ""

    def _derive_skillsmp_install_url(self, skill_id: str) -> str:
        parts = [part for part in self._safe_key(skill_id).split("-") if part]
        marker = self._find_subsequence(parts, ["skills"])
        suffix = ["skill", "md"]
        if marker <= 0 or parts[-2:] != suffix:
            return ""
        path_parts = parts[marker:-2]
        if len(path_parts) < 2:
            return ""
        repo_parts = parts[:marker]
        skill_path = "/".join(path_parts) + "/SKILL.md"
        candidates: list[tuple[str, str]] = []
        for split_index in range(1, len(repo_parts)):
            owner = "-".join(repo_parts[:split_index])
            repo = "-".join(repo_parts[split_index:])
            candidates.append((owner, repo))
        # Prefer common GitHub shape: hyphenated owner + short repo, e.g. vm0-ai/vm0.
        candidates.sort(key=lambda item: (len(item[1]), len(item[0])))
        for owner, repo in candidates:
            for branch in ("main", "master"):
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{skill_path}"
                try:
                    content = self._download_text(raw_url)
                except SkillDownloadError as exc:
                    logger.info(
                        "skill_marketplace_candidate_unavailable",
                        extra={"operation": "derive_install_url", "error_type": type(exc.__cause__).__name__},
                    )
                    continue
                if content.lstrip().startswith("---") and "description:" in content:
                    return raw_url
        return ""

    def _key_from_skillsmp_id(self, skill_id: str) -> str:
        parts = [part for part in self._safe_key(skill_id).split("-") if part]
        marker = self._find_subsequence(parts, ["skills"])
        if marker >= 0 and len(parts) > marker + 1:
            return self._safe_key(parts[marker + 1])
        return self._safe_key(skill_id)

    def _find_subsequence(self, parts: list[str], pattern: list[str]) -> int:
        for index in range(0, len(parts) - len(pattern) + 1):
            if parts[index : index + len(pattern)] == pattern:
                return index
        return -1

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, str], str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.S)
        if not match:
            return {}, content
        frontmatter: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                frontmatter[key.strip()] = value.strip().strip('"').strip("'")
        return frontmatter, match.group(2)

    def _first_sentence(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip(" #")
            if stripped:
                return stripped[:180]
        return ""

    def _safe_key(self, value: str) -> str:
        key = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip("-").lower()
        if not key:
            raise SkillManagementValidationError("Skill key cannot be empty.")
        return key[:96]

    def _safe_source(self, value: str) -> str:
        source = str(value or "").strip().lower()
        if source in {"github", "anthropics", "anthropic"}:
            return "anthropic"
        if source in {"skillsmp", "skills-mp"}:
            return "skillsmp"
        raise SkillManagementValidationError(f"Unsupported marketplace source: {value}")

    def _is_http_url(self, value: str) -> bool:
        return value.startswith(("http://", "https://"))

    def _safe_extract(self, archive: zipfile.ZipFile, target: Path) -> None:
        root = target.resolve()
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if root != destination and root not in destination.parents:
                raise SkillDownloadError("Refusing to extract marketplace archive entry outside target directory.")
        archive.extractall(target)
