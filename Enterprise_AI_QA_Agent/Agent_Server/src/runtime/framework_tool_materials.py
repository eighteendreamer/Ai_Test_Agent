"""Shared ordinary-invocation tool original checks, not execution authority."""
from __future__ import annotations

from langchain_core.messages import ToolMessage

from src.application.deep_agents.framework_transcript import deserialize_messages
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.framework_tool_projection import (
    FrameworkToolProjection, FrameworkRuntimeToolProjection, TOOL_PROJECTION_KEY, parse_tool_projection,
)


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


def read_unapproved_tool_materials(original, binding, messages, jobs, *, event_log=None):
    # Application ordinals are completion order, not native task-path order.
    by_job, materials = {job.id: job for job in jobs}, {}
    frozen_history = deserialize_messages(original.framework_messages)
    for message in messages:
        if not isinstance(message, ToolMessage) or not isinstance(message.artifact, dict) or TOOL_PROJECTION_KEY not in message.artifact:
            continue
        material = parse_tool_projection(message.artifact[TOOL_PROJECTION_KEY])
        if material.binding_fingerprint != binding.fingerprint:
            _require(any(message == item for item in frozen_history),
                "Message result has foreign tool material outside its original frozen history")
            continue
        material = FrameworkToolProjection.from_tool_message(message, binding=binding, source=material.source)
        record = material.record
        job = by_job.get(record.job_id)
        _require(isinstance(material, FrameworkRuntimeToolProjection),
            "Message result has missing or unsupported original tool material")
        if job is None and event_log is not None:
            # Pre-execution governance can refuse/fail without creating a Job.
            # This is an authenticated audit fact, never external evidence.
            event_type = {"denied": "tool.execution_denied", "failed": "tool.execution_failed"}.get(record.status)
            matches = [item for item in event_log if item["type"] == event_type
                and all(item["payload"].get(key) == value for key, value in {
                    "turn_id": binding.turn_id, "harness": binding.runtime_kind, "call_id": record.call_id,
                    "tool_key": record.tool_key, "tool_job_id": None, "approval_id": None,
                    "status": record.status, "message": record.summary}.items())]
            _require(record.job_id is None and record.approval_id is None and record.status in {"denied", "failed"}
                and record.started_at is None and record.completed_at is None and len(matches) == 1,
                "Original refused tool has no matching pre-execution governance audit fact")
            from datetime import datetime, timezone

            timestamp = datetime.fromisoformat(matches[0]["timestamp"])
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None) if timestamp.tzinfo else timestamp
            _require(material.message.created_at == timestamp, "Original refused tool has a different display time")
        else:
            _require(job is not None, "Message result has missing or unsupported original tool material")
            _require(job.metadata.get("framework_binding_id") == binding.binding_id and job.call_id == record.call_id
                and job.tool_key == record.tool_key and job.tool_name == record.tool_name and job.status.value == record.status
                and job.input_payload == record.input and job.output_payload == record.output and job.summary == record.summary
                and job.trace_id == record.trace_id and job.started_at == record.started_at and job.completed_at == record.completed_at
                and record.approval_id is None, "Message result original tool material differs from its settled ToolJob")
        _require(record.call_id not in materials or materials[record.call_id] == material,
            "Message result has conflicting original tool materials")
        materials[record.call_id] = material
    ordered = sorted(materials.values(), key=lambda item: item.message.metadata["ordinal"])
    _require({item.record.job_id for item in ordered if item.record.job_id is not None} == set(by_job)
        and [item.message.metadata["ordinal"] for item in ordered] == list(range(1, len(ordered) + 1)),
        "Message result has an unprepared business tool or conflicting original ordinals")
    return ordered
