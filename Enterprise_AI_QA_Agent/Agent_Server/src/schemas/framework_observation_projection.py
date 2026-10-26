"""Original observation intents and separately committed physical row receipts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash
from src.schemas.memory import MemoryWriteRequest
from src.schemas.observation import ObservationRecord


class FrameworkObservationAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[ObservationRecord]
    write_requests: list[MemoryWriteRequest]
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify(self):
        identities = [item.id for item in self.observations]
        if len(set(identities)) != len(identities) or len(self.write_requests) != len(identities):
            raise ValueError("Observation asset has duplicate or incomplete write identities")
        for observation, request in zip(self.observations, self.write_requests):
            if (request.kind != "observation" or request.metadata.get("memory_id") != observation.id
                or request.metadata.get("observation_id") != observation.id
                or request.session_id != observation.session_id or request.turn_id != observation.turn_id
                or request.trace_id != observation.trace_id or request.content != observation.content
                or request.turn_lease_token or request.compaction_key or request.compaction_owner_id):
                raise ValueError("Observation asset differs from its original write intent")
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Observation asset digest differs from its contents")
        return self


class FrameworkObservationCommits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_hashes: dict[str, str] = Field(default_factory=dict)
