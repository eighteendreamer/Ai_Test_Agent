"""One formal settlement transaction; exact terminal ACK, never execution recovery.

No upstream counterpart (checked: original RunStore completion kernel, AgentTask
finish, controlled Assessment, BoundPostgresReader, v4 §9/§12.1): neither native
terminal nor Session completion settles both business owners. Compose the original
writers and keep terminal facts separate from live execution authority.
"""
import asyncio
from copy import deepcopy
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_assessment import ControlledCaseAssessments
from src.runtime.controlled_case_execution import lock_case_execution, lock_case_delivery
from src.runtime.controlled_case_result_reader import ControlledCaseResultReader
from src.runtime.controlled_case_results import require_retained_case_asset
from src.runtime.execution_context import current_framework_owner, current_graph_event_sink
from src.runtime.framework_application_projection import _require, _require_event_intent, require_application_commit
from src.runtime.framework_projection_source import ControlledCaseProjectionSource
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, assert_runtime_owner, lock_owner_row
from src.schemas.controlled_case_admission import (
    ControlledCaseAssessmentCheckpoint, ControlledCaseSettlementCheckpoint, read_result_checkpoint,
)
from src.schemas.controlled_case_settlement import ControlledCaseSettlementReceipt
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input, ControlledCaseFinalizationTaskInputAsset
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)


def _hash(value):
    return approval_value_hash(TypeAdapter(Any).dump_python(value, mode="json"))


def settlement_event(receipt, checkpoint):
    asset = checkpoint.assessment
    return ExecutionEvent(id=str(uuid5(NAMESPACE_URL, "controlled-case-settlement:" + asset.binding.fingerprint)),
        session_id=asset.binding.session_id, timestamp=receipt.settled_at, type="test_run.case_settled",
        payload={"turn_id": asset.binding.turn_id, "task_id": asset.task_id, "attempt_id": asset.attempt_id,
            "owner_epoch": asset.owner_epoch, "run_id": asset.run_id, "run_item_id": asset.run_item_id,
            "run_item_execution_attempt_id": asset.run_item_execution_attempt_id,
            "result_id": receipt.result_id, "status": asset.completion.status,
            "assessment_sha256": asset.asset_sha256, "settlement_sha256": receipt.receipt_sha256})


