"""Agent input inbox, execution attempts and Outbox in PostgreSQL transactions.

Uses the same session row lock and lease as PostgresSessionStore. Redis carries
only command identity; the input and execution outcome remain database facts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import (
    AgentTaskCommand, AgentTaskExecution, AgentTaskRecord, TERMINAL_AGENT_TASK_STATUSES,
)
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ExecutionEvent,
    ResumeSessionRequest,
    SendMessageRequest,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


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

    async def get(self, task_id: str) -> AgentTaskExecution:
        return await asyncio.to_thread(self._get_sync, task_id)

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
        return await asyncio.to_thread(self._reap_expired_children_sync, now, timeout_seconds)

    async def claim(self, task_id: str, *, worker_id: str) -> AgentTaskExecution:
        return await asyncio.to_thread(self._claim_sync, task_id, worker_id)

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

    async def interrupt(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._interrupt_sync, session_id)

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
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS parent_task_id TEXT")
            cur.execute(f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS root_trace_id TEXT")
            cur.execute(
                f"ALTER TABLE {self._tasks} ADD COLUMN IF NOT EXISTS result_payload JSONB"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._tasks}_parent_idx "
                f"ON {self._tasks}(parent_task_id) WHERE parent_task_id IS NOT NULL"
            )
            cur.execute(f"ALTER TABLE {self._tasks} ALTER COLUMN user_message_id DROP NOT NULL")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._attempts} (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES {self._tasks}(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL, worker_id TEXT NOT NULL, lease_token TEXT NOT NULL,
                status TEXT NOT NULL, error_code TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ, UNIQUE(task_id, attempt_no)
            )""")

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

    def _submit_message_in_transaction(
        self,
        cur,
        command,
        payload,
        message,
        *,
        effective_key,
        content_hash,
    ):
        session = self._lock_session(cur, command.session_id)
        existing = self._find_idempotent(
            cur,
            command.session_id,
            effective_key,
            content_hash,
        )
        if existing:
            return existing
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
             parent_task_id,root_trace_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) RETURNING *""",
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
            ))
        row = cur.fetchone()
        if payload.interrupt_if_busy:
            cur.execute(
                f"UPDATE {self._tasks} SET interrupt_requested=TRUE "
                "WHERE session_id=%s AND status IN ('claimed','running')",
                (command.session_id,),
            )
        cur.execute(f"""INSERT INTO {self._settings.database.postgres_message_table}
            (id,session_id,role,content,created_at,metadata) VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
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
                "queue": self._settings.orchestration.agent_task_stream,
                "source": payload.source,
            },
        )
        cur.execute(f"""INSERT INTO {self._settings.database.postgres_task_outbox_table}
            (event_key,stream,payload) VALUES (%s,%s,%s::jsonb)""",
            (
                f"{command.task_type}:{command.task_id}",
                self._settings.orchestration.agent_task_stream,
                command.model_dump_json(),
            ))
        return row

    def _assert_coordinator_child_bounds(self, cur, command):
        if not command.parent_task_id:
            raise ValueError("coordinator_parent_task_id_required")
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
            f"SELECT count(*) AS count FROM {self._tasks} WHERE parent_task_id=%s",
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
                ), updated_at=NOW()
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

    def _get_sync(self, task_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(task_id)
        return self._record(row)

    @staticmethod
    def _idempotency_key(operation: str, value: str) -> str:
        normalized = value.strip()
        return normalized if operation == "message" else f"{operation}:{normalized}"

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

    def _interrupt_sync(self, session_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            self._lock_session(cur, session_id)
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
            cur.execute(
                f"SELECT * FROM {self._tasks} WHERE operation='coordinator_child' "
                "AND status IN ('queued','waiting_resource','claimed','running') "
                "AND created_at < %s - make_interval(secs=>%s) "
                "ORDER BY sequence FOR UPDATE SKIP LOCKED LIMIT 50",
                (now, timeout_seconds),
            )
            rows = cur.fetchall()
            reaped = []
            for row in rows:
                if row["status"] in {"claimed", "running"}:
                    self._end_attempt(cur, row, "failed", "coordinator_child_timeout")
                    self._clear_lease(cur, row)
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
        cur.execute(f"""SELECT *, COALESCE((metadata->>'turn_lease_expires_at')::timestamptz > NOW(), FALSE)
            AS lease_active FROM {self._session_table} WHERE id=%s FOR UPDATE""", (session_id,))
        session = cur.fetchone()
        if session is None:
            raise KeyError(session_id)
        return session

    def _claim_sync(self, task_id, worker_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT session_id FROM {self._tasks} WHERE id=%s", (task_id,))
            identity = cur.fetchone()
            if identity is None:
                raise KeyError(task_id)
            session = self._lock_session(cur, identity["session_id"])
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s FOR UPDATE", (task_id,))
            row = cur.fetchone()
            if row["status"] in TERMINAL_AGENT_TASK_STATUSES:
                return self._record(row)
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
            if session["lease_active"] or (
                session["status"] == "waiting_approval"
                and row.get("operation") != "approval_resume"
            ):
                raise TaskDeferred("Session is occupied or waiting for approval")
            if session["status"] == "running" and session["metadata"].get("turn_lease_token") != row["lease_token"]:
                raise TaskDeferred("Session has other active work")
            cur.execute(
                f"""SELECT id FROM {self._tasks} WHERE session_id=%s AND sequence<%s
                AND status IN ('queued','waiting_resource','claimed','running')
                LIMIT 1""",
                (row["session_id"], row["sequence"]),
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
                'turn_lease_expires_at',to_jsonb(NOW() + %s * INTERVAL '1 second')) WHERE id=%s""",
                (
                    row["turn_id"],
                    worker_id,
                    token,
                    self._settings.orchestration.turn_execution_lease_seconds,
                    row["session_id"],
                ))
            self._event(cur, row, "agent_task.claimed", {})
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
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            self._lock_session(cur, task.session_id)
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s FOR UPDATE", (task.id,))
            row = cur.fetchone()
            if not row or row["attempt_id"] != task.attempt_id or row["lease_token"] != task.lease_token:
                raise ContinuationLeaseLostError("Agent task attempt is no longer owned")
            self._sessions._assert_turn_lease_sync(cur, task.session_id, task.lease_token)
            if row["status"] not in {"claimed", "running"}:
                raise ContinuationLeaseLostError("Agent task attempt is already settled")
            if status in {"running", "waiting_resource"} and row["status"] != "claimed":
                raise ValueError("Only a claimed task can start or wait for admission")
            if status == "running":
                if not resource_id or fencing_token is None:
                    raise ValueError("Running Agent task requires resource fencing metadata")
                cur.execute(
                    f"UPDATE {self._tasks} SET resource_id=%s,fencing_token=%s WHERE id=%s RETURNING *",
                    (resource_id, fencing_token, row["id"]),
                )
                row = cur.fetchone()
            self._update_status(cur, row, status, result_status, reason)
            if status == "running":
                cur.execute(f"UPDATE {self._session_table} SET status='running' WHERE id=%s", (task.session_id,))
            else:
                if status in {"failed", "cancelled", "recovery_required"}:
                    self._interrupt_expired_session(cur, row)
                self._clear_lease(cur, row)

    def _update_status(self, cur, row, status, result_status, reason):
        cur.execute(f"""UPDATE {self._tasks} SET status=%s, result_status=%s,
            waiting_reason=%s, error_code=%s, updated_at=NOW() WHERE id=%s""",
            (status, result_status, reason if status == "waiting_resource" else None,
             reason if status != "waiting_resource" else None, row["id"]))
        self._end_attempt(cur, row, status, reason)
        if status in TERMINAL_AGENT_TASK_STATUSES and row.get("user_message_id"):
            cur.execute(f"""UPDATE {self._settings.database.postgres_message_table}
                SET metadata=metadata || jsonb_build_object('delivery_status',%s::text) WHERE id=%s""",
                (status, row["user_message_id"]))
        self._event(
            cur,
            row,
            f"agent_task.{status}",
            {
                "status": status,
                "result_status": result_status,
                "reason": reason,
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
            '{{"control_state":"interrupted","is_interrupted":true,"preserve_resources":true}}'::jsonb)
            WHERE id=%s AND (metadata->>'turn_lease_token'=%s OR NOT(metadata ? 'turn_lease_token'))""",
            (row["session_id"], row["lease_token"]))
        self._clear_lease(cur, row)

    def _clear_lease(self, cur, row):
        cur.execute(f"""UPDATE {self._session_table} SET metadata=metadata - 'turn_lease_turn_id' -
            'turn_lease_owner' - 'turn_lease_token' - 'turn_lease_expires_at'
            WHERE id=%s AND metadata->>'turn_lease_token'=%s""", (row["session_id"], row["lease_token"]))

    def _event(self, cur, row, event_type, payload):
        self._sessions.append_event_in_transaction(cur, row["session_id"], ExecutionEvent(
            type=event_type, session_id=row["session_id"], timestamp=datetime.now(timezone.utc),
            request_id=row["request_id"], trace_id=row["trace_id"], turn_id=row["turn_id"],
            run_id=row.get("run_id"), run_item_id=row.get("run_item_id"),
            attempt_id=row.get("attempt_id"), worker_id=row.get("worker_id"),
            resource_id=row.get("resource_id"),
            payload={"task_id": row["id"], "turn_id": row["turn_id"], **payload},
        ))

    @staticmethod
    def _record(row):
        return AgentTaskExecution.model_validate({**row, "input": row["input_payload"]})
