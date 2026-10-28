"""Completion material for the Runtime graph event_log, not Session finalization.

No upstream counterpart (checked: Session event ACK/projection transactions,
LangGraph 1.2.12 aget_state, v4 §9.2): a native checkpoint cannot declare our
callback event collection complete. The event table retains completion and
original rows; checkpoint metadata binds only the already acknowledged prefix.
Neither material admits a successor or authorizes graph/tool execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.application.deep_agents.native_terminal import NativeTerminalProof
from src.runtime.framework_application_projection import _event_row_hash, _require_event_intent
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ExecutionEvent

logger = logging.getLogger(__name__)


EVENT_TYPE = "runtime.event_log_completed"
CHECKPOINT_PREFIX_KEY = "framework_checkpoint_event_prefix"


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


def completion_event_id(binding):
    # One declaration per bound Turn, not one per retry/checkpoint candidate.
    return str(uuid5(NAMESPACE_URL, "enterprise-qa:runtime-event-log:" + binding.fingerprint))


class RuntimeEventReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    timestamp: str
    event_sequence: int = Field(ge=1)
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeEventCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["runtime_graph_event_log_only"] = "runtime_graph_event_log_only"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_terminal: NativeTerminalProof
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    worker_id: str = Field(min_length=1)
    returned_event_offset: int = Field(ge=0)
    events: list[RuntimeEventReference] = Field(min_length=1)
    completion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify(self):
        if self.completion_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"completion_sha256"})):
            raise ValueError("Runtime event completion digest differs")
        if (self.returned_event_offset >= len(self.events)
            or len({item.event_id for item in self.events}) != len(self.events)
            or any(a.event_sequence >= b.event_sequence for a, b in zip(self.events, self.events[1:]))):
            raise ValueError("Runtime event completion has no ordered original collection")
        return self


class RuntimeCheckpointEventPrefix(BaseModel):
    """An atomic ACK-prefix binding, never a graph/application completion grant."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["checkpoint_graph_event_prefix_only"] = "checkpoint_graph_event_prefix_only"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1, strict=True)
    worker_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_timestamp: str = Field(min_length=1)
    returned_event_offset: int = Field(ge=0, strict=True)
    events: list[RuntimeEventReference] = Field(min_length=1)
    prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Checkpoint event prefix requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        if (self.prefix_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"prefix_sha256"}))
            or self.returned_event_offset >= len(self.events)
            or len({item.event_id for item in self.events}) != len(self.events)
            or any(a.event_sequence >= b.event_sequence for a, b in zip(self.events, self.events[1:]))
            or datetime.fromisoformat(self.checkpoint_timestamp).utcoffset() is None):
            raise ValueError("Checkpoint event prefix has changed or unsupported material")
        return self


@dataclass(frozen=True)
class RuntimeEventCompletionRead:
    completion: RuntimeEventCompletion
    event_log: list[dict[str, Any]]


def _require_terminal(binding, raw):
    terminal = NativeTerminalProof.model_validate(raw)
    _require(terminal.binding_fingerprint == binding.fingerprint and terminal.thread_id == binding.thread_id
        and terminal.checkpoint_namespace == "", "Runtime event completion native terminal binding differs")
    return terminal


def _require_log(binding, event_log, offset):
    _require_ordered_log(binding, event_log, offset)
    _require(event_log[-1]["type"] == "runtime.turn_completed"
        and all(item["type"] != "runtime.turn_completed" for item in event_log[:-1]),
        "Runtime event completion has no original completed boundary")


def _require_ordered_log(binding, event_log, offset):
    _require(type(offset) is int and 0 <= offset < len(event_log), "Runtime event completion return offset differs")
    ids = [item["id"] for item in event_log]
    _require(len(set(ids)) == len(ids) and completion_event_id(binding) not in ids,
        "Runtime event completion contains duplicate or recursive identities")
    for step, item in enumerate(event_log, start=1):
        _require(set(item) == {"id", "timestamp", "type", "payload"}
            and item["payload"].get("turn_id") == binding.turn_id and item["payload"].get("step") == step
            and item["type"] != EVENT_TYPE,
            "Runtime event completion contains a foreign or unordered graph event")


