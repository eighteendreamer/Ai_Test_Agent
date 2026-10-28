"""Read a native terminal checkpoint without granting application recovery.

No upstream counterpart (checked: native_interrupts, pinned LangGraph
StateSnapshot/PostgresSaver, integration plan v4 §8–9 A2-2): the application
needs a binding-scoped reference, not just next==() or the last assistant text.
Model settlement, ToolJob and application projection proofs remain separate.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.channels import UntrackedValue
from pydantic import BaseModel, ConfigDict, Field

from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired


logger = logging.getLogger(__name__)


class NativeTerminalProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["native_graph_terminal_only"] = "native_graph_terminal_only"
    binding_fingerprint: str
    thread_id: str
    checkpoint_namespace: str
    checkpoint_id: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    serializer: str
    uncheckpointed_channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeTerminalRead:
    proof: NativeTerminalProof
    values: dict[str, Any]
    # Exact checkpoint creation time, not an application completion receipt.
    # Keep this read material outside the persisted schema-1 proof.
    checkpoint_timestamp: str | None = None
    checkpoint_event_prefix: dict[str, Any] | None = None


def _require(condition, message):
    if not condition:
        raise FrameworkMessageStageRecoveryRequired(message)


def require_closed_native_messages(messages):
    """Preserve per-source batches; providers may reuse IDs in later batches."""
    _require(isinstance(messages, list) and bool(messages)
        and all(isinstance(item, BaseMessage) for item in messages),
        "Native terminal has no complete framework message sequence")
    pending = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            _require(message.tool_call_id in pending, "Native terminal has an orphan or duplicate tool result")
            pending.remove(message.tool_call_id)
            continue
        _require(not pending, "Native terminal has an unfinished tool call batch")
        if isinstance(message, AIMessage):
            _require(not message.invalid_tool_calls, "Native terminal has an invalid model tool call")
            calls = message.tool_calls
            identities = [call.get("id") for call in calls]
            _require(all(isinstance(identity, str) and bool(identity) for identity in identities)
                and len(identities) == len(set(identities)) and (not calls or bool(message.id)),
                "Native terminal tool calls have no unambiguous source identities")
            pending.update(identities)
    _require(not pending and any(isinstance(item, AIMessage) for item in messages),
        "Native terminal message protocol is incomplete")


def _native_hash(serde, value):
    kind, content = serde.dumps_typed(value)
    return hashlib.sha256(kind.encode() + b"\0" + content).hexdigest()


def require_native_terminal_original(cur, settings, source, native, preparation):
    """Recheck the exact SDK original inside an existing admission transaction.

    No upstream counterpart (checked: PostgresSaver.get_tuple, source-only
    reader namespace mapping, v4 §8.1/§9.4): SDK reads do not bind application
    admission locks. The caller owns those locks, not this read material.
    """
    from psycopg import sql
    from psycopg.rows import dict_row
    from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    # Use the SAME application connection. A nested savepoint restores the
    # transaction-local search_path even if an SDK query fails; no pool/setup,
    # checkpoint-table mutation or second materialization algorithm is needed.
    with cur.postgres_driver_connection() as connection, connection.cursor(row_factory=dict_row) as native_cur:
        native_cur.execute("SELECT current_setting('search_path') AS path")
        path = native_cur.fetchone()["path"]
        native_cur.execute("SELECT set_config('search_path', %s, true)",
            (sql.Identifier(settings.deep_agents.checkpoint_schema).as_string(connection),))
        reader = BoundPostgresReader(connection, source=source)
        config = {"configurable": {"thread_id": source.binding.thread_id,
            "checkpoint_id": native.proof.checkpoint_id}}
        checkpoint = reader.get_tuple(config)
        graph = DeepAgentRuntimeAdapter(model_resolver=None).native_schema(preparation, source=source, saver=reader)
        snapshot = graph.get_state(config, subgraphs=True)
        native_cur.execute("SELECT set_config('search_path', %s, true)", (path,))
    _require(checkpoint is not None and not checkpoint.pending_writes
        and native.proof.serializer == type(reader.serde).__module__ + "." + type(reader.serde).__qualname__
        and _native_hash(reader.serde, checkpoint.checkpoint) == native.proof.checkpoint_sha256
        and _native_hash(reader.serde, native.values) == native.proof.state_sha256
        and _native_hash(reader.serde, snapshot.values) == native.proof.state_sha256
        and snapshot.config["configurable"]["checkpoint_id"] == native.proof.checkpoint_id
        and not snapshot.next and not snapshot.tasks and not snapshot.interrupts
        and snapshot.metadata.get("source") == "loop"
        and tuple(sorted(key for key, channel in graph.channels.items()
            if isinstance(channel, UntrackedValue))) == native.proof.uncheckpointed_channels
        and checkpoint.checkpoint.get("ts") == native.checkpoint_timestamp
        and checkpoint.metadata.get(CHECKPOINT_PREFIX_KEY) == native.checkpoint_event_prefix,
        "Original native checkpoint, native state or bound prefix changed before admission sealing")


async def collect_native_terminal(agent, saver, config, *, returned_values=None) -> NativeTerminalRead:
    """Read an exact root checkpoint. Never invoke/continue a graph or model.

    This is native-state evidence only: it cannot import model responses,
    create successors, prove external effects, or sign an application commit.
    Hashes use the pinned official saver codec, not display serialization.
    """
    source = getattr(saver, "runtime_source", None)
    _require(source is not None, "Native terminal inspection requires a bound saver")
    binding = source.binding
    try:
        location = (config or {}).get("configurable", {})
        _require(location.get("thread_id") == binding.thread_id and not location.get("checkpoint_ns"),
            "Native terminal config differs from its bound root thread")
        snapshot = await agent.aget_state(config, subgraphs=True)
        exact = (snapshot.config or {}).get("configurable", {})
        checkpoint_id = exact.get("checkpoint_id")
        _require(checkpoint_id and exact.get("thread_id") == binding.thread_id
            and not exact.get("checkpoint_ns") and not snapshot.next and not snapshot.tasks and not snapshot.interrupts,
            "Native terminal has pending tasks, interrupts, or no exact root checkpoint")
        _require((snapshot.metadata or {}).get("source") == "loop",
            "Native terminal is not an original graph execution checkpoint")
        original = await saver.aget_tuple(snapshot.config)
        _require(original is not None and original.checkpoint.get("id") == checkpoint_id
            and original.config.get("configurable", {}).get("checkpoint_id") == checkpoint_id
            and not original.pending_writes,
            "Native terminal has no matching checkpoint or has unapplied writes")
        values = snapshot.values
        _require(isinstance(values, dict), "Native terminal state is not a supported mapping")
        require_closed_native_messages(values.get("messages"))
        # Official run-local limits deliberately use UntrackedValue. They
        # cannot be compared to a restored checkpoint or claimed as durable
        # recovery input. Keep their schema identities in the proof instead
        # of hard-coding tool/model counter names or silently discarding them.
        uncheckpointed = tuple(sorted(key for key, channel in agent.channels.items()
            if isinstance(channel, UntrackedValue)))
        if returned_values is not None:
            _require(isinstance(returned_values, dict) and not returned_values.get("__interrupt__"),
                "Returned graph output contains unresolved interrupts")
            mismatches = [key for key, value in returned_values.items()
                if key != "__interrupt__" and key not in uncheckpointed
                and (key not in values or values[key] != value)]
            if "messages" in mismatches:
                from langchain_core.messages import messages_to_dict

                left, right = returned_values["messages"], values["messages"]
                detail = [f"messages.count:{len(left)}:{len(right)}"]
                for index, (a, b) in enumerate(zip(messages_to_dict(left), messages_to_dict(right))):
                    detail.extend(f"messages[{index}].{key}" for key in sorted(set(a["data"]) | set(b["data"]))
                        if a["data"].get(key) != b["data"].get(key))
                mismatches.extend(detail)
            _require(not mismatches,
                "Returned graph output differs from its exact native terminal state: " + ",".join(mismatches))
        proof = NativeTerminalProof(binding_fingerprint=binding.fingerprint, thread_id=binding.thread_id,
            checkpoint_namespace="", checkpoint_id=checkpoint_id,
            checkpoint_sha256=_native_hash(saver.serde, original.checkpoint),
            state_sha256=_native_hash(saver.serde, values),
            serializer=type(saver.serde).__module__ + "." + type(saver.serde).__qualname__,
            uncheckpointed_channels=uncheckpointed)
    except FrameworkMessageStageRecoveryRequired:
        logger.warning("framework_native_terminal_rejected", extra={"binding_id": binding.binding_id})
        raise
    logger.info("framework_native_terminal_read", extra={"binding_id": binding.binding_id,
        "checkpoint_id": proof.checkpoint_id})
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    return NativeTerminalRead(proof=proof, values=values, checkpoint_timestamp=original.checkpoint.get("ts"),
        checkpoint_event_prefix=deepcopy(original.metadata.get(CHECKPOINT_PREFIX_KEY)))
