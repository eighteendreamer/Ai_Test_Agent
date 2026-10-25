"""Durable interruption of a bound Turn in the caller's Session transaction.

No upstream counterpart (checked: current Agent inbox/owner/saver contracts,
integration plan §5.3/6): native graph interrupts do not revoke application
execution leases. This control never declares an external effect cancelled.
"""
from __future__ import annotations

import json
import logging

from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.postgres_runtime_owner import lock_owner_row, runtime_owner_table
from src.schemas.runtime_binding import RuntimeBinding


logger = logging.getLogger(__name__)
LEASE_KEYS = ("turn_lease_turn_id", "turn_lease_owner", "turn_lease_token", "turn_lease_expires_at")


class FrameworkTaskControl:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings

    def interrupt_in_transaction(self, cur, session, *, reason, source, exclude_task_id=None):
        """Session is already locked; acquire owner before mutating task rows.

        Return False only for an unbound legacy path. Corrupt bound identities
        require reconciliation and cannot silently fall back to local control.
        """
        tasks = self._tasks
        metadata = dict(session["metadata"] or {})
        control = dict(metadata.get("control") or {})
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE session_id=%s "
            "AND status IN ('claimed','running') ORDER BY sequence LIMIT 1", (session["id"],))
        active = cur.fetchone()
        if active is not None:
            bound = active["runtime_binding"]
        else:
            pending = metadata.get("pending_turn") or {}
            bound = (pending.get("context_bundle") or {}).get("runtime_binding")
            if bound is None and control.get("last_interrupt_binding_id") and session["status"] == "interrupted":
                cur.execute(f"SELECT runtime_binding FROM {runtime_owner_table(self._settings)} "
                    "WHERE binding_id=%s AND session_id=%s", (control["last_interrupt_binding_id"], session["id"]))
                saved = cur.fetchone()
                if saved is None:
                    raise FrameworkApprovalRecoveryRequired("Interrupted Turn has no canonical runtime binding")
                bound = saved["runtime_binding"]
            if bound is None:
                cur.execute(f"SELECT runtime_binding FROM {tasks._tasks} WHERE session_id=%s "
                    "AND status IN ('queued','waiting_resource') AND (%s::text IS NULL OR id<>%s) "
                    "ORDER BY sequence LIMIT 1", (session["id"], exclude_task_id, exclude_task_id))
                queued = cur.fetchone()
                bound = queued["runtime_binding"] if queued is not None else None
        if bound is None:
            return False
        binding = RuntimeBinding.model_validate(bound)
        if binding.session_id != session["id"]:
            raise FrameworkApprovalRecoveryRequired("Interrupted binding belongs to another Session")
        owner = lock_owner_row(cur, self._settings, binding)
        if (owner["execution_state"] == "interrupted" and control.get("last_interrupt_binding_id") == binding.binding_id):
            return True
        if session["lease_active"] and metadata.get("turn_lease_turn_id") != binding.turn_id:
            raise FrameworkApprovalRecoveryRequired("Interrupted Turn differs from the current execution lease")
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE session_id=%s AND turn_id=%s "
            "AND status IN ('queued','waiting_resource','claimed','running') ORDER BY sequence FOR UPDATE",
            (binding.session_id, binding.turn_id))
        pending_tasks = list(cur.fetchall())
        for row in pending_tasks:
            if row["runtime_binding"] is None or RuntimeBinding.model_validate(row["runtime_binding"]) != binding:
                raise FrameworkApprovalRecoveryRequired("Interrupted tasks disagree with their Turn binding")
            cur.execute(f"UPDATE {tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (row["id"],))
            state = "interrupted" if row["status"] in {"claimed", "running"} else "cancelled"
            tasks._update_status(cur, row, state, None, "user_interrupted_framework_turn")
        cur.execute(f"UPDATE {runtime_owner_table(self._settings)} SET execution_state='interrupted', "
            "expires_at=clock_timestamp() WHERE binding_id=%s", (binding.binding_id,))
        control.update(control_state="interrupted", is_interrupted=True, is_resumable=False,
            preserve_resources=True, last_interrupt_reason=reason, last_control_source=source,
            last_interrupt_binding_id=binding.binding_id, last_interrupt_turn_id=binding.turn_id)
        metadata["control"] = control
        if metadata.get("turn_lease_turn_id") == binding.turn_id:
            for key in LEASE_KEYS:
                metadata.pop(key, None)
        cur.execute(f"UPDATE {tasks._session_table} SET status='interrupted', metadata=%s::jsonb, "
            "state_revision=state_revision+1, updated_at=clock_timestamp() WHERE id=%s",
            (json.dumps(metadata, ensure_ascii=False), binding.session_id))
        event_task = active or next(iter(pending_tasks), None)
        if event_task is None and owner["task_id"]:
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (owner["task_id"],))
            event_task = cur.fetchone()
        if event_task is None:
            raise FrameworkApprovalRecoveryRequired("Interrupted Turn has no auditable task identity")
        tasks._event(cur, event_task, "runtime.interrupt_requested", {
            "source": source, "reason": reason, "binding_id": binding.binding_id,
            "owner_revoked": True, "automatic_resume": False})
        logger.info("framework_execution_owner_revocation_prepared", extra={"session_id": binding.session_id,
            "binding_id": binding.binding_id, "turn_id": binding.turn_id,
            "owner_epoch": owner["owner_epoch"], "task_count": len(pending_tasks), "source": source})
        return True
