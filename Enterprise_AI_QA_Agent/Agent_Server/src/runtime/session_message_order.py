"""Durable message order, distinct from clocks and framework recovery authority.

No upstream counterpart (checked: session event_sequence, local Codex retained
history/rollout ordinals, v4 B/H08): physical row order cannot recover an old
unsequenced business transcript. Never fill historical ordinals on a read.
"""
from collections import defaultdict
import hashlib
import logging

from psycopg import sql

from src.infrastructure.storage_utils import ensure_utc_datetime


logger = logging.getLogger(__name__)


def initialize_message_order(cur, table_name):
    """Add a future-write default without inventing order for existing rows."""
    cur.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.oid=to_regclass(%s)", (table_name,))
    relation = cur.fetchone()
    if relation is None:
        raise ValueError("Message order requires its existing transcript table")
    table = sql.Identifier(relation["nspname"], relation["relname"])
    identity = hashlib.sha256(f"{relation['nspname']}.{relation['relname']}".encode()).hexdigest()[:24]
    sequence = sql.Identifier(relation["nspname"], "qa_message_order_" + identity)
    cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS message_sequence BIGINT NULL").format(table))
    cur.execute(sql.SQL("CREATE SEQUENCE IF NOT EXISTS {} AS BIGINT").format(sequence))
    cur.execute(sql.SQL("ALTER SEQUENCE {} OWNED BY {}.message_sequence").format(sequence, table))
    cur.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN message_sequence SET DEFAULT nextval({}::regclass)").format(
        table, sql.Literal(sequence.as_string())))
    cur.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (session_id,message_sequence)").format(
        sql.Identifier("qa_message_order_idx_" + identity), table))
    logger.info("session_message_order_initialized", extra={"message_table": table_name,
        "historical_order_inferred": False})


def message_order_backup(unsequenced_message_ids):
    return {"schema_version": 1, "unsequenced_message_ids": sorted(unsequenced_message_ids)}


def parse_message_order_backup(value, messages):
    identities = {item.id for item in messages}
    if value is None:
        # Old backup arrays do not prove how equal-clock database rows were
        # ordered before export. Preserve this unknown through restore.
        return frozenset(identities)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "unsequenced_message_ids"}
        or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Unsupported session message order backup")
    unknown = value["unsequenced_message_ids"]
    if (not isinstance(unknown, list) or any(not isinstance(item, str) or item not in identities for item in unknown)
        or len(set(unknown)) != len(unknown)):
        raise ValueError("Session message order backup has invalid identities")
    return frozenset(unknown)


def require_context_message_order(session):
    """Only new context admission needs this fact; no replay or migration here."""
    if not session.unsequenced_message_ids:
        return
    from src.application.context.transcript_hygiene_service import TranscriptHygieneService

    hygiene, clocks = TranscriptHygieneService(), defaultdict(list)
    for item in session.messages:
        if (item.role.value in {"user", "assistant", "system"} and hygiene.is_context_eligible(item)
            and item.metadata.get("delivery_status") != "queued"
            and bool(item.metadata.get("persist_transcript", True))):
            clocks[ensure_utc_datetime(item.created_at)].append(item.id)
    if any(len(identities) > 1 and session.unsequenced_message_ids.intersection(identities)
        for identities in clocks.values()):
        logger.warning("session_message_order_migration_required", extra={"session_id": session.id,
            "message_count": len(session.messages), "unsequenced_message_count": len(session.unsequenced_message_ids)})
        raise ValueError("Framework context message order requires explicit migration")


def migrate_message_order(cur, table_name, session_id, ordered_message_ids):
    """Persist an operator-supplied order for a wholly unsequenced transcript.

    The caller must provide the evidence-backed order; this function never
    derives it from clocks, UUIDs, physical rows, or existing framework assets.
    Mixed known/unknown sessions are rejected because assigning new ordinals
    could place legacy rows after newer messages and silently alter history.
    """
    if (not isinstance(ordered_message_ids, list)
        or any(not isinstance(identity, str) or not identity for identity in ordered_message_ids)
        or len(set(ordered_message_ids)) != len(ordered_message_ids)):
        raise ValueError("Explicit message order must contain unique identities")
    table = sql.Identifier(*str(table_name).split(".", 1)) if "." in str(table_name) else sql.Identifier(str(table_name))
    cur.execute(sql.SQL("SELECT id,message_sequence FROM {} WHERE session_id=%s FOR UPDATE").format(table), (session_id,))
    rows = cur.fetchall() or []
    actual = [row["id"] for row in rows]
    if set(actual) != set(ordered_message_ids) or len(actual) != len(ordered_message_ids):
        raise ValueError("Explicit message order does not match the session transcript")
    if any(row["message_sequence"] is not None for row in rows):
        raise ValueError("Explicit migration cannot rewrite an already sequenced transcript")
    cur.execute("SELECT pg_get_serial_sequence(%s, 'message_sequence') AS sequence_name", (str(table_name),))
    sequence_name = cur.fetchone()["sequence_name"]
    if not sequence_name:
        raise ValueError("Explicit migration requires the message order sequence")
    for identity in ordered_message_ids:
        cur.execute(sql.SQL("UPDATE {} SET message_sequence=nextval(%s::regclass) WHERE session_id=%s AND id=%s").format(table),
            (sequence_name, session_id, identity))