def _event_rows(cur, settings, session_id, ids, *, schema=""):
    cur.execute(f"SELECT id,session_id,type,timestamp,payload,event_sequence FROM {schema}{settings.database.postgres_event_table} "
        "WHERE session_id=%s AND id=ANY(%s) ORDER BY event_sequence FOR UPDATE", (session_id, ids))
    return {row["id"]: dict(row) for row in cur.fetchall()}


def _require_producer(cur, settings, binding, material, *, schema=""):
    # Use the already-persisted claim/attempt journal, not a hash as authority.
    # Current owner state may have moved on; original attempts remain facts.
    cur.execute(f"SELECT event.id FROM {schema}{settings.database.postgres_agent_task_table} task "
        f"JOIN {schema}{settings.database.postgres_agent_attempt_table} attempt ON attempt.task_id=task.id "
        f"JOIN {schema}{settings.database.postgres_event_table} event ON event.session_id=task.session_id "
        "AND event.type='agent_task.claimed' AND event.payload->>'task_id'=task.id "
        "AND event.payload->>'binding_id'=%s AND event.payload->>'owner_epoch'=%s "
        "AND event.payload->'_harness_context'->>'attempt_id'=attempt.id "
        "AND event.payload->'_harness_context'->>'worker_id'=attempt.worker_id "
        "WHERE task.id=%s AND task.session_id=%s AND task.turn_id=%s AND task.runtime_binding=%s::jsonb "
        "AND attempt.id=%s AND attempt.worker_id=%s AND event.event_sequence<%s",
        (binding.binding_id, str(material.owner_epoch), material.task_id, binding.session_id, binding.turn_id,
            binding.model_dump_json(), material.attempt_id, material.worker_id, material.events[-1].event_sequence))
    _require(len(cur.fetchall()) == 1, "Runtime event completion has no original bound producer claim")


def _event_references(cur, settings, binding, event_log, *, schema=""):
    rows = _event_rows(cur, settings, binding.session_id, [item["id"] for item in event_log], schema=schema)
    references = []
    for item in event_log:
        row = rows.get(item["id"])
        _require_event_intent(row, ExecutionEvent(session_id=binding.session_id, **item))
        references.append(dict(event_id=item["id"], timestamp=item["timestamp"], event_sequence=row["event_sequence"],
            intent_sha256=approval_value_hash(item), row_sha256=_event_row_hash(row)))
    return references


def checkpoint_event_prefix(cur, settings, owner, preparation, acknowledged, *, checkpoint, serde):
    """Called ONLY by the fenced saver inside its existing owner transaction.

    No upstream counterpart (checked: PostgresSaver.put metadata support, ACK
    sink, SDK callback barrier, v4 §9.2): the SDK cannot authenticate application
    audit rows. Its metadata stores references, not a second graph state/log.
    """
    from src.application.deep_agents.native_terminal import _native_hash

    binding = owner.binding
    _require(preparation.runtime_preparation.identity.binding == binding
        and preparation.feature_flags.get("terminal_event_projection_version") == 1,
        "Checkpoint event prefix differs from its captured original preparation")
    initial = preparation.runtime_preparation.initial_state["event_log"]
    ids = {item["id"] for item in initial}
    overlap = 0
    for item in acknowledged:
        if item["id"] not in ids:
            break
        overlap += 1
    _require(overlap > 0 and acknowledged[:overlap] == initial[-overlap:],
        "Checkpoint event prefix has no acknowledged original invocation boundary")
    events = initial + acknowledged[overlap:]
    offset = len(initial) - 1 if initial[-1]["type"] == "turn.resumed" else 0
    _require_ordered_log(binding, events, offset)
    _require(all(item["type"] not in {EVENT_TYPE, "runtime.turn_completed", "graph.plan_built"} for item in events),
        "Checkpoint event prefix cannot claim a Runtime completion")
    value = dict(schema_version=1, scope="checkpoint_graph_event_prefix_only", binding_fingerprint=binding.fingerprint,
        graph_input_sha256=preparation.asset_sha256, task_id=owner.task_id, attempt_id=owner.attempt_id,
        owner_epoch=owner.owner_epoch, worker_id=owner.worker_id, checkpoint_id=checkpoint.checkpoint["id"],
        checkpoint_sha256=_native_hash(serde, checkpoint.checkpoint), checkpoint_timestamp=checkpoint.checkpoint["ts"],
        returned_event_offset=offset, events=_event_references(cur, settings, binding, events, schema="public."))
    material = RuntimeCheckpointEventPrefix(**value, prefix_sha256=approval_value_hash(value))
    _require_producer(cur, settings, binding, material, schema="public.")
    return material


