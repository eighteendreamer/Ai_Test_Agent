"""Durable cross-Turn transcript identity for framework-native messages."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


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
