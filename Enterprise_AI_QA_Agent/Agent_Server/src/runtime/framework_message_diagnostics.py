"""Inspect the first message without claiming, invoking or repairing anything.

No upstream counterpart (checked: LangGraph PostgresSaver 3.1.2,
FrameworkApprovalRecovery, plan §14.2): framework state alone cannot establish
the application's pre-graph admission or external execution history.
"""
from __future__ import annotations

import logging

from psycopg import sql
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, runtime_owner_table
from src.schemas.framework_model_call import FrameworkModelBudget, FrameworkModelBudgetSnapshot, FrameworkModelCallRecord
from src.schemas.framework_message_diagnostic import (
    FrameworkMessageDiagnostic, MessageCheckpointFact, MessageInputFact, MessageModelBudgetFact,
    MessageModelCallFact, MessageModelWireFact, MessageOwnerFact, MessageProjectionFact, MessageTaskFact, MessageToolJobFact,
)
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_message_diagnostic import MessageStageFact
from src.runtime.framework_message_stages import (
    FrameworkMessageStageRecoveryRequired, message_stage_table, validate_message_stage,
)


logger = logging.getLogger(__name__)


def _table(name, schema=None):
    return sql.Identifier(*((schema, name) if schema else (name,))).as_string()


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (name,))
    return cur.fetchone()["present"]


