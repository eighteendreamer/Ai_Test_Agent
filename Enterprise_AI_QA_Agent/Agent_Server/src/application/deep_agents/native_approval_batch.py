"""Exact formal pending batch on the caller's connection; no parking authority.

No upstream counterpart (checked: native_terminal original read, shared native
interrupt parser, pinned SDK get_state/get_tuple, v4 §7/§12.1): per-item links
cannot prove that a proposed batch includes every current pending interrupt.
The caller still owns original input admission, both owner locks, business
transitions and commit. This source-only read grants no resume, stop or ACK.
"""
from __future__ import annotations

from copy import deepcopy
import logging

from psycopg import sql
from psycopg.rows import dict_row

from src.application.deep_agents.checkpoint_provider import checkpoint_serializer
from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader
from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts_from_snapshot, _source_call
from src.application.runtime.framework_native_schema import native_graph_schema
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.schemas.controlled_case_graph_input import parse_controlled_graph_input
from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash
from src.schemas.runtime_binding import RuntimeCheckpointSource


logger = logging.getLogger(__name__)


def require_historical_tool_interrupts_original(cur, settings, source, graph_input, identities):
    """Exact retained checkpoints, NOT the latest pending batch or resume grant.

    The atomic business wait receipt proves batch completeness. Reuse the
    native journal's interrupt identity and the same source-AI kernel as live
    projection, but do not require historical tasks to remain unconsumed.
    """
    original = parse_controlled_graph_input(graph_input)
    if original.binding != source.binding or not identities:
        raise FrameworkApprovalRecoveryRequired("Historical wait has no matching frozen native input")
    with cur.postgres_driver_connection() as connection, connection.cursor(row_factory=dict_row) as native_cur:
        native_cur.execute("SELECT current_setting('search_path') AS path")
        path = native_cur.fetchone()["path"]
        native_cur.execute("SELECT set_config('search_path', %s, true)",
            (sql.Identifier(settings.deep_agents.checkpoint_schema).as_string(connection),))
        try:
            reader = BoundPostgresReader(connection, source=source, serde=checkpoint_serializer())
            graph = native_graph_schema(original, source=source, saver=reader)
            journal = FrameworkApprovalJournal(settings, serde=reader.serde)
            snapshots = {}
            for identity in identities:
                key = (identity.checkpoint_namespace, identity.checkpoint_id)
                config = {"configurable": {"thread_id": source.binding.thread_id,
                    "checkpoint_ns": key[0], "checkpoint_id": key[1]}}
                if key not in snapshots:
                    checkpoint = reader.get_tuple(config)
                    original.require_checkpoint(checkpoint)
                    snapshot = graph.get_state(config, subgraphs=True)
                    if (snapshot.config["configurable"].get("checkpoint_id") != key[1]
                        or snapshot.metadata.get("source") != "loop"):
                        raise FrameworkApprovalRecoveryRequired("Historical wait native checkpoint changed")
                    snapshots[key] = snapshot
                snapshot = snapshots[key]
                rows = journal.pending_task_in_transaction(native_cur, source.binding,
                    reader._storage_config(config), identity.graph_task_id)
                journal._assert_native_interrupt(identity, rows.get("__interrupt__"))
                message, _ = _source_call(snapshot.values.get("messages", []),
                    identity.tool_call_id, identity.tool_name, identity.arguments)
                if message.id != identity.source_message_id:
                    raise FrameworkApprovalRecoveryRequired("Historical wait source AI identity changed")
        finally:
            native_cur.execute("SELECT set_config('search_path', %s, true)", (path,))
    logger.info("framework_historical_wait_originals_read", extra={"task_id": source.task_id,
        "binding_id": source.binding.binding_id, "interrupt_count": len(identities),
        "scope": "historical_native_wait_only", "automatic_replay": False})