def read_checkpoint_event_prefix(cur, settings, source, preparation, native):
    """Read original ACK rows for this exact native checkpoint; no writes/rights."""
    from src.schemas.framework_graph_input import FrameworkPreparedGraphInputAsset, parse_graph_input_asset

    check_phase = "prefix_schema"
    try:
        material = RuntimeCheckpointEventPrefix.model_validate(native.checkpoint_event_prefix)
        check_phase = "original_input"
        # Frozen models can still contain caller-mutated nested dictionaries.
        # Reparse the actual contents, not just their retained digest string.
        preparation = parse_graph_input_asset(preparation)
        _require(isinstance(preparation, FrameworkPreparedGraphInputAsset)
            and preparation.feature_flags.get("terminal_event_projection_version") == 1,
            "Checkpoint event prefix requires its original prepared event rules")
        check_phase = "native_source"
        terminal = _require_terminal(source.binding, native.proof.model_dump(mode="json"))
        _require((material.binding_fingerprint, material.task_id, material.attempt_id, material.owner_epoch, material.worker_id)
            == (source.binding.fingerprint, source.task_id, source.attempt_id, source.owner_epoch, source.worker_id)
            and material.graph_input_sha256 == preparation.asset_sha256
            and preparation.runtime_preparation.identity.binding == source.binding
            and material.checkpoint_id == terminal.checkpoint_id and material.checkpoint_sha256 == terminal.checkpoint_sha256
            and material.checkpoint_timestamp == native.checkpoint_timestamp,
            "Checkpoint event prefix differs from its exact original source or checkpoint")
        check_phase = "producer_claim"
        _require_producer(cur, settings, source.binding, material)
        check_phase = "original_events"
        events = _read_event_references(cur, settings, source.binding, material.events)
        check_phase = "invocation_boundary"
        _require_ordered_log(source.binding, events, material.returned_event_offset)
        initial = preparation.runtime_preparation.initial_state["event_log"]
        _require(events[:len(initial)] == initial
            and material.returned_event_offset == (len(initial) - 1 if initial[-1]["type"] == "turn.resumed" else 0)
            and all(item["type"] not in {EVENT_TYPE, "runtime.turn_completed", "graph.plan_built"} for item in events),
            "Checkpoint event prefix differs from its original invocation or claims Runtime completion")
    except (FrameworkMessageStageRecoveryRequired, ValueError) as exc:
        logger.warning("framework_checkpoint_event_prefix_read_rejected", extra={
            "binding_id": source.binding.binding_id,
            "task_id": source.task_id,
            "attempt_id": source.attempt_id,
            "owner_epoch": source.owner_epoch,
            "error_type": type(exc).__name__,
            "check_phase": check_phase,
        })
        raise
    logger.info("framework_checkpoint_event_prefix_read", extra={
        "binding_id": source.binding.binding_id,
        "task_id": source.task_id,
        "attempt_id": source.attempt_id,
        "owner_epoch": source.owner_epoch,
        "event_count": len(events),
    })
    return events


