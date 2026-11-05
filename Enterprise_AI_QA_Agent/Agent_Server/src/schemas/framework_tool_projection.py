"""Original application tool materials, not authority to execute or recover.

No upstream counterpart (checked: ToolMessage.artifact in core 1.6.10,
RuntimeTurnResult, v4 §9.2): the framework stores arbitrary artifacts but does
not define our application record/display identity. This envelope is material,
not a ToolJob, ownership, application-commit or recovery-admission proof.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import FrameworkToolCallIdentity, approval_value_hash, framework_tool_call_id
from src.schemas.session import ChatMessage, MessageRole
from src.schemas.tool_runtime import ToolExecutionRecord


TOOL_PROJECTION_KEY = "enterprise_qa_tool_projection"
TOOL_PROJECTION_VERSION_KEY = "framework_tool_projection_version"


class FrameworkToolProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["tool_projection_material_only"] = "tool_projection_material_only"
    binding_fingerprint: str = Field(min_length=1)
    source: FrameworkToolCallIdentity
    record: ToolExecutionRecord
    message: ChatMessage
    model_content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_digest(self):
        if self.sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"sha256"})):
            raise ValueError("Original tool projection material digest changed")
        return self

    def require_binding(self, binding, source):
        call_id = framework_tool_call_id(binding, source)
        expected_source = source.model_dump(mode="json", include=set(FrameworkToolCallIdentity.model_fields))
        metadata = self.message.metadata
        expected = {"turn_id": binding.turn_id, "tool_call_id": call_id,
            "tool_key": self.record.tool_key, "tool_name": self.record.tool_name,
            "status": self.record.status, "trace_id": self.record.trace_id}
        if (self.binding_fingerprint != binding.fingerprint or self.source.model_dump(mode="json") != expected_source
            or self.record.call_id != call_id or self.record.tool_key != source.tool_name
            or self.record.status == "waiting_approval" or self.message.role != MessageRole.tool
            or self.message.id != str(uuid5(NAMESPACE_URL, f"framework-tool:{binding.binding_id}:{call_id}"))
            or any(metadata.get(key) != value for key, value in expected.items())
            or type(metadata.get("ordinal")) is not int or metadata["ordinal"] < 1):
            raise ValueError("Original tool projection material differs from its bound source or display identity")
        return self

    @classmethod
    def from_tool_message(cls, message, *, binding, source):
        artifact = message.artifact
        if (message.tool_call_id != source.tool_call_id or message.name != source.tool_name
            or message.status != "success" or not isinstance(artifact, dict)
            or TOOL_PROJECTION_KEY not in artifact):
            raise ValueError("Original governed tool result has no matching projection material")
        # Offload may replace content with a file preview. The original content
        # belongs to this envelope, not to the SDK's current display string.
        return parse_tool_projection(artifact[TOOL_PROJECTION_KEY]).require_binding(binding, source)

    def require_native_message(self, message, *, binding, source):
        """Compare material already authenticated by its scoped original reader.

        HITL reject messages have no application artifact. Their material must
        come from the applied journal reader, not from this comparison. Match
        the same native rejection fields as the existing application evidence;
        all other results still require their original artifact envelope.
        """
        self.require_binding(binding, source)
        if message.status == "error" and self.record.status == "denied" and self.record.approval_id is not None:
            if (message.tool_call_id != source.tool_call_id or message.name != source.tool_name
                or not isinstance(message.content, str) or not message.content
                or message.content != self.model_content or message.artifact is not None):
                raise ValueError("Original rejection material differs from its native message")
        elif self.from_tool_message(message, binding=binding, source=source) != self:
            raise ValueError("Original tool material differs from its native artifact")
        return self

    @classmethod
    def capture(cls, *, binding, source, record, message, model_content, runtime_message=None):
        payload = {"schema_version": 1, "scope": "tool_projection_material_only",
            "binding_fingerprint": binding.fingerprint,
            "source": source.model_dump(mode="json", include=set(FrameworkToolCallIdentity.model_fields)),
            "record": record.model_dump(mode="json"), "message": message.model_dump(mode="json"),
            "model_content": model_content}
        if runtime_message is not None:
            payload.update(schema_version=2, runtime_message=deepcopy(runtime_message))
        return parse_tool_projection({**payload, "sha256": approval_value_hash(payload)}).require_binding(binding, source)


class FrameworkRuntimeToolProjection(FrameworkToolProjection):
    """v2 adds the original application state slot, not the SDK tool content."""
    schema_version: Literal[2] = 2
    runtime_message: dict[str, Any]

    @model_validator(mode="after")
    def verify_runtime_message(self):
        slot = self.runtime_message
        if (set(slot) != {"role", "tool_call_id", "name", "content"}
            or slot.get("role") != "tool" or slot.get("tool_call_id") != self.record.call_id
            or slot.get("name") != self.record.tool_key or not isinstance(slot.get("content"), str)):
            raise ValueError("Original runtime tool message differs from its application record slot")
        return self


def parse_tool_projection(value) -> FrameworkToolProjection:
    """Keep v1 wire/digest unchanged; a v2 slot must never be invented for it."""
    if isinstance(value, FrameworkToolProjection):
        value = value.model_dump(mode="json")
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    selected = FrameworkRuntimeToolProjection if isinstance(value, dict) and value.get("schema_version") == 2 else FrameworkToolProjection
    return selected.model_validate(value)
