"""Identify the shipped code that interprets a prepared pre-graph input.

No upstream counterpart (checked: FrameworkWorkerCapabilities, prepared
semantic input, LangGraph persistence, v3 section 5.1): graph checkpoints do
not version application code executed before graph admission. Deploy these
sources as immutable files; this fingerprint is not a code archive or support
for hot patching already imported Python modules.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
import platform
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# The deterministic application boundary; model transports have their own
# frozen runtime parameters. Mode descriptor values are frozen separately.
ORCHESTRATION_SOURCES = (
    "application/orchestration/input_orchestrator_service.py",
    "application/models/framework_model_call_policy.py",
    "application/intent/intent_recognition_service.py",
    "application/intent/semantic_intent_service.py",
    "application/intent/mode_selection_policy.py",
    "application/intent/safety_intent_service.py",
    "application/intent/url_text.py",
    "application/security/authorization.py",
    "application/security/prompt_injection_policy.py",
    "application/testing/direction_service.py",
    "application/testing/mode_intent_service.py",
    "application/testing/router_service.py",
    "core/request_context.py",
    "domain/models.py",
    "registry/modes.py",
    "runtime/execution_context.py",
    "schemas/approval_progress.py",
    "schemas/framework_model_call.py",
    "schemas/framework_release.py",
    "schemas/framework_message_stage.py",
    "schemas/intent.py",
    "schemas/mode.py",
    "schemas/model_config.py",
    "schemas/model_request_options.py",
    "schemas/prompting.py",
    "schemas/runtime_binding.py",
    "schemas/semantic_input.py",
    "schemas/session.py",
    "schemas/tool_runtime.py",
    "schemas/tool_job.py",
    "runtime/framework_orchestration_artifact.py",
)


class OrchestrationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    sources: dict[str, str] = Field(min_length=1)
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    python_version: str = Field(min_length=1)
    dependencies: dict[str, str] = Field(min_length=1)


def orchestration_artifact() -> OrchestrationArtifact:
    source_root = Path(__file__).resolve().parents[1]
    # Normalize checkout encoding/line endings, preserving the Python source.
    sources = {name: hashlib.sha256((source_root / name).read_text(encoding="utf-8-sig")
        .encode("utf-8")).hexdigest() for name in ORCHESTRATION_SOURCES}
    lock = source_root.parents[1] / "uv.lock"
    return OrchestrationArtifact(sources=sources,
        dependency_lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
        python_version=platform.python_version(), dependencies={name: importlib.metadata.version(name)
            for name in ("pydantic", "pydantic-core")})


def require_orchestration_artifact(expected) -> OrchestrationArtifact:
    original = OrchestrationArtifact.model_validate(expected)
    if original != orchestration_artifact():
        raise ValueError("Frozen orchestration artifact differs from the available application code")
    return original
