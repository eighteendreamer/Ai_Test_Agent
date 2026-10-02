from __future__ import annotations

import base64
import binascii
import logging
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from src.application.security.upload_security_service import UploadSecurityService
from src.application.skills.errors import (
    SkillDownloadError,
    SkillManagementValidationError,
    SkillPathValidationError,
)
from src.registry.skills import SkillRegistry

logger = logging.getLogger(__name__)


class SkillManagementService:
    """Manage filesystem skills under Agent_Server/src/SKILLS."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        upload_security_service: UploadSecurityService | None = None,
    ) -> None:
        self._skill_registry = skill_registry
        self._upload_security_service = upload_security_service
        self._skills_root = Path(__file__).resolve().parents[2] / "SKILLS"
        self._skills_root.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[dict[str, Any]]:
        self._skill_registry.reload()
        descriptors = {item.key: item for item in self._skill_registry.list()}
        items: list[dict[str, Any]] = []
        for key, descriptor in sorted(descriptors.items()):
            skill_dir = self._skills_root / key
            skill_file = skill_dir / "SKILL.md"
            items.append(
                {
                    **descriptor.model_dump(mode="python"),
                    "installed": skill_file.exists(),
                    "managed_root": str(self._skills_root),
                    "path": str(skill_file) if skill_file.exists() else "",
                    "source": "filesystem" if skill_file.exists() else "builtin-fallback",
                    "references": self._list_references(skill_dir),
                }
            )
        return items

    def get_skill(self, key: str) -> dict[str, Any]:
        safe_key = self._safe_key(key)
        skill_dir = self._skills_root / safe_key
        self._ensure_within_root(skill_dir, operation="read")
        skill_file = skill_dir / "SKILL.md"
        self._ensure_within_root(skill_file, operation="read")
        if not skill_file.exists():
            descriptor = self._skill_registry.get(safe_key)
            return {
                **descriptor.model_dump(mode="python"),
                "installed": False,
                "managed_root": str(self._skills_root),
                "path": "",
                "content": "",
                "references": [],
            }
        descriptor = self._skill_registry.get(safe_key)
        return {
            **descriptor.model_dump(mode="python"),
            "installed": True,
            "managed_root": str(self._skills_root),
            "path": str(skill_file),
            "content": skill_file.read_text(encoding="utf-8"),
            "references": self._list_references(skill_dir),
        }

    def upsert_skill(self, key: str, content: str) -> dict[str, Any]:
        safe_key = self._safe_key(key)
        if not content.strip():
            raise SkillManagementValidationError("Skill content cannot be empty.")
        if not self._has_skill_frontmatter(content):
            raise SkillManagementValidationError("Skill content must start with SKILL.md frontmatter containing name and description.")
        skill_dir = self._skills_root / safe_key
        self._ensure_within_root(skill_dir, operation="update")
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        self._ensure_within_root(skill_file, operation="update")
        skill_file.write_text(content.rstrip() + "\n", encoding="utf-8")
        self._skill_registry.reload()
        return self.get_skill(safe_key)

    def delete_skill(self, key: str) -> dict[str, Any]:
        safe_key = self._safe_key(key)
        skill_dir = self._skills_root / safe_key
        self._ensure_within_root(skill_dir, operation="delete")
        if not skill_dir.exists():
            return {"ok": True, "message": f"Skill '{safe_key}' is already absent."}
        shutil.rmtree(skill_dir)
        self._skill_registry.reload()
        return {"ok": True, "message": f"Skill '{safe_key}' was deleted from src/SKILLS."}

    def install_from_path(self, source_path: str, key: str | None = None, overwrite: bool = False) -> dict[str, Any]:
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            raise SkillManagementValidationError(f"Source path does not exist: {source}")
        if source.is_file() and source.name.lower() == "skill.md":
            install_key = self._safe_key(key or source.parent.name)
            return self.upsert_skill(install_key, self._decode_skill_content(source.read_bytes()))
        if source.is_dir():
            skill_file = source / "SKILL.md"
            if not skill_file.exists():
                raise SkillManagementValidationError(f"Source directory must contain SKILL.md: {source}")
            install_key = self._safe_key(key or source.name)
            return self._copy_skill_dir(source, install_key, overwrite)
        if source.is_file() and source.suffix.lower() == ".zip":
            with TemporaryDirectory() as temp_dir:
                self._extract_skill_archive(source, Path(temp_dir))
                return self._install_extracted_skills(Path(temp_dir), key=key, overwrite=overwrite)
        raise SkillManagementValidationError("Source path must be a skill directory, SKILL.md file, or .zip archive.")

    def install_from_url(self, url: str, key: str | None = None, overwrite: bool = False) -> dict[str, Any]:
        if not url.startswith(("http://", "https://")):
            raise SkillManagementValidationError("Only http:// and https:// URLs are supported.")
        url = self._normalize_github_skill_url(url)
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skill-download"
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise SkillDownloadError("Skill download failed.") from exc
            if url.lower().endswith(".zip"):
                zip_path = target.with_suffix(".zip")
                zip_path.write_bytes(data)
                self._extract_skill_archive(zip_path, target)
                return self._install_extracted_skills(target, key=key, overwrite=overwrite)
            content = self._decode_skill_content(data)
            install_key = self._safe_key(key or self._name_from_content(content) or "downloaded-skill")
            return self.upsert_skill(install_key, content)

    async def install_from_upload(
        self,
        filename: str,
        content_base64: str,
        key: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        safe_name = Path(filename or "uploaded-skill").name
        try:
            data = base64.b64decode(content_base64)
        except (binascii.Error, ValueError) as exc:
            raise SkillManagementValidationError("Skill upload must contain valid Base64 data.") from exc
        upload_result = await self._secure_stage_upload(filename=safe_name, content=data)
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / safe_name
            target.write_bytes(data)
            if safe_name.lower().endswith(".zip"):
                self._extract_skill_archive(target, Path(temp_dir) / "extracted")
                result = self._install_extracted_skills(Path(temp_dir) / "extracted", key=key, overwrite=overwrite)
                return self._attach_upload_metadata(result, upload_result)
            content = self._decode_skill_content(data)
            install_key = self._safe_key(key or self._name_from_content(content) or Path(safe_name).stem)
            result = self.upsert_skill(install_key, content)
            return self._attach_upload_metadata(result, upload_result)

    def _copy_skill_dir(self, source: Path, key: str, overwrite: bool) -> dict[str, Any]:
        target = self._skills_root / key
        self._ensure_within_root(target, operation="install")
        if target.exists():
            if not overwrite:
                raise SkillManagementValidationError(f"Skill '{key}' already exists. Pass overwrite=true to replace it.")
            shutil.rmtree(target)
        shutil.copytree(source, target)
        if not (target / "SKILL.md").exists():
            raise SkillManagementValidationError("Installed skill is invalid because SKILL.md is missing.")
        self._skill_registry.reload()
        return self.get_skill(key)

    def _find_skill_source(self, root: Path) -> Path:
        candidates = self._find_skill_sources(root)
        if not candidates:
            raise SkillManagementValidationError("Downloaded archive does not contain SKILL.md.")
        return candidates[0]

    def _find_skill_sources(self, root: Path) -> list[Path]:
        candidates = [path.parent for path in root.rglob("SKILL.md")]
        unique = list(dict.fromkeys(candidates))
        return sorted(unique, key=lambda item: (len(item.parts), str(item).lower()))

    def _install_extracted_skills(self, root: Path, key: str | None, overwrite: bool) -> dict[str, Any]:
        sources = self._find_skill_sources(root)
        if not sources:
            raise SkillManagementValidationError("Downloaded archive does not contain SKILL.md.")
        if key and len(sources) > 1:
            raise SkillManagementValidationError("Cannot use a single custom key when installing multiple skills from one archive.")
        installed: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for source in sources:
            install_key = self._safe_key(key or source.name)
            try:
                installed.append(self._copy_skill_dir(source, install_key, overwrite))
            except Exception as exc:
                failed.append({"key": install_key, "source": str(source), "error": str(exc)})
        if len(sources) == 1 and installed and not failed:
            return installed[0]
        self._skill_registry.reload()
        return {
            "ok": not failed,
            "status": "completed" if not failed else "partial",
            "summary": f"Installed {len(installed)} skill(s) from archive; {len(failed)} failed.",
            "installed_count": len(installed),
            "failed_count": len(failed),
            "items": installed,
            "failed": failed,
        }

    def _list_references(self, skill_dir: Path) -> list[str]:
        references_dir = skill_dir / "references"
        if not references_dir.exists():
            return []
        return [str(path) for path in sorted(references_dir.rglob("*")) if path.is_file()]

    def _has_skill_frontmatter(self, content: str) -> bool:
        return bool(re.match(r"^---\s*\n(?=.*\bname\s*:)(?=.*\bdescription\s*:).*?\n---", content, re.S))

    def _name_from_content(self, content: str) -> str:
        match = re.search(r"^name\s*:\s*(.+)$", content, re.M)
        return self._safe_key(match.group(1)) if match else ""

    def _safe_key(self, value: str) -> str:
        key = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip("-").lower()
        if not key:
            raise SkillPathValidationError("Skill key cannot be empty.")
        return key[:96]

    def _ensure_within_root(self, path: Path, *, operation: str) -> None:
        root = self._skills_root.resolve()
        target = path.resolve()
        if root not in target.parents:
            logger.warning(
                "skill_target_rejected",
                extra={"operation": operation, "reason": "managed_root" if root == target else "outside_root"},
            )
            raise SkillPathValidationError("Skill target must be a child of the managed skills root.")

    def _normalize_github_skill_url(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() != "github.com":
            return url
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return url
        owner, repo = parts[0], parts[1]
        if len(parts) >= 5 and parts[2] == "blob":
            branch = parts[3]
            file_path = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
        if len(parts) >= 4 and parts[2] == "tree":
            branch = parts[3]
            return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"

    def _safe_extract(self, archive: zipfile.ZipFile, target: Path) -> None:
        root = target.resolve()
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if root != destination and root not in destination.parents:
                raise SkillManagementValidationError("Refusing to extract archive entry outside target directory.")
        archive.extractall(target)

    def _extract_skill_archive(self, source: Path, target: Path) -> None:
        try:
            with zipfile.ZipFile(source) as archive:
                self._safe_extract(archive, target)
        except zipfile.BadZipFile as exc:
            raise SkillManagementValidationError("Skill package must be a valid ZIP archive.") from exc

    def _decode_skill_content(self, data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillManagementValidationError("Skill content must use UTF-8 encoding.") from exc

    async def _secure_stage_upload(self, *, filename: str, content: bytes) -> dict[str, Any]:
        if self._upload_security_service is None:
            return {}
        return await self._upload_security_service.secure_store_upload(
            content=content,
            filename=filename,
            object_prefix=f"skills/{uuid4()}",
            profile="skill_package",
            source="skill_upload",
            content_type=None,
        )

    def _attach_upload_metadata(self, result: dict[str, Any], upload_result: dict[str, Any]) -> dict[str, Any]:
        if not upload_result:
            return result
        return {
            **result,
            "upload_uri": upload_result.get("uri"),
            "upload_bucket": upload_result.get("bucket"),
            "upload_object_name": upload_result.get("object_name"),
            "security_report": upload_result.get("security_report"),
        }
