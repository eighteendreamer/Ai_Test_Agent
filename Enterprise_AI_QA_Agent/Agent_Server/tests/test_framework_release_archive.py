from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    from src.runtime.framework_release_archive import archive_release

    store = tmp_path_factory.mktemp("release-store")
    return store, archive_release(ROOT, store)


def test_release_contains_readable_source_not_just_a_fingerprint(release):
    from src.runtime.framework_release_archive import verify_release

    store, reference = release
    manifest = verify_release(store, reference)
    path = store / (reference.archive_sha256 + ".zip")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference.archive_sha256
    with zipfile.ZipFile(path) as archive:
        assert b"class FrameworkWorkerCapabilities" in archive.read("Agent_Server/src/runtime/framework_worker_capabilities.py")
        assert archive.read("Agent_Server/src/SKILLS/playwright-cli/SKILL.md")
        assert archive.read("Agent_Server/src/application/prompting/prompt_assembly_service.py")
        assert archive.read("Agent_Server/src/registry/tools.py")
        assert archive.read("uv.lock")
        assert not any(".env" in name or "/data/" in name or "__pycache__" in name for name in archive.namelist())
        versions = json.loads(archive.read("release/formats.json"))["FrameworkMessageStage"]["properties"]["schema_version"]["enum"]
    assert manifest.source_revision == reference.source_revision
    assert manifest.environment["dependencies"]["deepagents"] == "0.7.13"
    assert manifest.environment["worker_capabilities"]["message_stage_max_version"] == max(versions)


