"""Prepare release-backed new-Turn values, without publishing or claiming work."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path

from src.runtime.framework_release_archive import require_installed_release
from src.schemas.framework_model_call import FrameworkModelBudget
from src.schemas.framework_release import FrameworkReleaseReference
from src.schemas.framework_transcript import SUMMARY_CONTEXT_RUNTIME_VERSION
from src.schemas.model_config import ModelConfigRecord, ModelInvocationParameters
from src.schemas.runtime_binding import RuntimeBinding


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedFrameworkSubmission:
    binding: RuntimeBinding
    model_budget: FrameworkModelBudget


def prepare_release_submission(
    store: Path,
    reference: FrameworkReleaseReference,
    *,
    binding_id: str,
    session_id: str,
    turn_id: str,
    checkpoint_namespace: str,
    root_model_key: str,
    model_configurations: Mapping[str, ModelConfigRecord],
    max_calls: int,
    max_tokens: int,
    created_at: datetime,
    deadline_at: datetime,
) -> PreparedFrameworkSubmission:
    """Capture models once, then verify this installed source's actual archive.

    Prompt/Skill versions identify the archived source bundle, not a fully
    assembled effective prompt or retrieved content. Those keep their existing
    graph-input asset contracts. This port grants no execution/recovery rights
    and has no database or queue side effects; submit_v2 owns atomic admission.
    Source/dependencies must remain immutable for the process lifetime, as for
    the original release verifier. This is not a security sandbox.
    """
    stage = "models"
    try:
        snapshots = {}
        for key, configuration in model_configurations.items():
            # Reuse the existing typed configuration validator before the sole
            # nonsecret projection. Do not retain a caller's mutable objects.
            captured = ModelConfigRecord.model_validate(configuration.model_dump(mode="json", warnings="error"))
            if key != captured.key or not captured.is_active or "task_execution" not in captured.applications:
                raise ValueError("Submission requires explicit active task model identities")
            snapshots[key] = ModelInvocationParameters.from_configuration(captured)
        root = snapshots[root_model_key]
        if created_at.tzinfo is None or created_at.utcoffset() is None or deadline_at <= created_at:
            raise ValueError("Submission requires an aware creation time before its deadline")
        budget = FrameworkModelBudget(schema_version=2, max_calls=max_calls, max_tokens=max_tokens,
            deadline_at=deadline_at, model_configurations=snapshots,
            model_config_fingerprints={key: snapshot.fingerprint for key, snapshot in snapshots.items()})
        stage = "release"
        reference = FrameworkReleaseReference.model_validate(reference)
        manifest = require_installed_release(Path(__file__).resolve().parents[3], store, reference)
        stage = "binding"
        binding = RuntimeBinding(binding_id=binding_id, session_id=session_id, turn_id=turn_id,
            runtime_kind="deepagents", runtime_version=SUMMARY_CONTEXT_RUNTIME_VERSION, state_schema_version=2,
            thread_id=turn_id, checkpoint_namespace=checkpoint_namespace,
            prompt_version="release:" + reference.archive_sha256, skill_version="release:" + reference.archive_sha256,
            tool_schema_version="sha256:" + manifest.files["release/tools.json"].sha256,
            dependency_lock_sha256=manifest.files["uv.lock"].sha256, release_reference=reference,
            model_provider=root.provider, model_transport=root.transport,
            model_config_fingerprint=root.fingerprint, created_at=created_at)
    except Exception as exc:
        # Validation input, model IDs and paths may contain private data. Keep
        # the original failure type and fixed phase, never its input or text.
        logger.error("framework_submission_preparation_failed", extra={
            "status": stage, "error_type": type(exc).__name__})
        raise ValueError("Framework release submission preparation failed") from None
    logger.info("framework_submission_preparation_verified", extra={
        "framework_release_revision": reference.source_revision,
        "framework_release_sha256": reference.archive_sha256})
    return PreparedFrameworkSubmission(binding=binding, model_budget=budget)