def completion_event(cur, settings, owner, event_log, native_terminal, returned_event_offset):
    """Called under the existing Session/owner transaction locks, after all ACKs."""
    binding = owner.binding
    terminal = _require_terminal(binding, native_terminal)
    _require_log(binding, event_log, returned_event_offset)
    references = _event_references(cur, settings, binding, event_log)
    value = dict(schema_version=1, scope="runtime_graph_event_log_only", binding_fingerprint=binding.fingerprint,
        native_terminal=terminal.model_dump(mode="json"), task_id=owner.task_id, attempt_id=owner.attempt_id,
        owner_epoch=owner.owner_epoch, worker_id=owner.worker_id, returned_event_offset=returned_event_offset,
        events=references)
    material = RuntimeEventCompletion(**value, completion_sha256=approval_value_hash(value))
    _require_producer(cur, settings, binding, material)
    # Inherit the last original event's declared source: legacy graph-log
    # boundary or versioned native checkpoint creation, never application commit.
    # Repeat calls reuse that exact intent without upgrading historical records.
    return _declaration(binding, material, event_log[-1]["timestamp"])


def _declaration(binding, material, timestamp):
    return ExecutionEvent(id=completion_event_id(binding), session_id=binding.session_id, type=EVENT_TYPE,
        timestamp=timestamp, payload={"turn_id": binding.turn_id, "phase": "runtime",
            "message": "Runtime graph event collection persisted; application finalization remains separate.",
            "completion": material.model_dump(mode="json")})


def read_completion(cur, settings, binding, native_terminal):
    """Read-only material verification; the caller separately verifies native state.

    This is not recovery admission. No new graph, result asset, timestamps,
    events, task receipts or execution rights are created by reading it.
    """
    terminal = _require_terminal(binding, native_terminal)
    rows = _event_rows(cur, settings, binding.session_id, [completion_event_id(binding)])
    row = rows.get(completion_event_id(binding))
    _require(row is not None and row["type"] == EVENT_TYPE
        and row["payload"].get("turn_id") == binding.turn_id, "Original Runtime event completion is missing")
    try:
        material = RuntimeEventCompletion.model_validate(row["payload"].get("completion"))
    except ValueError as exc:
        raise FrameworkMessageStageRecoveryRequired("Original Runtime event completion is invalid") from exc
    _require(material.binding_fingerprint == binding.fingerprint and material.native_terminal == terminal,
        "Runtime event completion differs from the exact native terminal")
    _require_producer(cur, settings, binding, material)
    event_log = _read_event_references(cur, settings, binding, material.events, before_sequence=row["event_sequence"])
    _require_log(binding, event_log, material.returned_event_offset)
    _require_event_intent(row, _declaration(binding, material, event_log[-1]["timestamp"]))
    return RuntimeEventCompletionRead(completion=material, event_log=event_log)


def _read_event_references(cur, settings, binding, references, *, before_sequence=None):
    originals = _event_rows(cur, settings, binding.session_id, [item.event_id for item in references])
    event_log = []
    for reference in references:
        original = originals.get(reference.event_id)
        _require(original is not None and _event_row_hash(original) == reference.row_sha256
            and original["event_sequence"] == reference.event_sequence
            and (before_sequence is None or original["event_sequence"] < before_sequence),
            "Original Runtime event row changed or disappeared")
        payload = dict(original["payload"] or {})
        payload.pop("_harness_context", None)
        intent = dict(id=reference.event_id, timestamp=reference.timestamp, type=original["type"], payload=payload)
        _require(approval_value_hash(intent) == reference.intent_sha256, "Original Runtime event intent changed")
        _require_event_intent(original, ExecutionEvent(session_id=binding.session_id, **intent))
        event_log.append(intent)
    return event_log
