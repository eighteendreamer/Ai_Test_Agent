"""Agent input inbox, execution attempts and Outbox in PostgreSQL transactions.

Uses the same session row lock and lease as PostgresSessionStore. Redis carries
only command identity; the input and execution outcome remain database facts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import (
    AgentTaskCommand,
    AgentTaskCommandV2,
    AgentTaskExecution,
    AgentTaskRecord,
    TERMINAL_AGENT_TASK_STATUSES,
)
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner
from src.runtime.postgres_runtime_owner import (
    assert_runtime_owner, initialize_runtime_owners, lock_owner_row,
    register_runtime_binding, runtime_owner_table, configure_runtime_owner_transaction,
)
from src.runtime.framework_approval_journal import initialize_framework_approvals
from src.schemas.framework_model_call import FrameworkModelBudget
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ExecutionEvent,
    ResumeSessionRequest,
    SendMessageRequest,
    ToolApprovalRequest,
    ToolApprovalStatus,
)

logger = logging.getLogger(__name__)


class AgentSubmissionConflict(ValueError):
    pass


class PostgresAgentTaskStore:
    MAX_COORDINATOR_DEPTH = 5

    def __init__(self, settings, session_store) -> None:
        self._settings = settings
        self._sessions = session_store
        self._tasks = settings.database.postgres_agent_task_table
        self._attempts = settings.database.postgres_agent_attempt_table
        self._session_table = settings.database.postgres_session_table

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    async def submit(self, command: AgentTaskCommand, payload: SendMessageRequest,
                     message: ChatMessage, *, idempotency_key: str) -> AgentTaskRecord:
        return await asyncio.to_thread(
            self._submit_sync,
            command,
            payload,
            message,
            idempotency_key,
        )

    async def submit_v2(
        self,
        command: AgentTaskCommandV2,
        payload: SendMessageRequest,
        message: ChatMessage,
        *,
        idempotency_key: str,
        model_budget: FrameworkModelBudget | None = None,
    ) -> AgentTaskRecord:
        """Persist task, binding and an explicitly supplied budget atomically.

        Budgetless historical callers retain their existing storage contract;
        the production Worker defers graph execution until a budget exists.
        """
        return await asyncio.to_thread(
            self._submit_v2_sync,
            command,
            payload,
            message,
            idempotency_key,
            model_budget,
        )

    async def submit_resume(
        self,
        command: AgentTaskCommand,
        payload: ResumeSessionRequest,
        *,
        idempotency_key: str,
    ) -> AgentTaskRecord:
        return await asyncio.to_thread(
            self._submit_resume_sync,
            command,
            payload,
            idempotency_key,
        )

    async def submit_approval(
        self,
        command: AgentTaskCommand,
        approval_id: str,
        payload: ApprovalDecisionRequest,
        *,
        idempotency_key: str,
    ) -> AgentTaskRecord:
        return await asyncio.to_thread(
            self._submit_approval_sync,
            command,
            approval_id,
            payload,
            idempotency_key,
        )

    async def submit_new_session(
        self,
        session,
        created_event: ExecutionEvent,
        command: AgentTaskCommand,
        payload: SendMessageRequest,
        message: ChatMessage,
        *,
        idempotency_key: str,
        submission_payload: dict,
    ) -> AgentTaskRecord:
        return await asyncio.to_thread(
            self._submit_new_session_sync,
            session,
            created_event,
            command,
            payload,
            message,
            idempotency_key,
            submission_payload,
        )

    async def get_approval_runtime_binding(self, session_id: str, approval_id: str) -> RuntimeBinding | None:
        from src.runtime.framework_approval_submission import FrameworkApprovalSubmission

        return await asyncio.to_thread(FrameworkApprovalSubmission(self).binding_for, session_id, approval_id)

    async def list_framework_approval_progress(self, session_id: str, turn_id: str):
        from src.runtime.framework_approval_read_model import FrameworkApprovalReadModel

        return await asyncio.to_thread(FrameworkApprovalReadModel(self._settings).list_turn, session_id, turn_id)

    async def submit_approval_v2(self, command: AgentTaskCommandV2, approval_id: str,
                                 payload: ApprovalDecisionRequest) -> AgentTaskRecord:
        """One continuation per approval, independent of the caller's click ID."""
        from src.runtime.framework_approval_submission import FrameworkApprovalSubmission

        return await asyncio.to_thread(FrameworkApprovalSubmission(self).submit, command, approval_id, payload)

    async def get(self, task_id: str, *, framework_capabilities=None) -> AgentTaskExecution:
        return await asyncio.to_thread(self._get_sync, task_id, framework_capabilities)

    async def get_framework_worker_activity(self, task_id: str, *, session_id: str):
        """Read observed attempt boundaries in one snapshot; never grant execution."""
        from src.runtime.framework_worker_activity import ACTIVITY_KEY, read_worker_activity

        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s AND session_id=%s", (task_id, session_id))
                task = cur.fetchone()
                if task is None:
                    raise KeyError(task_id)
                if task["runtime_binding"] is None:
                    return {}
                cur.execute(f"SELECT id,worker_id,status FROM {self._attempts} WHERE task_id=%s ORDER BY attempt_no",
                    (task_id,))
                attempts = cur.fetchall()
                cur.execute(f"SELECT * FROM {self._settings.database.postgres_event_table} "
                    "WHERE session_id=%s AND payload->>'task_id'=%s AND payload ? %s ORDER BY event_sequence",
                    (session_id, task_id, ACTIVITY_KEY))
                return read_worker_activity(task, attempts, cur.fetchall())

        try:
            return await asyncio.to_thread(read)
        except ValueError as exc:
            logger.warning("framework_worker_activity_read_rejected", extra={
                "task_id": task_id, "session_id": session_id, "error_type": type(exc).__name__})
            raise

    async def ensure_framework_reconciliation(self, task_id):
        from src.runtime.framework_approval_recovery import FrameworkApprovalRecovery

        return await asyncio.to_thread(FrameworkApprovalRecovery(self).ensure, task_id)

    async def diagnose_framework_message(self, task_id: str, *, session_id: str):
        """Read recovery facts only; callers must supply their scoped Session."""
        from src.runtime.framework_message_diagnostics import FrameworkMessageDiagnostics

        return await asyncio.to_thread(FrameworkMessageDiagnostics(self).inspect, task_id, session_id)

    async def get_framework_message_stage(self, task_id: str, *, session_id: str):
        from src.runtime.framework_message_stages import FrameworkMessageStages

        return await asyncio.to_thread(FrameworkMessageStages(self).get, task_id, session_id)

    async def get_framework_message_stage_version(self, task_id: str, *, session_id: str):
        from src.runtime.framework_message_stages import FrameworkMessageStages

        return await asyncio.to_thread(FrameworkMessageStages(self).get_version, task_id, session_id)

    async def ensure_framework_message_recovery(self, task_id: str, *, native_reader=None):
        from src.runtime.framework_message_recovery import FrameworkMessageRecovery

        return await FrameworkMessageRecovery(self).ensure(task_id, native_reader=native_reader)

    async def list_session(
        self,
        session_id: str,
        *,
        pending_only: bool = False,
        limit: int = 100,
    ) -> list[AgentTaskExecution]:
        return await asyncio.to_thread(self._list_sync, session_id, pending_only, limit)

    async def list_children(self, parent_task_id: str) -> list[AgentTaskRecord]:
        return await asyncio.to_thread(self._list_children_sync, parent_task_id)

    async def list_settled_children_needing_resume(self, *, limit: int = 50) -> list[AgentTaskExecution]:
        return await asyncio.to_thread(self._list_settled_children_needing_resume_sync, limit)

    async def set_result_payload(
        self,
        task_id: str,
        *,
        attempt_id: str | None,
        fencing_token: int | None,
        payload: dict,
    ) -> bool:
        return await asyncio.to_thread(
            self._set_result_payload_sync,
            task_id,
            attempt_id,
            fencing_token,
            payload,
        )

    async def cancel_unstarted_children(self, parent_task_id: str) -> int:
        return await asyncio.to_thread(self._cancel_unstarted_children_sync, parent_task_id)

    async def reap_expired_children(
        self,
        *,
        now: datetime,
        timeout_seconds: int,
    ) -> list[dict]:
        """Reap stalled claimed work; queue wait and live leases are not timeout."""
        return await asyncio.to_thread(self._reap_expired_children_sync, now, timeout_seconds)

    async def claim(self, task_id: str, *, worker_id: str,
                    expected_binding: RuntimeBinding | None = None,
                    framework_capabilities=None, check_model_ledger=False) -> AgentTaskExecution:
        try:
            return await asyncio.to_thread(self._claim_sync, task_id, worker_id, expected_binding,
                framework_capabilities, check_model_ledger)
        except DBAPIError as exc:
            if expected_binding is None or getattr(exc.orig, "sqlstate", None) != "55P03":
                raise
            logger.warning("framework_owner_lock_busy", extra={
                "task_id": task_id, "binding_id": expected_binding.binding_id, "worker_id": worker_id})
            raise TaskDeferred("Framework owner lock is busy") from exc

    async def renew_framework_execution(self, task: AgentTaskExecution, lease_seconds: int) -> bool:
        return await asyncio.to_thread(self._renew_framework_execution_sync, task, lease_seconds)

    async def begin(
        self,
        task: AgentTaskExecution,
        *,
        resource_id: str | None = None,
        fencing_token: int | None = None,
    ):
        await asyncio.to_thread(
            self._transition_sync,
            task,
            "running",
            None,
            None,
            resource_id,
            fencing_token,
        )

    async def interrupt(self, session_id: str, *, reason: str = "User requested task interruption", source: str = "user") -> bool:
        return await asyncio.to_thread(self._interrupt_sync, session_id, reason, source)

    async def interrupt_requested(self, task_id: str) -> bool:
        return await asyncio.to_thread(self._read_interrupt_sync, task_id)

    async def defer(self, task: AgentTaskExecution, reason: str):
        await asyncio.to_thread(
            self._transition_sync,
            task,
            "waiting_resource",
            None,
            reason,
            None,
            None,
        )

    async def finish(
        self,
        task: AgentTaskExecution,
        *,
        status: str,
        result_status=None,
        error_code=None,
    ):
        if status not in TERMINAL_AGENT_TASK_STATUSES:
            raise ValueError("Agent task finish requires a terminal status")
        await asyncio.to_thread(
            self._transition_sync,
            task,
            status,
            result_status,
            error_code,
            None,
            None,
        )

    def _initialize_sync(self):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._tasks} (
                id TEXT PRIMARY KEY, sequence BIGSERIAL NOT NULL,
                session_id TEXT NOT NULL REFERENCES {self._session_table}(id) ON DELETE CASCADE,
                operation TEXT NOT NULL DEFAULT 'message',
                turn_id TEXT NOT NULL, project_id TEXT,
                request_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                run_id TEXT, run_item_id TEXT, priority INTEGER NOT NULL DEFAULT 50,
                idempotency_key TEXT NOT NULL, input_hash TEXT NOT NULL,
                input_payload JSONB NOT NULL, user_message_id TEXT,
                reference_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued', attempt_id TEXT, attempt_no INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT, lease_token TEXT, resource_id TEXT, fencing_token BIGINT,
                waiting_reason TEXT, result_status TEXT, error_code TEXT,
                runtime_binding JSONB,
                interrupt_requested BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(session_id, idempotency_key)
            )""")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._tasks}_session_idx "
                f"ON {self._tasks}(session_id, sequence)"
            )
            cur.execute(
                f"ALTER TABLE {self._tasks} DROP CONSTRAINT IF EXISTS "
                f"{self._tasks}_turn_id_key"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._tasks}_session_turn_idx "
                f"ON {self._tasks}(session_id, turn_id)"
            )
            # The feature may have initialized its tables during development
            # before the complete TaskEnvelope fields were added.
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS run_id TEXT")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS run_item_id TEXT")
            cur.execute(
                f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS "
                "priority INTEGER NOT NULL DEFAULT 50"
            )
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS resource_id TEXT")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS fencing_token BIGINT")
            cur.execute(
                f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS "
                "operation TEXT NOT NULL DEFAULT 'message'"
            )
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS reference_id TEXT")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS runtime_binding JSONB")
            cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {self._tasks}_v2_initial_turn_idx "
                f"ON {self._tasks}(session_id,turn_id) WHERE runtime_binding IS NOT NULL AND operation='message'")
            cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {self._tasks}_v2_approval_idx "
                f"ON {self._tasks}(session_id,reference_id) "
                "WHERE runtime_binding IS NOT NULL AND operation='approval_resume'")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS parent_task_id TEXT")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS root_trace_id TEXT")
            cur.execute(
                f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS result_payload JSONB"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._tasks}_parent_idx "
                f"ON {self._tasks}(parent_task_id) WHERE parent_task_id IS NOT NULL"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._tasks}_settled_child_idx "
                f"ON {self._tasks}(updated_at, sequence) "
                "WHERE operation='coordinator_child' "
                "AND status IN ('completed','failed','cancelled','interrupted') "
                "AND result_payload IS NOT NULL"
            )
            cur.execute(f"ALTER TABLE {self._tasks} ALTER COLUMN user_message_id DROP NOT NULL")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._attempts} (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES {self._tasks}(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL, worker_id TEXT NOT NULL, lease_token TEXT NOT NULL,
                status TEXT NOT NULL, error_code TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ, UNIQUE(task_id, attempt_no)
            )""")
            initialize_runtime_owners(cur, self._settings)
            initialize_framework_approvals(cur, self._settings)
            from src.runtime.framework_message_stages import initialize_message_stages

            initialize_message_stages(cur, self._settings)

    def _submit_sync(self, command, payload, message, idempotency_key):
        serialized = payload.model_dump(mode="json")
        effective_key = self._idempotency_key("message", idempotency_key)
        content_hash = self._input_hash("message", serialized)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            row = self._submit_message_in_transaction(
                cur,
                command,
                payload,
                message,
                effective_key=effective_key,
                content_hash=content_hash,
            )
        return AgentTaskRecord.model_validate(row)

    def _submit_v2_sync(self, command, payload, message, idempotency_key, model_budget=None):
        if command.operation != "message" or command.task_type != "agent_turn":
            raise ValueError("v2 message submission supports only message agent_turn commands")
        serialized = payload.model_dump(mode="json")
        binding = command.runtime_binding
        if model_budget is not None:
            model_budget = FrameworkModelBudget.model_validate(model_budget.model_dump())
        content_hash = self._v2_input_hash(serialized, binding, model_budget)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            row = self._submit_message_in_transaction(
                cur,
                command,
                payload,
                message,
                effective_key=self._idempotency_key("message_v2", idempotency_key),
                content_hash=content_hash,
                runtime_binding=binding,
                stream=self._settings.orchestration.agent_task_v2_stream,
                model_budget=model_budget,
            )
        if model_budget is not None:
            logger.info("framework_model_budget_admitted", extra={"binding_id": binding.binding_id,
                "task_id": row["id"], "max_calls": model_budget.max_calls, "max_tokens": model_budget.max_tokens,
                "deadline_at": model_budget.deadline_at})
        return AgentTaskRecord.model_validate(row)

    def _submit_message_in_transaction(
        self,
        cur,
        command,
        payload,
        message,
        *,
        effective_key,
        content_hash,
        runtime_binding: RuntimeBinding | None = None,
        stream: str | None = None,
        model_budget: FrameworkModelBudget | None = None,
    ):
        session = self._lock_session(cur, command.session_id)
        if runtime_binding is not None:
            try:
                register_runtime_binding(cur, self._settings, runtime_binding)
                if model_budget is not None:
                    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

                    PostgresFrameworkModelCalls(self._settings).freeze_submission(cur, runtime_binding, model_budget)
            except ValueError as exc:
                raise AgentSubmissionConflict(str(exc)) from exc
        existing = self._find_idempotent(
            cur,
            command.session_id,
            effective_key,
            content_hash,
        )
        if existing:
            return existing
        if runtime_binding is not None:
            cur.execute(f"SELECT id FROM {self._tasks} WHERE session_id=%s AND turn_id=%s "
                "AND runtime_binding IS NOT NULL AND operation='message'", (command.session_id, command.turn_id))
            if cur.fetchone() is not None:
                raise AgentSubmissionConflict("Runtime binding already has its initial message task")
        if command.operation == "coordinator_child":
            self._assert_coordinator_child_bounds(cur, command)
        if session["project_id"] != command.project_id:
            raise AgentSubmissionConflict("Session project changed before submission")
        cur.execute(
            f"SELECT id FROM {self._tasks} WHERE session_id=%s "
            "AND status IN ('queued','waiting_resource','claimed','running') LIMIT 1",
            (command.session_id,),
        )
        pending = cur.fetchone()
        if (pending or session["status"] in {"running", "waiting_approval", "interrupted"}) and (
            payload.submit_mode not in {"queued", "enqueue", "background"}
            and not payload.interrupt_if_busy
        ):
            raise AgentSubmissionConflict(
                "Session has pending work; use queued submission or resume the interrupted turn"
            )
        cur.execute(f"""INSERT INTO {self._tasks}
            (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
             priority,idempotency_key,input_hash,input_payload,user_message_id,reference_id,
             parent_task_id,root_trace_id,runtime_binding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
            (
                command.task_id,
                command.session_id,
                command.operation,
                command.turn_id,
                command.project_id,
                command.request_id,
                command.trace_id,
                command.run_id,
                command.run_item_id,
                command.priority,
                effective_key,
                content_hash,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                message.id,
                command.reference_id,
                command.parent_task_id,
                command.root_trace_id,
                json.dumps(
                    runtime_binding.model_dump(mode="json"), ensure_ascii=False
                ) if runtime_binding else None,
            ))
        row = cur.fetchone()
        if runtime_binding is not None:
            from src.runtime.framework_message_stages import admit_message_stage

            admit_message_stage(cur, self._settings, row)
        if payload.interrupt_if_busy:
            from src.runtime.framework_task_control import FrameworkTaskControl

            if not FrameworkTaskControl(self).interrupt_in_transaction(cur, session,
                reason="Follow-up input requested interruption", source="followup", exclude_task_id=command.task_id):
                cur.execute(
                    f"UPDATE {self._tasks} SET interrupt_requested=TRUE "
                    "WHERE session_id=%s AND status IN ('claimed','running')",
                    (command.session_id,),
                )
        cur.execute(f"""INSERT INTO {self._settings.database.postgres_message_table}
            (id,session_id,role,content,created_at,metadata,message_sequence)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,DEFAULT)""",
            (message.id, command.session_id, message.role.value, message.content, message.created_at,
             json.dumps(message.metadata, ensure_ascii=False)))
        cur.execute(
            f"SELECT count(*) AS count FROM {self._tasks} WHERE session_id=%s "
            "AND status IN ('queued','waiting_resource','claimed')",
            (command.session_id,),
        )
        queue_depth = cur.fetchone()["count"]
        self._event(
            cur,
            row,
            "input.queued",
            {
                "queue_entry_id": row["id"],
                "queued_turn_id": row["turn_id"],
                "busy_status": session["status"],
                "queue_depth": queue_depth,
                "queue_behavior": "enqueue_if_busy",
                "queue": stream or self._settings.orchestration.agent_task_stream,
                "source": payload.source,
            },
        )
        cur.execute(f"""INSERT INTO {self._settings.database.postgres_task_outbox_table}
            (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)""",
            (
                f"{command.task_type}:{command.task_id}",
                stream or self._settings.orchestration.agent_task_stream,
                command.model_dump_json(),
            ))
        return row

    def _assert_coordinator_child_bounds(self, cur, command):
        if not command.parent_task_id:
            raise ValueError("coordinator_parent_task_id_required")
        # Serialize sibling submissions across child sessions. The limit is
        # simultaneous unfinished children, not the parent's lifetime total.
        cur.execute(
            f"SELECT id FROM {self._tasks} WHERE id=%s FOR UPDATE",
            (command.parent_task_id,),
        )
        if cur.fetchone() is None:
            raise ValueError("coordinator_parent_not_found")
        cur.execute(
            f"""WITH RECURSIVE chain AS (
                SELECT id, parent_task_id, 1 AS chain_depth, ARRAY[id] AS chain_path,
                    FALSE AS chain_cycle
                FROM {self._tasks} WHERE id=%s
                UNION ALL
                SELECT node.id, node.parent_task_id, chain.chain_depth + 1,
                    chain.chain_path || node.id, node.id = ANY(chain.chain_path)
                FROM {self._tasks} AS node JOIN chain ON node.id = chain.parent_task_id
                WHERE NOT chain.chain_cycle
            ) SELECT COALESCE(MAX(chain_depth), 0) AS chain_depth,
                COALESCE(BOOL_OR(chain_cycle), FALSE) AS chain_cycle FROM chain""",
            (command.parent_task_id,),
        )
        ancestors = cur.fetchone()
        if ancestors["chain_cycle"]:
            raise ValueError("coordinator_parent_cycle")
        if ancestors["chain_depth"] == 0:
            raise ValueError("coordinator_parent_not_found")
        if ancestors["chain_depth"] + 1 > self.MAX_COORDINATOR_DEPTH:
            raise ValueError("coordinator_depth_exceeded")
        cur.execute(
            f"SELECT count(*) AS count FROM {self._tasks} WHERE parent_task_id=%s "
            "AND status IN ('queued','waiting_resource','claimed','running',"
            "'waiting_children','coordinator_resuming')",
            (command.parent_task_id,),
        )
        if cur.fetchone()["count"] >= self._settings.orchestration.coordinator_max_workers:
            raise ValueError("coordinator_child_limit_exceeded")

    def _submit_resume_sync(self, command, payload, idempotency_key):
        if command.operation != "manual_resume" or not command.reference_id:
            raise ValueError("Manual resume command requires its checkpoint reference")
        serialized = payload.model_dump(mode="json")
        effective_key = self._idempotency_key("manual_resume", idempotency_key)
        content_hash = self._input_hash(
            "manual_resume",
            serialized,
            reference_id=command.reference_id,
        )
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            session = self._lock_session(cur, command.session_id)
            existing = self._find_idempotent(
                cur,
                command.session_id,
                effective_key,
                content_hash,
            )
            if existing:
                return AgentTaskRecord.model_validate(existing)
            if session["project_id"] != command.project_id:
                raise AgentSubmissionConflict("Session project changed before submission")
            cur.execute(
                f"SELECT id FROM {self._tasks} WHERE session_id=%s "
                "AND status IN ('queued','waiting_resource','claimed','running') LIMIT 1",
                (command.session_id,),
            )
            if cur.fetchone():
                raise AgentSubmissionConflict("Session already has pending Agent work")
            snapshot = self._latest_snapshot(cur, command.session_id)
            self._assert_resume_checkpoint(
                session,
                snapshot,
                reference_id=command.reference_id,
                turn_id=command.turn_id,
            )
            cur.execute(
                f"""INSERT INTO {self._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,user_message_id,reference_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NULL,%s) RETURNING *""",
                (
                    command.task_id,
                    command.session_id,
                    command.operation,
                    command.turn_id,
                    command.project_id,
                    command.request_id,
                    command.trace_id,
                    command.run_id,
                    command.run_item_id,
                    command.priority,
                    effective_key,
                    content_hash,
                    json.dumps(serialized, ensure_ascii=False),
                    command.reference_id,
                ),
            )
            row = cur.fetchone()
            self._event(
                cur,
                row,
                "runtime.resume_queued",
                {
                    "snapshot_id": command.reference_id,
                    "source": payload.source,
                    "reason": payload.reason or "Manual resume requested.",
                    "queue": self._settings.orchestration.agent_task_stream,
                },
            )
            cur.execute(
                f"""INSERT INTO {self._settings.database.postgres_task_outbox_table}
                (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)""",
                (
                    f"agent_turn:{command.task_id}",
                    self._settings.orchestration.agent_task_stream,
                    command.model_dump_json(),
                ),
            )
        return AgentTaskRecord.model_validate(row)

    def _submit_new_session_sync(
        self,
        session,
        created_event,
        command,
        payload,
        message,
        idempotency_key,
        submission_payload,
    ):
        if command.operation != "message" or command.session_id != session.id:
            raise ValueError("New-session Agent command identity is invalid")
        effective_key = self._idempotency_key("headless", idempotency_key)
        content_hash = self._input_hash("headless", submission_payload)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            inserted = self._sessions.create_session_in_transaction(cur, session)
            self._lock_session(cur, session.id)
            existing = self._find_idempotent(
                cur,
                session.id,
                effective_key,
                content_hash,
            )
            if existing:
                return AgentTaskRecord.model_validate(existing)
            if not inserted:
                raise AgentSubmissionConflict(
                    "Headless submission ID already belongs to a different request"
                )
            self._sessions.append_event_in_transaction(
                cur,
                session.id,
                created_event,
            )
            row = self._submit_message_in_transaction(
                cur,
                command,
                payload,
                message,
                effective_key=effective_key,
                content_hash=content_hash,
            )
        return AgentTaskRecord.model_validate(row)

    def _submit_approval_sync(self, command, approval_id, payload, idempotency_key):
        if command.operation != "approval_resume" or command.reference_id != approval_id:
            raise ValueError("Approval command identity is invalid")
        decision_payload = payload.model_dump(mode="json")
        if not payload.client_request_id:
            # Without an explicit client key, the approval itself is the
            # idempotency boundary. A repeated click may carry a different
            # explanatory reason but must not create a second continuation.
            decision_payload = {"decision": payload.decision.value}
        effective_key = self._idempotency_key("approval_resume", idempotency_key)
        content_hash = self._input_hash(
            "approval_resume",
            decision_payload,
            reference_id=approval_id,
        )
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            session = self._lock_session(cur, command.session_id)
            existing = self._find_idempotent(
                cur,
                command.session_id,
                effective_key,
                content_hash,
            )
            if existing:
                return AgentTaskRecord.model_validate(existing)
            if session["status"] != "waiting_approval":
                raise AgentSubmissionConflict(
                    "Session is not waiting for an approval continuation"
                )
            # Bound approvals use the separate v2 command/decision transaction.
            # A direct legacy store caller cannot bypass that routing contract.
            pending = (session.get("metadata") or {}).get("pending_turn") or {}
            if (pending.get("context_bundle") or {}).get("runtime_binding") is not None:
                raise AgentSubmissionConflict("Framework-bound approval requires a v2 continuation")
            cur.execute(
                f"SELECT * FROM {self._settings.database.postgres_approval_table} "
                "WHERE id=%s AND session_id=%s FOR UPDATE",
                (approval_id, command.session_id),
            )
            approval_row = cur.fetchone()
            if approval_row is None:
                raise KeyError(approval_id)
            if approval_row["status"] != ToolApprovalStatus.pending.value:
                raise AgentSubmissionConflict(f"Approval already resolved: {approval_id}")
            metadata = approval_row.get("metadata") or {}
            if (
                metadata.get("approval_type") == "ui_recording"
                or metadata.get("source") == "test_run_case_execution"
                or metadata.get("proxy_child_session_id")
            ):
                raise AgentSubmissionConflict(
                    "This approval type must use its dedicated approval endpoint"
                )
            approval = ToolApprovalRequest(
                id=str(approval_row["id"]),
                session_id=str(approval_row["session_id"]),
                tool_key=str(approval_row["tool_key"]),
                tool_name=str(approval_row["tool_name"]),
                reason=str(approval_row["reason"]),
                status=ToolApprovalStatus(payload.decision.value),
                created_at=approval_row["created_at"],
                resolved_at=datetime.now(timezone.utc),
                decision_note=payload.reason,
                metadata=metadata,
            )
            cur.execute(
                f"""UPDATE {self._settings.database.postgres_approval_table}
                SET status=%s,resolved_at=%s,decision_note=%s
                WHERE id=%s AND session_id=%s""",
                (
                    payload.decision.value,
                    approval.resolved_at,
                    payload.reason,
                    approval_id,
                    command.session_id,
                ),
            )
            cur.execute(
                f"""UPDATE {self._settings.database.postgres_session_table}
                SET metadata = metadata || jsonb_build_object(
                    'control', COALESCE(metadata->'control','{{}}'::jsonb) ||
                        jsonb_build_object(
                            'control_state','queued',
                            'is_interrupted',false,
                            'is_resumable',false,
                            'last_approval_id',%s::text
                        )
                ), updated_at=NOW(), state_revision=state_revision+1
                WHERE id=%s""",
                (approval_id, command.session_id),
            )
            input_payload = {
                "approval": approval.model_dump(mode="json"),
                "decision": decision_payload,
            }
            cur.execute(
                f"""INSERT INTO {self._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 priority,idempotency_key,input_hash,input_payload,user_message_id,reference_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NULL,%s)
                RETURNING *""",
                (
                    command.task_id,
                    command.session_id,
                    command.operation,
                    command.turn_id,
                    command.project_id,
                    command.request_id,
                    command.trace_id,
                    command.run_id,
                    command.run_item_id,
                    command.priority,
                    effective_key,
                    content_hash,
                    json.dumps(input_payload, ensure_ascii=False),
                    approval_id,
                ),
            )
            row = cur.fetchone()
            self._event(
                cur,
                row,
                "approval.resolved",
                {
                    "approval_id": approval_id,
                    "tool_key": approval.tool_key,
                    "decision": approval.status.value,
                },
            )
            self._event(
                cur,
                row,
                "approval.continuation_queued",
                {
                    "approval_id": approval_id,
                    "tool_key": approval.tool_key,
                    "decision": approval.status.value,
                    "queue": self._settings.orchestration.agent_task_stream,
                },
            )
            cur.execute(
                f"""INSERT INTO {self._settings.database.postgres_task_outbox_table}
                (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)""",
                (
                    f"agent_turn:{command.task_id}",
                    self._settings.orchestration.agent_task_stream,
                    command.model_dump_json(),
                ),
            )
        return AgentTaskRecord.model_validate(row)

    def _get_sync(self, task_id, framework_capabilities=None):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(task_id)
        if framework_capabilities is not None and row["runtime_binding"] is not None:
            row = {**row, "runtime_binding": framework_capabilities.require_binding_format(row["runtime_binding"])}
        return self._record(row)

    @staticmethod
    def _idempotency_key(operation: str, value: str) -> str:
        normalized = value.strip()
        return normalized if operation == "message" else f"{operation}:{normalized}"

    @staticmethod
    def _v2_input_hash(payload, binding, model_budget=None):
        identity = {"payload": payload, "runtime_binding_fingerprint": binding.fingerprint}
        if model_budget is not None:
            identity["model_budget"] = model_budget.model_dump(mode="json")
        return PostgresAgentTaskStore._input_hash("message_v2", identity)

    @staticmethod
    def _input_hash(operation: str, payload: dict, *, reference_id: str | None = None) -> str:
        hash_payload = payload if operation == "message" else {
            "operation": operation,
            "reference_id": reference_id,
            "payload": payload,
        }
        canonical_payload = json.dumps(
            hash_payload,
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(canonical_payload).hexdigest()

    def _find_idempotent(self, cur, session_id, idempotency_key, content_hash):
        cur.execute(
            f"SELECT * FROM {self._tasks} WHERE session_id=%s AND idempotency_key=%s",
            (session_id, idempotency_key),
        )
        existing = cur.fetchone()
        if existing and existing["input_hash"] != content_hash:
            raise AgentSubmissionConflict("Submission ID already belongs to different input")
        return existing

    def _latest_snapshot(self, cur, session_id):
        cur.execute(
            f"SELECT * FROM {self._settings.database.postgres_snapshot_table} "
            "WHERE session_id=%s ORDER BY version DESC LIMIT 1",
            (session_id,),
        )
        return cur.fetchone()

    @staticmethod
    def _assert_resume_checkpoint(session, snapshot, *, reference_id, turn_id):
        if snapshot is None:
            raise AgentSubmissionConflict("No snapshot is available for resume")
        if ((snapshot.get("graph_state") or {}).get("context_bundle") or {}).get("runtime_binding") is not None:
            raise AgentSubmissionConflict("Framework-bound checkpoint requires a v2 continuation")
        if snapshot["id"] != reference_id:
            raise AgentSubmissionConflict("Resume checkpoint changed before submission")
        if snapshot["stage"] == "waiting_approval":
            raise AgentSubmissionConflict(
                "Pending approval must be resolved through the approval API"
            )
        if snapshot["stage"] not in {"interrupted", "resumable"}:
            raise AgentSubmissionConflict("Latest snapshot is not resumable")
        snapshot_turn_id = str((snapshot.get("graph_state") or {}).get("turn_id") or f"resume_{snapshot['id']}")
        if snapshot_turn_id != turn_id:
            raise AgentSubmissionConflict("Resume turn identity no longer matches its checkpoint")
        if snapshot["stage"] == "interrupted" and not (session.get("metadata") or {}).get("pending_turn"):
            raise AgentSubmissionConflict(
                "The interrupted runtime has no durable checkpoint and cannot be resumed"
            )

    def _read_interrupt_sync(self, task_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT interrupt_requested FROM {self._tasks} WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(task_id)
            return row["interrupt_requested"]

    def _interrupt_sync(self, session_id, reason="User requested task interruption", source="user"):
        from src.runtime.framework_task_control import FrameworkTaskControl

        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            session = self._lock_session(cur, session_id)
            if FrameworkTaskControl(self).interrupt_in_transaction(cur, session, reason=reason, source=source):
                return True
            cur.execute(f"""SELECT * FROM {self._tasks} WHERE session_id=%s
                AND status IN ('queued','waiting_resource','claimed','running')
                ORDER BY sequence LIMIT 1 FOR UPDATE""", (session_id,))
            row = cur.fetchone()
            if row is None:
                return False
            if row["status"] in {"queued", "waiting_resource"}:
                self._update_status(cur, row, "cancelled", None, "user_cancelled_before_execution")
            else:
                cur.execute(
                    f"UPDATE {self._tasks} SET interrupt_requested=TRUE,updated_at=NOW() "
                    "WHERE id=%s",
                    (row["id"],),
                )
                self._event(cur, row, "runtime.interrupt_requested", {"source": "user"})
            return True

    def _list_sync(self, session_id, pending_only, limit):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            condition = "AND status IN ('queued','waiting_resource','claimed','running')" if pending_only else ""
            cur.execute(
                f"SELECT * FROM {self._tasks} WHERE session_id=%s {condition} "
                "ORDER BY sequence DESC LIMIT %s",
                (session_id, min(max(1, limit), 1000)),
            )
            return [self._record(row) for row in cur.fetchall()]

    def _list_children_sync(self, parent_task_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {self._tasks} WHERE parent_task_id=%s ORDER BY sequence",
                (parent_task_id,),
            )
            return [self._record(row) for row in cur.fetchall()]

    def _list_settled_children_needing_resume_sync(self, limit):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT child.* FROM {self._tasks} AS child
                JOIN {self._tasks} AS parent ON parent.id = child.parent_task_id
                JOIN {self._session_table} AS session ON session.id = parent.session_id
                WHERE child.operation = 'coordinator_child'
                  AND child.status IN ('completed', 'failed', 'cancelled', 'interrupted')
                  AND child.result_payload IS NOT NULL
                  AND child.updated_at < NOW() - INTERVAL '1 second'
                  AND (session.session_mode = 'coordinator'
                       OR session.selected_agent = 'coordinator')
                  AND session.status NOT IN ('running', 'waiting_approval')
                  AND NOT (
                      COALESCE((session.metadata->'worker_failure_guard'->>'blocked')::boolean, FALSE)
                      AND COALESCE(session.metadata->'worker_failure_guard'->>'turn_id', '') = parent.turn_id
                  )
                  AND COALESCE(child.result_payload->>'status', '') <> 'waiting_approval'
                  AND NOT EXISTS (
                      SELECT 1 FROM {self._tasks} AS resume
                      WHERE resume.session_id = parent.session_id
                        AND resume.idempotency_key =
                            'coordinator_resume:' || child.id || ':' ||
                            COALESCE(child.result_payload->>'attempt_id', child.attempt_id, 'na')
                  )
                ORDER BY child.updated_at, child.sequence
                LIMIT %s""",
                (max(1, min(int(limit), 50)),),
            )
            return [self._record(row) for row in cur.fetchall()]

    def _set_result_payload_sync(self, task_id, attempt_id, fencing_token, payload):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._tasks} SET result_payload=%s::jsonb,updated_at=NOW() "
                "WHERE id=%s AND attempt_id=%s AND (%s::bigint IS NULL OR fencing_token=%s)",
                (
                    json.dumps(payload, ensure_ascii=False),
                    task_id,
                    attempt_id,
                    fencing_token,
                    fencing_token,
                ),
            )
            return cur.rowcount > 0

    def _cancel_unstarted_children_sync(self, parent_task_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            # Keep the session -> task lock order used by claim/transition and
            # by message delivery-status projection below.
            cur.execute(
                f"""SELECT session.id FROM {self._session_table} AS session
                WHERE EXISTS (
                    SELECT 1 FROM {self._tasks} AS task
                    WHERE task.session_id=session.id AND task.parent_task_id=%s
                      AND task.status IN ('queued','waiting_resource')
                      AND task.interrupt_requested=FALSE
                )
                ORDER BY session.id FOR UPDATE OF session""",
                (parent_task_id,),
            )
            cur.execute(
                f"SELECT * FROM {self._tasks} WHERE parent_task_id=%s "
                "AND status IN ('queued','waiting_resource') AND interrupt_requested=FALSE "
                "ORDER BY sequence FOR UPDATE",
                (parent_task_id,),
            )
            rows = cur.fetchall()
            for row in rows:
                self._update_status(cur, row, "cancelled", None, "parent_cancelled")
            return len(rows)

    def _reap_expired_children_sync(self, now, timeout_seconds):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            expired_condition = """
                task.operation='coordinator_child'
                AND task.status IN ('claimed','running')
                AND task.updated_at < %s - make_interval(secs=>%s)
                AND NOT COALESCE(
                    session.metadata->>'turn_lease_token'=task.lease_token
                    AND (session.metadata->>'turn_lease_expires_at')::timestamptz
                        >= %s - make_interval(secs=>%s), FALSE)
            """
            cutoff_params = (now, timeout_seconds, now, timeout_seconds)
            # Claim/finish lock session before task. Take the same locks here,
            # so renewals cannot race the timeout decision or deadlock finish.
            cur.execute(
                f"""SELECT session.id FROM {self._session_table} AS session
                WHERE EXISTS (SELECT 1 FROM {self._tasks} AS task
                    WHERE task.session_id=session.id AND {expired_condition})
                ORDER BY session.id FOR UPDATE OF session SKIP LOCKED LIMIT 50""",
                cutoff_params,
            )
            sessions = cur.fetchall()
            rows = []
            for session in sessions:
                cur.execute(
                    f"""SELECT task.* FROM {self._tasks} AS task
                    JOIN {self._session_table} AS session ON session.id=task.session_id
                    WHERE session.id=%s AND {expired_condition}
                    ORDER BY task.sequence FOR UPDATE OF task SKIP LOCKED LIMIT %s""",
                    (session["id"], *cutoff_params, 50 - len(rows)),
                )
                rows.extend(cur.fetchall())
                if len(rows) == 50:
                    break
            reaped = []
            for row in rows:
                self._end_attempt(cur, row, "failed", "coordinator_child_timeout")
                self._interrupt_expired_session(cur, row)
                cur.execute(
                    f"""UPDATE {self._tasks} SET status='failed', result_status=NULL,
                    waiting_reason=NULL, error_code='coordinator_child_timeout',
                    result_payload=jsonb_set(jsonb_set(
                        COALESCE(result_payload,'{{}}'::jsonb),
                        '{{status}}',%s::jsonb),'{{summary}}',%s::jsonb),
                    updated_at=NOW() WHERE id=%s""",
                    (
                        json.dumps("failed", ensure_ascii=False),
                        json.dumps("coordinator child timeout", ensure_ascii=False),
                        row["id"],
                    ),
                )
                if row.get("user_message_id"):
                    cur.execute(
                        f"""UPDATE {self._settings.database.postgres_message_table}
                        SET metadata=metadata || jsonb_build_object('delivery_status',%s::text)
                        WHERE id=%s""",
                        ("failed", row["user_message_id"]),
                    )
                    if cur.rowcount > 0:
                        cur.execute(
                            f"UPDATE {self._session_table} "
                            "SET state_revision=state_revision+1 WHERE id=%s",
                            (row["session_id"],),
                        )
                self._event(
                    cur,
                    row,
                    "agent_task.failed",
                    {"status": "failed", "reason": "coordinator_child_timeout"},
                )
                reaped.append({
                    "id": row["id"],
                    "parent_task_id": row["parent_task_id"],
                    "session_id": row["session_id"],
                    "turn_id": row["turn_id"],
                })
            return reaped

    def _lock_session(self, cur, session_id):
        cur.execute(f"SELECT * FROM {self._session_table} WHERE id=%s FOR UPDATE", (session_id,))
        session = cur.fetchone()
        if session is None:
            raise KeyError(session_id)
        cur.execute("SELECT COALESCE(%s::timestamptz > clock_timestamp(), FALSE) AS lease_active",
            ((session["metadata"] or {}).get("turn_lease_expires_at"),))
        lease_active = cur.fetchone()["lease_active"]
        # Match PostgresSessionStore's public identity conversion: PostgreSQL
        # returns UUID objects, while AgentTaskCommand carries string IDs.
        session = {**session, "lease_active": lease_active,
            "project_id": str(session["project_id"]) if session["project_id"] is not None else None}
        logger.debug("agent_session_identity_loaded", extra={"session_id": session_id,
            "project_id": session["project_id"]})
        return session

    def _renew_framework_execution_sync(self, task, lease_seconds):
        if task.runtime_owner is None:
            raise ValueError("Framework heartbeat requires a captured execution owner")
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            try:
                assert_runtime_owner(cur, self._settings, task.runtime_owner)
            except ContinuationLeaseLostError:
                return False
            cur.execute(f"""UPDATE {self._session_table} SET metadata=metadata || jsonb_build_object(
                'turn_lease_expires_at', to_jsonb(clock_timestamp() + %s * INTERVAL '1 second'))
                WHERE id=%s RETURNING (metadata->>'turn_lease_expires_at')::timestamptz AS expires_at""",
                (lease_seconds, task.session_id))
            expires_at = cur.fetchone()["expires_at"]
            cur.execute(f"UPDATE {runtime_owner_table(self._settings)} SET expires_at=%s WHERE binding_id=%s",
                (expires_at, task.runtime_binding.binding_id))
        return True

    def _claim_sync(self, task_id, worker_id, expected_binding=None, framework_capabilities=None, check_model_ledger=False):
        if framework_capabilities is not None and expected_binding is None:
            raise ValueError("Framework compatibility requires a framework binding")
        if check_model_ledger and framework_capabilities is None:
            raise ValueError("Model ledger compatibility requires framework capabilities")
        if (expected_binding is not None and expected_binding.release_reference is not None
            and framework_capabilities is None):
            logger.warning("framework_release_reader_unavailable", extra={
                "task_id": task_id, "binding_id": expected_binding.binding_id,
                "worker_id": worker_id, "reason": "framework_release_artifact_unavailable"})
            raise TaskDeferred("framework_release_artifact_unavailable")
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            if expected_binding is not None:
                configure_runtime_owner_transaction(cur, self._settings)
            cur.execute(f"SELECT session_id FROM {self._tasks} WHERE id=%s", (task_id,))
            identity = cur.fetchone()
            if identity is None:
                raise KeyError(task_id)
            session = self._lock_session(cur, identity["session_id"])
            binding_reader = (framework_capabilities.require_binding_format
                if framework_capabilities is not None else RuntimeBinding.model_validate)
            try:
                if expected_binding is not None:
                    binding_owner = lock_owner_row(cur, self._settings, expected_binding, binding_reader=binding_reader)
                cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s FOR UPDATE", (task_id,))
                row = cur.fetchone()
                saved_binding = binding_reader(row["runtime_binding"]) if row.get("runtime_binding") is not None else None
            except TaskDeferred as exc:
                logger.warning("framework_binding_reader_unavailable", extra={
                    "task_id": task_id, "binding_id": expected_binding.binding_id,
                    "worker_id": worker_id, "reason": str(exc)})
                raise
            if saved_binding != expected_binding:
                raise ValueError("Agent claim runtime binding does not match the durable task")
            if framework_capabilities is not None:
                try:
                    framework_capabilities.require_release(saved_binding)
                except TaskDeferred as exc:
                    logger.warning("framework_release_reader_unavailable", extra={
                        "task_id": task_id, "binding_id": saved_binding.binding_id,
                        "worker_id": worker_id, "reason": str(exc)})
                    raise
                from src.runtime.framework_message_stages import FrameworkMessageStages

                try:
                    compatible_stage = FrameworkMessageStages(self).require_worker_compatibility(
                        cur, saved_binding, framework_capabilities)
                except TaskDeferred as exc:
                    logger.warning("framework_message_stage_reader_unavailable", extra={
                        "task_id": task_id, "binding_id": saved_binding.binding_id,
                        "worker_id": worker_id, "reason": str(exc)})
                    raise
                if (row["operation"] == "message" and row["status"] in {"queued", "waiting_resource", "claimed"}
                    and (compatible_stage is None or compatible_stage.phase in {"submitted", "orchestrating", "orchestrated"})):
                    try:
                        framework_capabilities.require_transcript_format(
                            SimpleNamespace(id=session["id"], metadata=session["metadata"]), saved_binding)
                    except TaskDeferred as exc:
                        logger.warning("framework_transcript_reader_unavailable", extra={
                            "task_id": task_id, "binding_id": saved_binding.binding_id,
                            "worker_id": worker_id, "reason": str(exc)})
                        raise
                from src.runtime.framework_approval_journal import FrameworkApprovalJournal

                try:
                    FrameworkApprovalJournal(self._settings).require_worker_compatibility(
                        cur, saved_binding, framework_capabilities)
                except TaskDeferred as exc:
                    logger.warning("framework_approval_reader_unavailable", extra={
                        "task_id": task_id, "binding_id": saved_binding.binding_id,
                        "worker_id": worker_id, "reason": str(exc)})
                    raise
                if row["operation"] == "approval_recover" and "recovery_proof" in (row["input_payload"] or {}):
                    try:
                        framework_capabilities.require_recovery_proof_format(row["input_payload"]["recovery_proof"])
                    except TaskDeferred as exc:
                        logger.warning("framework_recovery_proof_reader_unavailable", extra={
                            "task_id": task_id, "binding_id": saved_binding.binding_id,
                            "worker_id": worker_id, "reason": str(exc)})
                        raise
            if check_model_ledger:
                from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

                try:
                    PostgresFrameworkModelCalls(self._settings).require_worker_compatibility(
                        cur, saved_binding, framework_capabilities,
                        require_budget=(row["status"] not in TERMINAL_AGENT_TASK_STATUSES
                            and row["operation"] != "framework_reconcile"))
                except TaskDeferred as exc:
                    logger.warning("framework_model_reader_unavailable", extra={
                        "task_id": task_id, "binding_id": saved_binding.binding_id,
                        "worker_id": worker_id, "reason": str(exc)})
                    raise
            if row["status"] in TERMINAL_AGENT_TASK_STATUSES:
                return self._record(row)
            pending = (session["metadata"] or {}).get("pending_turn") or {}
            pending_binding = (pending.get("context_bundle") or {}).get("runtime_binding")
            control = (session["metadata"] or {}).get("control") or {}
            if (row["operation"] in {"message", "message_recover"} and pending_binding is not None
                and pending.get("turn_id") != row["turn_id"]
                and control.get("last_interrupt_binding_id") != pending_binding.get("binding_id")):
                raise TaskDeferred("Pending framework Turn must settle before a later message")
            if row["operation"] == "message":
                cur.execute(f"SELECT id FROM {self._tasks} WHERE session_id=%s AND operation='message_recover' "
                    "AND turn_id<>%s AND status IN ('queued','waiting_resource','claimed','running') LIMIT 1",
                    (row["session_id"], row["turn_id"]))
                if cur.fetchone() is not None:
                    raise TaskDeferred("Pending message recovery must settle before a later message")
            if row["status"] in {"claimed", "running"}:
                if session["lease_active"] and session["metadata"].get("turn_lease_token") == row["lease_token"]:
                    raise TaskDeferred("Agent task is owned by another worker")
                # A running model/tool may already have changed external state.
                # Never replay it merely because Redis redelivered its command.
                if row["status"] == "running":
                    self._update_status(cur, row, "recovery_required", None, "execution_owner_expired")
                    self._interrupt_expired_session(cur, row)
                    return self._record({
                        **row,
                        "status": "recovery_required",
                        "error_code": "execution_owner_expired",
                    })
                self._end_attempt(cur, row, "interrupted", "owner_expired_before_execution")
                if saved_binding is not None:
                    self._event(cur, row, "agent_task.interrupted", {
                        "status": "interrupted", "reason": "owner_expired_before_execution"})
            if row.get("operation") == "manual_resume":
                try:
                    self._assert_resume_checkpoint(
                        session,
                        self._latest_snapshot(cur, row["session_id"]),
                        reference_id=row.get("reference_id"),
                        turn_id=row["turn_id"],
                    )
                except AgentSubmissionConflict:
                    self._update_status(
                        cur,
                        row,
                        "failed",
                        None,
                        "resume_checkpoint_changed",
                    )
                    cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
                    return self._record(cur.fetchone())
            framework_approval = saved_binding is not None and row.get("operation") in {"approval_resume", "approval_recover"}
            framework_reconciliation = saved_binding is not None and row.get("operation") == "framework_reconcile"
            framework_message_recovery = saved_binding is not None and row.get("operation") == "message_recover"
            if framework_message_recovery:
                from src.runtime.framework_message_recovery import require_message_recovery_claim
                from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                try:
                    require_message_recovery_claim(cur, self, session, saved_binding, row, binding_owner)
                except (FrameworkMessageStageRecoveryRequired, ValidationError) as exc:
                    self._update_status(cur, row, "recovery_required", None, "framework_message_recovery_changed")
                    logger.warning("framework_message_recovery_claim_rejected", extra={"task_id": task_id,
                        "binding_id": saved_binding.binding_id, "error_type": type(exc).__name__})
                    cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
                    return self._record(cur.fetchone())
            if framework_reconciliation:
                from src.runtime.framework_approval_recovery import require_reconciliation_claim
                from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

                try:
                    require_reconciliation_claim(cur, self, session, saved_binding, row, binding_owner)
                except (FrameworkApprovalRecoveryRequired, ValidationError) as exc:
                    self._update_status(cur, row, "recovery_required", None, "framework_reconciliation_changed")
                    logger.warning("framework_reconciliation_claim_rejected", extra={"task_id": task_id,
                        "binding_id": saved_binding.binding_id, "error_type": type(exc).__name__})
                    cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
                    return self._record(cur.fetchone())
            if framework_approval:
                from src.runtime.framework_approval_submission import assert_claimable_approval
                from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
                from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                if row["operation"] == "approval_resume":
                    cur.execute(f"SELECT id FROM {self._tasks} WHERE session_id=%s AND turn_id=%s "
                        "AND operation IN ('framework_reconcile','approval_recover') "
                        "AND status IN ('queued','waiting_resource','claimed','running') LIMIT 1",
                        (row["session_id"], row["turn_id"]))
                    if cur.fetchone() is not None:
                        raise TaskDeferred("Framework recovery must finish before sibling continuations")
                if (binding_owner["execution_state"] in {"failed", "recovery_required"}
                    and not row["interrupt_requested"]):
                    raise TaskDeferred("Framework Turn requires reconciliation before approval continuation")
                try:
                    assert_claimable_approval(cur, self, session, saved_binding, row, binding_owner)
                except (AgentSubmissionConflict, FrameworkApprovalRecoveryRequired, FrameworkMessageStageRecoveryRequired, ValidationError) as exc:
                    obsolete = isinstance(exc, AgentSubmissionConflict)
                    status = "cancelled" if obsolete else "recovery_required"
                    self._update_status(cur, row, status, None, "approval_continuation_changed")
                    logger.warning("framework_approval_claim_rejected", extra={"task_id": task_id,
                        "binding_id": saved_binding.binding_id, "status": status, "error_type": type(exc).__name__})
                    cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
                    return self._record(cur.fetchone())
            if session["lease_active"] or (
                session["status"] == "waiting_approval"
                and row.get("operation") != "approval_resume"
                and not (framework_approval or framework_reconciliation)
            ):
                raise TaskDeferred("Session is occupied or waiting for approval")
            if session["status"] == "running" and session["metadata"].get("turn_lease_token") != row["lease_token"]:
                raise TaskDeferred("Session has other active work")
            cur.execute(
                f"""SELECT id FROM {self._tasks} WHERE session_id=%s AND sequence<%s
                AND status IN ('queued','waiting_resource','claimed','running')
                AND NOT (%s AND operation='message' AND turn_id<>%s
                    AND status IN ('queued','waiting_resource'))
                AND NOT (%s AND operation IN ('approval_resume','approval_recover') AND turn_id=%s
                    AND status IN ('queued','waiting_resource'))
                LIMIT 1""",
                (row["session_id"], row["sequence"], framework_approval or framework_reconciliation or framework_message_recovery,
                    row["turn_id"], framework_reconciliation or row["operation"] == "approval_recover", row["turn_id"]),
            )
            if cur.fetchone():
                raise TaskDeferred("An earlier session input is pending")
            token, attempt_id = uuid4().hex, str(uuid4())
            cur.execute(f"""UPDATE {self._tasks} SET status='claimed', attempt_no=attempt_no+1,
                attempt_id=%s, lease_token=%s, worker_id=%s, waiting_reason=NULL, updated_at=NOW()
                WHERE id=%s RETURNING *""", (attempt_id, token, worker_id, task_id))
            row = cur.fetchone()
            cur.execute(
                f"""INSERT INTO {self._attempts}
                (id,task_id,attempt_no,worker_id,lease_token,status)
                VALUES (%s,%s,%s,%s,%s,'claimed')""",
                (attempt_id, task_id, row["attempt_no"], worker_id, token),
            )
            cur.execute(f"""UPDATE {self._session_table} SET metadata=metadata || jsonb_build_object(
                'turn_lease_turn_id',%s::text,'turn_lease_owner',%s::text,'turn_lease_token',%s::text,
                'turn_lease_expires_at',to_jsonb(clock_timestamp() + %s * INTERVAL '1 second')),
                state_revision=state_revision+1, updated_at=NOW() WHERE id=%s""",
                (
                    row["turn_id"],
                    worker_id,
                    token,
                    self._settings.orchestration.turn_execution_lease_seconds,
                    row["session_id"],
                ))
            if saved_binding is not None:
                cur.execute(f"""UPDATE {runtime_owner_table(self._settings)} SET
                    owner_epoch=owner_epoch+1,owner_token=%s,task_id=%s,attempt_id=%s,worker_id=%s,
                    expires_at=(SELECT (metadata->>'turn_lease_expires_at')::timestamptz
                        FROM {self._session_table} WHERE id=%s),execution_state='claimed'
                    WHERE binding_id=%s RETURNING owner_epoch""",
                    (token, task_id, attempt_id, worker_id, row["session_id"], saved_binding.binding_id))
                row = {**row, "runtime_owner": RuntimeExecutionOwner(
                    binding=saved_binding, owner_epoch=cur.fetchone()["owner_epoch"],
                    owner_token=token, task_id=task_id, attempt_id=attempt_id, worker_id=worker_id)}
                if row["operation"] == "approval_recover":
                    from src.runtime.framework_approval_finalization import is_finalization_task, adopt_finalization_owner
                    from src.runtime.framework_approval_settlement import is_settlement_task, adopt_settlement_owner

                    if is_finalization_task(row):
                        adopt_finalization_owner(cur, self._settings, row, row["runtime_owner"])
                    elif is_settlement_task(row):
                        adopt_settlement_owner(cur, self._settings, row, row["runtime_owner"])
            self._event(cur, row, "agent_task.claimed", ({
                "binding_id": saved_binding.binding_id,
                "owner_epoch": row["runtime_owner"].owner_epoch,
            } if saved_binding is not None else {}))
        return self._record(row)

    def _transition_sync(
        self,
        task,
        status,
        result_status,
        reason,
        resource_id,
        fencing_token,
    ):
        settlement = None
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            if task.runtime_binding is not None:
                configure_runtime_owner_transaction(cur, self._settings)
            session = self._lock_session(cur, task.session_id)
            if task.runtime_binding is not None:
                if task.runtime_owner is None:
                    raise ContinuationLeaseLostError("Framework task requires its captured execution owner")
                assert_runtime_owner(cur, self._settings, task.runtime_owner, states=("claimed", "running"))
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s FOR UPDATE", (task.id,))
            row = cur.fetchone()
            if not row or row["attempt_id"] != task.attempt_id or row["lease_token"] != task.lease_token:
                raise ContinuationLeaseLostError("Agent task attempt is no longer owned")
            self._sessions._assert_turn_lease_sync(cur, task.session_id, task.lease_token)
            if row["status"] not in {"claimed", "running"}:
                raise ContinuationLeaseLostError("Agent task attempt is already settled")
            if status in {"running", "waiting_resource"} and row["status"] != "claimed":
                raise ValueError("Only a claimed task can start or wait for admission")
            if (task.runtime_binding is not None and row["operation"] in {"message", "message_recover", "approval_resume", "approval_recover"}
                and status == "completed"):
                from src.runtime.framework_message_recovery import _stage, require_projected_settlement
                from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                approval_settlement = row["operation"] in {"approval_resume", "approval_recover"}
                if approval_settlement:
                    from src.runtime.framework_approval_finalization import _stage as approval_stage
                    from src.runtime.framework_approval_settlement import require_projected_approval_settlement

                    stage = approval_stage(cur, self._settings, task.runtime_binding)
                else:
                    stage = _stage(cur, self, task.runtime_binding)
                if stage is not None and stage.phase == "projected":
                    if result_status != "completed":
                        raise FrameworkMessageStageRecoveryRequired("Projected settlement requires a completed result")
                    settlement = (require_projected_approval_settlement(cur, self._settings, session, row, task.runtime_owner, stage)
                        if approval_settlement else require_projected_settlement(cur, self, session, row, task.runtime_owner, stage))
                    cur.execute(f"UPDATE {self._session_table} SET status='completed', "
                        "metadata=jsonb_set(metadata,'{control}',COALESCE(metadata->'control','{}'::jsonb) || %s::jsonb), "
                        "state_revision=state_revision+1,updated_at=clock_timestamp() WHERE id=%s",
                        (json.dumps(settlement.control), task.session_id))
            if status == "running":
                if not resource_id or fencing_token is None:
                    raise ValueError("Running Agent task requires resource fencing metadata")
                cur.execute(
                    f"UPDATE {self._tasks} SET resource_id=%s,fencing_token=%s WHERE id=%s RETURNING *",
                    (resource_id, fencing_token, row["id"]),
                )
                row = cur.fetchone()
            # Only explicit Worker finish observes local execution settlement.
            # Control/reaper writes use _update_status's false default; neither
            # fact proves that a separately sent model/tool operation stopped.
            self._update_status(cur, row, status, result_status, reason,
                execution_returned=status in TERMINAL_AGENT_TASK_STATUSES)
            if status == "running":
                cur.execute(
                    f"UPDATE {self._session_table} SET status='running', "
                    "state_revision=state_revision+1, updated_at=NOW() WHERE id=%s",
                    (task.session_id,),
                )
            else:
                if status in {"failed", "cancelled", "recovery_required"}:
                    self._interrupt_expired_session(cur, row)
                self._clear_lease(cur, row)

        if settlement is not None:
            logger.info("framework_approval_projected_settled" if approval_settlement else "framework_message_projected_settled",
                extra={"task_id": task.id,
                "attempt_id": task.attempt_id, "binding_id": task.runtime_binding.binding_id,
                "origin_commit_task_id": settlement.task_id,
                "result_reference": settlement.output_message_id or settlement.snapshot_id})

    def _update_status(self, cur, row, status, result_status, reason, *, execution_returned=False):
        if row.get("runtime_binding") is not None:
            saved_binding = RuntimeBinding.model_validate(row["runtime_binding"])
            cur.execute(f"""UPDATE {runtime_owner_table(self._settings)} SET execution_state=%s,
                expires_at=CASE WHEN %s='running' THEN expires_at ELSE clock_timestamp() END
                WHERE binding_id=%s AND owner_token=%s""",
                (status, status, saved_binding.binding_id, row["lease_token"]))
        cur.execute(f"""UPDATE {self._tasks} SET status=%s, result_status=%s,
            waiting_reason=%s, error_code=%s, updated_at=NOW() WHERE id=%s""",
            (status, result_status, reason if status == "waiting_resource" else None,
             reason if status != "waiting_resource" else None, row["id"]))
        self._end_attempt(cur, row, status, reason)
        if status in TERMINAL_AGENT_TASK_STATUSES and row.get("user_message_id"):
            cur.execute(f"""UPDATE {self._settings.database.postgres_message_table}
                SET metadata=metadata || jsonb_build_object('delivery_status',%s::text) WHERE id=%s""",
                (status, row["user_message_id"]))
            if cur.rowcount > 0:
                cur.execute(
                    f"UPDATE {self._session_table} "
                    "SET state_revision=state_revision+1 WHERE id=%s",
                    (row["session_id"],),
                )
        self._event(
            cur,
            row,
            f"agent_task.{status}",
            {
                "status": status,
                "result_status": result_status,
                "reason": reason,
                **({"execution_returned": execution_returned} if row.get("runtime_binding") is not None else {}),
            },
        )

    def _end_attempt(self, cur, row, status, reason):
        if row["attempt_id"]:
            cur.execute(
                f"UPDATE {self._attempts} SET status=%s,error_code=%s,"
                "finished_at=CASE WHEN %s='running' THEN NULL ELSE NOW() END "
                "WHERE id=%s",
                (status, reason, status, row["attempt_id"]),
            )

    def _interrupt_expired_session(self, cur, row):
        cur.execute(f"""UPDATE {self._session_table} SET status='interrupted', metadata=jsonb_set(metadata,
            '{{control}}', COALESCE(metadata->'control','{{}}'::jsonb) ||
            '{{"control_state":"interrupted","is_interrupted":true,"preserve_resources":true}}'::jsonb),
            state_revision=state_revision+1, updated_at=NOW()
            WHERE id=%s AND (metadata->>'turn_lease_token'=%s OR NOT(metadata ? 'turn_lease_token'))""",
            (row["session_id"], row["lease_token"]))
        self._clear_lease(cur, row)

    def _clear_lease(self, cur, row):
        cur.execute(f"""UPDATE {self._session_table} SET metadata=metadata - 'turn_lease_turn_id' -
            'turn_lease_owner' - 'turn_lease_token' - 'turn_lease_expires_at',
            state_revision=state_revision+1, updated_at=NOW()
            WHERE id=%s AND metadata->>'turn_lease_token'=%s""", (row["session_id"], row["lease_token"]))

    def _event(self, cur, row, event_type, payload):
        observed_at = datetime.now(timezone.utc)
        if row.get("runtime_binding") is not None and row.get("attempt_id") and event_type.startswith("agent_task."):
            from src.runtime.framework_worker_activity import ACTIVITY_KEY, capture_worker_activity

            cur.execute("SELECT clock_timestamp() AS observed_at")
            observed_at = cur.fetchone()["observed_at"]
            payload = {**payload, ACTIVITY_KEY: capture_worker_activity(row,
                status=event_type.removeprefix("agent_task."), result_status=payload.get("result_status"),
                execution_returned=payload.get("execution_returned", False), observed_at=observed_at)}
        self._sessions.append_event_in_transaction(cur, row["session_id"], ExecutionEvent(
            type=event_type, session_id=row["session_id"], timestamp=observed_at,
            request_id=row["request_id"], trace_id=row["trace_id"], turn_id=row["turn_id"],
            run_id=row.get("run_id"), run_item_id=row.get("run_item_id"),
            attempt_id=row.get("attempt_id"), worker_id=row.get("worker_id"),
            resource_id=row.get("resource_id"),
            payload={"task_id": row["id"], "turn_id": row["turn_id"], **payload},
        ))

    @staticmethod
    def _record(row):
        return AgentTaskExecution.model_validate({**row, "input": row["input_payload"]})
