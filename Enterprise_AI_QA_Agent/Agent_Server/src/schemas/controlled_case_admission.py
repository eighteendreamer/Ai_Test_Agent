"""Original RunItem-to-task reservation, not an AgentTask execution lease."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.run_management import RunPlanSnapshotIdentity
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_runtime_result import ControlledCaseRuntimeResultAsset
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_projection_commit import (
    FrameworkSnapshotProjectionCommit, FrameworkEventProjectionCommits, FrameworkProjectionCommit,
    FrameworkTurnMemoryIntent, FrameworkTurnMemoryPlan, FrameworkTurnMemoryExecution,
)
from src.schemas.framework_observation_projection import FrameworkObservationAsset, FrameworkObservationCommits
from src.schemas.controlled_case_assessment import ControlledCaseAssessmentAsset
from src.schemas.controlled_case_settlement import ControlledCaseSettlementReceipt
from src.schemas.controlled_case_approval_state import ControlledCaseApprovalStateAsset
from src.schemas.controlled_case_approval_progress import ControlledCaseApprovalProgressAsset


CONTROLLED_CASE_ADMISSION_KEY = "controlled_case_admission"


class ControlledCaseAdmissionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    run_item_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    binding: RuntimeBinding
    plan_snapshot_identity: RunPlanSnapshotIdentity
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admitted_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled case admission requires its explicit integer version")
        return value

    @field_validator("admitted_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Controlled case admission requires an aware database time")
        return value

    @model_validator(mode="after")
    def verify_original_turn(self):
        expected = str(uuid5(NAMESPACE_URL,
            f"case-run:{self.run_id}:{self.run_item_id}:{self.run_item_execution_attempt_id}"))
        if self.binding.runtime_kind != "langgraph" or self.binding.turn_id != expected:
            raise ValueError("Controlled case admission differs from its original RunItem turn")
        return self


class ControlledCaseResultCheckpoint(BaseModel):
    """Retain the original reservation and result in the same attempt record."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2]
    admission: ControlledCaseAdmissionCheckpoint
    runtime_result_asset: ControlledCaseRuntimeResultAsset
    retained_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled result checkpoint requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_original(self):
        source, asset = self.admission, self.runtime_result_asset
        if (self.retained_at.utcoffset() is None or asset.binding != source.binding or asset.task_id != source.task_id
            or (asset.run_id, asset.run_item_id, asset.run_item_execution_attempt_id)
                != (source.run_id, source.run_item_id, source.run_item_execution_attempt_id)
            or asset.input_sha256 != source.input_sha256 or asset.execution_sha256 != source.submission_sha256
            or asset.source_sha256 != approval_value_hash(source.model_dump(mode="json"))):
            raise ValueError("Controlled result checkpoint differs from its original admission")
        return self