def test_restored_release_resolves_original_files_without_the_checkout(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release, verify_release

    store, reference = release
    destination = tmp_path / "restored"
    restore_release(store, reference, destination)
    manifest = verify_release(store, reference)
    for name, evidence in manifest.files.items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == evidence.sha256
    assert (destination / "Agent_Server/src/SKILLS/playwright-cli/SKILL.md").is_file()
    probe = """
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'Agent_Server'))
from src.runtime import framework_worker_capabilities as module
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
caps = module.FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
print(json.dumps({'inside': pathlib.Path(module.__file__).resolve().is_relative_to(root),
    'stage': caps.message_stage_max_version, 'lock': caps.dependency_lock_sha256,
    'skills': len(SkillRegistry().list()), 'tools': len(ToolRegistry().list())}))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(destination)], cwd=destination,
        capture_output=True, check=True, timeout=30)
    observed = json.loads(result.stdout)
    assert observed["inside"] and observed["stage"] == manifest.environment["worker_capabilities"]["message_stage_max_version"]
    assert observed["skills"] > 20 and observed["tools"] > 20
    assert observed["lock"] == manifest.environment["worker_capabilities"]["dependency_lock_sha256"]


def rewrite_release(tmp_path, release, change, *, modes=None):
    from src.runtime.framework_release_archive import FrameworkReleaseReference

    original_store, reference = release
    with zipfile.ZipFile(original_store / (reference.archive_sha256 + ".zip")) as archive:
        files = {item.filename: archive.read(item) for item in archive.infolist()}
    change(files)
    store = tmp_path / "changed-store"
    store.mkdir()
    path = store / "changed.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            # ZipInfo normalizes backslashes/NUL on construction. Preserve
            # the adversarial central-directory name instead of accidentally
            # testing a different, already-sanitized archive.
            entry = zipfile.ZipInfo()
            entry.filename = name
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = ((modes or {}).get(name, stat.S_IFREG) | 0o644) << 16
            archive.writestr(entry, content)
    raw = path.read_bytes()
    updated = FrameworkReleaseReference(archive_sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
        source_revision=reference.source_revision)
    path.rename(store / (updated.archive_sha256 + ".zip"))
    return store, updated


@pytest.mark.parametrize("change", ["file", "missing", "extra", "environment", "revision", "future_manifest"])
def test_changed_release_is_rejected_before_creating_a_restore_directory(tmp_path, release, change):
    from src.runtime.framework_release_archive import restore_release

    def alter(files):
        if change == "file":
            files["Agent_Server/src/runtime/framework_worker_capabilities.py"] += b"\n# altered\n"
        elif change == "missing":
            del files["Agent_Server/src/registry/tools.py"]
        elif change == "extra":
            files["Agent_Server/src/unlisted.py"] = b"# not in manifest"
        else:
            value = json.loads(files["release/manifest.json"])
            if change == "environment":
                value["environment"]["python_version"] = "different"
            elif change == "revision":
                value["source_revision"] = "0" * 40
            else:
                value["schema_version"] = 99
            files["release/manifest.json"] = json.dumps(value).encode()

    store, reference = rewrite_release(tmp_path, release, alter)
    destination = tmp_path / "not-created"
    with pytest.raises(ValueError):
        restore_release(store, reference, destination)
    assert not destination.exists()


@pytest.mark.parametrize("name", ["../escaped.py", "/escaped.py", "Agent_Server/src/../escaped.py",
    "Agent_Server/src/a\\b.py", "Agent_Server/src/a:stream.py", "Agent_Server/src/CON.py",
    "Agent_Server/src/space ./bad.py", "Agent_Server/src/data/private.py", "Agent_Server/.env",
    "Agent_Server/src/null.py\x00suffix"])
def test_archive_paths_cannot_escape_or_restore_private_runtime_data(tmp_path, release, name):
    from src.runtime.framework_release_archive import restore_release

    store, reference = rewrite_release(tmp_path, release, lambda files: files.update({name: b"synthetic-private-data"}))
    with pytest.raises(ValueError, match="non-release path"):
        restore_release(store, reference, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists() and not (tmp_path / "escaped.py").exists()


def test_archive_rejects_symbolic_links_before_restoring(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    name = "Agent_Server/src/runtime/framework_worker_capabilities.py"
    store, reference = rewrite_release(tmp_path, release, lambda files: None, modes={name: stat.S_IFLNK})
    with pytest.raises(ValueError, match="non-regular file"):
        restore_release(store, reference, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_archive_rejects_case_collisions_on_every_platform(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    store, reference = rewrite_release(tmp_path, release, lambda files: files.update({
        "Agent_Server/src/runtime/FRAMEWORK_WORKER_CAPABILITIES.py": b"# case collision"}))
    with pytest.raises(ValueError, match="invalid inventory"):
        restore_release(store, reference, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("limit", ["MAX_FILES", "MAX_CONTENT_BYTES"])
def test_archive_limits_are_checked_before_extracting(tmp_path, release, monkeypatch, limit):
    from src.runtime import framework_release_archive as module

    store, reference = release
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(ValueError, match="content limit"):
        module.restore_release(store, reference, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("version", [True, "1", 1.0, None, 2])
def test_reference_version_is_not_coerced(tmp_path, release, version):
    from src.runtime.framework_release_archive import restore_release

    store, reference = release
    value = reference.model_dump(mode="json")
    value["schema_version"] = version
    with pytest.raises(ValueError, match="reference version"):
        restore_release(store, value, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_digest_is_checked_against_the_original_reference_before_zip_read(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    original_store, reference = release
    store = tmp_path / "corrupted-store"
    store.mkdir()
    raw = bytearray((original_store / (reference.archive_sha256 + ".zip")).read_bytes())
    raw[-1] ^= 1
    (store / (reference.archive_sha256 + ".zip")).write_bytes(raw)
    with pytest.raises(ValueError, match="digest differs"):
        restore_release(store, reference, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_restore_does_not_overwrite_an_existing_directory(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    store, reference = release
    destination = tmp_path / "existing"
    destination.mkdir()
    original = destination / "original.txt"
    original.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_release(store, reference, destination)
    assert original.read_text() == "keep" and list(destination.iterdir()) == [original]


def test_repeated_publication_verifies_and_reuses_the_same_content_address(release):
    from src.runtime.framework_release_archive import archive_release

    store, reference = release
    path = store / (reference.archive_sha256 + ".zip")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert archive_release(ROOT, store, revision=reference.source_revision) == reference
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not list(store.glob(".release-*.tmp"))


def test_archive_excludes_tracked_credentials_mutable_data_and_uncommitted_edits(tmp_path, monkeypatch):
    from src.runtime import framework_release_archive as module

    project = tmp_path / "project"
    project.mkdir()
    files = {"uv.lock": b"locked", "pyproject.toml": b"[project]", "Agent_Server/pyproject.toml": b"[project]",
        "Agent_Server/src/runtime/framework_worker_capabilities.py": b"# original committed source",
        "Agent_Server/.env": b"synthetic-private-environment", "Agent_Server/src/data/private.py": b"private-state",
        "Agent_Server/src/SKILLS/demo/private.key": b"synthetic-private-key"}
    for name, value in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    for args in (("init",), ("add", "."), ("commit", "-m", "synthetic release fixture")):
        subprocess.run(["git", *args], cwd=project, capture_output=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Release Test", "GIT_COMMITTER_NAME": "Release Test",
                "GIT_AUTHOR_EMAIL": "release@example.invalid", "GIT_COMMITTER_EMAIL": "release@example.invalid"})
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=project, capture_output=True, check=True).stdout
    assert {name.encode() for name in files} <= set(tracked.split(b"\0"))
    source = project / "Agent_Server/src/runtime/framework_worker_capabilities.py"
    source.write_bytes(b"# uncommitted private modification")
    observed = {"environment": {"worker_capabilities": {"dependency_lock_sha256": hashlib.sha256(b"locked").hexdigest()}},
        "tools": {}, "formats": {}}

    def inspect(temporary):
        assert not (temporary / "Agent_Server/.env").exists()
        assert not (temporary / "Agent_Server/src/data").exists()
        assert not (temporary / "Agent_Server/src/SKILLS/demo/private.key").exists()
        assert (temporary / "Agent_Server/src/runtime/framework_worker_capabilities.py").read_bytes() == files[
            "Agent_Server/src/runtime/framework_worker_capabilities.py"]
        return observed

    monkeypatch.setattr(module, "_probe", inspect)
    store = tmp_path / "store"
    reference = module.archive_release(project, store)
    assert module.verify_release(store, reference).environment == observed["environment"]
    assert source.read_bytes() == b"# uncommitted private modification"


def test_cli_verifies_and_restores_without_starting_the_application(tmp_path, release):
    store, reference = release
    path = tmp_path / "reference.json"
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    for action in ("verify", "restore"):
        arguments = [sys.executable, "-m", "src.cli.framework_release_archive", action,
            "--store", str(store), "--reference", str(path)]
        if action == "restore":
            arguments += ["--destination", str(tmp_path / "restored-by-cli")]
        result = subprocess.run(arguments, cwd=ROOT / "Agent_Server", capture_output=True, check=True, timeout=30)
        observed = json.loads(result.stdout)
        assert observed["source_revision"] == reference.source_revision and observed["file_count"] > 900
        assert observed["archive_sha256"] == reference.archive_sha256


def test_cli_invalid_reference_is_visible_without_private_validation_input(tmp_path, release):
    store, reference = release
    path = tmp_path / "invalid.json"
    value = reference.model_dump(mode="json")
    value["archive_sha256"] = "private-reference-input"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "src.cli.framework_release_archive", "verify",
        "--store", str(store), "--reference", str(path)], cwd=ROOT / "Agent_Server", capture_output=True, timeout=30)
    assert result.returncode == 1 and result.stdout == b""
    assert b"operation=verify error_type=ValidationError" in result.stderr
    assert b"private-reference-input" not in result.stderr and b"Traceback" not in result.stderr


def replay_export(monkeypatch, release):
    from src.runtime import framework_release_archive as module

    store, reference = release
    manifest = module.verify_release(store, reference)
    with zipfile.ZipFile(store / (reference.archive_sha256 + ".zip")) as archive:
        source = {name: archive.read(name) for name in manifest.files if not name.startswith("release/")}
        observed = {name: json.loads(archive.read(f"release/{name}.json")) for name in ("environment", "formats", "tools")}
    monkeypatch.setattr(module, "_committed_files", lambda *args: (manifest.source_revision, manifest.repository_path, source))
    monkeypatch.setattr(module, "_probe", lambda *args: observed)
    return module, reference


def test_corrupt_existing_content_address_is_not_overwritten(tmp_path, release, monkeypatch):
    module, reference = replay_export(monkeypatch, release)
    store = tmp_path / "corrupt-existing"
    store.mkdir()
    path = store / (reference.archive_sha256 + ".zip")
    original = b"x" * reference.size_bytes
    path.write_bytes(original)
    with pytest.raises(ValueError, match="digest differs"):
        module.archive_release(ROOT, store)
    assert path.read_bytes() == original and not list(store.glob(".release-*.tmp"))


def test_failed_atomic_publication_does_not_leave_a_partial_release(tmp_path, release, monkeypatch):
    module, reference = replay_export(monkeypatch, release)
    store = tmp_path / "failed-publication"

    def denied(*args):
        raise PermissionError("synthetic artifact-store publication denial")

    monkeypatch.setattr(module.os, "link", denied)
    with pytest.raises(PermissionError, match="publication denial"):
        module.archive_release(ROOT, store)
    assert not list(store.iterdir())


def test_invalid_git_revision_does_not_create_an_artifact_store(tmp_path):
    from src.runtime.framework_release_archive import archive_release

    store = tmp_path / "not-created"
    with pytest.raises(ValueError, match="local Git revision"):
        archive_release(ROOT, store, revision="--remote=untrusted")
    assert not store.exists()


def test_cli_verifies_an_installed_release_offline_without_settings_or_service_startup(tmp_path, release):
    from src.runtime.framework_release_archive import restore_release

    store, reference = release
    destination = tmp_path / "installed-cli-source"
    restore_release(store, reference, destination)
    path = tmp_path / "reference.json"
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    arguments = [sys.executable, "-B", "-m", "src.cli.framework_release_archive", "verify-installed",
        "--project", str(destination), "--store", str(store), "--reference", str(path)]
    result = subprocess.run(arguments, cwd=ROOT / "Agent_Server", capture_output=True, timeout=120)
    assert result.returncode == 0, (result.stdout, result.stderr)
    observed = json.loads(result.stdout)
    assert observed["source_revision"] == reference.source_revision
    assert observed["archive_sha256"] == reference.archive_sha256 and observed["file_count"] > 900
    assert observed["installed_source_verified"] is True
    source = destination / "Agent_Server/src/runtime/framework_worker_capabilities.py"
    source.write_bytes(source.read_bytes() + b"\n# drift\n")
    changed = subprocess.run(arguments, cwd=ROOT / "Agent_Server", capture_output=True, timeout=120)
    assert changed.returncode == 1 and changed.stdout == b""
    assert b"operation=verify-installed error_type=ValueError" in changed.stderr
    assert b"Traceback" not in changed.stderr
