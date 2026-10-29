"""Durable cross-Turn transcript identity for framework-native messages."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


CONTEXT_RUNTIME_VERSION = "v2.1.0"
TURN_CONTEXT_RUNTIME_VERSION = "v2.2.0"
IDENTITY_CONTEXT_RUNTIME_VERSIONS = frozenset({CONTEXT_RUNTIME_VERSION, TURN_CONTEXT_RUNTIME_VERSION})
TRANSCRIPT_PREPARATION_KEY = "framework_transcript_preparation"


def uses_turn_context_assembly(context: Mapping[str, Any]) -> bool:
    binding = context.get("runtime_binding")
    return (isinstance(binding, dict) and binding.get("runtime_kind") == "deepagents"
        and binding.get("runtime_version") == TURN_CONTEXT_RUNTIME_VERSION)


class FrameworkTranscriptAsset(BaseModel):
    """A validated lossless message asset used to seed a later framework Turn.

    The application display transcript is deliberately not accepted here.  A
    framework message must retain LangChain's ``type``/``data`` wire shape so
    provider blocks, tool-call IDs and response metadata survive a new
    ``thread_id``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    runtime_kind: Literal["deepagents"] = "deepagents"
    session_id: str = Field(min_length=1)
    source_turn_id: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    framework_messages: list[dict[str, Any]] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_wire_messages_and_digest(self):
        for item in self.framework_messages:
            if not isinstance(item.get("type"), str) or not isinstance(item.get("data"), dict):
                raise ValueError("framework transcript must use LangChain message dictionaries")
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != approval_value_hash(payload):
            raise ValueError("Framework transcript asset digest does not match its contents")
        return self


class FrameworkTranscriptAssetV2(FrameworkTranscriptAsset):
    """Identity coverage is a new format, never guessed for schema-1 assets."""

    schema_version: Literal[2] = 2
    runtime_version: Literal["v2.1.0", "v2.2.0"] = CONTEXT_RUNTIME_VERSION
    source_input_message_id: str = Field(min_length=1)
    source_output_message_ids: list[str]
    covered_message_ids: list[str] = Field(min_length=1)
    business_message_map: dict[str, list[str]]
    model_config_fingerprint: str = Field(min_length=1)
    serializer: Literal["langchain.messages.v1"] = "langchain.messages.v1"

    @model_validator(mode="after")
    def validate_coverage(self):
        covered = self.covered_message_ids
        native_ids = [item["data"].get("id") for item in self.framework_messages]
        if (not all(isinstance(identity, str) and identity for identity in native_ids)
            or len(set(native_ids)) != len(native_ids)):
            raise ValueError("Covered native messages require unique stable identities")
        if len(set(covered)) != len(covered) or set(self.business_message_map) != set(covered):
            raise ValueError("Transcript coverage and business mapping must have the same unique identities")
        source = [self.source_input_message_id, *self.source_output_message_ids]
        if len(set(source)) != len(source) or covered[-len(source):] != source:
            raise ValueError("Transcript source identities must close its coverage")
        for identities in self.business_message_map.values():
            if (not identities or len(set(identities)) != len(identities)
                or not set(identities).issubset(native_ids)):
                raise ValueError("Transcript business mapping references unavailable native identities")
        return self


class FrameworkTranscriptPreparation(BaseModel):
    """Server assembly provenance carried in the original graph/result assets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    source_input_message_id: str = Field(min_length=1)
    covered_message_ids: list[str] = Field(min_length=1)
    business_message_map: dict[str, list[str]]
    model_config_fingerprint: str = Field(min_length=1)
    input_message_count: int = Field(ge=1)
    input_messages_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_input_coverage(self):
        if (self.covered_message_ids[-1] != self.source_input_message_id
            or len(set(self.covered_message_ids)) != len(self.covered_message_ids)
            or set(self.business_message_map) != set(self.covered_message_ids)):
            raise ValueError("Prepared transcript must end at its admitted input identity")
        return self


def parse_framework_transcript_asset(value: Any) -> FrameworkTranscriptAsset | FrameworkTranscriptAssetV2:
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return FrameworkTranscriptAssetV2.model_validate(value)
    return FrameworkTranscriptAsset.model_validate(value)


def framework_transcript_asset(
    *,
    session_id: str,
    source_turn_id: str,
    runtime_version: str,
    framework_messages: list[dict[str, Any]],
) -> FrameworkTranscriptAsset:
    payload = {
        "schema_version": 1,
        "runtime_kind": "deepagents",
        "session_id": session_id,
        "source_turn_id": source_turn_id,
        "runtime_version": runtime_version,
        "framework_messages": framework_messages,
    }
    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return FrameworkTranscriptAsset(**payload, content_sha256=approval_value_hash(payload))
