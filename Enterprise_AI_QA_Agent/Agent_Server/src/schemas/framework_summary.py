"""Identity-bound native summary evidence, not permission to reuse an offload."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


SUMMARY_COVERAGE_VERSION_KEY = "framework_summary_coverage_version"


def require_summary_coverage_version(version):
    if version is not None and (type(version) is not int or version != 1):
        raise ValueError("Native summary coverage requires its supported explicit version")
    return version


class FrameworkNativeSummaryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["native_summary_coverage_only"] = "native_summary_coverage_only"
    covered_message_ids: list[str] = Field(min_length=1)
    covered_messages_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary_message: dict[str, Any]
    # A source marker only. Neither this path nor a content hash proves that
    # the corresponding offloaded file is readable by a later Turn.
    file_path: str | None = None

    @model_validator(mode="after")
    def validate_summary(self):
        from langchain_core.messages import HumanMessage, messages_from_dict

        identities = self.covered_message_ids
        if not all(identities) or len(set(identities)) != len(identities):
            raise ValueError("Native summary coverage requires unique stable message identities")
        message, = messages_from_dict([self.summary_message])
        if (not isinstance(message, HumanMessage) or not message.text.strip()
            or message.additional_kwargs.get("lc_source") != "summarization"):
            raise ValueError("Native summary coverage has no original nonempty summary message")
        return self

    def require_messages(self, messages):
        from src.application.deep_agents.framework_transcript import deserialize_messages
        from src.application.deep_agents.native_terminal import require_closed_native_messages
        from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

        cutoff = len(self.covered_message_ids)
        prefix = messages[:cutoff]
        if (cutoff >= len(messages) or [item["data"].get("id") for item in prefix] != self.covered_message_ids
            or approval_value_hash(prefix) != self.covered_messages_sha256):
            raise ValueError("Native summary coverage differs from its original message prefix")
        try:
            require_closed_native_messages(deserialize_messages(messages))
            # Checking the actual retained tail catches a cutoff inside any
            # tool batch; the summary must never stand in for a tool result.
            require_closed_native_messages(deserialize_messages([self.summary_message, *messages[cutoff:]]))
        except FrameworkMessageStageRecoveryRequired as exc:
            raise ValueError("Native summary coverage splits or corrupts the tool protocol") from exc


def capture_native_summary_coverage(values):
    """Pure materialization from already verified native state; grants no rights.

    No upstream counterpart (checked: pinned SummarizationEvent, transcript
    identity mapping, v4 §5.1.5): upstream cutoff_index alone cannot identify
    an archived prefix. Keep its original message and content-bound identities.
    """
    from langchain_core.messages import messages_to_dict

    event = values.get("_summarization_event")
    if event is None:
        return None
    messages = values.get("messages")
    if (not isinstance(event, dict) or set(event) != {"cutoff_index", "summary_message", "file_path"}
        or not isinstance(messages, list) or type(event["cutoff_index"]) is not int
        or not 0 < event["cutoff_index"] < len(messages)):
        raise ValueError("Native summary event has no supported original coverage range")
    prefix = messages_to_dict(messages[:event["cutoff_index"]])
    material = FrameworkNativeSummaryCoverage(covered_message_ids=[item["data"].get("id") for item in prefix],
        covered_messages_sha256=approval_value_hash(prefix),
        summary_message=messages_to_dict([event["summary_message"]])[0], file_path=event["file_path"])
    material.require_messages(messages_to_dict(messages))
    return material.model_dump(mode="json", exclude_none=True)


def validate_summary_completion(context, summary, messages):
    version = require_summary_coverage_version(context.get(SUMMARY_COVERAGE_VERSION_KEY))
    if version is None:
        if "summary_coverage_version" in summary or "native_summary_coverage" in summary:
            raise ValueError("Native summary material cannot upgrade an unselected result contract")
        return None
    if (type(summary.get("summary_coverage_version")) is not int or summary["summary_coverage_version"] != version
        or "native_summary_coverage" not in summary):
        raise ValueError("Native summary completion has no original versioned coverage material")
    if summary["native_summary_coverage"] is None:
        return None
    material = FrameworkNativeSummaryCoverage.model_validate(summary["native_summary_coverage"])
    material.require_messages(messages)
    return material.model_dump(mode="json", exclude_none=True)
