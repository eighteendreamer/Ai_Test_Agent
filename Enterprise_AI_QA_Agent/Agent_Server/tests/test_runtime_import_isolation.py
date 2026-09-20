"""Fresh-interpreter guards: the lease modules must import as the first project import.

The lease manager reads business metrics, whose package pulled the security layer,
whose package pulled the Docker manager, whose import of the half-loaded lease
module raised ImportError -- so only import order decides the outcome.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module_name",
    ["src.runtime.resource_lease_manager", "src.runtime.docker_resource_manager"],
)
def test_runtime_lease_modules_import_as_the_first_project_import(module_name: str) -> None:
    environment = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))

    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=environment,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, f"{module_name} failed to import standalone:\n{result.stderr}"
