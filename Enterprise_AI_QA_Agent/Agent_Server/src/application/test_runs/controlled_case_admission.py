"""Atomic formal case admission on the existing PostgreSQL stores.

No upstream counterpart (checked: PostgresAgentTaskStore message admission,
RunStore.commit_approval_wait, v4 §9/§12.1): official graphs do not own the
formal RunItem attempt. Reuse its checkpoint and the existing task/outbox;
do not create a queue, claim a Worker lease or invoke the graph here.
"""
from __future__ import annotations

import asyncio
import json
import logging

from src.application.test_runs.controlled_case_preparation import prepare_controlled_case_input
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import postgres_url
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, register_runtime_binding
from src.schemas.agent_task import AgentTaskCommandV2, AgentTaskRecord
from src.schemas.controlled_case_admission import (
    CONTROLLED_CASE_ADMISSION_KEY, ControlledCaseAdmissionCheckpoint, read_admission_checkpoint,
)
from src.schemas.framework_model_call import FrameworkModelBudget


logger = logging.getLogger(__name__)


class ControlledCaseAdmission:
    """Internal, deliberately not installed in main or a public route yet.

    Caller prepares values outside locks. The original RunItem lease permits
    first admission only; an idempotent receipt is never execution authority.
    AgentTask's own attempt and the combined execution fence are later ports.
    """

    def __init__(self, *, run_store, case_store, task_store):
        self._runs, self._cases, self._tasks = run_store, case_store, task_store
        self._settings = task_store._settings
        # Compare actual connection identity (including credentials) without
        # rendering it. Unrelated Redis/MySQL/table defaults are not a reason
        # to reject explicitly supplied PostgreSQL transaction participants.
        if any(postgres_url(store._settings) != postgres_url(self._settings) for store in (run_store, case_store)):
            raise ValueError("Controlled case admission requires stores in the same PostgreSQL database configuration")

    async def submit(self, command: AgentTaskCommandV2, *, lease_token: str,
                     model_budget: FrameworkModelBudget) -> AgentTaskRecord:
        # frozen=True does not freeze nested JSON. Capture before yielding to a
        # thread, then use the strict readers rather than caller object aliases.
        original = command.case_execution_input or command.case_execution_reference
        scope = {"run_id": command.run_id, "run_item_id": command.run_item_id,
            "session_id": command.session_id, "task_id": command.task_id,
            "binding_id": command.runtime_binding.binding_id, "project_id": command.project_id,
            "request_id": command.request_id, "trace_id": command.trace_id, "turn_id": command.turn_id,
            "execution_id": original.run_item_execution_attempt_id if original is not None else None}
        audit = {"recovery_reason": "command_values"}
        try:
            command = AgentTaskCommandV2.model_validate(command.model_dump(mode="json", warnings="error"))
            budget = FrameworkModelBudget.model_validate(model_budget.model_dump(mode="json", warnings="error"))
            receipt, reused = await asyncio.to_thread(self._submit_sync, command, lease_token, budget, audit)
        except asyncio.CancelledError:
            # Cancelling to_thread's waiter does not cancel its SQL transaction.
            # Do not assert rollback; a retry must query the immutable receipt.
            logger.warning("controlled_case_admission_waiter_cancelled", extra={**scope,
                "recovery_reason": "admission_receipt_unknown"})
            raise
        except Exception as exc:
            logger.warning("controlled_case_admission_failed", extra={**scope, **audit,
                "error_type": type(exc).__name__})
            raise
        logger.info("controlled_case_admission_reused" if reused else "controlled_case_admission_committed",
            extra={**scope, "task_id": receipt.id, "artifact_id": command.case_execution_input.asset_sha256})
        return receipt

    def _submit_sync(self, command, lease_token, budget, audit):
        if command.operation != "case_execute" or command.case_execution_input is None:
            raise ValueError("Controlled case admission accepts only case_execute commands")
        asset, binding = command.case_execution_input, command.runtime_binding
        audit["recovery_reason"] = "model_originals"
        if budget.schema_version != 2:
            raise ValueError("Controlled case admission requires frozen model configuration originals")
        root = budget.root_configuration(binding.model_config_fingerprint)
        if (root is None or root.key != asset.graph_input.model_key
            or root.provider != binding.model_provider or root.transport != binding.model_transport):
            raise ValueError("Controlled case input differs from its frozen root model")
        payload = {"case_execution_input": asset.model_dump(mode="json")}
        content_hash = self._tasks._input_hash("case_execute", {"input": payload,
            "runtime_binding_fingerprint": binding.fingerprint, "model_budget": budget.model_dump(mode="json")})
        key = self._tasks._idempotency_key("case_execute", asset.run_item_execution_attempt_id)
        # Keep the existing identity-only Redis boundary. Neither Prompt,
        # messages nor formal business JSON is copied into delivery storage.
        delivery = AgentTaskCommandV2.model_validate({**command.model_dump(mode="json", exclude={"case_execution_input"}),
            "case_execution_reference": {"run_item_execution_attempt_id": asset.run_item_execution_attempt_id,
                "input_sha256": asset.asset_sha256}})
        calls = PostgresFrameworkModelCalls(self._settings)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            audit["recovery_reason"] = "original_execution_identity"
            # Existing cross-domain order: Session -> plan projection -> Item
            # -> original Attempt. No Run total row lock/promotion is needed.
            session = self._tasks._lock_session(cur, command.session_id)
            self._runs._lock_plan_projection(cur, command.run_id)
            item = self._runs._lock_item(cur, command.run_item_id)
            cur.execute(f"SELECT record FROM {self._runs._attempt_table} WHERE id=%s FOR UPDATE",
                (asset.run_item_execution_attempt_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError("Controlled case original RunItem attempt is missing")
            attempt = self._runs._attempt_from_value(row["record"])
            if ((item.run_id, attempt.run_id, attempt.run_item_id) != (command.run_id, command.run_id, item.id)
                or not lease_token or attempt.lease_token != lease_token):
                raise AgentSubmissionConflict("Controlled case original RunItem lease identity differs")
            audit["recovery_reason"] = "original_receipt"
            existing = self._tasks._find_idempotent(cur, command.session_id, key, content_hash)
            if existing is not None:
                checkpoint = read_admission_checkpoint(attempt)
                if (checkpoint.task_id != existing["id"] or checkpoint.binding != binding
                    or checkpoint.input_sha256 != asset.asset_sha256 or checkpoint.submission_sha256 != content_hash
                    or checkpoint.project_id != command.project_id
                    or any(existing[name] != getattr(command, name) for name in (
                        "operation", "session_id", "turn_id", "project_id", "run_id", "run_item_id", "reference_id"))
                    or existing["input_payload"] != payload or existing["runtime_binding"] != binding.model_dump(mode="json")
                    or calls._policy(cur, binding.binding_id) != budget):
                    raise AgentSubmissionConflict("Controlled case receipt differs from its original admission")
                return AgentTaskRecord.model_validate(existing), True
            audit["recovery_reason"] = "original_execution_reservation"
            if (attempt.checkpoint_version or attempt.checkpoint_key is not None or attempt.checkpoint_payload
                or attempt.checkpoint_at is not None or attempt.recovered_from_attempt_id is not None):
                raise AgentSubmissionConflict("RunItem already has an execution intent; reconcile the original admission")
            audit["recovery_reason"] = "dedicated_session"
            metadata = session["metadata"] or {}
            if (session["project_id"] != command.project_id
                or metadata.get("source") != "test_run_agent_execution"
                or metadata.get("run_id") != command.run_id or metadata.get("run_item_id") != item.id):
                raise AgentSubmissionConflict("Controlled case requires its original dedicated internal Session")
            if session["lease_active"] or session["status"] != "idle":
                raise AgentSubmissionConflict("Controlled case Session is busy or already started")
            for table in (self._tasks._tasks, self._settings.database.postgres_message_table,
                          self._settings.database.postgres_snapshot_table):
                cur.execute(f"SELECT id FROM {table} WHERE session_id=%s LIMIT 1", (command.session_id,))
                if cur.fetchone() is not None:
                    raise AgentSubmissionConflict("Controlled case Session already has another input or execution")
            audit["recovery_reason"] = "formal_source"
            detail = self._runs._detail_from_cursor(cur, command.run_id)
            if detail is None or detail.run.status in {"completed", "cancelled"}:
                raise AgentSubmissionConflict("Controlled case Run is no longer active")
            audit["recovery_reason"] = "claimed_plan"
            detail.run = self._runs._run_for_attempt_in_cursor(cur, detail.run, attempt)
            if session["mode_key"] != detail.run.mode_key:
                raise AgentSubmissionConflict("Controlled case Session mode differs from its formal Run")
            audit["recovery_reason"] = "formal_input"
            cur.execute(f"SELECT record FROM {self._cases._version_table} WHERE id=%s FOR SHARE", (item.case_version_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError("Controlled case formal fixed version is missing")
            version = self._cases._version_from_value(row["record"])
            original = prepare_controlled_case_input(detail=detail, item=item, version=version,
                attempt_id=attempt.id, binding=binding,
                graph_request=asset.graph_input.request(business_tools=asset.graph_input.tool_schemas),
                runtime_preparation=getattr(asset, "runtime_preparation", None),
                result_preparation=getattr(asset, "result_preparation", None),
                verification_preparation=getattr(asset, "verification_preparation", None))
            if asset.schema_version >= 2:
                state = asset.runtime_preparation.initial_state
                if (state.get("mode_key") != session["mode_key"]
                    or state.get("session_mode") != session["session_mode"]
                    or state.get("runtime_mode") != session["runtime_mode"]
                    or asset.runtime_preparation.identity.snapshot_version != 1):
                    raise AgentSubmissionConflict("Controlled runtime preparation differs from its dedicated Session")
            if original.asset_sha256 != asset.asset_sha256:
                raise AgentSubmissionConflict("Controlled case frozen input differs from its formal original")
            # These writes share the transaction and roll back on expiry. Take
            # their owner/budget locks BEFORE the final live admission check.
            audit["recovery_reason"] = "binding_and_budget"
            register_runtime_binding(cur, self._settings, binding)
            calls.freeze_submission(cur, binding, budget)
            # Use the database clock after every potentially waiting lock and
            # source check, never NOW() or a time supplied by the caller.
            audit["recovery_reason"] = "live_lease_and_deadline"
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            self._runs._validate_lease(item, lease_token, now, {"running"})
            if budget.deadline_at <= now:
                raise ValueError("Controlled case model budget deadline has expired")
            audit["recovery_reason"] = "atomic_write"
            cur.execute(f"""INSERT INTO {self._tasks._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,reference_id,parent_task_id,root_trace_id,runtime_binding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) RETURNING *""",
                (command.task_id, command.session_id, command.operation, command.turn_id, command.project_id,
                 command.request_id, command.trace_id, command.run_id, command.run_item_id, command.priority,
                 key, content_hash, json.dumps(payload, ensure_ascii=False), command.reference_id,
                 command.parent_task_id, command.root_trace_id, binding.model_dump_json()))
            task = cur.fetchone()
            cur.execute(f"INSERT INTO {self._settings.database.postgres_task_outbox_table} "
                "(event_key,stream,payload) VALUES (%s,%s,%s::jsonb)",
                (f"{command.task_type}:{command.task_id}", self._settings.orchestration.agent_task_v2_stream,
                 delivery.model_dump_json()))
            checkpoint = ControlledCaseAdmissionCheckpoint(schema_version=1, project_id=command.project_id,
                run_id=item.run_id, run_item_id=item.id, run_item_execution_attempt_id=attempt.id,
                task_id=task["id"], binding=binding, plan_snapshot_identity=asset.plan_snapshot_identity,
                input_sha256=asset.asset_sha256, submission_sha256=content_hash, admitted_at=now)
            self._runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_version": 1,
                "checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY, "checkpoint_payload": checkpoint.model_dump(mode="json"),
                "checkpoint_at": now}))
            self._tasks._event(cur, task, "test_run.case_admitted", {"run_item_execution_attempt_id": attempt.id,
                "binding_id": binding.binding_id, "input_sha256": asset.asset_sha256})
            audit["recovery_reason"] = "commit_or_receipt"
            return AgentTaskRecord.model_validate(task), False
