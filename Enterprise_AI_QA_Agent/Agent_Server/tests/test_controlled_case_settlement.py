"""Original Assessment and both execution owners settle in one PostgreSQL transaction."""
import importlib
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest, wait_for_session_sql_lock
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_projection_parts import checkpoint
from tests.test_framework_chat_models import TRANSPORTS


def implementation(p):
    return importlib.import_module("src.runtime.controlled_case_settlement").ControlledCaseSettlements(p.s.h.tasks)


async def complete(p):
    with framework_owner_scope(p.h.task.runtime_owner):
        return await implementation(p).complete(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)


@pytest.mark.asyncio
async def test_original_assessment_settles_result_item_attempt_totals_and_task_once(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    before = await p.s.d.runs.get_run(p.s.detail.run.id)
    receipt = await complete(p)
    saved = await p.s.d.runs.get_run(p.s.detail.run.id)
    item = next(i for i in saved.items if i.id == p.s.item.id)
    attempt = next(a for a in saved.attempts if a.id == p.s.attempt.id)
    result = next(r for r in saved.results if r.id == receipt.result_id)
    assert item.result_id == result.id and item.status == attempt.status == result.status == asset.completion.status
    assert result.model_dump(mode="json", include=set(type(asset.completion).model_fields)) == asset.completion.model_dump(mode="json")
    assert saved.run.stats.running == before.run.stats.running - 1
    assert saved.run.stats.passed == before.run.stats.passed + 1
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "completed"
    assert checkpoint(p).checkpoint_version == 6
    before_ack = database_digest(p.s)
    assert await complete(p) == receipt
    assert await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal) == receipt
    assert database_digest(p.s) == before_ack
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"receipt": kind} for kind in ["false_actual", "empty_checks"]], indirect=True)
async def test_settlement_preserves_original_failed_or_blocked_assessment(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    receipt = await complete(p)
    result = await p.s.d.runs.get_result(receipt.result_id)
    assert result.status == asset.completion.status in {"failed", "blocked"}
    assert result.model_dump(mode="json", include=set(type(asset.completion).model_fields)) == asset.completion.model_dump(mode="json")
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "completed"
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_load_never_creates_missing_settlement_and_missing_assessment_cannot_settle(assessment_case):
    p = assessment_case
    before = database_digest(p.s)
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    with pytest.raises(RuntimeError, match="assessment is missing"):
        await complete(p)
    assert database_digest(p.s) == before
    await freeze(p)
    before = database_digest(p.s)
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["scope", "event_sink", "missing_native", "native", "second_owner", "case_lease", "agent_lease", "interrupt"])
async def test_new_settlement_requires_original_current_dual_owner_and_drained_sink(assessment_case, drift):
    p = assessment_case
    await freeze(p)
    owner, native = p.h.task.runtime_owner, deepcopy(p.result.graph_outcome.native_terminal)
    if drift == "native":
        native.values["messages"][-1].content = "foreign"
    if drift == "second_owner":
        owner = owner.model_copy(update={"controlled_case_lease": None})
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if drift == "case_lease":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif drift == "agent_lease":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=true WHERE id=%s", (p.h.task.id,))
    before = database_digest(p.s)
    with framework_owner_scope(None if drift == "scope" else owner), graph_event_scope(object() if drift == "event_sink" else None):
        with pytest.raises((RuntimeError, ValueError)):
            await implementation(p).complete(owner, native=None if drift == "missing_native" else native)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["case", "agent"])
async def test_leases_rechecked_after_source_reads_before_any_terminal_write(assessment_case, monkeypatch, lease):
    import src.runtime.controlled_case_settlement as settlements
    p = assessment_case
    await freeze(p)
    original, observed = settlements.assert_runtime_owner, []
    def fence(cur, settings, owner):
        observed.append(1)
        if lease == "case":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        return original(cur, settings, owner)
    before = database_digest(p.s)
    monkeypatch.setattr(settlements, "assert_runtime_owner", fence)
    with pytest.raises(RuntimeError):
        await complete(p)
    assert observed == [1] and database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["result", "item", "attempt", "totals", "task", "clear_lease", "checkpoint", "audit", "ack", "cancel_waiter"])
