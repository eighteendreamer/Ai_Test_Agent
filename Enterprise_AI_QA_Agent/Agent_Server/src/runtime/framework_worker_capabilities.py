"""Artifact compatibility for versioned Agent Worker admission.

Application version dispatch is not supplied by LangGraph: the platform must
choose code that understands a persisted Turn before claiming its task (v2
integration plan, sections 3 and 8). Keep this separate from business leases.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2, parse_agent_task_command
from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelCallRecord, FrameworkModelEvidenceUpload,
)
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_release import FrameworkReleaseReference
from src.schemas.framework_message_stage import FrameworkMessageStage
from src.schemas.framework_transcript import (
    CONTEXT_RUNTIME_VERSION, TURN_CONTEXT_RUNTIME_VERSION, SUMMARY_CONTEXT_RUNTIME_VERSION, TURN_CONTEXT_RUNTIME_VERSIONS,
)


FRAMEWORK_RUNTIME_VERSION = "v2.0.0"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkWorkerCapabilities:
    dependency_lock_sha256: str
    deepagents_available: bool
    runtime_version: str = FRAMEWORK_RUNTIME_VERSION
    message_stage_max_version: int = 29
    context_transcript_enabled: bool = True
    turn_context_assembly_enabled: bool = True
    native_summary_coverage_enabled: bool = True
    model_call_max_version: int = 3
    release_reference: FrameworkReleaseReference | None = None
    model_budget_max_version: int = 2
    controlled_case_available: bool = False
    controlled_case_input_max_version: int = 4
    controlled_case_checkpoint_max_version: int = 6
    controlled_case_settlement_admission_available: bool = False
    controlled_case_settlement_max_version: int = 1
    controlled_case_settlement_available: bool = False
    controlled_case_recovery_receipt_max_version: int = 1
    controlled_case_approval_wait_max_version: int = 1
    controlled_case_approval_admission_max_version: int = 1

    @classmethod
    def from_artifact(cls, *, deepagents_available: bool):
        lockfile = Path(__file__).resolve().parents[3] / "uv.lock"
        return cls(
            dependency_lock_sha256=hashlib.sha256(lockfile.read_bytes()).hexdigest(),
            deepagents_available=deepagents_available,
        )

    @classmethod
    def from_release(cls, store: Path, reference, *, deepagents_available: bool):
        from src.runtime.framework_release_archive import require_installed_release

        reference = FrameworkReleaseReference.model_validate(reference)
        require_installed_release(Path(__file__).resolve().parents[3], store, reference)
        return replace(cls.from_artifact(deepagents_available=deepagents_available), release_reference=reference)

    @classmethod
    def from_configuration(cls, config):
        """Optional application preflight; never infer a release or enable writers."""
        try:
            if (config.release_store is None) != (config.release_reference is None):
                raise ValueError("Release store and reference must be configured together")
            if config.release_reference is None:
                LOGGER.info("framework_release_preflight_unconfigured")
                return None
            reference = json.loads(config.release_reference.read_text(encoding="utf-8-sig"))
            capabilities = cls.from_release(config.release_store, reference,
                deepagents_available=config.enabled and config.checkpoint_enabled)
        except Exception as exc:
            # Paths and reference validation input can contain private data.
            LOGGER.error("framework_release_preflight_failed", extra={"error_type": type(exc).__name__})
            raise ValueError("Configured framework release preflight failed") from None
        LOGGER.info("framework_release_preflight_verified", extra={
            "framework_release_revision": capabilities.release_reference.source_revision,
            "framework_release_sha256": capabilities.release_reference.archive_sha256})
        return capabilities

    def require_release(self, binding: RuntimeBinding) -> None:
        if binding.release_reference is not None and binding.release_reference != self.release_reference:
            raise TaskDeferred("framework_release_artifact_unavailable")

    def require(self, binding: RuntimeBinding, *, operation: str) -> None:
        """Unavailable compatibility leaves the durable task unclaimed.

        Deep Agents supports initial messages, owner-bound native approval
        continuations, deterministic reconciliation and proven successors.
        Other graph workflows and resume operations
        require their own execution contracts before they can be admitted.
        """
        self.require_runtime_artifact(binding)
        if (binding.runtime_kind == "langgraph" and operation == "case_execute"
            and self.controlled_case_available):
            return
        if binding.runtime_kind != "deepagents":
            raise TaskDeferred("framework_runtime_kind_unavailable")
        if not self.deepagents_available:
            raise TaskDeferred("framework_deepagents_unavailable")
        if operation not in {"message", "approval_resume", "framework_reconcile", "approval_recover", "message_recover"}:
            raise TaskDeferred("framework_operation_unavailable")

    def require_runtime_artifact(self, binding: RuntimeBinding) -> None:
        """Shared code/format compatibility, without enabling new execution."""
        self.require_release(binding)
        versions = {self.runtime_version}
        if self.context_transcript_enabled and self.runtime_version == FRAMEWORK_RUNTIME_VERSION:
            versions.add(CONTEXT_RUNTIME_VERSION)
            if self.turn_context_assembly_enabled:
                versions.add(TURN_CONTEXT_RUNTIME_VERSION)
                if self.native_summary_coverage_enabled:
                    versions.add(SUMMARY_CONTEXT_RUNTIME_VERSION)
        if (binding.runtime_version not in versions
            or binding.runtime_version in {CONTEXT_RUNTIME_VERSION, *TURN_CONTEXT_RUNTIME_VERSIONS}
            and not self.context_transcript_enabled
            or binding.runtime_version in TURN_CONTEXT_RUNTIME_VERSIONS and not self.turn_context_assembly_enabled
            or binding.runtime_version == SUMMARY_CONTEXT_RUNTIME_VERSION and not self.native_summary_coverage_enabled):
            raise TaskDeferred("framework_runtime_version_unavailable")
        if binding.state_schema_version != 2:
            raise TaskDeferred("framework_state_schema_unavailable")
        if binding.dependency_lock_sha256.lower() != self.dependency_lock_sha256.lower():
            raise TaskDeferred("framework_dependency_artifact_unavailable")

    def require_message_stage(self, schema_version: int | None) -> None:
        if schema_version is not None and (type(schema_version) is not int
            or not 1 <= schema_version <= self.message_stage_max_version):
            raise TaskDeferred("framework_message_stage_unavailable")

    def require_case_input_format(self, value):
        from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

        reason = "framework_case_input_format_unavailable"
        version = value.get("schema_version") if isinstance(value, dict) else None
        if (type(version) is not int or not 1 <= version <= self.controlled_case_input_max_version
            or version >= 2 and not self.turn_context_assembly_enabled):
            raise TaskDeferred(reason)
        return self._read_compatible(parse_controlled_case_task_input, value, reason)

    def require_case_checkpoint_format(self, attempt):
        from src.schemas.controlled_case_admission import read_admission_checkpoint

        reason = "framework_case_checkpoint_format_unavailable"
        if type(attempt.checkpoint_version) is not int or not 1 <= attempt.checkpoint_version <= self.controlled_case_checkpoint_max_version:
            raise TaskDeferred(reason)
        return self._read_compatible(read_admission_checkpoint, attempt, reason)

    @classmethod
    def require_binding_format(cls, value) -> RuntimeBinding:
        """One persisted binding reader; compatibility never grants identity."""
        return cls._read_compatible(RuntimeBinding.model_validate, value,
            "framework_binding_format_unavailable", literal_fields=("runtime_kind", "model_transport"))

    @classmethod
    def require_command_format(cls, value: object) -> AgentTaskCommandV2:
        """Read a v2 delivery without retrying an unavailable strict format.

        The wire reader remains authoritative for identities and constraints;
        a compatible format is not permission to claim its durable task.
        """
        reason = "framework_command_format_unavailable"
        version = value.get("schema_version", 1) if isinstance(value, dict) else None
        if isinstance(value, dict) and (type(version) is not int or version not in {1, 2}):
            raise TaskDeferred(reason)
        try:
            command = parse_agent_task_command(value)
        except ValidationError as exc:
            if version == 2:
                # Command selectors (operation/type and binding protocol) are
                # declared Literals in the existing codec, not arbitrary payload.
                if any(error["type"] == "literal_error" for error in exc.errors(include_input=False, include_url=False)):
                    raise TaskDeferred(reason) from None
                cls.defer_unsupported_format(exc, reason=reason)
            raise
        if not isinstance(command, AgentTaskCommandV2):
            raise ValueError("v2 worker requires a schema_version=2 agent command")
        return command

    def require_model_ledger_format(self, kind: str, value: object) -> None:
        """Check the installed strict reader, not model settlement authority.

        A future field can break an extra=forbid reader without a version bump.
        Ordinary validation/identity errors are not compatibility deferrals.
        """
        model = {"budget": FrameworkModelBudget, "call": FrameworkModelCallRecord,
            "upload": FrameworkModelEvidenceUpload}[kind]
        reason = f"framework_model_{kind}_format_unavailable"
        if not isinstance(value, dict):
            raise TaskDeferred(reason)
        version = value.get("schema_version", 1)
        maximum = {"call": self.model_call_max_version, "budget": self.model_budget_max_version, "upload": 1}[kind]
        if type(version) is not int or not 1 <= version <= maximum:
            raise TaskDeferred(reason)
        # Budget v2 includes the existing request-profile/mapping protocols.
        # Their Literal versions are format selectors, not semantic failures.
        self._read_compatible(model.model_validate, value, reason,
            literal_fields=("request_mapping_version", "version") if kind == "budget" else ())

    def require_message_stage_format(self, value: object) -> FrameworkMessageStage:
        from src.runtime.framework_message_stages import message_stage_material_readers

        reason = "framework_message_stage_format_unavailable"
        if not isinstance(value, dict):
            raise TaskDeferred(reason)
        self.require_message_stage(value.get("schema_version", 3))
        stage = self._read_compatible(FrameworkMessageStage.model_validate, value, reason)
        manifest = stage.source_manifest or {}
        for key, reader in message_stage_material_readers().items():
            if key in manifest:
                if not isinstance(manifest[key], dict):
                    raise TaskDeferred(reason)
                self._read_compatible(reader, manifest[key], reason)
        return stage

    def require_transcript_format(self, session, binding: RuntimeBinding) -> None:
        """Reuse the actual cross-Turn reader, not a second metadata policy.

        This is only for a message that still imports live Session history;
        continuations and frozen graph inputs must retain their own sources.
        """
        from src.application.context.framework_context_service import FrameworkContextService
        from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

        reason = "framework_transcript_format_unavailable"
        raw = (session.metadata or {}).get("framework_transcript_asset")
        if raw is not None and (not isinstance(raw, dict)
            or type(raw.get("schema_version", 1)) is not int or raw.get("schema_version", 1) not in {1, 2, 3}):
            raise TaskDeferred(reason)
        try:
            FrameworkContextService.compatible_asset(session, binding)
        except (ValueError, FrameworkMessageStageRecoveryRequired):
            # Preserve the existing preflight's migration/protocol deferral,
            # without attaching serialized private history to queue logs.
            raise TaskDeferred(reason) from None

    def require_approval_journal_format(self, kind: str, value: object) -> None:
        """Reuse existing journal readers without granting native delivery rights.

        Proof dictionaries retain their untyped metadata contract. Only their
        declared native protocol and existing typed application material are
        inspected here; native facts, identities and outcomes remain separate.
        """
        from src.schemas.framework_approval import FrameworkInterruptLink, FrameworkDecisionReceipt, NativeToolInterrupt
        from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY, parse_tool_projection

        reason = f"framework_approval_{kind}_format_unavailable"
        if not isinstance(value, Mapping):
            raise TaskDeferred(reason)
        model = {"link": FrameworkInterruptLink, "receipt": FrameworkDecisionReceipt}[kind]
        record = self._read_compatible(model.model_validate, value, reason)
        if kind == "receipt":
            proof = record.proof or {}
            application = proof.get("application")
            native_format = NativeToolInterrupt.model_fields["format_version"].default
            for material in (proof, application):
                if (isinstance(material, dict) and "format_version" in material
                    and material["format_version"] != native_format):
                    raise TaskDeferred(reason)
            if isinstance(application, dict) and TOOL_PROJECTION_KEY in application:
                self._read_compatible(parse_tool_projection, application[TOOL_PROJECTION_KEY], reason)

    def require_recovery_proof_format(self, value: object) -> None:
        """Read declared recovery formats without granting a recovery scope.

        Native-decision proofs keep their existing free-dictionary contract;
        application proofs use the same strict readers as their owned ports.
        Issuance, hashes and lineage are still checked by the original claim.
        """
        from src.schemas.framework_message_stage import (
            FrameworkApprovalFinalizationProof, FrameworkApprovalSettlementProof,
        )

        reason = "framework_recovery_proof_format_unavailable"
        if not isinstance(value, dict):
            raise TaskDeferred(reason)
        scope = value.get("scope")
        if scope is None:
            # There is no strict native proof codec. Do not invent a metadata
            # whitelist or grant authority to a missing/malformed native proof.
            if "schema_version" in value and (type(value["schema_version"]) is not int or value["schema_version"] != 1):
                raise TaskDeferred(reason)
            return
        model = {"application_finalization": FrameworkApprovalFinalizationProof,
            "application_settlement": FrameworkApprovalSettlementProof}.get(scope) if isinstance(scope, str) else None
        if model is None:
            raise TaskDeferred(reason)
        version = value.get("schema_version", model.model_fields["schema_version"].default)
        if type(version) is not int or version != model.model_fields["schema_version"].default:
            raise TaskDeferred(reason)
        self._read_compatible(model.model_validate, value, reason)

    def require_case_settlement_format(self, value):
        """A compatible lineage reader is not permission to claim or execute."""
        from src.schemas.controlled_case_settlement_recovery import ControlledCaseSettlementAdmission

        reason = "framework_case_settlement_format_unavailable"
        version = value.get("schema_version") if isinstance(value, dict) else None
        if type(version) is not int or not 1 <= version <= self.controlled_case_settlement_max_version:
            raise TaskDeferred(reason)
        return self._read_compatible(ControlledCaseSettlementAdmission.model_validate, value, reason,
            literal_fields=("scope",))

    def require_case_recovery_receipt_format(self, value):
        from src.schemas.controlled_case_settlement_recovery import ControlledCaseRecoverySettlementReceipt

        reason = "framework_case_recovery_receipt_format_unavailable"
        version = value.get("schema_version") if isinstance(value, dict) else None
        if type(version) is not int or not 1 <= version <= self.controlled_case_recovery_receipt_max_version:
            raise TaskDeferred(reason)
        return self._read_compatible(ControlledCaseRecoverySettlementReceipt.model_validate, value, reason,
            literal_fields=("scope",))

    def require_case_approval_wait_format(self, value):
        """Historical parking codec only, never decision/resume admission."""
        from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt

        reason = "framework_case_approval_wait_format_unavailable"
        version = value.get("schema_version") if isinstance(value, dict) else None
        if type(version) is not int or not 1 <= version <= self.controlled_case_approval_wait_max_version:
            raise TaskDeferred(reason)
        return self._read_compatible(ControlledCaseApprovalWaitReceipt.model_validate, value, reason,
            literal_fields=("scope",))

    def require_case_approval_admission_format(self, value):
        from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission

        reason = "framework_case_approval_admission_format_unavailable"
        version = value.get("schema_version") if isinstance(value, dict) else None
        if type(version) is not int or not 1 <= version <= self.controlled_case_approval_admission_max_version:
            raise TaskDeferred(reason)
        return self._read_compatible(ControlledCaseApprovalAdmission.model_validate, value, reason,
            literal_fields=("scope",))

    @staticmethod
    def defer_unsupported_format(exc: ValidationError, *, reason: str, literal_fields=()) -> None:
        if any(error["type"] == "extra_forbidden" or (
            error["type"] == "literal_error" and error["loc"]
            and error["loc"][-1] in {"schema_version", "position_version", "derivation_contract_version", "format_version", *literal_fields})
            for error in exc.errors(include_input=False, include_url=False)):
            # Do not attach the raw record / validation input to queue logs.
            raise TaskDeferred(reason) from None

    @classmethod
    def _read_compatible(cls, reader, value, reason, *, literal_fields=()):
        try:
            return reader(value)
        except ValidationError as exc:
            cls.defer_unsupported_format(exc, reason=reason, literal_fields=literal_fields)
            raise
