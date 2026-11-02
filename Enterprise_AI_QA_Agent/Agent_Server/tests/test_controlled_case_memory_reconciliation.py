"""Formal post-commit Memory evidence has its own read-only reconciliation scope."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import multiprocessing
from threading import Event
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeExecutionOwner
from src.runtime.task_deferred import TaskDeferred
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_assessment_reconciliation import lost
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_finalization import finalize
from tests.test_controlled_case_projection_parts import checkpoint, observation_services
from tests.test_controlled_case_settlement import implementation, complete
from tests.test_controlled_case_settlement_ack import originals_digest


pytestmark = [pytest.mark.asyncio, pytest.mark.parametrize("assessment_case", [{"finalize": False}], indirect=True)]


@pytest_asyncio.fixture
async def memory_case(assessment_case, request, monkeypatch):
    import src.runtime.framework_turn_memory_projection as memory_parts

    p = assessment_case
    options = getattr(request, "param", {})
    async with observation_services(p) as (store, memories, build):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()
        record, recorded = memory_parts.record_memory_row_projection, []
        def fail(*args, **kwargs):
            result = record(*args, **kwargs)
            recorded.append(result)
            if len(recorded) == options["fail_after"]:
                raise RuntimeError("isolated derived Memory row rollback after application commit")
            return result
        with monkeypatch.context() as patch:
            if options.get("fail_after"):
                patch.setattr(memory_parts, "record_memory_row_projection", fail)
            response = await finalize(p)
        assert response.session.status.value == "completed"
        assessment = await (freeze(p) if options.get("live") else lost(p))
        parts = checkpoint(p).checkpoint_payload["projection_receipts"]
        plan, execution = parts["post_commit_memory_plan"], parts["post_commit_memory_execution"]
        assert len(plan["requests"]) > 1
        if options.get("fail_after"):
            assert len(recorded) == options["fail_after"]
            if options["fail_after"] == 1:
                assert execution is None
            else:
                assert execution["state"] == "running"
        else:
            assert execution["state"] == "completed"
        yield SimpleNamespace(p=p, store=store, memories=memories, assessment=assessment, plan=plan, execution=execution)


def digest(m):
    with postgres_connect(m.p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows "
            f"FROM {m.p.s.h.settings.database.postgres_memory_table} t")
        rows = cur.fetchone()["rows"]
    return approval_value_hash({"originals": originals_digest(m.p), "memory_rows": rows})


async def read(m):
    return await implementation(m.p).read_expired_memory(delivery(m.p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, False))


async def test_expired_formal_memory_reconciliation_verifies_real_rows_without_owner_or_replay(memory_case, monkeypatch, caplog):
    m, p = memory_case, memory_case.p
    before = digest(m)
    def forbidden(*args, **kwargs):
        raise AssertionError("Memory evidence reads cannot acquire execution authority or write")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(m.memories, "execute_turn_memory_plan", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    for name in ("claim", "claim_controlled_case", "begin", "finish", "renew_framework_execution", "_update_status"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    for name in ("_write_item", "_write_attempt"):
        monkeypatch.setattr(type(p.s.d.runs), name, forbidden)
    monkeypatch.setattr(p.s.h.store, "append_event_in_transaction", forbidden)
    caplog.set_level("INFO")
    result = await read(m)
    assert result.scope == "derived_turn_memory_reconciliation_only" and result.status == "completed"
    assert result.reason_code == "all_rows_verified" and result.request_ids == [r["request_id"] for r in m.plan["requests"]]
    assert result.missing_request_ids == result.unknown_request_ids == result.drifted_request_ids == []
    assert await read(m) == result
    assert digest(m) == before and len(p.requests) == 2 and len(p.effects) == 1
    assert p.h.task.lease_token not in result.model_dump_json() and p.s.item.lease_token not in result.model_dump_json()
    assert any(r.message == "controlled_case_expired_memory_read" and r.automatic_replay is False for r in caplog.records)


@pytest.mark.parametrize("changed", ["missing", "content", "metadata", "embedding", "updated_at"])
async def test_physical_derived_row_drift_is_not_hidden_by_completed_receipts(memory_case, changed):
    m, p = memory_case, memory_case.p
    identity = m.plan["requests"][0]["request_id"]
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        table = p.s.h.settings.database.postgres_memory_table
        if changed == "missing":
            cur.execute(f"DELETE FROM {table} WHERE id=%s", (identity,))
        else:
            updates = {"content": "content='changed physical derived row'", "metadata": "metadata=metadata || '{\"foreign\":true}'::jsonb",
                "embedding": "embedding=CASE WHEN embedding IS NULL THEN '[1,2,3]'::vector ELSE NULL END",
                "updated_at": "updated_at=updated_at+interval '1 second'"}
            cur.execute(f"UPDATE {table} SET {updates[changed]} WHERE id=%s", (identity,))
        assert cur.rowcount == 1
    before = digest(m)
    result = await read(m)
    assert result.status == "drifted" and result.reason_code == "row_evidence_drifted"
    assert result.drifted_request_ids == [identity]
    assert await implementation(p).read_expired_assessment(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a"*64, False)) == m.assessment
    assert await read(m) == result and digest(m) == before


def change_execution(m, transform, *, delete_unreceipted=False):
    p = m.p
    saved = checkpoint(p)
    payload = deepcopy(saved.checkpoint_payload)
    parts = payload["projection_receipts"]
    parts["post_commit_memory_execution"] = transform(parts["post_commit_memory_execution"])
    value = parts["post_commit_memory_execution"]
    if value is not None:
        value["asset_sha256"] = approval_value_hash({k: v for k, v in value.items() if k != "asset_sha256"})
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        p.s.d.runs._write_attempt(cur, saved.model_copy(update={"checkpoint_payload": payload}))
        if delete_unreceipted:
            preserved = [r["memory_id"] for r in value["row_receipts"]] if value else []
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_memory_table} WHERE id=ANY(%s) AND NOT(id=ANY(%s))",
                ([r["request_id"] for r in m.plan["requests"]], preserved))


@pytest.mark.parametrize("changed", ["partial", "missing_execution", "bare_rows", "wrong_row_hash", "wrong_memory_id", "running_with_all_rows"])
async def test_durable_execution_and_physical_rows_keep_existing_classification(memory_case, changed):
    m = memory_case
    def transform(value):
        if changed in {"missing_execution", "bare_rows"}:
            return None
        if changed == "partial":
            value.update(state="running", row_receipts=value["row_receipts"][:1])
        elif changed == "wrong_row_hash":
            value["row_receipts"][0]["row_sha256"] = "f" * 64
        elif changed == "wrong_memory_id":
            value["row_receipts"][0]["memory_id"] = "foreign-memory-id"
        else:
            value["state"] = "running"
        return value
    change_execution(m, transform, delete_unreceipted=changed in {"partial", "missing_execution"})
    before = digest(m)
    result = await read(m)
    if changed == "partial":
        assert result.status == "partial" and result.reason_code == "partial_rows_verified"
        assert result.request_ids == [m.plan["requests"][0]["request_id"]]
        assert result.missing_request_ids == [r["request_id"] for r in m.plan["requests"][1:]]
    elif changed == "missing_execution":
        assert result.status == "unknown" and result.reason_code == "missing_execution_receipt"
        assert result.unknown_request_ids == [r["request_id"] for r in m.plan["requests"]]
    else:
        assert result.status == "drifted" and result.drifted_request_ids
    assert await read(m) == result and digest(m) == before


@pytest.mark.parametrize("memory_case", [{"fail_after": 1}, {"fail_after": 2}], indirect=True)
async def test_real_post_commit_row_transaction_failure_remains_unknown_without_replay(memory_case, monkeypatch):
    m, p = memory_case, memory_case.p
    events = [e for e in await p.s.h.store.list_events(p.s.session.id) if e.type == "session.post_commit_effect_failed"]
    assert len(events) == 1 and events[0].payload["effect_kind"] == "turn_memory" and events[0].payload["status"] == "unknown"
    before = digest(m)
    def forbidden(*args, **kwargs):
        raise AssertionError("read cannot replay a failed or unknown derived effect")
    monkeypatch.setattr(m.memories, "execute_turn_memory_plan", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    result = await read(m)
    committed = len(m.execution["row_receipts"]) if m.execution else 0
    ids = [r["request_id"] for r in m.plan["requests"]]
    assert result.status == "unknown" and result.failure_observed and result.reason_code == "effect_failure_observed"
    assert result.request_ids == ids[:committed] and result.unknown_request_ids == result.missing_request_ids == ids[committed:]
    assert not result.drifted_request_ids
    assert await read(m) == result and digest(m) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.parametrize("changed", ["future_field", "future_version", "wrong_plan", "wrong_ordinal", "incomplete_completed"])
async def test_incompatible_execution_receipts_are_rejected_not_repaired(memory_case, changed):
    m = memory_case
    def transform(value):
        if changed == "future_field":
            value["future_field"] = True
        elif changed == "future_version":
            value["schema_version"] = 99
        elif changed == "wrong_plan":
            value["plan_asset_sha256"] = "f" * 64
        elif changed == "wrong_ordinal":
            value["row_receipts"][0]["ordinal"] = 99
        else:
            value["row_receipts"] = value["row_receipts"][:1]
        return value
    change_execution(m, transform)
    before = digest(m)
    with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
        await read(m)
    assert digest(m) == before


@pytest.mark.parametrize("changed", ["missing", "content"])
async def test_required_application_observations_are_not_treated_as_disposable_derived_rows(memory_case, changed):
    m, p = memory_case, memory_case.p
    parts = checkpoint(p).checkpoint_payload["projection_receipts"]
    identities = list(parts["observation_projection_commits"]["row_hashes"])
    assert identities and not set(identities).intersection(r["request_id"] for r in m.plan["requests"])
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        table = p.s.h.settings.database.postgres_memory_table
        if changed == "missing":
            cur.execute(f"DELETE FROM {table} WHERE id=ANY(%s)", (identities,))
        else:
            cur.execute(f"UPDATE {table} SET content='changed required observation' WHERE id=ANY(%s)", (identities,))
        assert cur.rowcount == len(identities)
    before = digest(m)
    with pytest.raises(RuntimeError, match="[Oo]bservation"):
        await read(m)
    with pytest.raises(RuntimeError, match="[Oo]bservation"):
        await implementation(p).read_expired_assessment(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a"*64, False))
    assert digest(m) == before


@pytest.mark.parametrize("memory_case", [{"live": True}], indirect=True)
@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("required_observation", [False, True])
async def test_application_and_settlement_acks_distinguish_observations_from_derived_rows(memory_case, settled, required_observation):
    m, p = memory_case, memory_case.p
    if settled:
        receipt = await complete(p)
    parts = checkpoint(p).checkpoint_payload["projection_receipts"]
    ids = (list(parts["observation_projection_commits"]["row_hashes"]) if required_observation
        else [r["request_id"] for r in m.plan["requests"]])
    assert ids
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_memory_table} WHERE id=ANY(%s)", (ids,))
        assert cur.rowcount == len(ids)
    before = digest(m)
    if settled:
        with pytest.raises(ContinuationLeaseLostError):
            await finalize(p)
        if required_observation:
            with pytest.raises(RuntimeError, match="[Oo]bservation"):
                await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a"*64, False))
        else:
            assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a"*64, False)) == receipt
    elif required_observation:
        with pytest.raises(RuntimeError, match="[Oo]bservation"):
            await finalize(p)
        with pytest.raises(RuntimeError, match="[Oo]bservation"):
            await complete(p)
    else:
        assert (await finalize(p)).session.status.value == "completed"
    assert digest(m) == before


@pytest.mark.parametrize("changed", ["reference", "old_reader", "dependency", "runtime", "application", "producer"])
async def test_memory_read_rechecks_original_admission_and_required_proofs(memory_case, changed):
    m, p = memory_case, memory_case.p
    raw, caps = delivery(p.s), FrameworkWorkerCapabilities("a"*64, False)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if changed == "reference":
            raw["case_execution_reference"]["input_sha256"] = "f"*64
        elif changed in {"old_reader", "dependency", "runtime"}:
            values = {"old_reader": {"controlled_case_checkpoint_max_version": 4},
                "dependency": {"dependency_lock_sha256": "b"*64}, "runtime": {"runtime_version": "future-reader"}}
            caps = replace(caps, **values[changed])
        elif changed == "application":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_message_table} SET content='foreign' WHERE id=%s", (p.result.result_asset.assistant_message_id,))
            assert cur.rowcount == 1
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._attempts} SET error_code='not_expiry' WHERE id=%s", (p.h.task.attempt_id,))
            assert cur.rowcount == 1
    before = digest(m)
    with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
        await implementation(p).read_expired_memory(raw, capabilities=caps)
    assert digest(m) == before


async def test_memory_read_does_not_authorize_original_settlement_or_ack(memory_case):
    m, p = memory_case, memory_case.p
    before = digest(m)
    assert (await read(m)).status == "completed"
    with pytest.raises(RuntimeError):
        await complete(p)
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a"*64, False))
    assert digest(m) == before


@pytest.mark.parametrize("memory_case", [{"live": True}], indirect=True)
@pytest.mark.parametrize("phase", ["running", "explicit_reconciliation", "settled"])
async def test_memory_read_scope_does_not_admit_other_producer_states(memory_case, phase):
    m, p = memory_case, memory_case.p
    if phase == "explicit_reconciliation":
        await p.s.h.tasks.finish(p.h.task, status="recovery_required", error_code="explicit_reconciliation")
    elif phase == "settled":
        await complete(p)
    before = digest(m)
    with pytest.raises(RuntimeError):
        await read(m)
    assert digest(m) == before


async def test_concurrent_and_cancelled_memory_reads_do_not_write_or_replay(memory_case, monkeypatch, caplog):
    m, p = memory_case, memory_case.p
    before = digest(m)
    result = await read(m)
    assert await asyncio.gather(*(read(m) for _ in range(4))) == [result]*4
    entered, release, finished = Event(), Event(), Event()
    service, caps = implementation(p), FrameworkWorkerCapabilities("a"*64, False)
    original = service._expired_memory_original
    def delayed(*args):
        entered.set()
        try:
            assert release.wait(10)
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(service, "_expired_memory_original", delayed)
    waiting = asyncio.create_task(service.read_expired_memory(delivery(p.s), capabilities=caps))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
    assert digest(m) == before and len(p.requests) == 2 and len(p.effects) == 1
    assert any(getattr(r, "recovery_reason", None) == "expired_memory_originals_unsettled"
        and r.automatic_replay is False for r in caplog.records)


def memory_reader(settings_data, raw, pipe):
    """Fresh reader receives no owner/native object, Memory service or model config."""
    from src.core.config import Settings
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements

    async def work():
        settings = Settings.model_validate(settings_data)
        tasks = PostgresAgentTaskStore(settings, PostgresSessionStore(settings))
        def forbidden(*args, **kwargs):
            raise AssertionError("fresh Memory reader cannot construct execution authority or write")
        RuntimeExecutionOwner.__init__ = forbidden
        tasks.claim_controlled_case = tasks.begin = tasks._update_status = forbidden
        service, caps = ControlledCaseSettlements(tasks), FrameworkWorkerCapabilities("a"*64, False)
        first = await service.read_expired_memory(raw, capabilities=caps)
        assert await service.read_expired_memory(raw, capabilities=caps) == first
        pipe.send(first.model_dump(mode="json"))
        pipe.close()
    asyncio.run(work())


@pytest.mark.parametrize("missing", [False, True])
async def test_fresh_process_reconciles_persisted_memory_without_cached_owner(memory_case, missing):
    m, p = memory_case, memory_case.p
    if missing:
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_memory_table} WHERE id=%s", (m.plan["requests"][0]["request_id"],))
            assert cur.rowcount == 1
    before = digest(m)
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=memory_reader, args=(p.s.h.settings.model_dump(mode="json"), delivery(p.s), send))
    try:
        process.start()
        send.close()
        assert await asyncio.to_thread(receive.poll, 40), f"Reader exitcode={process.exitcode}"
        actual = receive.recv()
        await asyncio.to_thread(process.join, 15)
        assert not process.is_alive() and process.exitcode == 0
        assert actual["scope"] == "derived_turn_memory_reconciliation_only"
        assert actual["status"] == ("drifted" if missing else "completed")
        assert actual == (await read(m)).model_dump(mode="json")
    finally:
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 10)
        receive.close()
        send.close()
    assert digest(m) == before and len(p.requests) == 2 and len(p.effects) == 1