class ControlledCaseProjectionReceipts(BaseModel):
    """Only existing application part receipts; not a final commit or replay right."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_projection_commit: FrameworkSnapshotProjectionCommit | None = None
    event_projection_commits: FrameworkEventProjectionCommits | None = None
    observation_projection_asset: FrameworkObservationAsset | None = None
    observation_projection_commits: FrameworkObservationCommits | None = None


class ControlledCaseProjectionCheckpoint(ControlledCaseResultCheckpoint):
    schema_version: Literal[3]
    projection_receipts: ControlledCaseProjectionReceipts

    @model_validator(mode="after")
    def require_part_identities(self):
        asset, parts = self.runtime_result_asset, self.projection_receipts
        for receipt in (parts.snapshot_projection_commit, parts.event_projection_commits, parts.observation_projection_asset):
            if receipt is not None and (receipt.binding_fingerprint != asset.binding.fingerprint
                or receipt.runtime_result_asset_sha256 != asset.asset_sha256):
                raise ValueError("Controlled projection part differs from its original result")
        observations, commits = parts.observation_projection_asset, parts.observation_projection_commits
        if ((observations is None) != (commits is None)
            or (commits is not None and (commits.binding_fingerprint != asset.binding.fingerprint
                or commits.observation_asset_sha256 != observations.asset_sha256))):
            raise ValueError("Controlled observation parts have no matching original preparation")
        return self


class ControlledCaseApplicationReceipts(ControlledCaseProjectionReceipts):
    application_projection_commit: FrameworkProjectionCommit
    post_commit_memory_intent: FrameworkTurnMemoryIntent
    post_commit_memory_plan: FrameworkTurnMemoryPlan
    post_commit_memory_execution: FrameworkTurnMemoryExecution | None = None


class ControlledCaseApplicationCheckpoint(ControlledCaseProjectionCheckpoint):
    """Session application receipt only; not Assessment or RunItem completion."""
    schema_version: Literal[4]
    projection_receipts: ControlledCaseApplicationReceipts

    @model_validator(mode="after")
    def require_application_identity(self):
        asset, parts = self.runtime_result_asset, self.projection_receipts
        proof, plan, intent = parts.application_projection_commit, parts.post_commit_memory_plan, parts.post_commit_memory_intent
        if (proof.binding_fingerprint != asset.binding.fingerprint
            or parts.snapshot_projection_commit is None or parts.event_projection_commits is None
            or (proof.task_id, proof.attempt_id, proof.owner_epoch) != (asset.task_id, asset.attempt_id, asset.owner_epoch)
            or proof.execution_sha256 != asset.execution_sha256 or proof.snapshot_id != asset.runtime_result.snapshot.id
            or proof.output_message_id != (asset.assistant_message_id if asset.runtime_result.output_text else None)
            or intent != FrameworkTurnMemoryIntent.from_result_asset(asset)
            or (plan.effect_id, plan.session_id, plan.turn_id, plan.binding_fingerprint,
                plan.runtime_result_asset_sha256, plan.source_snapshot_id, plan.requested)
                != (intent.effect_id, intent.session_id, intent.turn_id, intent.binding_fingerprint,
                    intent.runtime_result_asset_sha256, intent.source_snapshot_id, intent.requested)):
            raise ValueError("Controlled application receipt differs from its original result")
        execution = parts.post_commit_memory_execution
        if execution is not None:
            identities = {item.request_id: item.ordinal for item in plan.requests}
            if ((execution.effect_id, execution.session_id, execution.turn_id, execution.binding_fingerprint,
                execution.runtime_result_asset_sha256, execution.source_snapshot_id, execution.plan_asset_sha256)
                != (plan.effect_id, plan.session_id, plan.turn_id, plan.binding_fingerprint,
                    plan.runtime_result_asset_sha256, plan.source_snapshot_id, plan.asset_sha256)
                or any(identities.get(item.request_id) != item.ordinal for item in execution.row_receipts)
                or execution.state == "completed" and {item.request_id for item in execution.row_receipts} != set(identities)):
                raise ValueError("Controlled Memory execution differs from its original plan")
        return self


class ControlledCaseAssessmentCheckpoint(ControlledCaseApplicationCheckpoint):
    schema_version: Literal[5]
    assessment: ControlledCaseAssessmentAsset

    @model_validator(mode="after")
    def require_assessment_identity(self):
        original, value = self.runtime_result_asset, self.assessment
        if (value.binding != original.binding
            or (value.task_id, value.attempt_id, value.owner_epoch) != (original.task_id, original.attempt_id, original.owner_epoch)
            or (value.run_id, value.run_item_id, value.run_item_execution_attempt_id)
                != (original.run_id, original.run_item_id, original.run_item_execution_attempt_id)
            or value.input_sha256 != original.input_sha256 or value.runtime_result_asset_sha256 != original.asset_sha256
            or value.application_commit_sha256 != approval_value_hash(
                self.projection_receipts.application_projection_commit.model_dump(mode="json"))):
            raise ValueError("Controlled assessment differs from its original application commit")
        return self


class ControlledCaseSettlementCheckpoint(ControlledCaseAssessmentCheckpoint):
    schema_version: Literal[6]
    settlement: ControlledCaseSettlementReceipt

    @model_validator(mode="after")
    def require_settlement_identity(self):
        asset, receipt = self.assessment, self.settlement
        if (receipt.binding_fingerprint != asset.binding.fingerprint
            or (receipt.task_id, receipt.attempt_id, receipt.owner_epoch) != (asset.task_id, asset.attempt_id, asset.owner_epoch)
            or receipt.assessment_sha256 != asset.asset_sha256):
            raise ValueError("Controlled settlement differs from its original assessment")
        return self


class ControlledCaseApprovalStateCheckpoint(BaseModel):
    """Waiting branch, deliberately NOT a result checkpoint subclass."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[7]
    admission: ControlledCaseAdmissionCheckpoint
    state_asset: ControlledCaseApprovalStateAsset
    retained_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled waiting checkpoint requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        source, asset = self.admission, self.state_asset
        if (self.retained_at != asset.retained_at or asset.binding != source.binding or asset.task_id != source.task_id
            or asset.run_item_execution_attempt_id != source.run_item_execution_attempt_id
            or asset.input_sha256 != source.input_sha256
            or asset.admission_sha256 != approval_value_hash(source.model_dump(mode="json"))):
            raise ValueError("Controlled waiting checkpoint differs from its original admission")
        return self


