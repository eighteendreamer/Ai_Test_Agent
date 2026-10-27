"""Versioned Verification allocation/rules, not a verdict or recovery proof.

No upstream counterpart (checked: VerificationService, original graph input,
v4 §9.2): native checkpoints cannot retain our verification rule selection or
allocate stable identities for application evidence. Provenance is checked by
the graph-input/result reader, not supplied by this digest alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.infrastructure.storage_utils import make_json_safe
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeBinding


VERIFICATION_PREPARATION_KEY = "framework_verification_preparation"


class VerificationCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    structured_output: bool
    parsed_fields: list[str]
    assertions: dict[str, list[str]]


class FrameworkVerificationPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["verification_projection_preparation_only"] = "verification_projection_preparation_only"
    rules_version: Literal[1] = 1
    binding: RuntimeBinding
    trace_id: str
    reserved_at: datetime
    security_capabilities: dict[str, VerificationCapabilities]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def strict_versions(cls, value):
        if isinstance(value, dict) and any(type(value.get(key, 1)) is not int
            for key in ("schema_version", "rules_version")):
            raise ValueError("Verification preparation versions must be supported integers")
        return value

    @model_validator(mode="after")
    def verify(self):
        if self.sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"sha256"})):
            raise ValueError("Original Verification preparation digest changed")
        return self

    def require_context(self, context, *, session_id, turn_id, trace_id):
        if (self.binding != RuntimeBinding.model_validate(context.get("runtime_binding"))
            or self.binding.session_id != session_id or self.binding.turn_id != turn_id or self.trace_id != trace_id):
            raise ValueError("Original Verification preparation differs from its bound Turn")
        return self


@dataclass(frozen=True)
class VerificationProjectionIdentity:
    preparation: FrameworkVerificationPreparation
    result_id: str
    created_at: datetime

    @classmethod
    def for_record(cls, preparation, record, context):
        if not isinstance(record.get("call_id"), str) or not record["call_id"]:
            raise ValueError("Prepared Verification has no original tool call identity")
        inputs = {key: context.get(key) for key in ("test_case", "performance_testing_request", "verification_mode")}
        digest = approval_value_hash(make_json_safe({"record": record, "rule_inputs": inputs}))
        identity = str(uuid5(NAMESPACE_URL, f"framework-verification:{preparation.sha256}:{digest}"))
        completed = record.get("completed_at")
        # A known result uses its original evidence time. Pending/no-time
        # material uses the explicitly reserved allocation time, never now.
        created_at = (completed if isinstance(completed, datetime) else datetime.fromisoformat(completed)
            if completed is not None else preparation.reserved_at)
        return cls(preparation, identity, created_at)

    def evidence_id(self, ordinal):
        return str(uuid5(NAMESPACE_URL, f"framework-verification-evidence:{self.result_id}:{ordinal}"))
