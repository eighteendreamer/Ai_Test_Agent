"""Artifact compatibility for versioned Agent Worker admission.

Application version dispatch is not supplied by LangGraph: the platform must
choose code that understands a persisted Turn before claiming its task (v2
integration plan, sections 3 and 8). Keep this separate from business leases.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from src.runtime.task_deferred import TaskDeferred
from src.schemas.runtime_binding import RuntimeBinding


FRAMEWORK_RUNTIME_VERSION = "v2.0.0"


@dataclass(frozen=True)
class FrameworkWorkerCapabilities:
    dependency_lock_sha256: str
    deepagents_available: bool
    runtime_version: str = FRAMEWORK_RUNTIME_VERSION

    @classmethod
    def from_artifact(cls, *, deepagents_available: bool):
        lockfile = Path(__file__).resolve().parents[3] / "uv.lock"
        return cls(
            dependency_lock_sha256=hashlib.sha256(lockfile.read_bytes()).hexdigest(),
            deepagents_available=deepagents_available,
        )

    def require(self, binding: RuntimeBinding, *, operation: str) -> None:
        """Unavailable compatibility leaves the durable task unclaimed.

        LangGraph controlled v2 workflows and v2 continuation transactions are
        not implemented yet. They must not fall through to the legacy loop.
        """
        if binding.runtime_version != self.runtime_version:
            raise TaskDeferred("framework_runtime_version_unavailable")
        if binding.state_schema_version != 2:
            raise TaskDeferred("framework_state_schema_unavailable")
        if binding.dependency_lock_sha256.lower() != self.dependency_lock_sha256.lower():
            raise TaskDeferred("framework_dependency_artifact_unavailable")
        if binding.runtime_kind != "deepagents":
            raise TaskDeferred("framework_runtime_kind_unavailable")
        if not self.deepagents_available:
            raise TaskDeferred("framework_deepagents_unavailable")
        if operation != "message":
            raise TaskDeferred("framework_operation_unavailable")