async def test_all_business_rows_and_receipt_commit_or_rollback_together(assessment_case, monkeypatch, failure, caplog):
    p = assessment_case
    await freeze(p)
    service, store, tasks = implementation(p), type(p.s.d.runs), p.s.h.tasks
    before = database_digest(p.s)
    entered, release, finished = Event(), Event(), Event()
    def failing_writer(original, *, matches=lambda args: True):
        def write(*args, **kwargs):
            value = original(*args, **kwargs)
            if matches(args):
                raise RuntimeError("isolated settlement rollback")
            return value
        return write
    material = type(service)._material
    append = tasks._sessions.append_event_in_transaction
    def append_part(*args, **kwargs):
        value = append(*args, **kwargs)
        if args[2].type == "test_run.case_settled":
            if failure == "audit":
                raise RuntimeError("isolated settlement rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return value
    def material_part(self, *args):
        try:
            value = material(self, *args)
            if failure == "ack":
                raise RuntimeError("isolated committed settlement ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_material", material_part)
        patch.setattr(tasks._sessions, "append_event_in_transaction", append_part)
        if failure in {"result", "item", "attempt", "totals", "checkpoint"}:
            name = {"result": "_complete_locked_item_in_transaction", "item": "_write_item", "attempt": "_write_attempt",
                "totals": "_refresh_run_delta_in_cursor", "checkpoint": "_write_attempt"}[failure]
            # Result case raises after the complete original kernel; the other
            # cases exercise its real intermediate SQL writes and schema6 save.
            matches = (lambda args: args[2].checkpoint_version == 6) if failure == "checkpoint" else (lambda args: True)
            patch.setattr(store, name, failing_writer(getattr(store, name), matches=matches))
        elif failure in {"task", "clear_lease"}:
            name = "_update_status" if failure == "task" else "_clear_lease"
            patch.setattr(tasks, name, failing_writer(getattr(tasks, name)))
        operation = asyncio.create_task(complete(p))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated"):
                await operation
    committed = failure in {"ack", "cancel_waiter"}
    assert checkpoint(p).checkpoint_version == (6 if committed else 5)
    if not committed:
        assert database_digest(p.s) == before
    assert any(getattr(r, "recovery_reason", None) == "settlement_write_receipt_unknown"
        and getattr(r, "automatic_replay", None) is False for r in caplog.records)
    if committed:
        def forbidden(*args, **kwargs):
            raise AssertionError("committed ACK cannot invoke any writer")
        monkeypatch.setattr(store, "_complete_locked_item_in_transaction", forbidden)
        receipt = await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    else:
        receipt = await complete(p)
    events = await p.s.h.store.list_events(p.s.session.id)
    assert len([e for e in events if e.type == "test_run.case_settled"]) == 1
    assert len([e for e in events if e.type == "agent_task.completed"]) == 1
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id == receipt.result_id
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_concurrent_settlement_uses_one_original_kernel_and_terminal_ack_is_read_only(assessment_case, monkeypatch):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    p = assessment_case
    await freeze(p)
    store, calls = type(p.s.d.runs), []
    original = store._complete_locked_item_in_transaction
    def write(self, *args):
        calls.append(1)
        return original(self, *args)
    monkeypatch.setattr(store, "_complete_locked_item_in_transaction", write)
    receipts = await asyncio.gather(*(complete(p) for _ in range(4)))
    assert receipts == [receipts[0]] * 4 and calls == [1]
    def forbidden(*args, **kwargs):
        raise AssertionError("terminal proof ACK must not rebuild, execute or write")
    monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    monkeypatch.setattr(store, "_complete_locked_item_in_transaction", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "_update_status", forbidden)
    monkeypatch.setattr(store, "_write_attempt", forbidden)
    before = database_digest(p.s)
    assert await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal) == receipts[0]
    assert await complete(p) == receipts[0] and database_digest(p.s) == before
    # The narrow terminal ACK must not make existing live ports accept a dead owner.
    with pytest.raises(RuntimeError):
        await freeze(p)
    with pytest.raises((RuntimeError, ValueError)):
        await p.s.h.tasks.finish(p.h.task, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_reader", "future_field", "future_version", "receipt_field", "receipt_version", "receipt_identity"])
async def test_schema6_is_strict_and_old_workers_defer_without_changing_original(assessment_case, change):
    p = assessment_case
    await freeze(p)
    await complete(p)
    attempt = checkpoint(p)
    raw = deepcopy(attempt.checkpoint_payload)
    if change == "future_field":
        raw["future_settlement_field"] = True
    elif change == "receipt_field":
        raw["settlement"]["future_receipt_field"] = True
    elif change == "receipt_version":
        raw["settlement"]["schema_version"] = True
    elif change == "receipt_identity":
        receipt = raw["settlement"]
        receipt["attempt_id"] = "foreign"
        receipt["receipt_sha256"] = approval_value_hash({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    attempt = attempt.model_copy(update={"checkpoint_version": 7 if change == "future_version" else 6,
        "checkpoint_payload": raw})
    before = database_digest(p.s)
    with pytest.raises((TaskDeferred, ValueError)):
        FrameworkWorkerCapabilities("a" * 64, True,
            controlled_case_checkpoint_max_version=5 if change == "old_reader" else 6).require_case_checkpoint_format(attempt)
    assert database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["task", "task_attempt", "owner", "result", "missing_result", "item",
    "case_attempt", "totals", "task_event", "audit", "missing_audit", "assessment", "asset", "input",
    "artifact", "native", "second_owner", "receipt"])
async def test_terminal_ack_rechecks_exact_originals_without_repair_or_execution(assessment_case, drift):
    p = assessment_case
    await freeze(p)
    receipt = await complete(p)
    owner, native = p.h.task.runtime_owner, deepcopy(p.result.graph_outcome.native_terminal)
    settings = p.s.h.settings
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        if drift in {"task", "task_attempt", "owner"}:
            table, key, identity = {"task": (p.s.h.tasks._tasks, "id", p.h.task.id),
                "task_attempt": (p.s.h.tasks._attempts, "id", owner.attempt_id),
                "owner": (f"{p.s.h.tasks._tasks}_runtime", "binding_id", owner.binding.binding_id)}[drift]
            field = "worker_id"
            cur.execute(f"UPDATE {table} SET {field}='foreign' WHERE {key}=%s", (identity,))
        elif drift in {"result", "missing_result"}:
            if drift == "missing_result":
                cur.execute(f"DELETE FROM {p.s.d.runs._result_table} WHERE id=%s", (receipt.result_id,))
            else:
                # This field is ignored by the public DTO but the physical
                # result row must still be covered by the atomic receipt.
                cur.execute(f"UPDATE {p.s.d.runs._result_table} SET record=record || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (receipt.result_id,))
        elif drift == "item":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=record || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (p.s.item.id,))
        elif drift == "totals":
            cur.execute(f"UPDATE {p.s.d.runs._run_table} SET record=jsonb_set(record,'{{stats,passed}}','999'::jsonb) WHERE id=%s", (p.s.item.run_id,))
        elif drift in {"task_event", "audit", "missing_audit"}:
            event_type = "agent_task.completed" if drift == "task_event" else "test_run.case_settled"
            if drift == "missing_audit":
                cur.execute(f"DELETE FROM {settings.database.postgres_event_table} WHERE session_id=%s AND type=%s", (p.s.session.id, event_type))
            else:
                cur.execute(f"UPDATE {settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE session_id=%s AND type=%s", (p.s.session.id, event_type))
        elif drift == "input":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload=input_payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (p.h.task.id,))
        elif drift == "artifact":
            cur.execute(f"UPDATE {settings.database.postgres_tool_artifact_table} SET content_text='foreign' WHERE tool_job_id=%s", (p.effects[0],))
        elif drift in {"assessment", "asset", "case_attempt", "receipt"}:
            attempt = checkpoint(p)
            raw = deepcopy(attempt.checkpoint_payload)
            if drift == "assessment":
                raw["assessment"]["completion"]["summary"] = "foreign"
                raw["assessment"]["asset_sha256"] = approval_value_hash({k: v for k, v in raw["assessment"].items() if k != "asset_sha256"})
            elif drift == "asset":
                raw["runtime_result_asset"]["runtime_result"]["output_text"] = "foreign"
            elif drift == "receipt":
                raw["settlement"]["result_id"] = "foreign"
                raw["settlement"]["receipt_sha256"] = approval_value_hash({k: v for k, v in raw["settlement"].items() if k != "receipt_sha256"})
            value = attempt.model_dump(mode="json")
            value["checkpoint_payload"] = raw
            if drift == "case_attempt":
                value["foreign"] = True
            # SQL deliberately preserves the unknown field; DTO writers would
            # silently discard it and fail to reproduce physical-row drift.
            import json
            cur.execute(f"UPDATE {p.s.d.runs._attempt_table} SET record=%s::jsonb WHERE id=%s", (json.dumps(value), attempt.id))
        elif drift == "native":
            native.values["messages"][-1].content = "foreign"
        elif drift == "second_owner":
            owner = owner.model_copy(update={"controlled_case_lease": None})
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await implementation(p).load(owner, native=native)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"auto": True, "settle": True, "transport": t} for t in TRANSPORTS], indirect=True)
async def test_explicit_runtime_assembly_settles_only_after_the_original_sink_drains(assessment_case):
    from src.runtime.execution_context import current_framework_owner, current_graph_event_sink
    p = assessment_case
    receipt = p.result.settlement_receipt
    assert receipt is not None and p.result.assessment_asset is not None
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "completed"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id == receipt.result_id
    assert current_framework_owner() is None and current_graph_event_sink() is None
    before = database_digest(p.s)
    assert await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal) == receipt
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.parametrize("services", [{}, {"session_service": object()}])
def test_runtime_settlement_requires_explicit_original_assessment_assembly(services):
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
    with pytest.raises(ValueError, match="original assessment assembly"):
        ControlledCaseRuntime(runtime_service=object(), settlement_service=object(), **services)


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["case", "agent"])
async def test_real_run_lock_wait_cannot_spend_a_pre_wait_lease(assessment_case, lease):
    p = assessment_case
    await freeze(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp()+interval '700 milliseconds' AS deadline")
        deadline = cur.fetchone()["deadline"]
        if lease == "case":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',to_jsonb(%s::text)) WHERE id=%s",
                (deadline.isoformat(), p.s.item.id))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=%s WHERE binding_id=%s", (deadline, p.s.binding.binding_id))
    before = database_digest(p.s)
    operation = None
    try:
        with postgres_connect(p.s.h.settings) as holder, holder.cursor() as cur:
            p.s.d.runs._lock_run(cur, p.s.item.run_id)
            operation = asyncio.create_task(complete(p))
            await wait_for_session_sql_lock(p.s, p.s.d.runs._run_table)
            await asyncio.sleep(.8)
        with pytest.raises(ContinuationLeaseLostError):
            await operation
    finally:
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 5


