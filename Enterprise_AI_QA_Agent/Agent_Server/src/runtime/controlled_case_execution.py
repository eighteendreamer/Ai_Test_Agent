"""RunItem side of the existing task/owner transaction, not a new scheduler.

No upstream counterpart (checked: original task claim/runtime owner,
RunStore leases and v4 §8/§12.1): LangGraph does not own the formal RunItem.
Use Session -> plan projection -> Item -> original Attempt -> runtime owner;
the caller checks both live leases after all waiting locks, on one DB clock.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_runs.plan_revision_lock import lock_plan_projection
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.controlled_case_admission import ControlledCaseAdmissionCheckpoint, read_admission_checkpoint
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input
from src.schemas.runtime_binding import ControlledCaseExecutionLease
from src.schemas.agent_task import TERMINAL_AGENT_TASK_STATUSES
from src.schemas.run_management import TestRunAttemptRecord, TestRunItemRecord


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LockedCaseExecution:
    store: PostgresTestRunStore
    item: TestRunItemRecord
    attempt: TestRunAttemptRecord
    checkpoint: ControlledCaseAdmissionCheckpoint
    lease: ControlledCaseExecutionLease


def lock_case_execution(cur, settings, lease, *, task_id, binding, schema="", require_active=True):
    store = PostgresTestRunStore(settings)
    lock_plan_projection(cur, run_table=f"{schema}{store._run_table}", run_id=lease.run_id)
    cur.execute(f"SELECT record FROM {schema}{store._item_table} WHERE id=%s FOR UPDATE", (lease.run_item_id,))
    row = cur.fetchone()
    if row is None:
        raise ContinuationLeaseLostError("Controlled case RunItem is missing")
    item = store._item_from_value(row["record"])
    cur.execute(f"SELECT record FROM {schema}{store._attempt_table} WHERE id=%s FOR UPDATE",
        (lease.run_item_execution_attempt_id,))
    row = cur.fetchone()
    if row is None:
        raise ContinuationLeaseLostError("Controlled case original attempt is missing")
    attempt = store._attempt_from_value(row["record"])
    checkpoint = read_admission_checkpoint(attempt)
    cur.execute(f"SELECT record FROM {schema}{store._run_table} WHERE id=%s", (lease.run_id,))
    row = cur.fetchone()
    if (row is None
        or (item.run_id, attempt.run_id, attempt.run_item_id) != (lease.run_id, lease.run_id, item.id)
        or checkpoint.task_id != task_id or checkpoint.binding != binding
        or checkpoint.input_sha256 != lease.input_sha256):
        raise ContinuationLeaseLostError("Controlled case original admission identity differs")
    if require_active and (store._run_from_value(row["record"]).status in {"completed", "cancelled"}
        or item.status != "running" or attempt.status != "running"
        or item.attempt_no != lease.attempt_no or attempt.attempt_no != lease.attempt_no
        or item.lease_owner != lease.worker_id or attempt.worker_id != lease.worker_id
        or item.lease_token != lease.lease_token or attempt.lease_token != lease.lease_token
        or attempt.recovered_from_attempt_id is not None):
        raise ContinuationLeaseLostError("Controlled case original execution identity is no longer active")
    return LockedCaseExecution(store, item, attempt, checkpoint, lease)


def require_case_live(locked, now):
    try:
        locked.store._validate_lease(locked.item, locked.lease.lease_token, now, {"running"})
    except ValueError:
        logger.warning("controlled_case_execution_lease_rejected", extra={"run_id": locked.lease.run_id,
            "run_item_id": locked.item.id, "execution_id": locked.attempt.id,
            "authority_observed_at": now.isoformat(), "recovery_reason": "original_run_item_lease_inactive"})
        raise ContinuationLeaseLostError("Controlled case original RunItem lease is no longer active") from None


def prepare_case_claim(cur, tasks, command, capabilities):
    """Only frozen PostgreSQL originals are execution input; Redis is identity."""
    if command.operation != "case_execute" or command.case_execution_reference is None:
        raise ValueError("Controlled case claim requires its reference-only delivery")
    capabilities.require(command.runtime_binding, operation=command.operation)
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s", (command.task_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(command.task_id)
    if (any(row[name] != getattr(command, name) for name in (
        "operation", "session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "reference_id"))
        or capabilities.require_binding_format(row["runtime_binding"]) != command.runtime_binding):
        raise ValueError("Controlled case delivery differs from its durable task identity")
    payload = row["input_payload"]
    if not isinstance(payload, dict) or set(payload) != {"case_execution_input"}:
        raise ValueError("Controlled case task requires its single frozen input original")
    asset = capabilities.require_case_input_format(payload["case_execution_input"])
    reference = command.case_execution_reference
    if (reference.run_item_execution_attempt_id != asset.run_item_execution_attempt_id
        or reference.input_sha256 != asset.asset_sha256 or command.runtime_binding != asset.graph_input.binding
        or command.trace_id != asset.graph_input.trace_id
        or (command.project_id, command.run_id, command.run_item_id, command.reference_id)
        != (asset.context.project_id, asset.context.run_id, asset.context.run_item_id, asset.case_version_id)):
        raise ValueError("Controlled case delivery differs from its frozen input original")
    store = PostgresTestRunStore(tasks._settings)
    attempt = store._get_attempt_by_id(cur, reference.run_item_execution_attempt_id)
    lease = ControlledCaseExecutionLease(run_id=command.run_id, run_item_id=command.run_item_id,
        run_item_execution_attempt_id=attempt.id, attempt_no=attempt.attempt_no,
        worker_id=attempt.worker_id, lease_token=attempt.lease_token, input_sha256=asset.asset_sha256)
    locked = lock_case_execution(cur, tasks._settings, lease, task_id=command.task_id, binding=command.runtime_binding,
        require_active=row["status"] not in TERMINAL_AGENT_TASK_STATUSES)
    if (locked.checkpoint.submission_sha256 != row["input_hash"]
        or locked.checkpoint.project_id != command.project_id or locked.item.case_version_id != command.reference_id
        or locked.checkpoint.plan_snapshot_identity != asset.plan_snapshot_identity):
        raise ValueError("Controlled case task differs from its original admission checkpoint")
    return locked


def require_case_submission(cur, tasks, row, locked, now):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    policy = PostgresFrameworkModelCalls(tasks._settings)._policy(cur, locked.checkpoint.binding.binding_id)
    expected = tasks._input_hash("case_execute", {"input": row["input_payload"],
        "runtime_binding_fingerprint": locked.checkpoint.binding.fingerprint,
        "model_budget": policy.model_dump(mode="json")})
    if expected != locked.checkpoint.submission_sha256 or policy.schema_version != 2:
        raise ValueError("Controlled case input or budget differs from its original submission")
    require_case_live(locked, now)
    if policy.deadline_at <= now:
        raise ValueError("Controlled case original model deadline has expired")


def prepare_case_start(cur, tasks, row, locked, task):
    """Recheck original facts in the existing begin transaction, not a new intent.

    The Worker copy is preflight input only. Return an independently parsed
    durable original after all potentially waiting owner/task/budget locks.
    """
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.schemas.framework_approval import approval_value_hash

    try:
        payload = row["input_payload"]
        if (locked is None or row["operation"] != task.operation or task.runtime_owner.task_id != row["id"]
            or row["runtime_binding"] != task.runtime_binding.model_dump(mode="json")
            or any(row[name] != getattr(task, name) for name in (
                "session_id", "turn_id", "project_id", "run_id", "run_item_id", "reference_id", "trace_id", "worker_id"))
            or not isinstance(payload, dict) or set(payload) != {"case_execution_input"}
            or approval_value_hash(payload) != approval_value_hash(task.input)):
            raise ValueError("Controlled case start differs from its captured original input or identity")
        asset = parse_controlled_case_task_input(payload["case_execution_input"])
        if (asset.asset_sha256 != locked.lease.input_sha256
            or asset.graph_input.binding != task.runtime_binding or asset.graph_input.trace_id != row["trace_id"]
            or asset.run_item_execution_attempt_id != locked.attempt.id
            or asset.plan_snapshot_identity != locked.checkpoint.plan_snapshot_identity
            or (asset.context.project_id, asset.context.run_id, asset.context.run_item_id, asset.case_version_id)
            != (row["project_id"], row["run_id"], row["run_item_id"], row["reference_id"])
            or row["input_hash"] != locked.checkpoint.submission_sha256):
            raise ValueError("Controlled case start differs from its original admission input")
        if row["interrupt_requested"]:
            raise ValueError("Controlled case start was interrupted before execution")
        calls = PostgresFrameworkModelCalls(tasks._settings)
        cur.execute(f"SELECT binding_id FROM {calls.budgets_table} WHERE binding_id=%s FOR SHARE",
            (task.runtime_binding.binding_id,))
        # Acquiring the budget lock may have waited. Recheck BOTH leases with
        # the original assertion after it, not a caller timestamp or NOW().
        from src.runtime.postgres_runtime_owner import assert_runtime_owner

        assert_runtime_owner(cur, tasks._settings, task.runtime_owner, states=("claimed",))
        cur.execute("SELECT clock_timestamp() AS now")
        require_case_submission(cur, tasks, row, locked, cur.fetchone()["now"])
        return asset
    except (ValueError, ContinuationLeaseLostError) as exc:
        logger.warning("controlled_case_start_rejected", extra={"task_id": task.id,
            "attempt_id": task.attempt_id, "error_type": type(exc).__name__})
        raise


def lock_case_owner(cur, settings, owner, *, schema=""):
    """A stripped owner/source may not bypass the second lease check."""
    if owner.binding.runtime_kind != "langgraph" and owner.controlled_case_lease is None:
        return None
    cur.execute(f"SELECT operation FROM {schema}{settings.database.postgres_agent_task_table} WHERE id=%s", (owner.task_id,))
    row = cur.fetchone()
    is_case = row is not None and row["operation"] == "case_execute"
    if not is_case and owner.controlled_case_lease is None:
        return None  # Existing non-case direct graph harnesses keep their contract.
    if not is_case or owner.controlled_case_lease is None or owner.binding.runtime_kind != "langgraph":
        logger.warning("controlled_case_execution_owner_rejected", extra={"task_id": owner.task_id,
            "attempt_id": owner.attempt_id, "binding_id": owner.binding.binding_id,
            "recovery_reason": "captured_case_lease_missing_or_mismatched"})
        raise ContinuationLeaseLostError("Controlled case writes require both captured execution leases")
    try:
        return lock_case_execution(cur, settings, owner.controlled_case_lease,
            task_id=owner.task_id, binding=owner.binding, schema=schema)
    except (ValueError, ContinuationLeaseLostError) as exc:
        logger.warning("controlled_case_execution_owner_rejected", extra={"task_id": owner.task_id,
            "attempt_id": owner.attempt_id, "binding_id": owner.binding.binding_id,
            "execution_id": owner.controlled_case_lease.run_item_execution_attempt_id,
            "recovery_reason": "original_execution_identity_changed", "error_type": type(exc).__name__})
        raise ContinuationLeaseLostError("Controlled case original execution owner is no longer active") from None
