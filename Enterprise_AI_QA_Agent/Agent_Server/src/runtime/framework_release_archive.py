"""Content-addressed source releases, separate from Turn execution authority.

No upstream counterpart (checked: legacy-runtime.json, orchestration_artifact,
Codex publish_r2_release.py, v4 section 14.1): application fingerprints cannot
restore code. Archive committed public source and probe that source offline;
never bundle the working tree, credentials, mutable data or a Python venv.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.framework_release import FrameworkReleaseReference, GIT_REVISION_PATTERN, MAX_ARCHIVE_BYTES


logger = logging.getLogger(__name__)
MANIFEST = "release/manifest.json"
GENERATED = {"release/environment.json", "release/formats.json", "release/tools.json"}
METADATA = {"uv.lock", "pyproject.toml", "Agent_Server/pyproject.toml"}
MAX_CONTENT_BYTES = 256 * 1024 * 1024
MAX_FILES = 20000


class ReleaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, strict=True)


class FrameworkReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    artifact_kind: Literal["framework-source-release"] = "framework-source-release"
    source_revision: str = Field(pattern=GIT_REVISION_PATTERN)
    repository_path: str
    files: dict[str, ReleaseFile] = Field(min_length=1, max_length=MAX_FILES)
    environment: dict[str, Any]


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and path.as_posix() == name and "\\" not in name
        and all(part not in {".", ".."} and not part.endswith((".", " ")) and ":" not in part
            and not any(ord(char) < 32 for char in part)
            and part.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))} for part in path.parts))


def _source_name(name: str) -> bool:
    if not _safe_name(name):
        return False
    path = PurePosixPath(name)
    if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
        return False
    if name in METADATA:
        return True
    root = "Agent_Server/src/"
    if not name.startswith(root) or name.startswith(root + "data/"):
        return False
    return (path.suffix == ".py" or name.startswith(root + "SKILLS/") and path.suffix in {".md", ".yaml", ".yml"}
        or name.startswith(root + "templates/") and path.suffix == ".html"
        or name == root + "application/recorder/assets/recorder.js")


def _git(root: Path, *args) -> bytes:
    # Preserve both captured streams on CalledProcessError; archive bytes are
    # parsed, never printed as diagnostics or mixed with a JSON response.
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=120)
    if result.stderr:
        logger.info("framework_release_git_diagnostic", extra={"operation": args[0], "stderr_bytes": len(result.stderr)})
    return result.stdout


def _committed_files(project: Path, revision: str) -> tuple[str, str, dict[str, bytes]]:
    if not re.fullmatch(r"(?!-)[A-Za-z0-9_./~^+\-]+", revision):
        raise ValueError("Release revision must be a local Git revision")
    repository = Path(_git(project, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    prefix = project.resolve().relative_to(repository).as_posix()
    commit = _git(repository, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    tree = commit + (":" + prefix if prefix != "." else "")
    entries = _git(repository, "ls-tree", "-r", "-z", tree).split(b"\0")
    selected = []
    for entry in filter(None, entries):
        header, name = entry.split(b"\t", 1)
        mode, kind, _ = header.split()
        name = name.decode("utf-8")
        if not _source_name(name):
            continue
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError("Release source must contain regular Git blobs only")
        selected.append(name)
    if not METADATA <= set(selected) or "Agent_Server/src/runtime/framework_worker_capabilities.py" not in selected:
        raise ValueError("Release revision lacks required framework source or workspace metadata")
    files = {}
    # Bounded path batches stay below Windows command-line limits and read
    # only the selected blobs, never .env/private data even if tracked later.
    for offset in range(0, len(selected), 50):
        raw = _git(repository, "archive", "--format=zip", tree, "--", *selected[offset:offset + 50])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for item in archive.infolist():
                if not item.is_dir():
                    files[item.filename] = archive.read(item)
    if set(files) != set(selected):
        raise ValueError("Git archive omitted selected release source")
    return commit, prefix, files


PROBE = r'''
import dataclasses, importlib.metadata, importlib.util, json, pathlib, platform, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "Agent_Server"))
from src.runtime import framework_worker_capabilities as module
if not pathlib.Path(module.__file__).resolve().is_relative_to(root):
    raise RuntimeError("Release probe imported code outside its archive")
from src.registry.tools import ToolRegistry
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_message_stage import FrameworkMessageStage, FrameworkApprovalFinalizationProof, FrameworkApprovalSettlementProof
from src.schemas.framework_model_call import FrameworkModelBudget, FrameworkModelCallRecord, FrameworkModelEvidenceUpload
from src.schemas.framework_approval import FrameworkInterruptLink, FrameworkDecisionReceipt
from src.schemas.framework_transcript import FrameworkTranscriptAsset, FrameworkTranscriptAssetV2, FrameworkTranscriptAssetV3
models = (AgentTaskCommandV2, RuntimeBinding, FrameworkMessageStage, FrameworkModelBudget, FrameworkModelCallRecord,
    FrameworkModelEvidenceUpload, FrameworkInterruptLink, FrameworkDecisionReceipt, FrameworkApprovalFinalizationProof,
    FrameworkApprovalSettlementProof, FrameworkTranscriptAsset, FrameworkTranscriptAssetV2, FrameworkTranscriptAssetV3)
dependencies = {}
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name")
    if name:
        name = name.lower().replace("_", "-").replace(".", "-")
        if name in dependencies and dependencies[name] != distribution.version:
            raise RuntimeError("Conflicting installed dependency versions")
        dependencies[name] = distribution.version
environment = {"python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
    "platform": sys.platform, "machine": platform.machine(), "dependencies": dependencies,
    "worker_capabilities": dataclasses.asdict(module.FrameworkWorkerCapabilities.from_artifact(
        deepagents_available=importlib.util.find_spec("deepagents") is not None))}
formats = {model.__name__: model.model_json_schema() for model in models}
from src.runtime import framework_message_stages
# Old source releases have no catalogue export. Preserve their exact inventory,
# rather than inventing definitions or rewriting an immutable historical object.
if hasattr(framework_message_stages, "message_stage_material_schemas"):
    formats["message_stage_materials"] = framework_message_stages.message_stage_material_schemas()
print(json.dumps({"environment": environment, "formats": formats,
    "tools": {tool.key: tool.model_dump(mode="json") for tool in ToolRegistry().list()}}, ensure_ascii=True, sort_keys=True))
'''


def _probe(source: Path) -> dict:
    environment = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    result = subprocess.run([sys.executable, "-I", "-B", "-c", PROBE, str(source)], cwd=source,
        env=environment, capture_output=True, check=True, timeout=120)
    if result.stderr:
        logger.info("framework_release_probe_diagnostic", extra={"stderr_bytes": len(result.stderr)})
    return json.loads(result.stdout)


def _write_sources(destination: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(content)


def archive_release(project: Path, store: Path, *, revision: str = "HEAD") -> FrameworkReleaseReference:
    """Export a trusted local committed revision; probing executes its imports.

    Uses the calling interpreter's installed dependencies, not a new install.
    Source-only restoration is not a wheel, dependency cache or deployment.
    """
    commit, prefix, files = _committed_files(Path(project), revision)
    with tempfile.TemporaryDirectory(prefix="framework-release-probe-") as temporary:
        source = Path(temporary)
        _write_sources(source, files)
        observed = _probe(source)
    files.update({f"release/{name}.json": _json(observed[name]) for name in ("environment", "formats", "tools")})
    manifest = FrameworkReleaseManifest(source_revision=commit, repository_path=prefix,
        files={name: ReleaseFile(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
            for name, content in files.items()}, environment=observed["environment"])
    files[MANIFEST] = _json(manifest.model_dump(mode="json"))
    root = Path(store).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".release-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in sorted(files.items()):
                    entry = zipfile.ZipInfo(name)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(entry, content)
        content = temporary.read_bytes()
        reference = FrameworkReleaseReference(archive_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content), source_revision=commit)
        # Hard-link publication is atomic and cannot replace an existing
        # content-addressed release. No mutable "latest" pointer is advanced.
        try:
            os.link(temporary, root / (reference.archive_sha256 + ".zip"))
        except FileExistsError:
            pass  # Verified below; never silently accept a corrupt old object.
        verify_release(root, reference)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    logger.info("framework_release_archived", extra={"source_revision": commit,
        "archive_sha256": reference.archive_sha256, "file_count": len(manifest.files), "size_bytes": reference.size_bytes})
    return reference


def _verified_files(store: Path, reference) -> tuple[FrameworkReleaseManifest, dict[str, bytes]]:
    if isinstance(reference, FrameworkReleaseReference):
        reference = reference.model_dump(mode="json")
    if (not isinstance(reference, dict) or type(reference.get("schema_version", 1)) is not int
        or reference.get("schema_version", 1) != 1):
        raise ValueError("Unsupported release reference version")
    reference = FrameworkReleaseReference.model_validate(reference)
    root = Path(store).resolve()
    path = root / (reference.archive_sha256 + ".zip")
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("Release object must remain inside its artifact store")
    if path.stat().st_size != reference.size_bytes:
        raise ValueError("Release object size differs from its reference")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference.archive_sha256:
        raise ValueError("Release archive digest differs from its reference")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (len(entries) > MAX_FILES + 1 or len(names) != len({name.casefold() for name in names}) or MANIFEST not in names
            or sum(item.file_size for item in entries) > MAX_CONTENT_BYTES):
            raise ValueError("Release archive has invalid inventory or exceeds its content limit")
        for item in entries:
            if (item.orig_filename != item.filename or not _safe_name(item.orig_filename)
                or not (_source_name(item.filename) or item.filename in GENERATED | {MANIFEST})
                or item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}):
                raise ValueError("Release archive contains a non-release path or non-regular file")
        raw_manifest = json.loads(archive.read(MANIFEST))
        if (not isinstance(raw_manifest, dict) or type(raw_manifest.get("schema_version", 1)) is not int
            or raw_manifest.get("schema_version", 1) != 1):
            raise ValueError("Unsupported release manifest version")
        manifest = FrameworkReleaseManifest.model_validate(raw_manifest)
        if (manifest.source_revision != reference.source_revision or set(names) != set(manifest.files) | {MANIFEST}
            or not METADATA | GENERATED <= set(manifest.files)):
            raise ValueError("Release manifest identity or inventory differs from its reference")
        files = {}
        for name, evidence in manifest.files.items():
            content = archive.read(name)
            if len(content) != evidence.size_bytes or hashlib.sha256(content).hexdigest() != evidence.sha256:
                raise ValueError("Release file differs from its manifest: " + name)
            files[name] = content
        if (json.loads(files["release/environment.json"]) != manifest.environment
            or manifest.environment.get("worker_capabilities", {}).get("dependency_lock_sha256")
                != hashlib.sha256(files["uv.lock"]).hexdigest()):
            raise ValueError("Release environment differs from its archived lock or manifest")
    return manifest, files


def verify_release(store: Path, reference) -> FrameworkReleaseManifest:
    """Verify bytes and inventory only; never import code from an input archive."""
    manifest, _ = _verified_files(store, reference)
    return manifest


def require_installed_release(project: Path, store: Path, reference) -> FrameworkReleaseManifest:
    """Check trusted local installed source and the active interpreter offline.

    No upstream counterpart (checked: orchestration_artifact, release verifier,
    v4 section 14.1): a matching lock/runtime label cannot identify deployed
    application code. Compare the entire release inventory, then its actual
    environment and definitions. This probe imports trusted local source.
    Deployments must keep source/dependencies immutable for the process lifetime.
    """
    manifest, files = _verified_files(store, reference)
    project = Path(project).resolve()
    source_names = set(files) - GENERATED
    installed = {name for name in METADATA if (project / name).is_file()}
    for path in (project / "Agent_Server/src").rglob("*"):
        name = path.relative_to(project).as_posix()
        if _source_name(name) and path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(project):
                raise ValueError("Installed release contains an external source path")
            installed.add(name)
    if installed != source_names:
        raise ValueError("Installed release source inventory differs from its archive")
    for name in sorted(source_names):
        path = project / name
        if path.is_symlink() or not path.resolve().is_relative_to(project):
            raise ValueError("Installed release contains an external source path")
        # Match the existing orchestration fingerprint's UTF-8/newline policy.
        expected = files[name].decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if path.read_text(encoding="utf-8-sig") != expected:
            raise ValueError("Installed release source differs from its archive: " + name)
    observed = _probe(project)
    if (observed["environment"] != manifest.environment
        or _json(observed["formats"]) != files["release/formats.json"]
        or _json(observed["tools"]) != files["release/tools.json"]):
        raise ValueError("Installed release environment or definitions differ from its archive")
    logger.info("framework_release_installation_verified", extra={"source_revision": manifest.source_revision,
        "file_count": len(files)})
    return manifest


def restore_release(store: Path, reference, destination: Path) -> FrameworkReleaseManifest:
    """Restore into a new directory only, after verifying every archive member."""
    manifest, files = _verified_files(store, reference)
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    _write_sources(destination, files)
    logger.info("framework_release_restored", extra={"source_revision": manifest.source_revision, "file_count": len(files)})
    return manifest
