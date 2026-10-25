"""The v2 environment must resolve the reviewed direct dependency contract."""
from pathlib import Path
import hashlib
import json
import subprocess
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "deepagents": "0.7.13", "langchain": "1.4.6", "langchain-core": "1.6.10",
    "langgraph": "1.2.12", "langchain-openai": "1.6.2", "langchain-anthropic": "1.7.1",
    "langchain-google-genai": "4.4.0", "langgraph-checkpoint-postgres": "3.1.2",
    "psycopg-pool": "3.3.1",
}


@pytest.mark.parametrize("name,version", PINS.items())
def test_reviewed_framework_version_is_declared_and_locked(name, version):
    project = tomllib.loads((ROOT / "Agent_Server/pyproject.toml").read_text(encoding="utf-8"))
    assert f"{name}=={version}" in project["project"]["dependencies"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert {package["version"] for package in lock["package"] if package["name"] == name} == {version}


def test_legacy_code_and_dependency_lock_are_reproducible_from_frozen_revision():
    baseline = json.loads((ROOT / "deployment/legacy-runtime.json").read_text(encoding="utf-8"))
    revision = baseline["source_revision"]
    assert len(revision) == 40
    subprocess.run(["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=ROOT,
        check=True, capture_output=True)
    for path, expected_hash in baseline["file_sha256"].items():
        original = subprocess.run(["git", "show", f"{revision}:{baseline['repository_path']}/{path}"],
            cwd=ROOT, check=True, capture_output=True).stdout
        assert hashlib.sha256(original).hexdigest() == expected_hash
