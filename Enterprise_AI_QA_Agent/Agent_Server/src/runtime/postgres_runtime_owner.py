"""Application execution authority, shared by task and official saver transactions.

No upstream counterpart (checked: LangGraph PostgresSaver 3.1.2, Session
leases, integration plan section 6): the framework does not own business leases.
All mutations lock Session first, then this binding's owner, before task rows.
"""
from __future__ import annotations

import json
import logging

from src.runtime.store import ContinuationLeaseLostError
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner


logger = logging.getLogger(__name__)


def runtime_owner_table(settings) -> str:
    # Derive from the task table so isolated/deployed inboxes never share owners.
    return f"{settings.database.postgres_agent_task_table}_runtime"


def configure_runtime_owner_transaction(cur, settings) -> None:
    cur.execute("SELECT set_config('lock_timeout', %s, true)",
        (f"{settings.deep_agents.checkpoint_lock_timeout_ms}ms",))
    cur.execute("SELECT set_config('statement_timeout', %s, true)",
        (f"{settings.deep_agents.checkpoint_statement_timeout_ms}ms",))


def initialize_runtime_owners(cur, settings) -> None:
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {runtime_owner_table(settings)} (
        binding_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES {settings.database.postgres_session_table}(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL, thread_id TEXT NOT NULL UNIQUE,
        runtime_binding JSONB NOT NULL,
        owner_epoch BIGINT NOT NULL DEFAULT 0,
        owner_token TEXT, task_id TEXT, attempt_id TEXT, worker_id TEXT,
        expires_at TIMESTAMPTZ, execution_state TEXT NOT NULL DEFAULT 'idle',
        UNIQUE(session_id, turn_id)
    )""")
    cur.execute(f"""SELECT DISTINCT task.runtime_binding, owner.runtime_binding AS owner_binding
        FROM {settings.database.postgres_agent_task_table} task
        LEFT JOIN {runtime_owner_table(settings)} owner ON owner.binding_id=task.runtime_binding->>'binding_id'
        WHERE task.runtime_binding IS NOT NULL""")
    pending = list(cur.fetchall())
    for row in pending:
        binding = RuntimeBinding.model_validate(row["runtime_binding"])
        if row["owner_binding"] is None:
            register_runtime_binding(cur, settings, binding)
        elif RuntimeBinding.model_validate(row["owner_binding"]) != binding:
            raise ValueError("Persisted task and runtime owner binding disagree during initialization")
    if pending:
        logger.info("framework_runtime_bindings_checked", extra={"binding_count": len(pending)})


def register_runtime_binding(cur, settings, binding: RuntimeBinding) -> None:
    cur.execute(f"""INSERT INTO {runtime_owner_table(settings)}
        (binding_id,session_id,turn_id,thread_id,runtime_binding)
        VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
        (binding.binding_id, binding.session_id, binding.turn_id, binding.thread_id,
         json.dumps(binding.model_dump(mode="json"), ensure_ascii=False)))
    cur.execute(f"SELECT runtime_binding FROM {runtime_owner_table(settings)} WHERE binding_id=%s",
        (binding.binding_id,))
    saved = cur.fetchone()
    if saved is None or RuntimeBinding.model_validate(saved["runtime_binding"]) != binding:
        raise ValueError("Turn already has a different runtime binding")


def lock_owner_row(cur, settings, binding: RuntimeBinding) -> dict:
    cur.execute(f"SELECT * FROM {runtime_owner_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (binding.binding_id,))
    row = cur.fetchone()
    if row is None or RuntimeBinding.model_validate(row["runtime_binding"]) != binding:
        raise ValueError("Agent claim runtime binding does not match the durable owner")
    return row


def assert_runtime_owner(cur, settings, owner: RuntimeExecutionOwner, *,
                         states=("running",), schema: str = "") -> None:
    """Lock before reading the clock; keep these locks through the actual write.

    The saver pool has a checkpoint-only search_path. Business tables currently
    live in public, explicitly selected by its caller; task transactions use
    their existing application search_path.
    """
    cur.execute(f"SELECT metadata FROM {schema}{settings.database.postgres_session_table} WHERE id=%s FOR UPDATE",
        (owner.binding.session_id,))
    session = cur.fetchone()
    cur.execute(f"SELECT * FROM {schema}{runtime_owner_table(settings)} WHERE binding_id=%s FOR UPDATE",
        (owner.binding.binding_id,))
    saved = cur.fetchone()
    cur.execute("SELECT clock_timestamp() AS now")
    now = cur.fetchone()["now"]
    metadata = session["metadata"] if session else {}
    # PostgreSQL parses and compares the lease after lock acquisition, avoiding
    # transaction-start NOW() and a pre-wait lease observation.
    cur.execute("SELECT %s::timestamptz > %s AS active",
        (metadata.get("turn_lease_expires_at"), now))
    active = cur.fetchone()["active"]
    if (saved is None or not active or saved["expires_at"] is None or saved["expires_at"] <= now
        or metadata.get("turn_lease_turn_id") != owner.binding.turn_id
        or metadata.get("turn_lease_token") != owner.owner_token
        or saved["owner_epoch"] != owner.owner_epoch or saved["owner_token"] != owner.owner_token
        or saved["attempt_id"] != owner.attempt_id or saved["task_id"] != owner.task_id
        or saved["worker_id"] != owner.worker_id or saved["execution_state"] not in states
        or RuntimeBinding.model_validate(saved["runtime_binding"]) != owner.binding):
        raise ContinuationLeaseLostError("Framework execution owner is no longer active")
