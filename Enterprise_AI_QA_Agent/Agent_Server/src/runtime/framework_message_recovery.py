"""Recover an admitted message only before any orchestration has started.

No upstream counterpart (checked: FrameworkApprovalRecovery, owner/stage
journals, LangGraph interrupts/persistence, plan §14.2): a graph cannot restore
an application phase that has never entered it. Never reset a terminal task.
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from psycopg import sql
from pydantic import ValidationError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_message_stages import (
    FrameworkMessageStageRecoveryRequired, message_stage_table, validate_message_stage,
)
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls, FrameworkModelBudgetMissing
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, lock_owner_row
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_message_stage import FrameworkMessageRecoveryLink
from src.schemas.runtime_binding import RuntimeBinding


logger = logging.getLogger(__name__)
MESSAGE_EXECUTIONS = frozenset({"message", "message_recover"})


def _reject(reason):
    raise FrameworkMessageStageRecoveryRequired(reason)


def _expired(row):
    return (row["operation"] in MESSAGE_EXECUTIONS and row["status"] == "recovery_required"
        and row["error_code"] == "execution_owner_expired" and bool(row["attempt_id"])
        and not row["interrupt_requested"])


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
    return cur.fetchone()["present"]


def _stage(cur, tasks, binding):
    if not _exists(cur, message_stage_table(tasks._settings)):
        _reject("Initial message admission store is unavailable")
    cur.execute(f"SELECT record FROM {message_stage_table(tasks._settings)} WHERE binding_id=%s FOR UPDATE",
        (binding.binding_id,))
    row = cur.fetchone()
    if row is None:
        _reject("Initial message admission is missing")
    return validate_message_stage(row["record"])


def _same_input(stage, origin, row, binding):
    names = ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id",
        "root_trace_id", "input_hash", "input_payload", "user_message_id", "runtime_binding")
    if (origin is None or origin["operation"] != "message" or origin["id"] != stage.task_id
        or origin["runtime_binding"] != binding.model_dump(mode="json")
        or origin["input_hash"] != stage.input_sha256 or origin["user_message_id"] != stage.user_message_id
        or any(row[name] != origin[name] for name in names)):
        _reject("Message recovery differs from its admitted input")


def _unstarted(cur, tasks, session, binding, stage, origin):
    if stage.phase != "submitted":
        _reject("Message orchestration already started; its result requires reconciliation")
    if (session["project_id"] != origin["project_id"] or session["id"] != binding.session_id
        or origin["turn_id"] != binding.turn_id or stage.binding_id != binding.binding_id):
        _reject("Message recovery Session or project changed")
    metadata = session["metadata"] or {}
    if metadata.get("pending_turn") or (metadata.get("control") or {}).get("last_interrupt_binding_id") == binding.binding_id:
        _reject("Message recovery cannot replace a pending or explicitly interrupted Turn")
    cur.execute(f"SELECT id FROM {tasks._tasks} WHERE session_id=%s AND "
        "((turn_id=%s AND interrupt_requested) OR (turn_id<>%s AND sequence>%s AND attempt_id IS NOT NULL)) LIMIT 1",
        (binding.session_id, binding.turn_id, binding.turn_id, origin["sequence"]))
    if cur.fetchone() is not None:
        _reject("Message Turn was interrupted or a later input already acquired execution")
    ledger = PostgresFrameworkModelCalls(tasks._settings)
    if not _exists(cur, ledger.budgets_table) or not _exists(cur, ledger.calls_table):
        _reject("Initial message model admission store is unavailable")
    try:
        budget = ledger._policy(cur, binding.binding_id)
    except FrameworkModelBudgetMissing:
        _reject("Initial message model budget is unavailable")
    if tasks._v2_input_hash(origin["input_payload"], binding, budget) != stage.input_sha256:
        _reject("Message recovery budget or input digest differs from admission")
    cur.execute(f"SELECT attempt_id FROM {ledger.calls_table} WHERE binding_id=%s LIMIT 1", (binding.binding_id,))
    if cur.fetchone() is not None:
        _reject("Submitted message has model execution facts")
    cur.execute(f"SELECT role,content,metadata FROM {tasks._settings.database.postgres_message_table} "
        "WHERE id=%s AND session_id=%s", (stage.user_message_id, binding.session_id))
    message = cur.fetchone()
    if (message is None or message["role"] != "user" or message["content"] != origin["input_payload"].get("content", "").strip()
        or message["metadata"].get("turn_id") != binding.turn_id):
        _reject("Admitted user message is missing or changed")
    # These are contradictions to the causal submitted-stage proof, not a
    # substitute for that proof. Optional stores may not have been set up yet.
    candidates = [(sql.Identifier(tasks._settings.database.postgres_tool_job_table).as_string(),
        "session_id=%s AND turn_id=%s", (binding.session_id, binding.turn_id))]
    prefix = binding.checkpoint_namespace + "|"
    for name in ("checkpoints", "checkpoint_writes"):
        candidates.append((sql.Identifier(tasks._settings.deep_agents.checkpoint_schema, name).as_string(),
            "thread_id=%s AND (checkpoint_ns=%s OR left(checkpoint_ns,length(%s))=%s)",
            (binding.thread_id, binding.checkpoint_namespace, prefix, prefix)))
    for table, where, parameters in candidates:
        if _exists(cur, table):
            cur.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", parameters)
            if cur.fetchone() is not None:
                _reject("Submitted message has tool or checkpoint execution facts")
    return budget


def require_message_recovery_claim(cur, tasks, session, binding, row, owner):
    stage = _stage(cur, tasks, binding)
    if row["operation"] != "message_recover" or stage.current_task_id != row["id"] or not stage.recoveries:
        _reject("Message successor has no current admission proof")
    link = stage.recoveries[-1]
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (stage.task_id,))
    origin = cur.fetchone()
    _same_input(stage, origin, row, binding)
    cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (link.predecessor_task_id,))
    predecessor = cur.fetchone()
    if (predecessor is None or not _expired(predecessor) or predecessor["attempt_id"] != link.predecessor_attempt_id
        or row["parent_task_id"] != predecessor["id"] or row["reference_id"] != stage.task_id):
        _reject("Message predecessor no longer matches its expired attempt")
    _same_input(stage, origin, predecessor, binding)
    if (owner["task_id"] not in {predecessor["id"], row["id"]}
        or (owner["task_id"] == predecessor["id"] and (owner["attempt_id"] != predecessor["attempt_id"]
            or owner["owner_epoch"] != link.predecessor_owner_epoch))):
        _reject("Message recovery execution owner changed")
    budget = _unstarted(cur, tasks, session, binding, stage, origin)
    if approval_value_hash(budget.model_dump(mode="json")) != link.budget_sha256:
        _reject("Message successor no longer matches its admitted budget")
    return stage


class FrameworkMessageRecovery:
    def __init__(self, tasks):
        self._tasks, self._settings = tasks, tasks._settings

    async def ensure(self, task_id):
        try:
            result = await asyncio.to_thread(self._ensure, task_id)
        except (FrameworkMessageStageRecoveryRequired, ValidationError) as exc:
            logger.info("framework_message_recovery_not_admitted", extra={"task_id": task_id,
                "reason": str(exc) if isinstance(exc, FrameworkMessageStageRecoveryRequired) else type(exc).__name__})
            return None
        if result is not None:
            logger.info("framework_message_recovery_admitted", extra={"predecessor_task_id": task_id,
                "task_id": result.id, "binding_id": result.runtime_binding.binding_id})
        return result

    def _ensure(self, task_id):
        tasks = self._tasks
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            configure_runtime_owner_transaction(cur, self._settings)
            cur.execute(f"SELECT session_id,runtime_binding FROM {tasks._tasks} WHERE id=%s", (task_id,))
            identity = cur.fetchone()
            if identity is None:
                raise KeyError(task_id)
            if identity["runtime_binding"] is None:
                return None
            binding = RuntimeBinding.model_validate(identity["runtime_binding"])
            session = tasks._lock_session(cur, identity["session_id"])
            owner = lock_owner_row(cur, self._settings, binding)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (task_id,))
            predecessor = cur.fetchone()
            if not _expired(predecessor):
                return None
            stage = _stage(cur, tasks, binding)
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE operation='message_recover' AND parent_task_id=%s", (task_id,))
            existing = cur.fetchone()
            if existing is not None:
                if not any(link.task_id == existing["id"] and link.predecessor_task_id == task_id
                    and link.predecessor_attempt_id == predecessor["attempt_id"] for link in stage.recoveries):
                    _reject("Existing message successor has no matching stage proof")
                return tasks._record(existing)
            if (stage.current_task_id != task_id or session["lease_active"]
                or owner["task_id"] != task_id or owner["attempt_id"] != predecessor["attempt_id"]):
                _reject("Message predecessor is no longer the inactive execution owner")
            cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s FOR UPDATE", (stage.task_id,))
            origin = cur.fetchone()
            _same_input(stage, origin, predecessor, binding)
            budget = _unstarted(cur, tasks, session, binding, stage, origin)
            new_id = str(uuid5(NAMESPACE_URL, f"framework:message_recover:{task_id}:{predecessor['attempt_id']}"))
            command = AgentTaskCommandV2(task_id=new_id, operation="message_recover", runtime_binding=binding,
                reference_id=origin["id"], parent_task_id=task_id, **{name: origin[name] for name in
                    ("session_id", "turn_id", "project_id", "request_id", "trace_id", "run_id", "run_item_id", "root_trace_id")})
            cur.execute(f"""INSERT INTO {tasks._tasks}
                (id,session_id,operation,turn_id,project_id,request_id,trace_id,run_id,run_item_id,
                 idempotency_key,input_hash,input_payload,user_message_id,reference_id,parent_task_id,root_trace_id,runtime_binding)
                VALUES (%s,%s,'message_recover',%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                (new_id, binding.session_id, binding.turn_id, origin["project_id"], origin["request_id"], origin["trace_id"],
                    origin["run_id"], origin["run_item_id"], tasks._idempotency_key("message_recover", new_id),
                    origin["input_hash"], json.dumps(origin["input_payload"], ensure_ascii=False), origin["user_message_id"],
                    origin["id"], task_id, origin["root_trace_id"], binding.model_dump_json()))
            created = cur.fetchone()
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            link = FrameworkMessageRecoveryLink(task_id=new_id, predecessor_task_id=task_id,
                predecessor_attempt_id=predecessor["attempt_id"], predecessor_owner_epoch=owner["owner_epoch"],
                budget_sha256=approval_value_hash(budget.model_dump(mode="json")), admitted_at=now)
            updated = validate_message_stage(stage.model_copy(update={"schema_version": 2, "recoveries": [*stage.recoveries, link],
                "updated_at": now}).model_dump())
            cur.execute(f"UPDATE {message_stage_table(self._settings)} SET record=%s::jsonb WHERE task_id=%s",
                (updated.model_dump_json(), origin["id"]))
            cur.execute(f"INSERT INTO {self._settings.database.postgres_task_outbox_table}(event_key,stream,payload) "
                "VALUES (%s,%s,%s::jsonb)", (f"agent_turn:{new_id}", self._settings.orchestration.agent_task_v2_stream,
                    command.model_dump_json()))
            tasks._event(cur, created, "framework.message_recover_queued", {"origin_task_id": origin["id"],
                "predecessor_task_id": task_id, "predecessor_attempt_id": predecessor["attempt_id"],
                "binding_id": binding.binding_id})
            return tasks._record(created)