class ControlledCaseSettlements:
    def __init__(self, tasks):
        self._tasks = tasks
        self._assessments = ControlledCaseAssessments(tasks)

    async def complete(self, owner, *, native):
        return await self._run(owner, native, create=True)

    async def load(self, owner, *, native):
        """Read only an already proven atomic settlement; never create/repair it."""
        return await self._run(owner, native, create=False)

    async def ensure_recovery(self, delivery, *, capabilities):
        from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery

        return await ControlledCaseSettlementRecovery(self._tasks).ensure(delivery, capabilities=capabilities)

    async def acknowledge_delivery(self, delivery, *, capabilities):
        """ACK persisted originals, without acquiring/reconstructing a live owner."""
        scope = {"operation": "terminal_delivery_ack"}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope["task_id"] = command.task_id
            capabilities.require_runtime_artifact(command.runtime_binding)
            receipt = await asyncio.to_thread(self._delivery_original, command, capabilities)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_settlement_unsettled", extra={**scope, "error_type": type(exc).__name__,
                "recovery_reason": "settlement_read_unsettled", "automatic_replay": False})
            raise
        logger.info("controlled_case_settlement_acknowledged", extra={**scope,
            "result_id": receipt.result_id, "settlement_sha256": receipt.receipt_sha256, "automatic_replay": False})
        return receipt

    async def read_expired_assessment(self, delivery, *, capabilities):
        """Read the retained assessment, not a recovery/settlement grant.

        The original execution owner is deliberately not reconstructed. The
        returned asset keeps its preparation-only scope; admission/claim of
        any successor must recheck its own lineage and current facts.
        """
        scope = {"operation": "expired_assessment_read"}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope["task_id"] = command.task_id
            capabilities.require_runtime_artifact(command.runtime_binding)
            asset = await asyncio.to_thread(self._expired_assessment_original, command, capabilities)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_expired_assessment_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "expired_assessment_originals_unsettled",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_expired_assessment_read", extra={**scope,
            "run_item_id": asset.run_item_id, "assessment_sha256": asset.asset_sha256, "automatic_replay": False})
        return asset

    def _expired_assessment_original(self, command, capabilities):
        settings = self._tasks._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            _, asset = self._expired_assessment_source(cur, command, capabilities)
            return asset

    async def read_expired_memory(self, delivery, *, capabilities):
        """Reconcile derived Memory only; never grant compensation or settlement."""
        scope = {"operation": "expired_memory_read"}
        try:
            command = deepcopy(capabilities.require_command_format(delivery))
            scope["task_id"] = command.task_id
            capabilities.require_runtime_artifact(command.runtime_binding)
            result = await asyncio.to_thread(self._expired_memory_original, command, capabilities)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_expired_memory_unsettled", extra={**scope,
                "error_type": type(exc).__name__, "recovery_reason": "expired_memory_originals_unsettled",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_expired_memory_read", extra={**scope,
            "effect_id": result.effect_id, "status": result.status, "reason_code": result.reason_code,
            "automatic_replay": False})
        return result

    def _expired_memory_original(self, command, capabilities):
        from src.runtime.framework_turn_memory_projection import read_source_memory_reconciliation

        settings = self._tasks._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            source, _ = self._expired_assessment_source(cur, command, capabilities)
            return read_source_memory_reconciliation(cur, settings, source, source.owner)

    def _expired_assessment_source(self, cur, command, capabilities):
        """Verify the lost producer and all required facts in the caller's transaction."""
        from src.runtime.framework_event_completion import original_checkpoint_source
        from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

        tasks, settings = self._tasks, self._tasks._settings
        session = tasks._lock_session(cur, command.session_id)
        case, row = lock_case_delivery(cur, tasks, command, capabilities)
        checkpoint = read_result_checkpoint(case.attempt)
        _require(isinstance(checkpoint, ControlledCaseAssessmentCheckpoint)
            and not isinstance(checkpoint, ControlledCaseSettlementCheckpoint),
            "Controlled expired original assessment is missing or already settled")
        source = original_checkpoint_source(cur, settings, command.runtime_binding, row, checkpoint.assessment.owner_epoch)
        task, attempt, saved_owner = self._lock_identity_rows(cur, source)
        self._require_case_identity(case)
        _require(task["status"] == attempt["status"] == saved_owner["execution_state"] == "recovery_required"
            and task["error_code"] == attempt["error_code"] == "execution_owner_expired"
            and attempt["finished_at"] is not None and not task["interrupt_requested"],
            "Controlled assessment producer is not an expired execution original")
        case.store._lock_run(cur, case.item.run_id)
        PostgresFrameworkModelCalls(settings).require_worker_compatibility(cur, command.runtime_binding,
            capabilities, require_budget=False)
        asset = self._application_assessment_original(cur, source, None, case, session, task, checkpoint)
        return ControlledCaseProjectionSource(case, source, checkpoint), asset

    def _delivery_original(self, command, capabilities):
        from src.runtime.framework_event_completion import original_checkpoint_source
        from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

        tasks, settings = self._tasks, self._tasks._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            session = tasks._lock_session(cur, command.session_id)
            case, row = lock_case_delivery(cur, tasks, command, capabilities)
            checkpoint = read_result_checkpoint(case.attempt)
            if (isinstance(checkpoint, ControlledCaseAssessmentCheckpoint)
                and not isinstance(checkpoint, ControlledCaseSettlementCheckpoint) and case.item.result_id is not None):
                from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery
                from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement

                return ControlledCaseRecoverySettlement(ControlledCaseSettlementRecovery(tasks)).acknowledge_original_in_transaction(
                    cur, command, capabilities)
            _require(isinstance(checkpoint, ControlledCaseSettlementCheckpoint), "Controlled atomic settlement is missing")
            source = original_checkpoint_source(cur, settings, command.runtime_binding, row, checkpoint.settlement.owner_epoch)
            task, attempt, saved_owner = self._lock_identity_rows(cur, source)
            case.store._lock_run(cur, case.item.run_id)
            PostgresFrameworkModelCalls(settings).require_worker_compatibility(cur, command.runtime_binding,
                capabilities, require_budget=False)
            return self._ack(cur, source, None, case, session, task, checkpoint,
                (case, session, task, attempt, saved_owner))

    async def _run(self, owner, native, *, create):
        scope = {"task_id": getattr(owner, "task_id", None), "operation": "complete" if create else "load"}
        try:
            _require(owner is not None and owner.controlled_case_lease is not None and native is not None,
                "Controlled settlement requires its captured dual-owner identity and exact native original")
            if create:
                _require(owner == current_framework_owner() and current_graph_event_sink() is None,
                    "Controlled settlement requires its original owner after graph event drain")
            owner, native = deepcopy((owner, native))
            receipt, reused = await asyncio.to_thread(self._material, owner, native, create)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("controlled_case_settlement_unsettled", extra={**scope, "error_type": type(exc).__name__,
                "recovery_reason": "settlement_write_receipt_unknown" if create else "settlement_read_unsettled",
                "automatic_replay": False})
            raise
        logger.info("controlled_case_settlement_acknowledged" if reused else "controlled_case_settled", extra={
            **scope, "result_id": receipt.result_id, "settlement_sha256": receipt.receipt_sha256,
            "automatic_replay": False})
        return receipt

    def _lock(self, cur, owner):
        tasks, settings = self._tasks, self._tasks._settings
        session = tasks._lock_session(cur, owner.binding.session_id)
        case = lock_case_execution(cur, settings, owner.controlled_case_lease,
            task_id=owner.task_id, binding=owner.binding, require_active=False)
        task, attempt, saved_owner = self._lock_identity_rows(cur, owner)
        _require(task["lease_token"] == owner.owner_token,
            "Controlled settlement differs from its captured task lease")
        self._require_case_identity(case)
        case.store._lock_run(cur, case.item.run_id)
        return case, session, task, attempt, saved_owner

    def _require_case_identity(self, case):
        lease = case.lease
        _require((case.item.lease_token, case.attempt.lease_token, case.item.lease_owner, case.attempt.worker_id,
                case.item.attempt_no, case.attempt.attempt_no, case.attempt.recovered_from_attempt_id)
            == (lease.lease_token, lease.lease_token, lease.worker_id, lease.worker_id,
                lease.attempt_no, lease.attempt_no, None),
            "Controlled settlement differs from its original dual execution identity")

    def _lock_identity_rows(self, cur, owner):
        """Producer identity is not execution authority; terminal hashes follow."""
        tasks, settings = self._tasks, self._tasks._settings
        saved_owner = lock_owner_row(cur, settings, owner.binding)
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (owner.task_id,))
        task = cur.fetchone()
        cur.execute(f"SELECT * FROM {tasks._attempts} WHERE id=%s FOR UPDATE", (owner.attempt_id,))
        attempt = cur.fetchone()
        _require(task is not None and attempt is not None, "Controlled settlement original task/attempt is missing")
        _require((task["attempt_id"], task["worker_id"], task["operation"])
            == (owner.attempt_id, owner.worker_id, "case_execute")
            and (attempt["task_id"], attempt["worker_id"], attempt["lease_token"], attempt["attempt_no"])
            == (owner.task_id, owner.worker_id, task["lease_token"], task["attempt_no"])
            and (saved_owner["task_id"], saved_owner["attempt_id"], saved_owner["worker_id"],
                saved_owner["owner_token"], saved_owner["owner_epoch"])
            == (owner.task_id, owner.attempt_id, owner.worker_id, task["lease_token"], owner.owner_epoch),
            "Controlled settlement differs from its original dual execution identity")
        return task, attempt, saved_owner

    def _case_result_materials(self, cur, case, result_id, asset):
        """Shared formal result/RunItem/attempt/totals checks, never authority."""
        _require(case.item.result_id == result_id and case.item.status == case.attempt.status == asset.completion.status,
            "Controlled settlement case result or terminal state changed")
        cur.execute(f"SELECT * FROM {case.store._result_table} WHERE id=%s FOR SHARE", (result_id,))
        result_row = cur.fetchone()
        _require(result_row is not None, "Controlled settlement original result is missing")
        result = case.store._result_from_value(result_row["record"])
        _require((result.run_id, result.run_item_id, result.case_id, result.case_version_id, result.attempt_id,
                result.attempt_no, result.regression_source_result_id)
            == (case.item.run_id, case.item.id, case.item.case_id, case.item.case_version_id,
                case.attempt.id, case.attempt.attempt_no, case.item.regression_source_result_id)
            and result.model_dump(mode="json", include=set(type(asset.completion).model_fields))
                == asset.completion.model_dump(mode="json")
            and result.created_at == case.item.completed_at == case.attempt.completed_at,
            "Controlled settlement result differs from its original Assessment")
        cur.execute(f"SELECT * FROM {case.store._item_table} WHERE id=%s", (case.item.id,))
        item_row = dict(cur.fetchone())
        cur.execute(f"SELECT * FROM {case.store._attempt_table} WHERE id=%s", (case.attempt.id,))
        attempt_row = dict(cur.fetchone())
        # Hash all physical columns and the original schema5 preparation. Only
        # the self-referential schema6 receipt envelope is normalized away.
        record = dict(attempt_row["record"])
        payload = dict(record["checkpoint_payload"])
        payload.pop("settlement", None)
        payload["schema_version"] = 5
        attempt_row["record"] = {**record, "checkpoint_version": 5, "checkpoint_payload": payload}
        run = case.store._read_run(cur, case.item.run_id)
        cur.execute(f"SELECT status,count(*) AS n FROM {case.store._item_table} WHERE run_id=%s GROUP BY status",
            (case.item.run_id,))
        counts = {row["status"]: int(row["n"]) for row in cur.fetchall()}
        expected = {key: counts.get(key, 0) for key in run.stats.model_dump() if key != "total"}
        expected["total"] = sum(counts.values())
        _require(run.stats.model_dump() == expected, "Controlled settlement run totals differ from actual items")
        return {key + "_sha256": _hash(value) for key, value in {
            "result": dict(result_row), "item": item_row, "attempt": attempt_row}.items()}

    def _terminal_materials(self, cur, owner, locked, result_id):
        tasks, settings = self._tasks, self._tasks._settings
        case, session, task, attempt, saved_owner = locked
        checkpoint = read_result_checkpoint(case.attempt)
        asset = checkpoint.assessment
        _require(task["status"] == attempt["status"] == saved_owner["execution_state"] == "completed"
            and task["result_status"] == "completed" and not task["interrupt_requested"]
            and attempt["finished_at"] is not None and attempt["error_code"] is None and task["error_code"] is None
            and session["metadata"].get("turn_lease_token") != task["lease_token"]
            and session["metadata"].get("turn_lease_turn_id") != owner.binding.turn_id
            and case.item.result_id == result_id
            and case.item.status == case.attempt.status == asset.completion.status,
            "Controlled settlement terminal state or cleared original lease changed")
        hashes = self._case_result_materials(cur, case, result_id, asset)
        cur.execute(f"SELECT * FROM {settings.database.postgres_event_table} WHERE session_id=%s "
            "AND type='agent_task.completed' AND payload->>'task_id'=%s FOR SHARE",
            (owner.binding.session_id, owner.task_id))
        events = cur.fetchall()
        _require(len(events) == 1 and events[0]["payload"].get("execution_returned") is True
            and events[0]["payload"].get("result_status") == "completed",
            "Controlled settlement has no unique original task completion event")
        return {**hashes, **{key + "_sha256": _hash(value) for key, value in {
            "task": dict(task), "task_attempt": dict(attempt), "owner": dict(saved_owner), "task_event": dict(events[0])}.items()}}

    def _ack(self, cur, owner, native, case, session, task, checkpoint, locked):
        receipt = checkpoint.settlement
        hashes = self._terminal_materials(cur, owner, locked, receipt.result_id)
        _require(all(getattr(receipt, key) == value for key, value in hashes.items()),
            "Controlled settlement original terminal rows changed")
        self._application_assessment_original(cur, owner, native, case, session, task, checkpoint)
        event = settlement_event(receipt, checkpoint)
        cur.execute(f"SELECT * FROM {self._tasks._settings.database.postgres_event_table} WHERE id=%s FOR SHARE", (event.id,))
        row = cur.fetchone()
        _require(row is not None, "Controlled settlement has no atomic audit receipt")
        _require_event_intent(row, event)
        return receipt

    def _application_assessment_original(self, cur, owner, native, case, session, task, checkpoint):
        """Shared facts only, after the caller verifies its scoped originals.

        Both terminal ACK and lost-producer reads use the same native/result/
        application/evidence checks. Neither call grants execution authority.
        """
        original = parse_controlled_case_task_input(task["input_payload"]["case_execution_input"])
        _require(isinstance(original, ControlledCaseFinalizationTaskInputAsset),
            "Controlled settlement has no original finalization input")
        if native is None:
            from src.application.deep_agents.native_terminal import read_native_terminal_original

            proof = checkpoint.runtime_result_asset.runtime_result.state["model_response_summary"]["native_terminal"]
            native = read_native_terminal_original(cur, self._tasks._settings, owner, proof, original.graph_input)
        result = ControlledCaseResultReader(self._tasks)._read_originals_in_transaction(
            cur, owner, original, native, case, task)
        require_retained_case_asset(cur, self._tasks._settings, owner, original, checkpoint,
            FrameworkRuntimeResult.from_result(result).model_dump(mode="json"))
        source = ControlledCaseProjectionSource(case, owner, checkpoint)
        require_application_commit(cur, self._tasks._settings, session, owner.binding, source)
        return self._assessments._require_original(cur, source, original)

    def _material(self, owner, native, create):
        tasks, settings = self._tasks, self._tasks._settings
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, settings)
            locked_rows = self._lock(cur, owner)
            case, session, task, attempt, saved_owner = locked_rows
            checkpoint = read_result_checkpoint(case.attempt)
            if isinstance(checkpoint, ControlledCaseSettlementCheckpoint):
                return self._ack(cur, owner, native, case, session, task, checkpoint, locked_rows), True
            _require(create, "Controlled atomic settlement is missing")
            _require(task["status"] == attempt["status"] == "running" and not task["interrupt_requested"]
                and session["status"] == "completed", "Controlled case is not ready for its atomic settlement")
            source, original, proof = self._assessments._source(cur, owner, native)
            asset = self._assessments._require_original(cur, source, original)
            # All potentially waiting original/run/task locks precede a fresh
            # DB clock fence. No result/terminal mutation precedes this check.
            locked = assert_runtime_owner(cur, settings, owner)
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            result = locked.store._complete_locked_item_in_transaction(cur, locked.item, locked.attempt, asset.completion, now)
            tasks._update_status(cur, task, "completed", "completed", None, execution_returned=True)
            tasks._clear_lease(cur, task)
            hashes = self._terminal_materials(cur, owner, self._lock(cur, owner), result.id)
            values = TypeAdapter(Any).dump_python(dict(schema_version=1, scope="controlled_case_atomic_settlement_only",
                binding_fingerprint=owner.binding.fingerprint, task_id=owner.task_id, attempt_id=owner.attempt_id,
                owner_epoch=owner.owner_epoch, assessment_sha256=asset.asset_sha256,
                result_id=result.id, settled_at=now, **hashes), mode="json")
            receipt = ControlledCaseSettlementReceipt(**values, receipt_sha256=approval_value_hash(values))
            checkpoint = ControlledCaseSettlementCheckpoint.model_validate({**source.checkpoint.model_dump(mode="json"),
                "schema_version": 6, "settlement": receipt.model_dump(mode="json")})
            completed_attempt = locked.store._get_attempt_by_id(cur, locked.attempt.id)
            locked.store._write_attempt(cur, completed_attempt.model_copy(update={
                "checkpoint_version": 6, "checkpoint_payload": checkpoint.model_dump(mode="json")}))
            inserted = tasks._sessions.append_event_in_transaction(cur, owner.binding.session_id, settlement_event(receipt, checkpoint))
            _require(inserted, "Controlled settlement audit exists without its atomic original checkpoint")
            return receipt, False