class FrameworkMessageDiagnostics:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings

    def inspect(self, task_id: str, session_id: str) -> FrameworkMessageDiagnostic:
        try:
            report = self._inspect(task_id, session_id)
        except Exception as exc:
            logger.warning("framework_message_diagnostic_failed", extra={"task_id": task_id,
                "session_id": session_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_message_diagnosed", extra={"task_id": task_id,
            "binding_id": report.binding.binding_id, "issue_codes": report.issues,
            "model_call_count": len(report.model_calls), "checkpoint_count": len(report.checkpoints)})
        return report

    def _inspect(self, task_id, session_id):
        tasks = self._tasks
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            # Must be the first statement; all stores, including the official
            # checkpoint tables, share this snapshot and cannot mutate it.
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            configure_runtime_owner_transaction(cur, self._settings)
            cur.execute("SELECT transaction_timestamp() AS observed_at, pg_current_snapshot()::text AS snapshot_id")
            observation = cur.fetchone()
            cur.execute(f"SELECT * FROM {_table(tasks._tasks)} WHERE id=%s AND session_id=%s", (task_id, session_id))
            task = cur.fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["operation"] != "message" or task["runtime_binding"] is None:
                raise ValueError("Diagnosis requires a bound initial message task")
            binding = RuntimeBinding.model_validate(task["runtime_binding"])
            issues = []
            if binding.session_id != session_id or binding.turn_id != task["turn_id"]:
                # A corrupt root identity must not redirect a scoped read to
                # another Turn's model ledger or checkpoint namespace.
                raise ValueError("Initial message binding identity does not match the scoped task")
            cur.execute(f"SELECT status, state_revision, metadata FROM {_table(tasks._session_table)} WHERE id=%s",
                (session_id,))
            session = cur.fetchone()
            if session is None:
                raise ValueError("Initial message session is missing")
            attempt_status = None
            if task["attempt_id"]:
                cur.execute(f"SELECT status FROM {_table(tasks._attempts)} WHERE id=%s AND task_id=%s",
                    (task["attempt_id"], task_id))
                attempt = cur.fetchone()
                attempt_status = attempt["status"] if attempt else None
                if attempt is None:
                    issues.append("task_attempt_missing")
            owner = self._owner(cur, task, binding, session, observation["observed_at"], issues)
            budget, calls = self._models(cur, binding, issues)
            admission = self._input(cur, task, binding, budget.policy, issues)
            checkpoints = self._checkpoints(cur, binding, issues)
            jobs = self._jobs(cur, task, binding, issues)
            projection = self._projection(cur, task, binding, session, issues)
            stage = self._stage(cur, task, binding, issues)
            return FrameworkMessageDiagnostic(**observation, binding=binding,
                task=MessageTaskFact(id=task_id, status=task["status"], error_code=task["error_code"],
                    attempt_id=task["attempt_id"], attempt_no=task["attempt_no"], attempt_status=attempt_status,
                    interrupt_requested=task["interrupt_requested"]),
                input=admission, owner=owner, stage=stage, model_budget=budget, model_calls=calls,
                checkpoints=checkpoints, tool_jobs=jobs, projection=projection, issues=list(dict.fromkeys(issues)))

    def _stage(self, cur, task, binding, issues):
        table = _table(message_stage_table(self._settings))
        if _exists(cur, table):
            cur.execute(f"SELECT record FROM {table} WHERE task_id=%s AND binding_id=%s", (task["id"], binding.binding_id))
            row = cur.fetchone()
            if row:
                try:
                    stage = validate_message_stage(row["record"])
                except (ValidationError, FrameworkMessageStageRecoveryRequired):
                    issues.append("stage_admission_invalid")
                else:
                    if (stage.task_id == task["id"] and stage.binding_id == binding.binding_id
                        and stage.input_sha256 == task["input_hash"] and stage.user_message_id == task["user_message_id"]):
                        issues.append("stage_recovery_requires_admission" if stage.phase in {"submitted", "orchestrating", "orchestrated"}
                            else "stage_recovery_not_implemented")
                        return MessageStageFact(**stage.model_dump(include=set(MessageStageFact.model_fields)),
                            execution_task_id=stage.current_task_id, recovery_count=len(stage.recoveries))
                    issues.append("stage_admission_identity_mismatch")
        issues.append("stage_admission_unavailable")
        return None

    def _input(self, cur, task, binding, policy, issues):
        cur.execute(f"SELECT role, content, metadata FROM {_table(self._settings.database.postgres_message_table)} "
            "WHERE id=%s AND session_id=%s", (task["user_message_id"], task["session_id"]))
        message = cur.fetchone()
        payload = task["input_payload"]
        # Historical v2 submissions may have acquired their budget later.
        # Validate the two existing admission shapes, never fabricate a policy.
        matches = task["input_hash"] in {
            self._tasks._v2_input_hash(payload, binding),
            self._tasks._v2_input_hash(payload, binding, policy),
        }
        message_matches = bool(message is not None and message["role"] == "user"
            and message["metadata"].get("turn_id") == task["turn_id"]
            and isinstance(payload.get("content"), str) and message["content"] == payload["content"].strip())
        if not matches:
            issues.append("input_hash_mismatch" if policy is not None else "input_hash_unverifiable")
        if message is None:
            issues.append("user_message_missing")
        elif not message_matches:
            issues.append("user_message_mismatch")
        return MessageInputFact(payload_present=bool(payload), payload_hash=task["input_hash"],
            payload_hash_matches=matches, user_message_id=task["user_message_id"],
            user_message_present=message is not None, user_message_matches=message_matches)

    def _owner(self, cur, task, binding, session, observed_at, issues):
        table = _table(runtime_owner_table(self._settings))
        if not _exists(cur, table):
            issues.append("owner_store_missing")
            return MessageOwnerFact(present=False)
        cur.execute(f"SELECT * FROM {table} WHERE binding_id=%s", (binding.binding_id,))
        owner = cur.fetchone()
        if owner is None:
            issues.append("owner_missing")
            return MessageOwnerFact(present=False)
        if owner["runtime_binding"] != binding.model_dump(mode="json"):
            issues.append("owner_binding_mismatch")
        matches = bool(task["attempt_id"] and owner["task_id"] == task["id"]
            and owner["attempt_id"] == task["attempt_id"] and owner["owner_token"] == task["lease_token"])
        metadata = session["metadata"] or {}
        # Let PostgreSQL interpret the persisted lease exactly as the owner
        # protocol does. No FOR UPDATE or owner assertion grants authority here.
        cur.execute("SELECT %s::timestamptz > %s AS active", (metadata.get("turn_lease_expires_at"), observed_at))
        active = bool(cur.fetchone()["active"] and owner["expires_at"] and owner["expires_at"] > observed_at
            and owner["owner_token"] and metadata.get("turn_lease_token") == owner["owner_token"]
            and metadata.get("turn_lease_turn_id") == binding.turn_id)
        if task["attempt_id"] and not matches:
            issues.append("owner_origin_mismatch")
        if task["attempt_id"] and not active:
            issues.append("owner_lease_inactive")
        return MessageOwnerFact(present=True, epoch=owner["owner_epoch"], task_id=owner["task_id"],
            attempt_id=owner["attempt_id"], execution_state=owner["execution_state"],
            expires_at=owner["expires_at"], matches_origin=matches, lease_active=active)

    def _models(self, cur, binding, issues):
        ledger = PostgresFrameworkModelCalls(self._settings)
        policy, calls, rows = None, [], []
        if _exists(cur, _table(ledger.budgets_table)):
            cur.execute(f"SELECT policy FROM {_table(ledger.budgets_table)} WHERE binding_id=%s", (binding.binding_id,))
            row = cur.fetchone()
            if row is not None:
                try:
                    policy = FrameworkModelBudget.model_validate(row["policy"])
                except ValidationError:
                    issues.append("model_budget_invalid")
        else:
            issues.append("model_budget_store_missing")
        if policy is None:
            issues.append("model_budget_missing")
        calls_available = _exists(cur, _table(ledger.calls_table))
        if calls_available:
            cur.execute(f"SELECT attempt_id, record FROM {_table(ledger.calls_table)} WHERE binding_id=%s "
                "ORDER BY record->>'created_at', attempt_id", (binding.binding_id,))
            rows = cur.fetchall()
            for row in rows:
                try:
                    call = FrameworkModelCallRecord.model_validate(row["record"])
                except ValidationError:
                    issues.append("model_call_invalid")
                    continue
                if call.binding_id != binding.binding_id or call.intent.attempt_id != row["attempt_id"]:
                    issues.append("model_call_identity_mismatch")
                    continue
                calls.append(call)
        else:
            issues.append("model_call_store_missing")
        accounting = None
        if policy is not None and calls_available and len(calls) == len(rows):
            accounting = FrameworkModelBudgetSnapshot(policy=policy, calls=calls)
        budget = MessageModelBudgetFact(policy=policy, ledger_rows=len(rows), accounting_complete=accounting is not None,
            reserved_calls=accounting.reserved_calls if accounting else None,
            charged_tokens=accounting.charged_tokens if accounting else None,
            actual_tokens=accounting.actual_tokens if accounting else None)
        facts = []
        for call in calls:
            if call.phase in {"ready", "invoking", "unknown"}:
                issues.append(f"model_{call.phase}_requires_reconciliation")
            if call.usage is None:
                issues.append("model_usage_missing")
            if call.request is None:
                issues.append("model_request_asset_missing")
            if call.phase == "completed" and call.settled_response_id is None:
                issues.append("model_settled_response_missing")
            origin = call.reservation_transfers[0].predecessor if call.reservation_transfers else call
            facts.append(MessageModelCallFact(attempt_id=call.intent.attempt_id, logical_call_id=call.intent.logical_call_id,
                branch_id=call.intent.branch_id, purpose=call.intent.purpose, owner_task_id=call.owner_task_id,
                owner_attempt_id=call.owner_attempt_id, owner_epoch=call.owner_epoch, phase=call.phase,
                reservation_transfer_count=len(call.reservation_transfers),
                reservation_origin_task_id=origin.owner_task_id, reservation_origin_attempt_id=origin.owner_attempt_id,
                reservation_origin_epoch=origin.owner_epoch,
                request=call.request, request_sha256=call.intent.request_sha256, usage=call.usage,
                response_observations=call.response_observations, settled_response_id=call.settled_response_id,
                wire_observations=[MessageModelWireFact(**item.model_dump(exclude={"schema_version", "request_headers",
                    "response_headers"})) for item in call.wire_observations],
                created_at=call.created_at, started_at=call.started_at, settled_at=call.settled_at))
        return budget, facts

    def _checkpoints(self, cur, binding, issues):
        schema = self._settings.deep_agents.checkpoint_schema
        checkpoints, writes = _table("checkpoints", schema), _table("checkpoint_writes", schema)
        if not _exists(cur, checkpoints):
            issues.append("checkpoint_store_missing")
            return []
        has_writes = _exists(cur, writes)
        if not has_writes:
            issues.append("checkpoint_writes_store_missing")
        # Same physical namespace boundary as FencedPostgresSaver; do not use
        # LIKE (binding namespaces can contain wildcard characters).
        prefix = binding.checkpoint_namespace + "|"
        cur.execute(f"SELECT DISTINCT ON (checkpoint_ns) checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
            f"metadata->'step' AS step FROM {checkpoints} WHERE thread_id=%s "
            "AND (checkpoint_ns=%s OR left(checkpoint_ns, length(%s))=%s) "
            "ORDER BY checkpoint_ns, checkpoint_id DESC", (binding.thread_id, binding.checkpoint_namespace, prefix, prefix))
        rows = cur.fetchall()
        result = []
        for row in rows:
            pending = []
            if has_writes:
                cur.execute(f"SELECT task_id, idx AS index, channel FROM {writes} "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s ORDER BY task_id, idx",
                    (binding.thread_id, row["checkpoint_ns"], row["checkpoint_id"]))
                pending = [dict(item) for item in cur.fetchall()]
            result.append(MessageCheckpointFact(namespace=row["checkpoint_ns"], checkpoint_id=row["checkpoint_id"],
                parent_checkpoint_id=row["parent_checkpoint_id"], step=row["step"], pending_writes=pending))
        issues.append("checkpoint_not_resume_permission" if result else "checkpoint_missing")
        return result

    def _jobs(self, cur, task, binding, issues):
        table = _table(self._settings.database.postgres_tool_job_table)
        if not _exists(cur, table):
            issues.append("tool_job_store_missing")
            return []
        cur.execute(f"SELECT id, call_id, tool_key, status, started_at, completed_at, "
            "metadata->>'framework_binding_id' AS binding_id, output_payload <> '{}'::jsonb AS output_present "
            ", metadata->'framework_execution_owner'->>'task_id' AS execution_task_id, "
            "metadata->'framework_execution_owner'->>'attempt_id' AS execution_attempt_id, "
            "metadata->'framework_execution_owner'->'owner_epoch' AS execution_epoch "
            f"FROM {table} WHERE session_id=%s AND turn_id=%s ORDER BY id", (task["session_id"], task["turn_id"]))
        facts = []
        for row in cur.fetchall():
            values = dict(row)
            values["binding_matches"] = values.pop("binding_id") == binding.binding_id
            if not values["binding_matches"]:
                issues.append("tool_job_binding_mismatch")
            if values["status"] in {"running", "queued", "waiting_resource", "retry_requested", "resume_requested"}:
                issues.append("tool_job_requires_reconciliation")
            facts.append(MessageToolJobFact(**values))
        return facts

    def _projection(self, cur, task, binding, session, issues):
        pending = (session["metadata"] or {}).get("pending_turn") or {}
        pending_binding = (pending.get("context_bundle") or {}).get("runtime_binding")
        matches = pending_binding == binding.model_dump(mode="json") if pending_binding else None
        if pending and (pending.get("turn_id") != task["turn_id"] or matches is not True):
            issues.append("pending_turn_mismatch")
        cur.execute(f"SELECT id, stage, version, jsonb_array_length(COALESCE(graph_state->'framework_messages', '[]')) "
            f"AS framework_message_count FROM {_table(self._settings.database.postgres_snapshot_table)} "
            "WHERE session_id=%s AND graph_state->>'turn_id'=%s ORDER BY version DESC LIMIT 1",
            (task["session_id"], task["turn_id"]))
        snapshot = cur.fetchone()
        if snapshot is None:
            issues.append("turn_snapshot_missing")
        return MessageProjectionFact(session_status=session["status"], state_revision=session["state_revision"],
            pending_turn_id=pending.get("turn_id"), pending_binding_matches=matches,
            snapshot_id=snapshot["id"] if snapshot else None, snapshot_stage=snapshot["stage"] if snapshot else None,
            snapshot_version=snapshot["version"] if snapshot else None,
            framework_message_count=snapshot["framework_message_count"] if snapshot else 0)