class ControlledCaseApprovalProgressCheckpoint(BaseModel):
    """Append successor materials, retaining the initial checkpoint verbatim."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[8]
    initial_wait: ControlledCaseApprovalStateCheckpoint
    progress_assets: tuple[ControlledCaseApprovalProgressAsset, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled progress checkpoint requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        from src.schemas.framework_approval import framework_approval_id

        root = self.initial_wait.state_asset
        parent, tasks = root, set()
        for asset in self.progress_assets:
            selected = [n for n in parent.natives if framework_approval_id(root.binding, n) == asset.admission.approval_id]
            material = [m for m in asset.tool_materials if m.record.approval_id == asset.admission.approval_id]
            if (len(selected) != 1 or len(material) != 1 or asset.task_id in tasks
                or asset.binding != root.binding or asset.origin_task_id != root.task_id
                or asset.admission.run_item_execution_attempt_id != root.run_item_execution_attempt_id
                or asset.admission.input_sha256 != root.input_sha256
                or asset.parent_state_sha256 != parent.asset_sha256 or asset.owner_epoch <= parent.owner_epoch
                or asset.retained_at < parent.retained_at
                or asset.root_model_budget != root.root_model_budget or asset.hygiene_metadata != root.hygiene_metadata
                or {n.interrupt_id: n for n in asset.natives}
                    != {n.interrupt_id: n for n in parent.natives if n not in selected}
                or asset.tool_materials != (*parent.tool_materials, material[0])):
                raise ValueError("Controlled progress checkpoint changed its original state lineage")
            tasks.add(asset.task_id)
            parent = asset
        return self


def read_wait_checkpoint(attempt) -> ControlledCaseApprovalStateCheckpoint:
    if attempt.checkpoint_version == 8:
        return ControlledCaseApprovalProgressCheckpoint.model_validate(attempt.checkpoint_payload).initial_wait
    if attempt.checkpoint_version == 7:
        return ControlledCaseApprovalStateCheckpoint.model_validate(attempt.checkpoint_payload)
    raise ValueError("RunItem has no original controlled waiting checkpoint")


def read_result_checkpoint(attempt) -> ControlledCaseResultCheckpoint | None:
    if attempt.checkpoint_version == 1:
        return None
    reader = {2: ControlledCaseResultCheckpoint, 3: ControlledCaseProjectionCheckpoint,
        4: ControlledCaseApplicationCheckpoint, 5: ControlledCaseAssessmentCheckpoint,
        6: ControlledCaseSettlementCheckpoint}.get(attempt.checkpoint_version)
    if reader is None:
        raise ValueError("RunItem has no supported controlled result checkpoint")
    return reader.model_validate(attempt.checkpoint_payload)


def read_admission_checkpoint(attempt) -> ControlledCaseAdmissionCheckpoint:
    """Read the original reservation only; inherited attempts need reconciliation."""
    if attempt.checkpoint_key != CONTROLLED_CASE_ADMISSION_KEY or attempt.checkpoint_version not in {1, 2, 3, 4, 5, 6, 7, 8}:
        raise ValueError("RunItem has no supported original controlled case admission checkpoint")
    if attempt.checkpoint_version in {7, 8}:
        checkpoint = read_wait_checkpoint(attempt).admission
    else:
        result = read_result_checkpoint(attempt)
        checkpoint = result.admission if result is not None else ControlledCaseAdmissionCheckpoint.model_validate(attempt.checkpoint_payload)
    if ((checkpoint.run_id, checkpoint.run_item_id, checkpoint.run_item_execution_attempt_id)
        != (attempt.run_id, attempt.run_item_id, attempt.id)
        or checkpoint.plan_snapshot_identity != attempt.plan_snapshot_identity
        or checkpoint.admitted_at != attempt.checkpoint_at):
        raise ValueError("Controlled case admission checkpoint differs from its original RunItem attempt")
    return checkpoint
