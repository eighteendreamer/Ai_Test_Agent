from __future__ import annotations

import asyncio
import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_framework_approval_continuation import continuation_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@pytest_asyncio.fixture
async def model_calls_harness(harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = harness
    h.calls = PostgresFrameworkModelCalls(h.settings)
    await h.calls.initialize()
    h.binding = make_binding(h.session_id, f"turn-{uuid4().hex}")
    submitted = await h.tasks.submit_v2(make_command(h, h.binding), make_payload(),
        make_message(h, h.binding), idempotency_key=uuid4().hex)
    h.task = await h.tasks.claim(submitted.id, worker_id="model-owner", expected_binding=h.binding)
    h.owner = h.task.runtime_owner
    try:
        yield h
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {h.calls.calls_table}")
            cur.execute(f"DROP TABLE {h.calls.budgets_table}")


def policy(h, **updates):
    from src.schemas.framework_model_call import FrameworkModelBudget

    values = dict(max_calls=4, max_tokens=1000,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model_config_fingerprints={"primary": h.binding.model_config_fingerprint,
            "researcher": "researcher-fingerprint"})
    values.update(updates)
    return FrameworkModelBudget(**values)


def intent(*, attempt_id=None, logical_call_id=None, **updates):
    from src.schemas.framework_model_call import FrameworkModelCallIntent

    values = dict(attempt_id=attempt_id or uuid4().hex, logical_call_id=logical_call_id or uuid4().hex,
        branch_id="root", purpose="agent", model_key="primary",
        model_config_fingerprint="model-sha256", transport="openai_chat_completions",
        request_sha256="b" * 64, reserved_input_tokens=100, reserved_output_tokens=100)
    values.update(updates)
    return FrameworkModelCallIntent(**values)


def usage(total=70):
    from src.schemas.framework_model_call import FrameworkModelUsage

    return FrameworkModelUsage(input_tokens=total // 2, output_tokens=total - total // 2,
        total_tokens=total)


async def begin(h):
    await h.tasks.begin(h.task, resource_id="model-call-test", fencing_token=1)


def reserve_in_process(settings_data, owner_data, intent_data, gate):
    from redis import Redis
    from src.core.config import Settings
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded, PostgresFrameworkModelCalls
    from src.schemas.framework_model_call import FrameworkModelCallIntent
    from src.schemas.runtime_binding import RuntimeExecutionOwner

    settings = Settings.model_validate(settings_data)
    with Redis.from_url(settings.database.redis_url, decode_responses=True) as redis:
        redis.rpush(gate + ":ready", "ready")
        if redis.blpop(gate + ":release", timeout=30) is None:
            raise TimeoutError("Model ledger process gate timed out")
        try:
            asyncio.run(PostgresFrameworkModelCalls(settings).prepare(RuntimeExecutionOwner.model_validate(owner_data),
                FrameworkModelCallIntent.model_validate(intent_data)))
            result = {"status": "reserved"}
        except FrameworkModelBudgetExceeded as exc:
            result = {"status": "rejected", "kind": exc.kind}
        redis.rpush(gate + ":result", json.dumps(result))


@pytest.mark.asyncio
async def test_budget_is_frozen_idempotently_and_cannot_extend_after_restart(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = model_calls_harness
    frozen = policy(h)
    assert await h.calls.freeze(h.owner, frozen) == frozen
    restarted = PostgresFrameworkModelCalls(h.settings)
    await restarted.initialize()
    assert await restarted.freeze(h.owner, frozen) == frozen
    changed = frozen.model_copy(update={"max_tokens": 2000})
    with pytest.raises(ValueError, match="budget"):
        await restarted.freeze(h.owner, changed)
    assert (await restarted.snapshot(h.binding)).policy == frozen


@pytest.mark.asyncio
async def test_preparation_requires_frozen_budget_and_matching_model(model_calls_harness):
    h = model_calls_harness
    with pytest.raises(ValueError, match="budget"):
        await h.calls.prepare(h.owner, intent())
    await h.calls.freeze(h.owner, policy(h))
    for bad in (intent(model_key="unfrozen"), intent(model_config_fingerprint="changed")):
        with pytest.raises(ValueError, match="model"):
            await h.calls.prepare(h.owner, bad)
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
async def test_attempt_identity_is_immutable_and_intent_is_idempotent(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    selected = intent()
    rows = await asyncio.gather(*(h.calls.prepare(h.owner, selected) for _ in range(4)))
    assert all(row == rows[0] for row in rows)
    assert rows[0].owner_task_id == h.task.id and rows[0].owner_attempt_id == h.task.attempt_id
    assert "owner_token" not in rows[0].model_dump_json()
    for change in ({"request_sha256": "c" * 64}, {"logical_call_id": "different"},
                   {"reserved_output_tokens": 1}, {"branch_id": "changed"}):
        with pytest.raises(ValueError, match="identity"):
            await h.calls.prepare(h.owner, selected.model_copy(update=change))
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.reserved_calls == 1 and snapshot.charged_tokens == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["calls", "tokens"])
async def test_parallel_branches_share_atomic_root_budget(model_calls_harness, limit):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    h = model_calls_harness
    frozen = policy(h).model_copy(update={"max_calls": 2, "max_tokens": 10000} if limit == "calls"
        else {"max_calls": 20, "max_tokens": 400})
    await h.calls.freeze(h.owner, frozen)
    candidates = [intent(branch_id=f"root-{index}", purpose="summary" if index % 2 else "agent")
        for index in range(8)]
    results = await asyncio.gather(*(h.calls.prepare(h.owner, item) for item in candidates), return_exceptions=True)
    assert sum(not isinstance(result, BaseException) for result in results) == 2
    failures = [result for result in results if isinstance(result, BaseException)]
    assert all(isinstance(result, FrameworkModelBudgetExceeded) and result.kind == limit for result in failures)
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.reserved_calls == 2 and snapshot.charged_tokens == 400


@pytest.mark.asyncio
async def test_independent_processes_cannot_oversubscribe_shared_root(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    assert await h.tasks.renew_framework_execution(h.task, 120)
    await h.calls.freeze(h.owner, policy(h, max_tokens=400))
    await h.calls.prepare(h.owner, intent())
    gate = h.prefix + ":model-budget"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=reserve_in_process, args=(h.settings.model_dump(mode="json"),
        h.owner.model_dump(mode="json"), intent(branch_id=f"research-{index}", parent_branch_id="root",
            purpose="subagent").model_dump(mode="json"), gate)) for index in range(3)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert await h.redis.blpop(gate + ":ready", timeout=30) is not None
        await h.redis.rpush(gate + ":release", *["release" for _ in processes])
        results = []
        for _ in processes:
            observed = await h.redis.blpop(gate + ":result", timeout=30)
            assert observed is not None
            results.append(json.loads(observed[1]))
        for process in processes:
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode == 0
        assert results.count({"status": "reserved"}) == 1
        assert results.count({"status": "rejected", "kind": "tokens"}) == 2
        snapshot = await h.calls.snapshot(h.binding)
        assert snapshot.reserved_calls == 2 and snapshot.charged_tokens == 400
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 10)


@pytest.mark.asyncio
async def test_subagent_and_summary_reservations_are_in_same_root(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    root = await h.calls.prepare(h.owner, intent())
    subagent = await h.calls.prepare(h.owner, intent(branch_id="research", parent_branch_id="root",
        purpose="subagent", model_key="researcher", model_config_fingerprint="researcher-fingerprint"))
    summary = await h.calls.prepare(h.owner, intent(branch_id="research", parent_branch_id="root",
        purpose="summary", model_key="researcher", model_config_fingerprint="researcher-fingerprint"))
    assert {row.binding_id for row in (root, subagent, summary)} == {h.binding.binding_id}
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 3
    with pytest.raises(ValueError, match="branch"):
        await h.calls.prepare(h.owner, intent(branch_id="research", parent_branch_id="changed", purpose="subagent"))


@pytest.mark.asyncio
async def test_send_intent_grants_permission_once_and_requires_running_owner(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    saved = await h.calls.prepare(h.owner, intent())
    with pytest.raises(ContinuationLeaseLostError):
        await h.calls.start(h.owner, saved.intent.attempt_id)
    await begin(h)
    results = await asyncio.gather(*(h.calls.start(h.owner, saved.intent.attempt_id) for _ in range(5)))
    assert sum(granted for _, granted in results) == 1
    assert all(row.phase == "invoking" and row.started_at is not None for row, _ in results)


@pytest.mark.asyncio
async def test_usage_settles_once_and_survives_reload(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    saved = await h.calls.prepare(h.owner, intent())
    await h.calls.start(h.owner, saved.intent.attempt_id)
    rows = await asyncio.gather(*(h.calls.complete(h.owner, saved.intent.attempt_id, usage=usage()) for _ in range(3)))
    assert all(row == rows[0] and row.phase == "completed" for row in rows)
    restarted = PostgresFrameworkModelCalls(h.settings)
    snapshot = await restarted.snapshot(h.binding)
    assert snapshot.actual_tokens == snapshot.charged_tokens == 70
    assert snapshot.reserved_calls == 1
    with pytest.raises(ValueError, match="result"):
        await restarted.complete(h.owner, saved.intent.attempt_id, usage=usage(71))
    assert (await restarted.start(h.owner, saved.intent.attempt_id))[1] is False


@pytest.mark.asyncio
async def test_missing_usage_keeps_reservation_instead_of_charging_zero(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    saved = await h.calls.prepare(h.owner, intent())
    await h.calls.start(h.owner, saved.intent.attempt_id)
    await h.calls.complete(h.owner, saved.intent.attempt_id, usage=None)
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.charged_tokens == 200 and snapshot.actual_tokens == 0
    assert snapshot.unmeasured_calls == 1


@pytest.mark.asyncio
async def test_unknown_call_retains_charge_and_explicit_retry_counts_again(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent()
    await h.calls.prepare(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    unknown = await h.calls.mark_unknown(h.owner, selected.attempt_id, code="connection_lost")
    assert unknown.phase == "unknown"
    assert await h.calls.mark_unknown(h.owner, selected.attempt_id, code="connection_lost") == unknown
    with pytest.raises(ValueError, match="retry"):
        await h.calls.prepare(h.owner, selected.model_copy(update={"attempt_id": uuid4().hex}))
    retry = selected.model_copy(update={"attempt_id": uuid4().hex, "retry_of_attempt_id": selected.attempt_id})
    await h.calls.prepare(h.owner, retry)
    with pytest.raises(ValueError, match="retry"):
        await h.calls.prepare(h.owner, retry.model_copy(update={"attempt_id": uuid4().hex}))
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.reserved_calls == 2 and snapshot.charged_tokens == 400 and snapshot.unknown_calls == 1
    with pytest.raises(ValueError, match="phase"):
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage())


@pytest.mark.asyncio
async def test_actual_overrun_is_saved_and_blocks_later_calls(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h).model_copy(update={"max_tokens": 300}))
    saved = await h.calls.prepare(h.owner, intent())
    await h.calls.start(h.owner, saved.intent.attempt_id)
    await h.calls.complete(h.owner, saved.intent.attempt_id, usage=usage(450))
    assert (await h.calls.snapshot(h.binding)).charged_tokens == 450
    with pytest.raises(FrameworkModelBudgetExceeded, match="tokens"):
        await h.calls.prepare(h.owner, intent())


@pytest.mark.asyncio
async def test_actual_overrun_also_blocks_previously_prepared_unsent_call(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h).model_copy(update={"max_tokens": 500}))
    sent, waiting = intent(), intent()
    await h.calls.prepare(h.owner, sent)
    await h.calls.prepare(h.owner, waiting)
    await h.calls.start(h.owner, sent.attempt_id)
    await h.calls.complete(h.owner, sent.attempt_id, usage=usage(400))
    with pytest.raises(FrameworkModelBudgetExceeded, match="tokens"):
        await h.calls.start(h.owner, waiting.attempt_id)
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.charged_tokens == 600
    assert next(row for row in snapshot.calls if row.intent == waiting).phase == "ready"


@pytest.mark.asyncio
async def test_deadline_checked_at_reserve_and_send_but_allows_late_settlement(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    sent, prepared = intent(), intent()
    await h.calls.prepare(h.owner, sent)
    await h.calls.prepare(h.owner, prepared)
    await h.calls.start(h.owner, sent.attempt_id)
    # Advance the durable deadline without a wall-clock sleep.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{deadline_at}}',"
            "to_jsonb(clock_timestamp()-INTERVAL '1 second')) WHERE binding_id=%s", (h.binding.binding_id,))
    for action in (h.calls.prepare(h.owner, intent()), h.calls.start(h.owner, prepared.attempt_id)):
        with pytest.raises(FrameworkModelBudgetExceeded, match="deadline"):
            await action
    await h.calls.complete(h.owner, sent.attempt_id, usage=usage())
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.actual_tokens == 70 and snapshot.charged_tokens == 270


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["freeze", "prepare", "start", "complete", "unknown"])
async def test_expired_owner_cannot_mutate_model_ledger(model_calls_harness, operation):
    h = model_calls_harness
    await begin(h)
    frozen = policy(h)
    await h.calls.freeze(h.owner, frozen)
    selected = intent()
    await h.calls.prepare(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ContinuationLeaseLostError):
        if operation == "freeze":
            await h.calls.freeze(h.owner, frozen)
        elif operation == "prepare":
            await h.calls.prepare(h.owner, intent())
        elif operation == "start":
            await h.calls.start(h.owner, selected.attempt_id)
        elif operation == "complete":
            await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
        else:
            await h.calls.mark_unknown(h.owner, selected.attempt_id, code="late")
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_new_claim_preserves_root_budget_but_cannot_send_old_preparation(model_calls_harness):
    h = model_calls_harness
    frozen = policy(h)
    await h.calls.freeze(h.owner, frozen)
    saved = await h.calls.prepare(h.owner, intent())
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    replacement = await h.tasks.claim(h.task.id, worker_id="replacement", expected_binding=h.binding)
    assert replacement.runtime_owner.owner_epoch > h.owner.owner_epoch
    await h.tasks.begin(replacement, resource_id="replacement", fencing_token=2)
    assert await h.calls.freeze(replacement.runtime_owner, frozen) == frozen
    assert (await h.calls.snapshot(h.binding)).charged_tokens == 200
    with pytest.raises(ValueError, match="owner"):
        await h.calls.start(replacement.runtime_owner, saved.intent.attempt_id)
    with pytest.raises(ContinuationLeaseLostError):
        await h.calls.prepare(h.owner, intent())


@pytest.mark.asyncio
async def test_failed_prepare_and_completion_transactions_leave_no_partial_charge(model_calls_harness, monkeypatch):
    h = model_calls_harness
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    original = h.calls._write

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("injected ledger commit failure")

    selected = intent()
    with monkeypatch.context() as patch:
        patch.setattr(h.calls, "_write", fail_after_write)
        with pytest.raises(ConnectionError, match="injected"):
            await h.calls.prepare(h.owner, selected)
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 0
    await h.calls.prepare(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    with monkeypatch.context() as patch:
        patch.setattr(h.calls, "_write", fail_after_write)
        with pytest.raises(ConnectionError, match="injected"):
            await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.calls[0].phase == "invoking" and snapshot.charged_tokens == 200
    await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    assert (await h.calls.snapshot(h.binding)).charged_tokens == 70


@pytest.mark.asyncio
async def test_real_approval_successor_can_classify_unknown_without_resetting_budget(continuation_harness):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from tests.test_framework_approval_continuation import claim_decision

    h = continuation_harness
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    try:
        old = await claim_decision(h)
        frozen = policy(h)
        await calls.freeze(old.runtime_owner, frozen)
        selected = intent()
        await calls.prepare(old.runtime_owner, selected)
        await calls.start(old.runtime_owner, selected.attempt_id)
        assert await h.store.renew_turn_execution(h.session_id, old.lease_token, 0)
        expired = await h.tasks.claim(old.id, worker_id="expiry", expected_binding=h.binding)
        assert expired.status == "recovery_required"
        expired = await h.tasks.get(old.id)
        reconcile = await h.tasks.ensure_framework_reconciliation(old.id)
        successor = await h.tasks.claim(reconcile.id, worker_id="reconciler", expected_binding=h.binding)
        await h.tasks.begin(successor, resource_id="model-ledger-reconcile", fencing_token=3)
        assert (await calls.snapshot(h.binding)).calls[0].phase == "invoking"
        saved = await calls.mark_unknown(successor.runtime_owner, selected.attempt_id, code="owner_lost_response")
        assert saved.phase == "unknown" and saved.owner_attempt_id == old.attempt_id
        with pytest.raises(ContinuationLeaseLostError):
            await calls.complete(old.runtime_owner, selected.attempt_id, usage=usage())
        with pytest.raises(ValueError, match="owner"):
            await calls.complete(successor.runtime_owner, selected.attempt_id, usage=usage())
        assert await calls.freeze(successor.runtime_owner, frozen) == frozen
        snapshot = await calls.snapshot(h.binding)
        assert snapshot.reserved_calls == 1 and snapshot.charged_tokens == 200
        assert await h.tasks.get(old.id) == expired
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {calls.calls_table}")
            cur.execute(f"DROP TABLE {calls.budgets_table}")
