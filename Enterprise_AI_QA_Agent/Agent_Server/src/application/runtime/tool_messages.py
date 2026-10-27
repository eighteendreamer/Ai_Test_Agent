"""Shared model-facing and application-facing projections of tool facts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.infrastructure.storage_utils import make_json_safe
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.schemas.session import ChatMessage, MessageRole
from src.schemas.tool_runtime import ToolExecutionRecord


logger = logging.getLogger(__name__)


def build_chat_tool_messages(turn_id: str, tool_results: list[dict], *, hygiene_metadata: dict,
                             binding_id: str | None = None, previous_messages=(), event_log=(),
                             original_materials=()) -> list[ChatMessage]:
    """Project original facts without assigning a new clock on bound replay.

    Existing published messages retain their original time (including older
    pending Turns). Otherwise use the persisted operation's completion time.
    Pre-execution governance refusals legitimately have no Job: their existing
    acknowledged completion event is the time source, not a fabricated Job.
    Original materials have already been source-checked by their reader. Reuse
    their exact display strings (JSONB can reorder record output objects), but
    never let them hide a record, ordinal, or published-message conflict.
    This pure projector neither creates facts nor grants graph recovery rights.
    """
    from src.schemas.framework_tool_projection import FrameworkToolProjection

    originals = {}
    for value in original_materials:
        material = FrameworkToolProjection.model_validate_json(value.model_dump_json())
        call_id = material.record.call_id
        if call_id in originals and originals[call_id] != material:
            raise FrameworkApprovalRecoveryRequired("Original tool projection material has conflicting contents")
        originals[call_id] = material
    previous = {}
    if binding_id is not None:
        identities = {str(uuid5(NAMESPACE_URL, f"framework-tool:{binding_id}:{item['call_id']}"))
            for item in tool_results if item.get("status") != "waiting_approval"}
        for message in previous_messages:
            if message.id not in identities:
                continue
            if message.id in previous and previous[message.id] != message:
                raise FrameworkApprovalRecoveryRequired("Original tool message identity has conflicting published facts")
            previous[message.id] = message
    messages = []
    for index, item in enumerate(tool_results, start=1):
        if item.get("status") == "waiting_approval":
            continue
        identity = (str(uuid5(NAMESPACE_URL, f"framework-tool:{binding_id}:{item['call_id']}"))
            if binding_id is not None else str(uuid4()))
        original = originals.get(item["call_id"])
        if original is not None:
            message = original.message
            if (binding_id is None or original.record != ToolExecutionRecord.model_validate(item)
                or message.id != identity or message.role != MessageRole.tool
                or message.metadata.get("turn_id") != turn_id or message.metadata.get("tool_call_id") != item["call_id"]
                or message.metadata.get("ordinal") != index
                or (identity in previous and previous[identity] != message)):
                raise FrameworkApprovalRecoveryRequired("Original tool projection differs from its result or published message")
            messages.append(message.model_copy(deep=True))
            continue
        content = (
            f"{item.get('tool_name', item.get('tool_key', 'tool'))}\n"
            f"status: {item.get('status', 'unknown')}\n"
            f"summary: {item.get('summary', '')}\n\n"
            f"{json.dumps(make_json_safe(item.get('output', {})), ensure_ascii=False, indent=2)}"
        ).strip()
        metadata = {"turn_id": turn_id, "tool_key": item.get("tool_key", ""),
            "tool_name": item.get("tool_name", ""), "tool_call_id": item.get("call_id", ""),
            "status": item.get("status", ""), "trace_id": item.get("trace_id", ""),
            "ordinal": index} | hygiene_metadata
        created_at = datetime.utcnow() if binding_id is None else _bound_message_time(
            turn_id, item, identity, content, metadata, previous.get(identity), event_log)
        messages.append(ChatMessage(id=identity, role=MessageRole.tool, content=content,
            created_at=created_at, metadata=metadata))
    return messages


def _bound_message_time(turn_id, item, identity, content, metadata, previous, event_log):
    if previous is not None:
        if (previous.role != MessageRole.tool or previous.metadata.get("turn_id") != turn_id
            or previous.metadata.get("tool_call_id") != item["call_id"]):
            raise FrameworkApprovalRecoveryRequired("Original tool message scope differs from its bound call")
        if previous.metadata.get("status") in {"completed", "partial", "failed", "denied"}:
            if previous.content != content or any(previous.metadata.get(key) != value for key, value in metadata.items()):
                raise FrameworkApprovalRecoveryRequired("Original terminal tool message contents or ordinal changed")
            return previous.created_at
    value = item.get("completed_at")
    if value is None:
        event_type = {"completed": "tool.execution_completed", "denied": "tool.execution_denied"}.get(
            item.get("status"), "tool.execution_failed")
        value = next((entry["timestamp"] for entry in event_log
            if entry.get("type") == event_type
            and entry.get("payload", {}).get("turn_id") == turn_id
            and entry["payload"].get("call_id") == item["call_id"]
            and entry["payload"].get("tool_key") == item.get("tool_key")
            and entry["payload"].get("tool_job_id") == item.get("job_id")
            and entry["payload"].get("status") == item.get("status")
            and entry["payload"].get("harness") == "deepagents"), None)
    if value is None:
        logger.warning("framework_tool_message_time_unavailable", extra={
            "turn_id": turn_id, "call_id": item["call_id"], "message_id": identity})
        raise FrameworkApprovalRecoveryRequired("Original tool message time is unavailable; reconcile its facts")
    timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    # Business timestamps are UTC without tzinfo in PostgreSQL. Normalize both
    # the first in-process fact and JSON/PG replay, not by local timezone.
    return timestamp.astimezone(timezone.utc).replace(tzinfo=None) if timestamp.tzinfo is not None else timestamp


def build_tool_message(record: ToolExecutionRecord) -> dict[str, Any]:
    # Same reference root as AgentCaseAssessmentService.evidence_manifest. A
    # pre-execution denial or an unfinished job is not settled tool evidence.
    settled = bool(record.job_id) and record.status in {
        "completed", "partial", "failed", "denied",
    }
    payload = {
        "call_id": record.call_id,
        "job_id": record.job_id,
        "output_reference": (
            {"call_id": record.call_id, "output_path_tokens": []} if settled else None
        ),
        "status": record.status,
        "summary": record.summary,
        "output": record.output,
        "content_provenance": "tool_output",
        "instruction_authority": "none",
    }
    return {
        "role": "tool",
        "tool_call_id": record.call_id,
        "name": record.tool_key,
        "content": json.dumps(make_json_safe(payload), ensure_ascii=False),
    }
