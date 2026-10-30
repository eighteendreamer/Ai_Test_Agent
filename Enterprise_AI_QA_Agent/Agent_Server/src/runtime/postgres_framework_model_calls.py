"""Root-Turn model reservations and attempts under the application owner fence.

No upstream counterpart (checked: LangChain 1.4.6 ModelCallLimitMiddleware,
LangGraph persistence, PostgresDocumentImpactCalls, integration plan section
7.2): graph counters do not atomically account for attempts shared by branches
and replacement Workers. This store accounts; it does not retry or call a model.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_identifiers import postgres_relation_parts
from src.runtime.postgres_runtime_owner import (
    assert_runtime_owner, configure_runtime_owner_transaction, lock_owner_row, runtime_owner_table,
)
from src.runtime.framework_tool_activity import read_tool_activity
from src.runtime.framework_worker_activity import read_worker_activity
from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelBudgetSnapshot, FrameworkModelCallIntent,
    FrameworkModelCallRecord, FrameworkModelUsage, FrameworkModelObject, FrameworkModelResponseObservation,
    FrameworkModelWireObservation,
    FrameworkModelReservationOwner, FrameworkModelReservationTransfer,
    response_observation_id, union_activity_intervals,
)
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner


logger = logging.getLogger(__name__)


class FrameworkModelBudgetExceeded(RuntimeError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"Framework model budget exhausted: {kind}")


class FrameworkModelBudgetMissing(ValueError):
    """A historical binding has no admitted budget; never infer a fresh one."""


class FrameworkModelRecoveryRequired(ValueError):
    def __init__(self, reason: str, call: FrameworkModelCallRecord):
        self.reason, self.attempt_id, self.phase = reason, call.intent.attempt_id, call.phase
        super().__init__(f"Model retry requires reconciliation: {reason}")


@dataclass(frozen=True)
class _Settlement:
    phase: str
    usage: FrameworkModelUsage | None = None
    code: str | None = None
    response_id: str | None = None


class PostgresFrameworkModelCalls:
    def __init__(self, settings):
        self._settings = settings
        base = settings.database.postgres_agent_task_table
        self.budgets_table = f"{base}_model_budget"
        self.calls_table = f"{base}_model_call"
        self.tool_jobs_table = settings.database.postgres_tool_job_table
        postgres_relation_parts(self.tool_jobs_table)
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
                return self._freeze_policy(cur, owner.binding, policy)
        result = await asyncio.to_thread(save)
        logger.info("framework_model_budget_frozen", extra={"binding_id": owner.binding.binding_id,
            "max_calls": result.max_calls, "max_tokens": result.max_tokens, "deadline_at": result.deadline_at})
        return result

    def freeze_submission(self, cur, binding: RuntimeBinding, policy: FrameworkModelBudget) -> FrameworkModelBudget:
        """Freeze in the caller's task/input/outbox transaction, before claim.

        A started historical Turn without a budget requires explicit recovery;
        submission must not manufacture a fresh allowance for its past calls.
        """
        policy = FrameworkModelBudget.model_validate(policy.model_dump())
        cur.execute(f"SELECT id FROM {self._settings.database.postgres_session_table} WHERE id=%s FOR UPDATE",
            (binding.session_id,))
        if cur.fetchone() is None:
            raise ValueError("Model budget submission has no durable Session")
        saved_owner = lock_owner_row(cur, self._settings, binding)
        if saved_owner["owner_epoch"] != 0 or saved_owner["execution_state"] != "idle":
            saved = self._policy(cur, binding.binding_id)
            if saved != policy:
                raise ValueError("Frozen model budget cannot change during a Turn")
            return saved
        return self._freeze_policy(cur, binding, policy)

    def _freeze_policy(self, cur, binding, policy):
        if binding.model_config_fingerprint not in policy.model_config_fingerprints.values():
            raise ValueError("Model budget does not include the bound root model")
        cur.execute(f"INSERT INTO {self.budgets_table}(binding_id,policy) VALUES(%s,%s::jsonb) "
            "ON CONFLICT DO NOTHING", (binding.binding_id, policy.model_dump_json()))
        saved = self._policy(cur, binding.binding_id)
        if saved != policy:
            raise ValueError("Frozen model budget cannot change during a Turn")
        return saved

    async def snapshot(self, binding: RuntimeBinding) -> FrameworkModelBudgetSnapshot:
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._require_binding(cur, binding)
                return self._snapshot(cur, binding.binding_id)
        return await asyncio.to_thread(read)

    async def root_activity_intervals(
        self,
        binding: RuntimeBinding,
        *,
        as_of: datetime | None = None,
    ) -> list[tuple[datetime, datetime]]:
        """Read model plus Worker-attempt activity without counting approval wait.

        The task-attempt table is the existing durable owner timeline.  Only
        framework execution operations are included; application-only
        ``approval_recover`` settlement/finalization tasks are deliberately
        excluded.  An approval wait therefore appears as a gap between the
        finished original attempt and a later ``approval_resume`` attempt.
        """
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return self.root_activity_intervals_in_transaction(cur, binding, as_of=as_of)

        result = await asyncio.to_thread(read)
        logger.info("framework_root_activity_read", extra={
            "binding_id": binding.binding_id,
            "interval_count": len(result),
            "activity_seconds": sum((end - start).total_seconds() for start, end in result),
            "open_tail_observed": as_of is not None,
        })
        return result

    def root_activity_intervals_in_transaction(
        self,
        cur,
        binding: RuntimeBinding,
        *,
        as_of: datetime | None = None,
        snapshot: FrameworkModelBudgetSnapshot | None = None,
        include_tool_jobs: bool = True,
    ) -> list[tuple[datetime, datetime]]:
        """Read persisted execution envelopes, not exact active/handler time.

        Owner expiry fences platform writes; it does not prove an external
        request stopped. Open intervals retain the explicit observation bound.
        """
        self._require_binding(cur, binding)
        snapshot = snapshot or self._snapshot(cur, binding.binding_id)
        intervals = [
            (call.started_at, call.settled_at)
            for call in snapshot.calls
            if call.started_at is not None
        ]
        tasks_table = self._settings.database.postgres_agent_task_table
        attempts_table = self._settings.database.postgres_agent_attempt_table
        event_table = getattr(self._settings.database, "postgres_event_table", None)

        # Prefer the server-owned Worker journal when it exists.  Attempt.started_at
        # is a claim/admission timestamp, so using it as an execution start would
        # charge resource and queue waits to the root activity envelope.  Historical
        # rows without the journal retain the old envelope as an explicitly weaker
        # compatibility source; they cannot be backfilled from newer event facts.
        if event_table is None:
            cur.execute(
                f"""SELECT attempt.started_at, attempt.finished_at
                FROM {tasks_table} AS task
                JOIN {attempts_table} AS attempt ON attempt.task_id=task.id
                WHERE task.runtime_binding->>'binding_id'=%s
                  AND task.session_id=%s AND task.turn_id=%s
                  AND task.operation IN ('message','message_recover','approval_resume')""",
                (binding.binding_id, binding.session_id, binding.turn_id),
            )
            intervals.extend((row["started_at"], row["finished_at"]) for row in cur.fetchall())
        else:
            cur.execute(
                f"""SELECT task.id, task.session_id, task.turn_id, task.runtime_binding,
                    task.attempt_id, task.status
                FROM {tasks_table} AS task
                WHERE task.runtime_binding->>'binding_id'=%s
                  AND task.session_id=%s AND task.turn_id=%s
                  AND task.operation IN ('message','message_recover','approval_resume')""",
                (binding.binding_id, binding.session_id, binding.turn_id),
            )
            for task in cur.fetchall():
                cur.execute(
                    f"SELECT id, worker_id, status, started_at, finished_at FROM {attempts_table} "
                    "WHERE task_id=%s ORDER BY attempt_no",
                    (task["id"],),
                )
                attempts = cur.fetchall()
                cur.execute(
                    f"SELECT session_id, type, timestamp, payload FROM {event_table} "
                    "WHERE session_id=%s AND payload->>'task_id'=%s AND payload ? %s ORDER BY event_sequence",
                    (task["session_id"], task["id"], "framework_worker_activity"),
                )
                journals = read_worker_activity(task, attempts, cur.fetchall())
                for attempt in attempts:
                    journal = journals.get(attempt["id"])
                    if journal is not None:
                        intervals.extend(journal.activity_intervals(as_of=as_of))
                    elif attempt["started_at"] is not None:
                        intervals.append((attempt["started_at"], attempt["finished_at"]))
        if include_tool_jobs:
            # A journalized ToolJob exposes each running segment around an
            # explicit wait.  Legacy rows retain the old start/end envelope,
            # but a waiting state alone must never invent a pre-wait segment.
            if event_table is None:
                cur.execute(
                    f"""SELECT job.started_at, job.completed_at
                    FROM {self.tool_jobs_table} AS job
                    WHERE job.metadata->>'framework_binding_id'=%s
                      AND job.session_id=%s AND job.turn_id=%s
                      AND job.started_at IS NOT NULL
                      AND job.status IN ('running','resume_requested','completed','partial','failed','cancelled')""",
                    (binding.binding_id, binding.session_id, binding.turn_id),
                )
                intervals.extend((row["started_at"], row["completed_at"]) for row in cur.fetchall())
            else:
                cur.execute(
                    f"""SELECT job.id, job.session_id, job.turn_id, job.status,
                        job.started_at, job.completed_at, job.metadata
                    FROM {self.tool_jobs_table} AS job
                    WHERE job.metadata->>'framework_binding_id'=%s
                      AND job.session_id=%s AND job.turn_id=%s""",
                    (binding.binding_id, binding.session_id, binding.turn_id),
                )
                for job in cur.fetchall():
                    journal = read_tool_activity(job)
                    if journal is not None:
                        intervals.extend(journal.activity_intervals(as_of=as_of))
                    elif (job["started_at"] is not None and job["status"] in {
                        "running", "resume_requested", "completed", "partial", "failed", "cancelled",
                    }):
                        intervals.append((job["started_at"], job["completed_at"]))
        return union_activity_intervals(intervals, as_of=as_of)

    async def root_activity_seconds(
        self,
        binding: RuntimeBinding,
        *,
        as_of: datetime | None = None,
    ) -> float:
        intervals = await self.root_activity_intervals(binding, as_of=as_of)
        return sum((end - start).total_seconds() for start, end in intervals)

    async def frozen_policy(self, binding: RuntimeBinding) -> FrameworkModelBudget:
        """Read admission policy without loading the Turn's entire call history."""
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._require_binding(cur, binding)
                return self._policy(cur, binding.binding_id)
        return await asyncio.to_thread(read)

    def _require_binding(self, cur, binding):
        cur.execute(f"SELECT runtime_binding FROM {runtime_owner_table(self._settings)} WHERE binding_id=%s",
            (binding.binding_id,))
        saved = cur.fetchone()
        if saved is None or RuntimeBinding.model_validate(saved["runtime_binding"]) != binding:
            raise ValueError("Model ledger binding does not match its durable identity")

    async def prepare(self, owner: RuntimeExecutionOwner, intent: FrameworkModelCallIntent, *,
                      request: FrameworkModelObject | None = None) -> FrameworkModelCallRecord:
        return await self._prepare(owner, intent, request=request, initial=False)

    async def prepare_initial(self, owner: RuntimeExecutionOwner, intent: FrameworkModelCallIntent, *,
                              request: FrameworkModelObject | None = None) -> FrameworkModelCallRecord:
        """Admit a logical position once, returning its original attempt on reentry.

        No upstream counterpart (checked: LangGraph task replay, _check_retry,
        plan §14.2): request digests validate input; they do not define position.
        Explicit retries continue through prepare with their original lineage.
        """
        if intent.position_version != 1 or intent.retry_of_attempt_id is not None:
            raise ValueError("Initial model admission requires an explicit versioned call position")
        try:
            return await self._prepare(owner, intent, request=request, initial=True)
        except FrameworkModelRecoveryRequired as exc:
            logger.warning("framework_model_position_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": exc.attempt_id, "reason": exc.reason, "phase": exc.phase})
            raise

    async def _prepare(self, owner, intent, *, request, initial):
        intent = FrameworkModelCallIntent.model_validate(intent.model_dump())
        if request is not None:
            request = FrameworkModelObject.model_validate(request.model_dump())
            if request.sha256 != intent.request_sha256:
                raise ValueError("Model request asset does not match the intent digest")
        def reserve():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner, before_execution=True)
                snapshot = self._snapshot(cur, owner.binding.binding_id)
                if initial:
                    original = self._initial_position(snapshot.calls, intent)
                    if original is not None:
                        return original
                existing = next((call for call in snapshot.calls if call.intent.attempt_id == intent.attempt_id), None)
                if existing is not None:
                    if existing.intent != intent:
                        raise ValueError("Model attempt identity cannot change its intent")
                    # Legacy records created before evidence assets have no
                    # request reference. Preserve that historical fact during
                    # recovery; never fabricate an asset or rewrite its
                    # original request from a later projection.
                    if existing.request is not None and existing.request != request:
                        raise ValueError("Model request asset cannot change after preparation")
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
                    observer_token_sha256=hashlib.sha256(owner.owner_token.encode()).hexdigest(), request=request,
                    phase="ready", created_at=self._now(cur))
                return self._write(cur, record, insert=True)
        try:
            saved = await asyncio.to_thread(reserve)
        except FrameworkModelBudgetExceeded as exc:
            logger.warning("framework_model_budget_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": intent.attempt_id, "budget_kind": exc.kind})
            raise
        logger.info("framework_model_call_prepared", extra={"binding_id": saved.binding_id,
            "model_attempt_id": saved.intent.attempt_id, "logical_call_id": saved.intent.logical_call_id,
            "branch_id": saved.intent.branch_id, "purpose": saved.intent.purpose,
            "reserved_tokens": saved.intent.reserved_tokens})
        return saved

    @staticmethod
    def _initial_position(calls, intent):
        originals = [call for call in calls if call.intent.logical_call_id == intent.logical_call_id
            and call.intent.branch_id == intent.branch_id and call.intent.retry_of_attempt_id is None]
        if len(originals) > 1:
            raise FrameworkModelRecoveryRequired("position_has_multiple_origins", originals[0])
        if originals:
            original = originals[0]
            if original.intent.model_dump(exclude={"attempt_id"}) != intent.model_dump(exclude={"attempt_id"}):
                raise FrameworkModelRecoveryRequired("position_input_changed", original)
            return original
        legacy = next((call for call in calls if call.intent.position_version is None), None)
        if legacy is not None:
            raise FrameworkModelRecoveryRequired("legacy_position_unavailable", legacy)
        return None

    async def attach_request(self, owner: RuntimeExecutionOwner, attempt_id: str, *,
                             request: FrameworkModelObject) -> FrameworkModelCallRecord:
        """Bind immutable bytes to an admitted unsent intent before permission."""
        request = FrameworkModelObject.model_validate(request.model_dump())

        def attach():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner, before_execution=True)
                call = self._call(cur, owner.binding.binding_id, attempt_id)
                self._require_origin_owner(call, owner)
                if request.sha256 != call.intent.request_sha256:
                    raise ValueError("Model request asset does not match its admitted intent digest")
                if call.request is not None:
                    if call.request.sha256 != request.sha256 or call.request.size_bytes != request.size_bytes:
                        raise ValueError("Model request asset cannot change after admission")
                    return call
                if call.phase != "ready" or call.intent.position_version != 1:
                    raise FrameworkModelRecoveryRequired("request_asset_cannot_be_reconstructed", call)
                return self._write(cur, call.model_copy(update={"request": request}))

        saved = await asyncio.to_thread(attach)
        logger.info("framework_model_request_asset_bound", extra={"binding_id": saved.binding_id,
            "model_attempt_id": saved.intent.attempt_id, "request_sha256": saved.intent.request_sha256})
        return saved

    async def append_response_observation(self, owner: RuntimeExecutionOwner, attempt_id: str, *,
                                          response: FrameworkModelObject, response_kind: str
                                          ) -> FrameworkModelResponseObservation:
        """Append evidence from the original observer without granting authority.

        No upstream counterpart (checked: LangGraph checkpoints, plan section
        6.2, PostgresToolJobStore.append_framework_late_output): model responses
        may arrive after lease expiry or an unconfirmed completion commit.
        """
        response = FrameworkModelObject.model_validate(response.model_dump())

        def append():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._require_binding(cur, owner.binding)
                # Evidence-only writes deliberately do not require a live lease.
                # Lock the call, as settlement does, to preserve both facts.
                call = self._call(cur, owner.binding.binding_id, attempt_id)
                self._require_origin_owner(call, owner)
                digest = hashlib.sha256(owner.owner_token.encode()).hexdigest()
                if call.observer_token_sha256 is None or not hmac.compare_digest(call.observer_token_sha256, digest):
                    raise ValueError("Model response observer does not match the original attempt")
                if call.started_at is None or call.phase == "ready":
                    raise ValueError("Model response evidence requires a sent attempt")
                observation_id = response_observation_id(call.binding_id, attempt_id, response_kind, response.sha256)
                # Validate kind even for duplicate submissions.
                observation = FrameworkModelResponseObservation(observation_id=observation_id,
                    response=response, response_kind=response_kind, observed_at=self._now(cur))
                existing = next((item for item in call.response_observations
                    if item.observation_id == observation_id), None)
                if existing is not None:
                    if existing.response.size_bytes != response.size_bytes:
                        raise ValueError("Model response digest has conflicting object size")
                    return existing
                self._write(cur, call.model_copy(update={
                    "response_observations": [*call.response_observations, observation]}))
                return observation

        result = await asyncio.to_thread(append)
        logger.info("framework_model_response_observed", extra={"binding_id": owner.binding.binding_id,
            "model_attempt_id": attempt_id, "owner_epoch": owner.owner_epoch,
            "observation_id": result.observation_id, "authoritative": False})
        return result

    async def append_wire_observation(self, owner: RuntimeExecutionOwner, attempt_id: str, *,
                                      observation: dict) -> FrameworkModelWireObservation:
        def append():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._require_binding(cur, owner.binding)
                call = self._call(cur, owner.binding.binding_id, attempt_id)
                self._require_origin_owner(call, owner)
                if call.observer_token_sha256 is None:
                    raise ValueError("Model wire observer identity is unavailable")
                request, response = observation.get("request"), observation.get("response")
                identity = json.dumps([call.binding_id, attempt_id,
                    request.sha256 if request else None, response.sha256 if response else None,
                    observation.get("attempt_sequence")], separators=(",", ":"))
                item = FrameworkModelWireObservation(observation_id=hashlib.sha256(identity.encode()).hexdigest(),
                    **observation, observed_at=self._now(cur))
                existing = next((row for row in call.wire_observations
                    if row.observation_id == item.observation_id), None)
                if existing is not None:
                    return existing
                self._write(cur, call.model_copy(update={
                    "wire_observations": [*call.wire_observations, item]}))
                return item
        result = await asyncio.to_thread(append)
        logger.info("framework_model_wire_observed", extra={"binding_id": owner.binding.binding_id,
            "model_attempt_id": attempt_id, "observation_id": result.observation_id,
            "authoritative": False})
        return result

    async def transfer_ready(self, owner: RuntimeExecutionOwner, *,
                             expected: FrameworkModelCallRecord) -> FrameworkModelCallRecord:
        """Explicitly reconcile an unsent reservation without spending a new slot.

        No upstream counterpart (checked: LangGraph checkpointers/persistence,
        PostgresDocumentImpactCalls, plan §14.2): the application owns model
        send permission and replacement leases. This does not resume a task or
        grant orchestration admission. Callers must supply the exact durable
        predecessor; normal model reentry never invokes this port implicitly.
        """
        expected = FrameworkModelCallRecord.model_validate(expected.model_dump())
        successor = FrameworkModelReservationOwner(owner_epoch=owner.owner_epoch,
            owner_task_id=owner.task_id, owner_attempt_id=owner.attempt_id,
            observer_token_sha256=hashlib.sha256(owner.owner_token.encode()).hexdigest())
        digest = hashlib.sha256(json.dumps(expected.model_dump(mode="json"), sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

        def transfer():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner, before_execution=True)
                self._require_unsent_reservation(expected)
                if expected.binding_id != owner.binding.binding_id:
                    raise ValueError("Model reservation transfer binding differs from the execution owner")
                predecessor = self._reservation_owner(expected)
                call = self._call(cur, owner.binding.binding_id, expected.intent.attempt_id)
                # Resolve an uncertain transfer commit, including after its
                # unique start has committed. Never rewind the persisted phase.
                if (call.reservation_transfers and self._reservation_owner(call) == successor
                    and call.reservation_transfers[-1].predecessor_record_sha256 == digest
                    and call.reservation_transfers[-1].predecessor == predecessor):
                    return call
                if call != expected:
                    raise FrameworkModelRecoveryRequired("reservation_predecessor_changed", call)
                self._require_unsent_reservation(call)
                snapshot = self._snapshot(cur, owner.binding.binding_id)
                self._require_time(cur, snapshot.policy)
                if snapshot.charged_tokens > snapshot.policy.max_tokens:
                    raise FrameworkModelBudgetExceeded("tokens")
                if snapshot.policy.model_config_fingerprints.get(call.intent.model_key) != call.intent.model_config_fingerprint:
                    raise ValueError("Model reservation configuration differs from the frozen budget")
                link = FrameworkModelReservationTransfer(predecessor=predecessor, successor=successor,
                    predecessor_record_sha256=digest, transferred_at=self._now(cur))
                return self._write(cur, call.model_copy(update={"schema_version": 2,
                    **successor.model_dump(), "reservation_transfers": [*call.reservation_transfers, link]}))

        try:
            saved = await asyncio.to_thread(transfer)
        except Exception as exc:
            logger.warning("framework_model_reservation_transfer_rejected", extra={
                "binding_id": owner.binding.binding_id, "model_attempt_id": expected.intent.attempt_id,
                "owner_epoch": owner.owner_epoch, "error_type": type(exc).__name__})
            raise
        logger.info("framework_model_reservation_transferred", extra={
            "binding_id": saved.binding_id, "model_attempt_id": saved.intent.attempt_id,
            "predecessor_task_id": expected.owner_task_id,
            "predecessor_attempt_id": expected.owner_attempt_id, "owner_task_id": owner.task_id,
            "owner_attempt_id": owner.attempt_id, "owner_epoch": owner.owner_epoch,
            "transfer_count": len(saved.reservation_transfers), "phase": saved.phase})
        return saved

    @staticmethod
    def _reservation_owner(call):
        return FrameworkModelReservationOwner(**{field: getattr(call, field)
            for field in FrameworkModelReservationOwner.model_fields})

    @staticmethod
    def _require_unsent_reservation(call):
        if (call.phase != "ready" or call.intent.position_version != 1
            or call.started_at is not None or call.settled_at is not None or call.usage is not None
            or call.outcome_code is not None or call.response_observations or call.wire_observations
            or call.observer_token_sha256 is None
            or (call.request is not None and call.request.sha256 != call.intent.request_sha256)):
            raise FrameworkModelRecoveryRequired("reservation_is_not_provably_unsent", call)

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
                       usage: FrameworkModelUsage | None, response_id: str | None = None) -> FrameworkModelCallRecord:
        if usage is not None:
            usage = FrameworkModelUsage.model_validate(usage.model_dump())
        return await self._settle(owner, attempt_id, _Settlement("completed", usage=usage, response_id=response_id))

    async def verify_completed(self, owner: RuntimeExecutionOwner, *, expected: FrameworkModelCallRecord) -> FrameworkModelCallRecord:
        """Read the exact completed result under the current owner, without importing it.

        No upstream counterpart (checked: framework response observations,
        LangGraph persistence, plan §14.2): an application DTO has no graph
        task result to read. This verifies a settled asset; it grants no replay.
        """
        expected = FrameworkModelCallRecord.model_validate_json(expected.model_dump_json())

        def verify():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, owner)
                if expected.binding_id != owner.binding.binding_id:
                    raise ValueError("Completed model result differs from the current binding")
                call = self._call(cur, owner.binding.binding_id, expected.intent.attempt_id)
                if call != expected:
                    raise FrameworkModelRecoveryRequired("completed_result_changed", call)
                if call.phase != "completed" or call.intent.position_version != 1 or call.settled_response_id is None:
                    raise FrameworkModelRecoveryRequired("completed_response_unavailable", call)
                return call

        try:
            return await asyncio.to_thread(verify)
        except Exception as exc:
            logger.warning("framework_model_result_read_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": expected.intent.attempt_id, "error_type": type(exc).__name__})
            raise

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
                    if (call.usage != settlement.usage or call.outcome_code != settlement.code
                        or call.settled_response_id != settlement.response_id):
                        raise ValueError("Settled model call result cannot change")
                    return call
                if call.phase != "invoking":
                    raise ValueError("Model call phase cannot accept this result")
                saved = call.model_copy(update={"phase": settlement.phase, "usage": settlement.usage,
                    "outcome_code": settlement.code, "settled_at": self._now(cur),
                    "settled_response_id": settlement.response_id,
                    "schema_version": 3 if settlement.response_id is not None else call.schema_version})
                return self._write(cur, saved)
        result = await asyncio.to_thread(save)
        logger.log(logging.WARNING if settlement.phase == "unknown" else logging.INFO,
            "framework_model_call_settled", extra={"binding_id": result.binding_id,
                "model_attempt_id": attempt_id, "phase": result.phase, "outcome_code": result.outcome_code,
                "usage_known": result.usage is not None,
                "settled_response_id": result.settled_response_id,
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
            raise FrameworkModelBudgetMissing("Turn has no frozen model budget")
        return FrameworkModelBudget.model_validate(row["policy"])

    def _snapshot(self, cur, binding_id):
        policy = self._policy(cur, binding_id)
        cur.execute(f"SELECT attempt_id,record FROM {self.calls_table} WHERE binding_id=%s "
            "ORDER BY record->>'created_at', attempt_id", (binding_id,))
        calls = [self._decode_call(row["record"], binding_id, row["attempt_id"]) for row in cur.fetchall()]
        return FrameworkModelBudgetSnapshot(policy=policy, calls=calls)

    def _call(self, cur, binding_id, attempt_id):
        cur.execute(f"SELECT record FROM {self.calls_table} WHERE binding_id=%s AND attempt_id=%s FOR UPDATE",
            (binding_id, attempt_id))
        row = cur.fetchone()
        if row is None:
            raise KeyError("Model call attempt does not exist in this Turn")
        return self._decode_call(row["record"], binding_id, attempt_id)

    @staticmethod
    def _decode_call(value, binding_id, attempt_id):
        record = FrameworkModelCallRecord.model_validate(value)
        if record.binding_id != binding_id or record.intent.attempt_id != attempt_id:
            raise FrameworkModelRecoveryRequired("ledger_identity_mismatch", record)
        return record

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
