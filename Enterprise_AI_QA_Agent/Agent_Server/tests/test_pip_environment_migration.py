"""Daily pip setup migrates without rewriting historical binding evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import pytest

from tests.test_framework_release_archive import rewrite_release
from tests.test_framework_release_binding import current_source_release


ROOT = Path(__file__).resolve().parents[2]


def test_pip_lock_preserves_all_original_registry_versions_and_hashes():
    original = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {entry["name"]: entry for entry in original["package"] if "registry" in entry["source"]}
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest() in text
    blocks = re.split(r"(?m)(?=^[a-zA-Z0-9][a-zA-Z0-9_.-]*==)", text)[1:]
    names = set()
    for block in blocks:
        name, version = re.match(r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", block).groups()
        assert name not in names
        names.add(name)
        expected = packages[name]
        assert version == expected["version"]
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
        archived = {entry["hash"].removeprefix("sha256:") for entry in expected.get("wheels", [])}
        if expected.get("sdist"):
            archived.add(expected["sdist"]["hash"].removeprefix("sha256:"))
        assert hashes and hashes == archived
    assert names == set(packages)


def test_active_workspace_setup_has_no_uv_resolver_or_launcher():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "uv" not in project.get("tool", {})
    assert "enterprise-ai-qa-agent-server" not in project["project"].get("dependencies", [])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    startup = readme.split("### 1. 启动后端", 1)[1].split("### 1.1", 1)[0]
    assert "pip install --require-hashes -r requirements.lock" in startup
    assert "uv sync" not in startup and "uv run" not in startup
    quality_gates = readme.split("指标专项质量门", 1)[1].split("## 当前进度", 1)[0]
    assert "uv run" not in quality_gates
    assert "cd ../agent_web\nnpm test" in readme


def test_current_source_release_carries_pip_lock_without_changing_legacy_lock(current_source_release):
    import zipfile

    store, reference = current_source_release
    with zipfile.ZipFile(store / (reference.archive_sha256 + ".zip")) as archive:
        assert archive.read("requirements.lock") == (ROOT / "requirements.lock").read_bytes()
        assert archive.read("uv.lock") == (ROOT / "uv.lock").read_bytes()


def test_legacy_inventory_without_pip_lock_remains_readable(tmp_path, current_source_release):
    from src.runtime.framework_release_archive import require_installed_release, restore_release

    # Compatibility fixture, not a new historical release: remove only the
    # optional member and its inventory entry; keep the original lock/probe.
    def without_pip(files):
        del files["requirements.lock"]
        manifest = json.loads(files["release/manifest.json"])
        del manifest["files"]["requirements.lock"]
        files["release/manifest.json"] = json.dumps(manifest).encode()

    store, reference = rewrite_release(tmp_path, current_source_release, without_pip)
    destination = tmp_path / "legacy-inventory-installation"
    restore_release(store, reference, destination)
    assert not (destination / "requirements.lock").exists()
    assert require_installed_release(destination, store, reference).source_revision == reference.source_revision
    (destination / "requirements.lock").write_bytes((ROOT / "requirements.lock").read_bytes())
    with pytest.raises(ValueError, match="source inventory differs"):
        require_installed_release(destination, store, reference)


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_pip_lock_is_part_of_installed_source_verification(tmp_path, current_source_release, change):
    from src.runtime.framework_release_archive import require_installed_release, restore_release

    store, reference = current_source_release
    destination = tmp_path / "pip-installation"
    restore_release(store, reference, destination)
    lock = destination / "requirements.lock"
    if change == "missing":
        lock.unlink()
    else:
        lock.write_bytes(lock.read_bytes() + b"\n# altered\n")
    with pytest.raises(ValueError, match="Installed release source"):
        require_installed_release(destination, store, reference)


@pytest.mark.parametrize("shell_name", ["pwsh", "powershell"])
def test_process_cleanup_removes_only_uv_variables_and_dead_uv_paths(tmp_path, shell_name):
    script = ROOT / "scripts/clear_uv_environment.ps1"
    assert script.is_file()
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"requires {shell_name}")
    live = tmp_path / "Python_UV/bin"
    live.mkdir(parents=True)
    dead = tmp_path / "missing/Python_UV/bin"
    ordinary = tmp_path / "ordinary-missing"
    config = tmp_path / "uv.toml"
    config.write_text("# retained original user configuration\n", encoding="utf-8")
    environment = {**os.environ, "UV_CONFIG_FILE": str(config), "UV_SYNTHETIC_PRIVATE": "private-value",
        "KEEP_TEST": "retained", "TEST_DEAD": str(dead), "TEST_LIVE": str(live), "TEST_ORDINARY": str(ordinary)}
    code = """
$ErrorActionPreference = 'Stop'
$env:Path = "$env:TEST_DEAD;$env:TEST_LIVE;$env:TEST_ORDINARY;$env:Path"
& $args[0] -ProcessOnly | Out-Null
& $args[0] -ProcessOnly | Out-Null
[pscustomobject]@{uv=@([Environment]::GetEnvironmentVariables('Process').Keys | Where-Object {$_ -like 'UV_*'}); keep=$env:KEEP_TEST; paths=@($env:Path -split ';')} | ConvertTo-Json -Compress
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", "& {" + code + "}", str(script)],
        env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    actual = json.loads(result.stdout)
    assert actual["uv"] == [] and actual["keep"] == "retained"
    assert str(dead) not in actual["paths"]
    assert str(live) in actual["paths"] and str(ordinary) in actual["paths"]
    assert config.read_text(encoding="utf-8") == "# retained original user configuration\n"
    assert "private-value" not in result.stdout + result.stderr
