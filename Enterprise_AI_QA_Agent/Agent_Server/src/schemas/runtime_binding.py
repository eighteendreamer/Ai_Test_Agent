"""Immutable runtime identity shared by task dispatch and framework recovery."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


RuntimeKind = Literal["legacy", "langgraph", "deepagents"]


class RuntimeBinding(BaseModel):
    """The code/state contract a Turn must use for its entire lifetime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    runtime_kind: RuntimeKind
    runtime_version: str = Field(min_length=1, max_length=128)
    state_schema_version: int = Field(ge=1, le=2_147_483_647)
    thread_id: str = Field(min_length=1, max_length=255)
    checkpoint_namespace: str = Field(min_length=1, max_length=255)
    prompt_version: str = Field(min_length=1, max_length=255)
    skill_version: str = Field(min_length=1, max_length=255)
    tool_schema_version: str = Field(min_length=1, max_length=255)
    model_config_fingerprint: str = Field(min_length=1, max_length=128)
    dependency_lock_sha256: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "binding_id", "session_id", "turn_id", "runtime_version", "thread_id",
        "checkpoint_namespace", "prompt_version", "skill_version", "tool_schema_version",
        "model_config_fingerprint", "dependency_lock_sha256", mode="before",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value):
        return value or datetime.now(timezone.utc)

    @model_validator(mode="before")
    @classmethod
    def validate_serialized_fingerprint(cls, value):
        if not isinstance(value, dict) or "fingerprint" not in value:
            return value
        supplied = str(value["fingerprint"])
        payload = dict(value)
        payload.pop("fingerprint", None)
        candidate = cls.model_validate(payload)
        if supplied != candidate.fingerprint:
            raise ValueError("runtime binding fingerprint does not match its immutable fields")
        return payload

    @model_validator(mode="after")
    def validate_identity(self):
        if self.thread_id != self.turn_id:
            raise ValueError("runtime binding thread_id must equal turn_id")
        if self.runtime_kind == "legacy" and self.state_schema_version != 1:
            raise ValueError("legacy runtime bindings must use state_schema_version=1")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.dependency_lock_sha256):
            raise ValueError("dependency_lock_sha256 must be a SHA-256 hex digest")
        return self

    @computed_field
    @property
    def fingerprint(self) -> str:
        payload = {
            name: getattr(self, name)
            for name in type(self).model_fields
            if name not in {"binding_id", "created_at"}
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