def require_pending_tool_interrupts_original(cur, settings, source, graph_input, expected):
    """Read the LATEST root, not an arbitrary historical candidate checkpoint.

    Keep BoundPostgresReader.get_interrupt_task closed: the explicit journal
    callback only reads original writes on this same connection, without an
    owner impersonation or decision-consumption permission. No new connection,
    saver setup, graph invocation, ToolJob mutation or private receipt is made.
    """
    scope = {"operation": "formal_pending_approval_originals_read"}
    phase = "original_input_validation"
    try:
        source, graph_input, expected = deepcopy((source, graph_input, expected))
        source = RuntimeCheckpointSource.model_validate(source.model_dump(mode="json")
            if isinstance(source, RuntimeCheckpointSource) else source)
        scope.update(binding_id=source.binding.binding_id, task_id=source.task_id)
        original = parse_controlled_graph_input(graph_input)
        if original.binding != source.binding:
            raise FrameworkApprovalRecoveryRequired("Pending batch input differs from its complete original binding")
        phase = "candidate_identity_validation"
        proposed = [NativeToolInterrupt.model_validate(item.model_dump(mode="json")
            if isinstance(item, NativeToolInterrupt) else item) for item in expected]
        if not proposed or len({item.interrupt_id for item in proposed}) != len(proposed):
            raise FrameworkApprovalRecoveryRequired("Pending batch requires nonempty unique native identities")
        phase = "latest_native_checkpoint_read"
        # Follow native_terminal's SQLAlchemy-managed savepoint and official
        # source-only materializer; restore search_path even on failed reads.
        with cur.postgres_driver_connection() as connection, connection.cursor(row_factory=dict_row) as native_cur:
            native_cur.execute("SELECT current_setting('search_path') AS path")
            path = native_cur.fetchone()["path"]
            native_cur.execute("SELECT set_config('search_path', %s, true)",
                (sql.Identifier(settings.deep_agents.checkpoint_schema).as_string(connection),))
            reader = BoundPostgresReader(connection, source=source, serde=checkpoint_serializer())
            graph = native_graph_schema(original, source=source, saver=reader)
            config = {"configurable": {"thread_id": source.binding.thread_id, "checkpoint_ns": ""}}
            checkpoint = reader.get_tuple(config)
            original.require_checkpoint(checkpoint)
            snapshot = graph.get_state(config, subgraphs=True)
            if (snapshot.config["configurable"].get("checkpoint_id") != checkpoint.checkpoint["id"]
                or (snapshot.metadata or {}).get("source") != "loop"):
                raise FrameworkApprovalRecoveryRequired("Pending batch has no original latest execution checkpoint")
            journal = FrameworkApprovalJournal(settings, serde=reader.serde)
            phase = "durable_pending_identity_read"
            identities = collect_pending_tool_interrupts_from_snapshot(snapshot,
                read_task=lambda task_config, task_id: journal.pending_task_in_transaction(
                    native_cur, source.binding, reader._storage_config(task_config), task_id))
            found = {item.interrupt_id: item.model_dump(mode="json") for item in identities}
            candidates = {item.interrupt_id: item.model_dump(mode="json") for item in proposed}
            phase = "complete_pending_batch_comparison"
            if approval_value_hash(found) != approval_value_hash(candidates):
                raise FrameworkApprovalRecoveryRequired("Proposed approvals differ from the complete pending native batch")
            native_cur.execute("SELECT set_config('search_path', %s, true)", (path,))
    except Exception as exc:
        logger.warning("framework_pending_approval_originals_rejected", extra={**scope,
            "error_type": type(exc).__name__, "recovery_reason": phase, "automatic_replay": False})
        if isinstance(exc, ValueError) and not isinstance(exc, FrameworkApprovalRecoveryRequired):
            raise FrameworkApprovalRecoveryRequired("Pending approval original input requires reconciliation") from exc
        raise
    logger.info("framework_pending_approval_originals_read", extra={**scope,
        "checkpoint_id": checkpoint.checkpoint["id"], "interrupt_count": len(identities),
        "scope": "native_pending_identities_only", "automatic_replay": False})
    return identities
