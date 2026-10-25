"""Root-Turn model reservations and attempts under the application owner fence.

No upstream counterpart (checked: LangChain 1.4.6 ModelCallLimitMiddleware,
LangGraph persistence, PostgresDocumentImpactCalls, integration plan section
7.2): graph counters do not atomically account for attempts shared by branches
and replacement Workers. This store accounts; it does not retry or call a model.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_runtime_owner import (
    assert_runtime_owner, configure_runtime_owner_transaction, runtime_owner_table,
)
from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelBudgetSnapshot, FrameworkModelCallIntent,
    FrameworkModelCallRecord, FrameworkModelUsage,
)
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner


logger = logging.getLogger(__name__)


class FrameworkModelBudgetExceeded(RuntimeError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"Framework model budget exhausted: {kind}")


@dataclass(frozen=True)
class _Settlement:
    phase: str
    usage: FrameworkModelUsage | None = None
    code: str | None = None


class PostgresFrameworkModelCalls:
    def __init__(self, settings):
        self._settings = settings
        base = settings.database.postgres_agent_task_table
        self.budgets_table = f"{base}_model_budget"
        self.calls_table = f"{base}_model_call"
        for table in (self.budgets_table, self.calls_table):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid framework model ledger table identifier")

    async def initialize(self):
        # Deployment setup is explicit; never create schema in a model request.
        def setup():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.budgets_table} (
                    binding_id TEXT PRIMARY KEY REFERENCES {runtime_owner_table(self._settings)}(binding_id)
                        ON DELETE CASCADE,
                    policy JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                )""")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.calls_table} (
                    binding_id TEXT NOT NULL REFERENCES {self.budgets_table}(binding_id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL, record JSONB NOT NULL,
                    PRIMARY KEY(binding_id, attempt_id)
                )""")
        await asyncio.to_thread(setup)

    async def freeze(self, owner: RuntimeExecutionOwner, policy: FrameworkModelBudget) -> FrameworkModelBudget:
        policy = FrameworkModelBudget.model_validate(policy.model_dump())
        def save():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner, before_execution=True)
                if owner.binding.model_config_fingerprint not in policy.model_config_fingerprints.values():
                    raise ValueError("Model budget does not include the bound root model")
                cur.execute(f"INSERT INTO {self.budgets_table}(binding_id,policy) VALUES(%s,%s::jsonb) "
                    "ON CONFLICT DO NOTHING", (owner.binding.binding_id, policy.model_dump_json()))
                saved = self._policy(cur, owner.binding.binding_id)
                if saved != policy:
                    raise ValueError("Frozen model budget cannot change during a Turn")
                return saved
        result = await asyncio.to_thread(save)
        logger.info("framework_model_budget_frozen", extra={"binding_id": owner.binding.binding_id,
            "max_calls": result.max_calls, "max_tokens": result.max_tokens, "deadline_at": result.deadline_at})
        return result

    async def snapshot(self, binding: RuntimeBinding) -> FrameworkModelBudgetSnapshot:
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT runtime_binding FROM {runtime_owner_table(self._settings)} WHERE binding_id=%s",
                    (binding.binding_id,))
                saved = cur.fetchone()
                if saved is None or RuntimeBinding.model_validate(saved["runtime_binding"]) != binding:
                    raise ValueError("Model ledger binding does not match its durable identity")
                return self._snapshot(cur, binding.binding_id)
        return await asyncio.to_thread(read)

    async def prepare(self, owner: RuntimeExecutionOwner, intent: FrameworkModelCallIntent) -> FrameworkModelCallRecord:
        intent = FrameworkModelCallIntent.model_validate(intent.model_dump())
        def reserve():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner, before_execution=True)
                snapshot = self._snapshot(cur, owner.binding.binding_id)
                existing = next((call for call in snapshot.calls if call.intent.attempt_id == intent.attempt_id), None)
                if existing is not None:
                    if existing.intent != intent:
                        raise ValueError("Model attempt identity cannot change its intent")
                    return existing
                if snapshot.policy.model_config_fingerprints.get(intent.model_key) != intent.model_config_fingerprint:
                    raise ValueError("Model call configuration does not match its frozen model policy")
                self._check_branch(snapshot.calls, intent)
                self._check_retry(snapshot.calls, intent)
                self._require_time(cur, snapshot.policy)
                if snapshot.reserved_calls >= snapshot.policy.max_calls:
                    raise FrameworkModelBudgetExceeded("calls")
                if snapshot.charged_tokens + intent.reserved_tokens > snapshot.policy.max_tokens:
                    raise FrameworkModelBudgetExceeded("tokens")
                record = FrameworkModelCallRecord(binding_id=owner.binding.binding_id, intent=intent,
                    owner_epoch=owner.owner_epoch, owner_task_id=owner.task_id, owner_attempt_id=owner.attempt_id,
                    phase="ready", created_at=self._now(cur))
                return self._write(cur, record, insert=True)
        try:
            saved = await asyncio.to_thread(reserve)
        except FrameworkModelBudgetExceeded as exc:
            logger.warning("framework_model_budget_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": intent.attempt_id, "budget_kind": exc.kind})
            raise
        logger.info("framework_model_call_prepared", extra={"binding_id": saved.binding_id,
            "model_attempt_id": intent.attempt_id, "logical_call_id": intent.logical_call_id,
            "branch_id": intent.branch_id, "purpose": intent.purpose, "reserved_tokens": intent.reserved_tokens})
        return saved

    async def start(self, owner: RuntimeExecutionOwner, attempt_id: str) -> tuple[FrameworkModelCallRecord, bool]:
        def grant():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner)
                call = self._call(cur, owner.binding.binding_id, attempt_id)
                self._require_origin_owner(call, owner)
                if call.phase != "ready":
                    return call, False
                snapshot = self._snapshot(cur, owner.binding.binding_id)
                self._require_time(cur, snapshot.policy)
                # Actual usage can exceed a prior estimate after other branches
                # prepared. Recheck before granting a still-unsent request.
                if snapshot.charged_tokens > snapshot.policy.max_tokens:
                    raise FrameworkModelBudgetExceeded("tokens")
                call = call.model_copy(update={"phase": "invoking", "started_at": self._now(cur)})
                return self._write(cur, call), True
        try:
            result = await asyncio.to_thread(grant)
        except FrameworkModelBudgetExceeded as exc:
            logger.warning("framework_model_budget_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": attempt_id, "budget_kind": exc.kind})
            raise
        logger.info("framework_model_call_send_intent", extra={"binding_id": owner.binding.binding_id,
            "model_attempt_id": attempt_id, "send_owner": result[1]})
        return result

    async def complete(self, owner: RuntimeExecutionOwner, attempt_id: str, *,
                       usage: FrameworkModelUsage | None) -> FrameworkModelCallRecord:
        if usage is not None:
            usage = FrameworkModelUsage.model_validate(usage.model_dump())
        return await self._settle(owner, attempt_id, _Settlement("completed", usage=usage))

    async def mark_unknown(self, owner: RuntimeExecutionOwner, attempt_id: str, *, code: str) -> FrameworkModelCallRecord:
        # Codes describe outcomes, never raw provider errors/prompts/credentials.
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
            raise ValueError("Model unknown outcome requires a stable outcome code")
        return await self._settle(owner, attempt_id, _Settlement("unknown", code=code))

    async def _settle(self, owner, attempt_id, settlement):
        def save():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner)
                call = self._call(cur, owner.binding.binding_id, attempt_id)
                # A replacement may classify an abandoned request as unknown;
                # it cannot impersonate the original response observer.
                if settlement.phase == "completed":
                    self._require_origin_owner(call, owner)
                if call.phase == settlement.phase:
                    if call.usage != settlement.usage or call.outcome_code != settlement.code:
                        raise ValueError("Settled model call result cannot change")
                    return call
                if call.phase != "invoking":
                    raise ValueError("Model call phase cannot accept this result")
                saved = call.model_copy(update={"phase": settlement.phase, "usage": settlement.usage,
                    "outcome_code": settlement.code, "settled_at": self._now(cur)})
                return self._write(cur, saved)
        result = await asyncio.to_thread(save)
        logger.log(logging.WARNING if settlement.phase == "unknown" else logging.INFO,
            "framework_model_call_settled", extra={"binding_id": result.binding_id,
                "model_attempt_id": attempt_id, "phase": result.phase, "outcome_code": result.outcome_code,
                "usage_known": result.usage is not None,
                "charged_tokens": result.usage.total_tokens if result.usage else result.intent.reserved_tokens})
        if result.usage is not None and result.usage.total_tokens > result.intent.reserved_tokens:
            logger.warning("framework_model_usage_exceeded_reservation", extra={"binding_id": result.binding_id,
                "model_attempt_id": attempt_id, "actual_tokens": result.usage.total_tokens,
                "reserved_tokens": result.intent.reserved_tokens})
        return result

    def _owned(self, cur, owner, *, before_execution=False):
        configure_runtime_owner_transaction(cur, self._settings)
        assert_runtime_owner(cur, self._settings, owner,
            states=("claimed", "running") if before_execution else ("running",))

    def _policy(self, cur, binding_id):
        cur.execute(f"SELECT policy FROM {self.budgets_table} WHERE binding_id=%s", (binding_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("Turn has no frozen model budget")
        return FrameworkModelBudget.model_validate(row["policy"])

    def _snapshot(self, cur, binding_id):
        policy = self._policy(cur, binding_id)
        cur.execute(f"SELECT record FROM {self.calls_table} WHERE binding_id=%s "
            "ORDER BY record->>'created_at', attempt_id", (binding_id,))
        calls = [FrameworkModelCallRecord.model_validate(row["record"]) for row in cur.fetchall()]
        return FrameworkModelBudgetSnapshot(policy=policy, calls=calls)

    def _call(self, cur, binding_id, attempt_id):
        cur.execute(f"SELECT record FROM {self.calls_table} WHERE binding_id=%s AND attempt_id=%s",
            (binding_id, attempt_id))
        row = cur.fetchone()
        if row is None:
            raise KeyError("Model call attempt does not exist in this Turn")
        return FrameworkModelCallRecord.model_validate(row["record"])

    def _write(self, cur, record, *, insert=False):
        record = FrameworkModelCallRecord.model_validate(record.model_dump())
        if insert:
            cur.execute(f"INSERT INTO {self.calls_table}(binding_id,attempt_id,record) VALUES(%s,%s,%s::jsonb)",
                (record.binding_id, record.intent.attempt_id, record.model_dump_json()))
        else:
            cur.execute(f"UPDATE {self.calls_table} SET record=%s::jsonb WHERE binding_id=%s AND attempt_id=%s",
                (record.model_dump_json(), record.binding_id, record.intent.attempt_id))
            if cur.rowcount != 1:
                raise ValueError("Model call update lost its durable identity")
        return record

    @staticmethod
    def _now(cur):
        cur.execute("SELECT clock_timestamp() AS now")
        return cur.fetchone()["now"]

    def _require_time(self, cur, policy):
        if self._now(cur) >= policy.deadline_at:
            raise FrameworkModelBudgetExceeded("deadline")

    @staticmethod
    def _require_origin_owner(call, owner):
        if (call.owner_epoch != owner.owner_epoch or call.owner_task_id != owner.task_id
            or call.owner_attempt_id != owner.attempt_id):
            raise ValueError("Model call belongs to a different execution owner")

    @staticmethod
    def _check_branch(calls, intent):
        existing = [call for call in calls if call.intent.branch_id == intent.branch_id]
        if any(call.intent.parent_branch_id != intent.parent_branch_id for call in existing):
            raise ValueError("Model branch parent identity cannot change")
        if intent.parent_branch_id is not None and not any(
            call.intent.branch_id == intent.parent_branch_id for call in calls):
            raise ValueError("Model branch must reference an existing parent branch")

    @staticmethod
    def _check_retry(calls, intent):
        logical = [call for call in calls if call.intent.branch_id == intent.branch_id
            and call.intent.logical_call_id == intent.logical_call_id]
        if not logical and intent.retry_of_attempt_id is None:
            return
        predecessor = next((call for call in logical if call.intent.attempt_id == intent.retry_of_attempt_id), None)
        ignored = {"attempt_id", "retry_of_attempt_id"}
        if (predecessor is None or predecessor.phase != "unknown"
            or predecessor.intent.model_dump(exclude=ignored) != intent.model_dump(exclude=ignored)
            or any(call.intent.retry_of_attempt_id == intent.retry_of_attempt_id for call in calls)):
            raise ValueError("Model retry requires its unique unknown predecessor and unchanged request")
