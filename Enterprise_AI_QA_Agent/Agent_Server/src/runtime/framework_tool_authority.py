"""Framework tool authority inside the actual PostgreSQL mutation transaction.

No upstream counterpart (checked: existing ToolJob CAS, runtime owner/saver,
integration plan §6.2): graph persistence cannot fence application ToolJobs.
External dispatch is admitted at a short transaction boundary; its physical
effects cannot be rolled back by revoking the coordinating Turn afterwards.
"""
from __future__ import annotations

import logging

from src.runtime.execution_context import current_framework_owner
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.store import ContinuationLeaseLostError


logger = logging.getLogger(__name__)
EXECUTION_KEY = "framework_execution_owner"


def execution_reference(owner):
    return {"binding_id": owner.binding.binding_id, "owner_epoch": owner.owner_epoch,
        "task_id": owner.task_id, "attempt_id": owner.attempt_id, "worker_id": owner.worker_id}


def assert_tool_scope(value, owner):
    bound = value["metadata"].get("framework_binding_id")
    if owner is None:
        if bound or value["metadata"].get(EXECUTION_KEY):
            raise ContinuationLeaseLostError("Bound ToolJob requires its captured framework owner")
    elif (value["session_id"] != owner.binding.session_id or value["turn_id"] != owner.binding.turn_id
        or (bound is not None and bound != owner.binding.binding_id)):
        raise ContinuationLeaseLostError("ToolJob differs from the captured framework binding")


def lock_tool_authority(cur, settings, job_id, *, candidate=None, owner=None, check_execution=True):
    """Lock Session → owner → Job, and retain all locks through the caller's write.

    Explicit owner supports already owner-bound projection transactions. Ordinary
    async storage calls inherit the captured ContextVar through asyncio.to_thread.
    Missing context cannot turn a persisted bound Job into a legacy mutation.
    """
    owner = owner or current_framework_owner()
    if owner is not None and candidate is not None:
        candidate.metadata.setdefault("framework_binding_id", owner.binding.binding_id)
    try:
        if owner is not None:
            configure_runtime_owner_transaction(cur, settings)
            assert_runtime_owner(cur, settings, owner)
        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE id=%s FOR UPDATE", (job_id,))
        saved = cur.fetchone()
        values = ([saved] if saved else []) + ([candidate.model_dump(mode="python")] if candidate else [])
        for value in values:
            assert_tool_scope(value, owner)
        if owner is not None and saved is not None and check_execution and saved["started_at"] is not None:
            if saved["metadata"].get(EXECUTION_KEY) != execution_reference(owner):
                raise ContinuationLeaseLostError("ToolJob execution belongs to a different framework attempt; reconcile it")
        if candidate is not None and saved is not None:
            if owner is not None and any(saved[key] != getattr(candidate, key)
                for key in ("session_id", "turn_id", "call_id", "tool_key", "input_payload")):
                raise ContinuationLeaseLostError("Whole-row replacement would change frozen ToolJob identity")
            if ((check_execution and saved["metadata"].get(EXECUTION_KEY) != candidate.metadata.get(EXECUTION_KEY))
                or (saved["metadata"].get("framework_binding_id") is not None
                    and saved["metadata"]["framework_binding_id"] != candidate.metadata.get("framework_binding_id"))):
                raise ContinuationLeaseLostError("Whole-row replacement would change ToolJob execution identity")
        if owner is not None and candidate is not None:
            candidate.metadata["framework_binding_id"] = owner.binding.binding_id
        return saved
    except ContinuationLeaseLostError:
        logger.warning("framework_tool_write_rejected", extra={"tool_job_id": job_id,
            "binding_id": owner.binding.binding_id if owner else None,
            "owner_epoch": owner.owner_epoch if owner else None})
        raise
