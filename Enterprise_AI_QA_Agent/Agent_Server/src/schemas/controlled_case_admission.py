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


def read_result_checkpoint(attempt) -> ControlledCaseResultCheckpoint | None:
    if attempt.checkpoint_version == 1:
        return None
    reader = {2: ControlledCaseResultCheckpoint, 3: ControlledCaseProjectionCheckpoint,
        4: ControlledCaseApplicationCheckpoint}.get(attempt.checkpoint_version)
    if reader is None:
        raise ValueError("RunItem has no supported controlled result checkpoint")
    return reader.model_validate(attempt.checkpoint_payload)


def read_admission_checkpoint(attempt) -> ControlledCaseAdmissionCheckpoint:
    """Read the original reservation only; inherited attempts need reconciliation."""
    if attempt.checkpoint_key != CONTROLLED_CASE_ADMISSION_KEY or attempt.checkpoint_version not in {1, 2, 3, 4}:
        raise ValueError("RunItem has no supported original controlled case admission checkpoint")
    result = read_result_checkpoint(attempt)
    checkpoint = result.admission if result is not None else ControlledCaseAdmissionCheckpoint.model_validate(attempt.checkpoint_payload)
    if ((checkpoint.run_id, checkpoint.run_item_id, checkpoint.run_item_execution_attempt_id)
        != (attempt.run_id, attempt.run_item_id, attempt.id)
        or checkpoint.plan_snapshot_identity != attempt.plan_snapshot_identity
        or checkpoint.admitted_at != attempt.checkpoint_at):
        raise ValueError("Controlled case admission checkpoint differs from its original RunItem attempt")
    return checkpoint
