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
SETTLEMENT_SCOPE = "controlled_case_recovery_settlement_only"


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
        candidate = active
        if candidate is None:
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE session_id=%s "
                "AND status IN ('queued','waiting_resource') AND (%s::text IS NULL OR id<>%s) "
                "ORDER BY sequence LIMIT 1", (session["id"], exclude_task_id, exclude_task_id))
            candidate = cur.fetchone()
        if candidate is not None and candidate["operation"] == "case_settle":
            return self._interrupt_settlement(cur, session, candidate, reason=reason, source=source)
        if (candidate is None and not metadata.get("pending_turn") and session["status"] == "interrupted"
            and control.get("last_interrupt_scope") == SETTLEMENT_SCOPE):
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s AND session_id=%s FOR UPDATE",
                (control.get("last_interrupt_task_id"), session["id"]))
            saved = cur.fetchone()
            if (saved is None or saved["operation"] != "case_settle" or not saved["interrupt_requested"]
                or saved["status"] not in {"cancelled", "interrupted"}
                or RuntimeBinding.model_validate(saved["runtime_binding"]).binding_id != control.get("last_interrupt_binding_id")):
                raise FrameworkApprovalRecoveryRequired("Interrupted settlement has no canonical stopped task")
            return True  # It never owned the original graph lease, including on repeat.
        if active is not None:
            bound = active["runtime_binding"]
        else:
            pending = metadata.get("pending_turn") or {}
            bound = (pending.get("context_bundle") or {}).get("runtime_binding")
            # A scoped stop records lineage, not graph revocation authority.
            # A later queued Turn must resolve its own binding, never that one.
            if (bound is None and control.get("last_interrupt_binding_id") and session["status"] == "interrupted"
                and control.get("last_interrupt_scope") != SETTLEMENT_SCOPE):
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
        control.pop("last_interrupt_scope", None)
        control.pop("last_interrupt_task_id", None)
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

    def _interrupt_settlement(self, cur, session, candidate, *, reason, source):
        """Cancel the scoped Task/Session lease, never the lost producer's owner.

        No upstream counterpart (checked: Task control, scoped settlement lease,
        v4 §10.1/§11): a settlement successor shares a binding but holds no graph
        authority. Use the existing task status kernel without owner mutation.
        """
        tasks = self._tasks
        binding = RuntimeBinding.model_validate(candidate["runtime_binding"])
        if binding.session_id != session["id"]:
            raise FrameworkApprovalRecoveryRequired("Interrupted settlement belongs to another Session")
        metadata = dict(session["metadata"] or {})
        if session["lease_active"] and metadata.get("turn_lease_turn_id") != binding.turn_id:
            raise FrameworkApprovalRecoveryRequired("Interrupted settlement differs from the current execution lease")
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE session_id=%s AND turn_id=%s "
            "AND operation='case_settle' AND status IN ('queued','waiting_resource','claimed') "
            "ORDER BY sequence FOR UPDATE", (session["id"], binding.turn_id))
        rows = list(cur.fetchall())
        for row in rows:
            if RuntimeBinding.model_validate(row["runtime_binding"]) != binding:
                raise FrameworkApprovalRecoveryRequired("Interrupted settlement tasks disagree with their binding")
            cur.execute(f"UPDATE {tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (row["id"],))
            tasks._update_task_status(cur, row, "interrupted" if row["status"] == "claimed" else "cancelled",
                None, "user_interrupted_case_settlement")
        control = dict(metadata.get("control") or {})
        control.update(control_state="interrupted", is_interrupted=True, is_resumable=False,
            preserve_resources=True, last_interrupt_reason=reason, last_control_source=source,
            last_interrupt_binding_id=binding.binding_id, last_interrupt_turn_id=binding.turn_id,
            last_interrupt_scope=SETTLEMENT_SCOPE, last_interrupt_task_id=candidate["id"])
        metadata["control"] = control
        if metadata.get("turn_lease_turn_id") == binding.turn_id:
            for key in LEASE_KEYS:
                metadata.pop(key, None)
        cur.execute(f"UPDATE {tasks._session_table} SET status='interrupted',metadata=%s::jsonb, "
            "state_revision=state_revision+1,updated_at=clock_timestamp() WHERE id=%s",
            (json.dumps(metadata, ensure_ascii=False), binding.session_id))
        tasks._event(cur, candidate, "runtime.interrupt_requested", {"source": source, "reason": reason,
            "binding_id": binding.binding_id, "owner_revoked": False, "scope": SETTLEMENT_SCOPE,
            "automatic_resume": False})
        logger.info("controlled_case_settlement_interruption_prepared", extra={"session_id": binding.session_id,
            "binding_id": binding.binding_id, "task_id": candidate["id"], "task_count": len(rows),
            "source": source, "owner_revoked": False})
        return True