@pytest.mark.asyncio
async def test_historical_half_result_is_not_promoted_to_an_atomic_settlement(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    now = datetime.now(timezone.utc)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        p.s.h.tasks._lock_session(cur, p.s.session.id)
        item = p.s.d.runs._lock_item(cur, p.s.item.id)
        attempt = p.s.d.runs._lock_attempt(cur, item.id, item.lease_token)
        result = p.s.d.runs._complete_locked_item_in_transaction(cur, item, attempt, asset.completion, now)
    before = database_digest(p.s)
    with pytest.raises(RuntimeError):
        await complete(p)
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id == result.id
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert checkpoint(p).checkpoint_version == 5 and database_digest(p.s) == before


@pytest.mark.asyncio
async def test_terminal_receipt_allows_legitimate_sibling_totals_but_never_downgrades_itself(assessment_case):
    from src.runtime.framework_projection_source import ControlledCaseProjectionSource, save_projection_manifest
    from src.runtime.controlled_case_execution import lock_case_execution
    from src.schemas.controlled_case_admission import read_result_checkpoint
    from tests.test_controlled_case_completion_boundary import completion
    p = assessment_case
    await freeze(p)
    receipt = await complete(p)
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    sibling = next(item for item in detail.items if item.id != p.s.item.id)
    now = datetime.now(timezone.utc)
    await p.s.d.runs.start_item(sibling.id, sibling.lease_token, now)
    await p.s.d.runs.complete_item(sibling.id, sibling.lease_token, completion(sibling.lease_token, "blocked"), now)
    before = database_digest(p.s)
    assert await implementation(p).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal) == receipt
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        owner = p.h.task.runtime_owner
        p.s.h.tasks._lock_session(cur, p.s.session.id)
        case = lock_case_execution(cur, p.s.h.settings, owner.controlled_case_lease,
            task_id=owner.task_id, binding=owner.binding, require_active=False)
        source = ControlledCaseProjectionSource(case, owner, read_result_checkpoint(case.attempt))
        with pytest.raises(RuntimeError, match="read-only"):
            save_projection_manifest(cur, p.s.h.settings, source, source.source_manifest, message_version=27)
    assert checkpoint(p).checkpoint_version == 6 and database_digest(p.s) == before
