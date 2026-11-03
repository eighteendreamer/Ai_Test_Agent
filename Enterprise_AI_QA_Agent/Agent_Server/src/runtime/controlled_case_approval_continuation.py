"""Dual-owned formal approval continuation and single native decision delivery.

No upstream counterpart (checked: Task claim/owner kernels, RunStore resume,
formal admission and v4 §7.3/§12.1): the native graph cannot adopt a RunItem.
Keep the original reservation and logical Attempt; rotate live capabilities
in the same transaction as the new physical AgentAttempt. No replay/dispatch.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import logging
from uuid import uuid4

from sqlalchemy.exc import DBAPIError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_execution import lock_case_delivery, require_case_deadline
from src.runtime.controlled_case_approval_submission import ENVELOPE
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_approval_continuation import FrameworkApprovalContinuation
from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row
from src.runtime.task_deferred import TaskDeferred
from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY
from src.schemas.runtime_binding import ControlledCaseExecutionLease
from src.schemas.session import ApprovalDecisionRequest


logger = logging.getLogger(__name__)


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def require_owner_admission(row, owner):
    """Resolve reservation identity from durable input, never a caller's hint.

    This is a live fence component, not a replacement for full admission reads
    at claim/start. Changing the physical producer must not change the root.
    """
    raw, binding, lease = row["input_payload"], owner.binding, owner.controlled_case_lease
    _require(isinstance(raw, dict) and set(raw) == {"controlled_case_approval_admission"},
        "Controlled approval owner has no single original admission")
    proof = ControlledCaseApprovalAdmission.model_validate(raw["controlled_case_approval_admission"])
    _require(lease is not None and (row["id"], row["parent_task_id"], row["reference_id"], row["run_id"], row["run_item_id"])
        == (proof.task_id, proof.origin_task_id, proof.approval_id, lease.run_id, lease.run_item_id)
        and proof.run_item_execution_attempt_id == lease.run_item_execution_attempt_id
        and proof.input_sha256 == lease.input_sha256 and proof.binding_fingerprint == binding.fingerprint
        and row["runtime_binding"] == binding.model_dump(mode="json")
        and row["input_hash"] == PostgresAgentTaskStore._input_hash("case_approval",
            {"input": raw, "runtime_binding_fingerprint": binding.fingerprint}),
        "Controlled approval owner differs from its original reservation")
    return proof.origin_task_id


@dataclass(frozen=True)
class LockedApprovalContinuation:
    case: object
    origin_command: object
    origin: dict
    session: dict
    owner: dict
    wait: object
    task: dict
    admission: ControlledCaseApprovalAdmission
    receipt: object


class ControlledCaseApprovalContinuations:
    def __init__(self, submission):
        self._submission, self._tasks = submission, submission._tasks
        self._settings = self._tasks._settings

    async def claim(self, delivery, *, worker_id, capabilities):
        command = deepcopy(capabilities.require_command_format(delivery))
        _require(command.operation == "case_approval", "Formal approval claim requires its dedicated delivery")
        capabilities.require_runtime_artifact(command.runtime_binding)
        if not capabilities.controlled_case_approval_claim_available:
            raise TaskDeferred("framework_case_approval_claim_unavailable")
        return await self._run("claim", self._claim, command.task_id, command.runtime_binding,
            command, worker_id, capabilities)

    async def begin(self, task, *, resource_id, fencing_token, capabilities):
        task = task.model_copy(deep=True)
        _require(task.operation == "case_approval", "Formal approval begin requires its dedicated captured claim")
        capabilities.require_runtime_artifact(task.runtime_binding)
        if not capabilities.controlled_case_approval_claim_available:
            raise TaskDeferred("framework_case_approval_claim_unavailable")
        def begin():
            return self._tasks._transition_sync(task, "running", None, None, resource_id, fencing_token,
                case_approval_preparer=lambda cur, row, case, captured:
                    self._prepare_begin(cur, row, case, captured, capabilities))
        return await self._run("begin", begin, task.id, task.runtime_binding)

    def require_delivery_available(self, capabilities):
        if capabilities is None or not capabilities.controlled_case_approval_delivery_available:
            raise TaskDeferred("framework_case_approval_delivery_unavailable")
        if not capabilities.controlled_case_approval_claim_available:
            raise TaskDeferred("framework_case_approval_claim_unavailable")

    def _capture_delivery(self, task, capabilities):
        task = task.model_copy(deep=True)
        self.require_delivery_available(capabilities)
        owner = task.runtime_owner
        _require(task.operation == "case_approval" and owner is not None and owner.controlled_case_lease is not None
            and owner.binding == task.runtime_binding and owner.task_id == task.id
            and owner.attempt_id == task.attempt_id and owner.owner_token == task.lease_token
            and owner.worker_id == task.worker_id,
            "Formal decision delivery requires its captured case owner")
        capabilities.require_runtime_artifact(task.runtime_binding)
        return task

    async def prepare_delivery(self, task, *, capabilities):
        """Admit exactly one first send, never replay a sending/unknown intent."""
        task = self._capture_delivery(task, capabilities)
        return await self._run("prepare_delivery", self._prepare_delivery, task.id, task.runtime_binding, task, capabilities)

    async def reconcile_delivery(self, task, *, capabilities):
        """Read/project consumed facts only, without graph execution authority."""
        task = self._capture_delivery(task, capabilities)
        return await self._run("reconcile_delivery", self._reconcile_delivery, task.id, task.runtime_binding, task, capabilities)

    async def apply_decision(self, task, *, capabilities, rejection_projector=None):
        """Apply one known native tool outcome, not the formal case result.

        A refusal projector is pure synchronous application policy, as in the
        ordinary approval port; it cannot execute, do I/O or authorize replay.
        Full waiting-state/ordinal persistence remains a separate admission.
        """
        task = self._capture_delivery(task, capabilities)
        return await self._run("apply_decision", self._apply_decision, task.id, task.runtime_binding,
            task, capabilities, rejection_projector)

    async def read_tool_projection(self, task, *, capabilities):
        """Read exact applied material; neither regenerate it nor grant replay."""
        task = self._capture_delivery(task, capabilities)
        return await self._run("read_tool_projection", self._read_tool_projection, task.id, task.runtime_binding,
            task, capabilities)

    async def prepare_runtime(self, task, *, capabilities):
        """Read claimed original application material, before committing running.

        This is not begin/resume authority. The existing dedicated begin and
        prepare_delivery must still recheck the same captured identities.
        """
        task = self._capture_delivery(task, capabilities)
        return await self._run("prepare_runtime", self._prepare_runtime, task.id, task.runtime_binding,
            task, capabilities)

    def _prepare_runtime(self, task, capabilities):
        from src.schemas.controlled_case_admission import ControlledCaseApprovalStateCheckpoint

        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._captured_originals(cur, task, capabilities, states=("claimed",), session_state="waiting_approval")
            _require(saved.wait.schema_version == 2 and saved.case.attempt.checkpoint_version == 7,
                "Controlled runtime requires its frozen full application preparation and waiting slots")
            # _locked already verified this exact state/receipt and every
            # historical original on this connection, holding their row locks.
            asset = ControlledCaseApprovalStateCheckpoint.model_validate(saved.case.attempt.checkpoint_payload).state_asset
            prepared = capabilities.require_case_input_format(saved.origin["input_payload"]["case_execution_input"])
            assert_runtime_owner(cur, self._settings, task.runtime_owner, states=("claimed",))
            return prepared, asset

    def _captured_originals(self, cur, task, capabilities, *, states=("running",), session_state="running"):
        assert_runtime_owner(cur, self._settings, task.runtime_owner, states=states)
        saved = self._locked(cur, self._wire(cur, task.id, capabilities), capabilities)
        row = saved.task
        _require(task.operation == row["operation"] == "case_approval"
            and task.input == row["input_payload"]
            and all(row[name] == getattr(task, name) for name in (*ENVELOPE, "parent_task_id", "reference_id", "priority"))
            and row["runtime_binding"] == task.runtime_binding.model_dump(mode="json")
            and row["status"] in states
            and (row["attempt_id"], row["lease_token"], row["worker_id"])
                == (task.attempt_id, task.lease_token, task.worker_id)
            and saved.session["status"] == session_state and saved.session["lease_active"]
            and saved.session["project_id"] == row["project_id"],
            "Formal decision delivery differs from its captured running task")
        return saved

    def _delivery_originals(self, cur, task, capabilities):
        return self._captured_originals(cur, task, capabilities)

    def _decision_reader(self):
        return FrameworkApprovalContinuation(self._settings, sessions=self._tasks._sessions,
            jobs=self._submission._waits._projection._jobs)

    def _inspect_decision(self, cur, task, saved, shared):
        return shared.inspect_decision_originals_in_transaction(cur, task.runtime_binding,
            approval_id=saved.admission.approval_id, decision_id=saved.admission.decision_id,
            decision_sha256=saved.admission.decision_sha256)

    def _reconcile_delivery(self, task, capabilities):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._delivery_originals(cur, task, capabilities)
            shared = self._decision_reader()
            plan, consumed = self._inspect_decision(cur, task, saved, shared)
            _require(consumed, "Formal decision is not durably consumed; delivery remains unknown")
            receipt = shared.reconcile_delivery_in_transaction(cur, task.runtime_owner, plan, saved.task)
            # Historical delivery is not a fresh execution admission: neither
            # current complete pending nor the old budget grants replay rights.
            assert_runtime_owner(cur, self._settings, task.runtime_owner)
            return plan.model_copy(update={"receipt": receipt})

    def _apply_decision(self, task, capabilities, rejection_projector):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._delivery_originals(cur, task, capabilities)
            shared = self._decision_reader()
            plan, consumed = self._inspect_decision(cur, task, saved, shared)
            _require(consumed, "Formal decision is not durably consumed; application remains unknown")
            if plan.approval.status.value == "denied":
                material = (plan.receipt.proof or {}).get("application", {}).get(TOOL_PROJECTION_KEY)
                _require(rejection_projector is not None or (plan.receipt.applied_at is not None and material is not None),
                    "Formal refusal requires its explicit pure application projector or original applied material")
            receipt = shared.reconcile_delivery_in_transaction(cur, task.runtime_owner, plan, saved.task)
            result = shared.apply_decision_in_transaction(cur, task.runtime_owner,
                plan.model_copy(update={"receipt": receipt}), rejection_projector=rejection_projector)
            # Formal application must retain readable original material before
            # committing; the ordinary historical path keeps its compatibility.
            shared._applied_tool_projection_in_transaction(cur, task.runtime_binding, result, consumed)
            assert_runtime_owner(cur, self._settings, task.runtime_owner)
            return result

    def _read_tool_projection(self, task, capabilities):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._delivery_originals(cur, task, capabilities)
            shared = self._decision_reader()
            plan, consumed = self._inspect_decision(cur, task, saved, shared)
            material = shared._applied_tool_projection_in_transaction(cur, task.runtime_binding, plan, consumed)
            assert_runtime_owner(cur, self._settings, task.runtime_owner)
            return material

    def _prepare_delivery(self, task, capabilities):
        tasks = self._tasks
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._delivery_originals(cur, task, capabilities)
            row = saved.task
            _require(saved.receipt.state == "queued" and saved.receipt.proof is None
                and saved.receipt.delivered_at is saved.receipt.applied_at is None,
                "Formal decision already sending or delivered; reconcile instead of replay")
            self._submission.require_pending_originals_in_transaction(cur, saved.origin_command, saved.case,
                saved.session, saved.owner, saved.wait, saved.origin, capabilities)
            plan, consumed = self._inspect_decision(cur, task, saved, self._decision_reader())
            _require(not consumed, "Formal decision already consumed; reconcile instead of replay")
            self._deadline(cur, task.runtime_binding)
            receipt = self._submission._journal.mark_sending_in_transaction(cur, plan.approval.id)
            tasks._event(cur, row, "test_run.case_approval_sending", {
                "origin_task_id": saved.origin["id"], "execution_id": saved.case.attempt.id,
                "approval_id": saved.admission.approval_id, "decision_id": saved.admission.decision_id,
                "admission_sha256": saved.admission.admission_sha256,
                "interrupt_id": plan.link.interrupt_id, "automatic_replay": False})
            assert_runtime_owner(cur, self._settings, task.runtime_owner)
            self._deadline(cur, task.runtime_binding)
            return plan.model_copy(update={"receipt": receipt})

    async def _run(self, operation, function, task_id, binding, *args):
        scope = {"operation": operation, "task_id": task_id, "binding_id": binding.binding_id, "automatic_replay": False}
        try:
            result = await asyncio.to_thread(function, *args)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                logger.warning("controlled_case_approval_owner_lock_busy", extra=scope)
                raise TaskDeferred("Formal approval owner lock is busy") from exc
            logger.warning("controlled_case_approval_transaction_unsettled", extra={**scope, "error_type": type(exc).__name__})
            raise
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_approval_transaction_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "approval_owner_receipt_unknown"})
            raise
        logger.info("controlled_case_approval_" + operation + "_observed", extra=scope)
        return result

    def _wire(self, cur, task_id, capabilities):
        cur.execute(f"SELECT payload FROM {self._settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + task_id,))
        outbox = cur.fetchone()
        _require(outbox is not None, "Formal approval has no original outbox")
        return capabilities.require_command_format(outbox["payload"])

    def _locked(self, cur, command, capabilities):
        tasks, submission, binding = self._tasks, self._submission, command.runtime_binding
        session = tasks._lock_session(cur, command.session_id)
        original = self._wire(cur, command.case_approval_reference.origin_task_id, capabilities)
        case, origin = lock_case_delivery(cur, tasks, original, capabilities)
        _require(original.runtime_binding == binding, "Formal approval binding differs from its original producer")
        owner = lock_owner_row(cur, self._settings, binding)
        wait = submission._waits.require_original_in_transaction(cur, original, capabilities, {})
        _require(command.reference_id in {entry.approval_id for entry in wait.batch},
            "Formal approval is outside its original complete batch")
        cur.execute(f"SELECT * FROM {self._settings.database.postgres_approval_table} WHERE id=%s FOR UPDATE",
            (command.reference_id,))
        approval = cur.fetchone()
        submission._journal.require_link_in_transaction(cur, binding, command.reference_id, approval)
        receipt = submission._journal.get_receipt_in_transaction(cur, command.reference_id)
        if receipt is not None:
            capabilities.require_approval_journal_format("receipt", receipt.model_dump(mode="json"))
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (command.task_id,))
        row = cur.fetchone()
        _require(row is not None, "Formal approval successor is missing")
        submission._existing(cur, row, origin, binding, wait, approval, receipt,
            ApprovalDecisionRequest(decision=approval["status"]), capabilities,
            tasks._idempotency_key("case_approval", command.task_id))
        proof = capabilities.require_case_approval_admission_format(row["input_payload"]["controlled_case_approval_admission"])
        _require(command == submission._command(proof, origin, binding), "Formal approval delivery differs from its exact admission")
        return LockedApprovalContinuation(case, original, origin, session, owner, wait, row, proof, receipt)

    def _deadline(self, cur, binding):
        return require_case_deadline(cur, self._settings, binding)

    def _claim(self, command, worker_id, capabilities):
        _require(isinstance(worker_id, str) and bool(worker_id.strip()), "Formal approval claim requires a worker identity")
        tasks = self._tasks
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            saved = self._locked(cur, command, capabilities)
            row, case = saved.task, saved.case
            if row["status"] != "queued" or row["attempt_no"] != 0 or saved.session["lease_active"] \
                or saved.owner["execution_state"] in {"claimed", "running"}:
                raise TaskDeferred("Formal approval continuation already owned or requires reconciliation")
            _require(row["result_payload"] is row["attempt_id"] is row["error_code"] is None
                and not row["interrupt_requested"] and case.attempt.checkpoint_version in {1, 7}
                and saved.receipt.state == "queued" and saved.receipt.proof is None
                and saved.receipt.delivered_at is saved.receipt.applied_at is None,
                "Formal approval has prior execution or native delivery requiring reconciliation")
            self._submission._require_parked(cur, saved.origin_command, case, saved.session,
                saved.owner, saved.wait, saved.origin, capabilities)
            cur.execute(f"SELECT id FROM {tasks._tasks} WHERE session_id=%s AND sequence<%s "
                "AND status IN ('queued','waiting_resource','claimed','running') "
                "AND NOT (operation='message' AND turn_id<>%s AND status IN ('queued','waiting_resource')) LIMIT 1",
                (command.session_id, row["sequence"], command.turn_id))
            if cur.fetchone() is not None:
                raise TaskDeferred("An earlier formal approval continuation is pending")
            self._deadline(cur, command.runtime_binding)
            row = tasks._claim_task_attempt(cur, row, worker_id)
            cur.execute(f"SELECT (metadata->>'turn_lease_expires_at')::timestamptz AS expires_at,clock_timestamp() AS now "
                f"FROM {tasks._session_table} WHERE id=%s", (command.session_id,))
            clock = cur.fetchone()
            token = uuid4().hex
            item, attempt = case.store._resume_waiting_approval_in_transaction(cur,
                case.item.model_copy(update={"lease_owner": worker_id, "lease_token": token}),
                case.attempt.model_copy(update={"worker_id": worker_id, "lease_token": token}), clock["expires_at"], clock["now"])
            lease = ControlledCaseExecutionLease(run_id=item.run_id, run_item_id=item.id,
                run_item_execution_attempt_id=attempt.id, attempt_no=attempt.attempt_no,
                worker_id=worker_id, lease_token=token, input_sha256=case.checkpoint.input_sha256)
            row = tasks._adopt_runtime_owner(cur, row, command.runtime_binding, worker_id, case_lease=lease)
            tasks._event(cur, row, "agent_task.claimed", {"binding_id": command.runtime_binding.binding_id,
                "owner_epoch": row["runtime_owner"].owner_epoch})
            tasks._event(cur, row, "test_run.case_approval_claimed", {"origin_task_id": saved.origin["id"],
                "origin_attempt_id": saved.wait.attempt_id, "execution_id": attempt.id,
                "admission_sha256": saved.admission.admission_sha256, "wait_sha256": saved.wait.receipt_sha256,
                "previous_case_lease_sha256": approval_value_hash(case.lease.model_dump(mode="json")),
                "case_lease_sha256": approval_value_hash(lease.model_dump(mode="json"))})
            assert_runtime_owner(cur, self._settings, row["runtime_owner"], states=("claimed",))
            self._deadline(cur, command.runtime_binding)
            return tasks._record(row)

    def _prepare_begin(self, cur, row, case, task, capabilities):
        saved = self._locked(cur, self._wire(cur, task.id, capabilities), capabilities)
        _require(task.input == row["input_payload"] == saved.task["input_payload"]
            and all(row[name] == getattr(task, name) for name in (*ENVELOPE, "parent_task_id", "reference_id", "priority"))
            and row["runtime_binding"] == task.runtime_binding.model_dump(mode="json")
            and task.runtime_owner.binding == task.runtime_binding
            and row["worker_id"] == task.worker_id and case.lease == task.runtime_owner.controlled_case_lease
            and saved.task["status"] == case.item.status == case.attempt.status == "claimed"
            and saved.session["status"] == "waiting_approval" and saved.session["lease_active"]
            and saved.receipt.state == "queued" and saved.receipt.proof is None
            and saved.receipt.delivered_at is saved.receipt.applied_at is None,
            "Formal approval start differs from its original captured claim or queued decision")
        self._submission.require_pending_originals_in_transaction(cur, saved.origin_command, case, saved.session,
            saved.owner, saved.wait, saved.origin, capabilities)
        assert_runtime_owner(cur, self._settings, task.runtime_owner, states=("claimed",))
        now = self._deadline(cur, task.runtime_binding)
        case.store._start_claimed_item_in_transaction(cur, case.item, case.attempt, now)
        return capabilities.require_case_input_format(saved.origin["input_payload"]["case_execution_input"])
