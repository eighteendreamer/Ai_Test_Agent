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
from src.schemas.framework_transcript import CONTEXT_RUNTIME_VERSION


FRAMEWORK_RUNTIME_VERSION = "v2.0.0"


@dataclass(frozen=True)
class FrameworkWorkerCapabilities:
    dependency_lock_sha256: str
    deepagents_available: bool
    runtime_version: str = FRAMEWORK_RUNTIME_VERSION
    message_stage_max_version: int = 27
    context_transcript_enabled: bool = True

    @classmethod
    def from_artifact(cls, *, deepagents_available: bool):
        lockfile = Path(__file__).resolve().parents[3] / "uv.lock"
        return cls(
            dependency_lock_sha256=hashlib.sha256(lockfile.read_bytes()).hexdigest(),
            deepagents_available=deepagents_available,
        )

    def require(self, binding: RuntimeBinding, *, operation: str) -> None:
        """Unavailable compatibility leaves the durable task unclaimed.

        Deep Agents supports initial messages, owner-bound native approval
        continuations, deterministic reconciliation and proven successors.
        Other graph workflows and resume operations
        require their own execution contracts before they can be admitted.
        """
        versions = {self.runtime_version}
        if self.context_transcript_enabled and self.runtime_version == FRAMEWORK_RUNTIME_VERSION:
            versions.add(CONTEXT_RUNTIME_VERSION)
        if (binding.runtime_version not in versions
            or binding.runtime_version == CONTEXT_RUNTIME_VERSION and not self.context_transcript_enabled):
            raise TaskDeferred("framework_runtime_version_unavailable")
        if binding.state_schema_version != 2:
            raise TaskDeferred("framework_state_schema_unavailable")
        if binding.dependency_lock_sha256.lower() != self.dependency_lock_sha256.lower():
            raise TaskDeferred("framework_dependency_artifact_unavailable")
        if binding.runtime_kind != "deepagents":
            raise TaskDeferred("framework_runtime_kind_unavailable")
        if not self.deepagents_available:
            raise TaskDeferred("framework_deepagents_unavailable")
        if operation not in {"message", "approval_resume", "framework_reconcile", "approval_recover", "message_recover"}:
            raise TaskDeferred("framework_operation_unavailable")

    def require_message_stage(self, schema_version: int | None) -> None:
        if schema_version is not None and (type(schema_version) is not int
            or not 1 <= schema_version <= self.message_stage_max_version):
            raise TaskDeferred("framework_message_stage_unavailable")
