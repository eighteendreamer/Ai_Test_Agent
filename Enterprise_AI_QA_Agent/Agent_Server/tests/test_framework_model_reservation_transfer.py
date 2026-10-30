from __future__ import annotations

import asyncio
import hashlib
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_model_calls import model_calls_harness, policy, intent, usage


def transfer_and_wait_after_commit(settings_data, owner_data, original_data, gate):
    from src.core.config import Settings
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.schemas.framework_model_call import FrameworkModelCallRecord
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from redis.asyncio import Redis

    async def run():
        settings = Settings.model_validate(settings_data)
        await PostgresFrameworkModelCalls(settings).transfer_ready(RuntimeExecutionOwner.model_validate(owner_data),
            expected=FrameworkModelCallRecord.model_validate(original_data))
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        try:
            await redis.rpush(gate, "transfer_committed")
            await asyncio.Event().wait()
        finally:
            await redis.aclose()

    asyncio.run(run())


async def replacement(h, old, *, begin=True):
    assert await h.store.renew_turn_execution(h.session_id, old.lease_token, 0)
    current = await h.tasks.claim(old.id, worker_id="reservation-replacement", expected_binding=h.binding)
    assert current.runtime_owner.owner_epoch > old.runtime_owner.owner_epoch
    if begin:
        await h.tasks.begin(current, resource_id="reservation-replacement", fencing_token=2)
    return current


@pytest.mark.asyncio
async def test_ready_transfer_is_explicit_idempotent_and_retains_one_original_reservation(model_calls_harness):
    h = model_calls_harness
    frozen = policy(h)
    await h.calls.freeze(h.owner, frozen)
    selected = intent(position_version=1)
    original = await h.calls.prepare_initial(h.owner, selected)
    current = await replacement(h, h.task)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="owner"):
        await h.calls.start(current.runtime_owner, selected.attempt_id)
    rows = await asyncio.gather(*(h.calls.transfer_ready(current.runtime_owner, expected=original) for _ in range(4)))
    saved = rows[0]
    assert all(row == saved for row in rows)
    assert saved.intent == original.intent and saved.created_at == original.created_at
    assert saved.owner_task_id == current.id and saved.owner_attempt_id == current.attempt_id
    assert saved.owner_epoch == current.runtime_owner.owner_epoch
    assert saved.observer_token_sha256 == hashlib.sha256(current.lease_token.encode()).hexdigest()
    assert len(saved.reservation_transfers) == 1
    link = saved.reservation_transfers[0]
    assert link.predecessor.owner_attempt_id == h.task.attempt_id
    assert link.successor.owner_attempt_id == current.attempt_id
    after = await h.calls.snapshot(h.binding)
    assert after.policy == before.policy == frozen
    assert after.reserved_calls == before.reserved_calls == 1
    assert after.charged_tokens == before.charged_tokens == selected.reserved_tokens
    assert h.task.lease_token not in saved.model_dump_json() and current.lease_token not in saved.model_dump_json()
    with pytest.raises(ContinuationLeaseLostError):
        await h.calls.start(h.owner, selected.attempt_id)
    permits = await asyncio.gather(*(h.calls.start(current.runtime_owner, selected.attempt_id) for _ in range(4)))
    assert sum(permitted for _, permitted in permits) == 1
    # A lost transfer acknowledgement can be checked after start without rewinding.
    invoking = permits[0][0]
    assert await h.calls.transfer_ready(current.runtime_owner, expected=original) == invoking
    from src.schemas.framework_model_call import FrameworkModelObject

    with pytest.raises(ValueError, match="owner"):
        await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=FrameworkModelObject(storage_uri="rustfs://fixture/late-response", sha256="d" * 64, size_bytes=1),
            response_kind="langchain_messages")
    await h.calls.complete(current.runtime_owner, selected.attempt_id, usage=usage())
    final = await h.calls.snapshot(h.binding)
    assert final.reserved_calls == 1 and final.actual_tokens == 70
    assert final.calls[0].reservation_transfers == saved.reservation_transfers


@pytest.mark.asyncio
async def test_transfer_commit_survives_process_death_before_its_result_is_consumed(model_calls_harness):
    from redis.asyncio import Redis
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    current = await replacement(h, h.task)
    assert await h.tasks.renew_framework_execution(current, 120)
    gate = h.prefix + ":reservation-transfer-committed"
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=40)
    process = multiprocessing.get_context("spawn").Process(target=transfer_and_wait_after_commit,
        args=(h.settings.model_dump(mode="json"), current.runtime_owner.model_dump(mode="json"),
            original.model_dump(mode="json"), gate))
    try:
        process.start()
        assert await waiting.blpop(gate, timeout=35) is not None
        saved, = (await h.calls.snapshot(h.binding)).calls
        assert saved.phase == "ready" and len(saved.reservation_transfers) == 1
        assert process.is_alive()
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode != 0
        restarted = PostgresFrameworkModelCalls(h.settings)
        assert await restarted.transfer_ready(current.runtime_owner, expected=original) == saved
        _, permitted = await restarted.start(current.runtime_owner, original.intent.attempt_id)
        assert permitted
        assert not (await restarted.start(current.runtime_owner, original.intent.attempt_id))[1]
        assert (await restarted.snapshot(h.binding)).reserved_calls == 1
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()


@pytest.mark.asyncio
async def test_same_owner_cannot_invent_a_transfer_and_exhausted_deadline_cannot_be_renewed(model_calls_harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded

    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="newer"):
        await h.calls.transfer_ready(h.owner, expected=original)
    assert await h.calls.snapshot(h.binding) == before
    current = await replacement(h, h.task)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{deadline_at}}',"
            "to_jsonb(clock_timestamp()-INTERVAL '1 second')) WHERE binding_id=%s", (h.binding.binding_id,))
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(FrameworkModelBudgetExceeded, match="deadline"):
        await h.calls.transfer_ready(current.runtime_owner, expected=original)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", ["started_at", "settled_at", "usage", "outcome_code", "observer_token_sha256"])
async def test_ready_label_without_consistent_unsent_facts_does_not_authorize_transfer(model_calls_harness, contradiction):
    from datetime import datetime, timezone

    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    value = (usage() if contradiction == "usage" else "request_failed" if contradiction == "outcome_code"
        else None if contradiction == "observer_token_sha256" else datetime.now(timezone.utc))
    bad = original.model_copy(update={contradiction: value})
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        h.calls._write(cur, bad)
    current = await replacement(h, h.task)
    before = await h.calls.snapshot(h.binding)
    with pytest.raises(ValueError, match="provably_unsent"):
        await h.calls.transfer_ready(current.runtime_owner, expected=bad)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_transfer_preserves_request_asset_and_rolls_back_a_failed_journal_write(model_calls_harness, monkeypatch):
    from src.schemas.framework_model_call import FrameworkModelObject

    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    selected = intent(position_version=1)
    asset = FrameworkModelObject(storage_uri="rustfs://fixture/immutable-request", sha256=selected.request_sha256, size_bytes=10)
    original = await h.calls.prepare_initial(h.owner, selected, request=asset)
    current = await replacement(h, h.task)
    before = await h.calls.snapshot(h.binding)
    write = h.calls._write

    def fail(*args, **kwargs):
        write(*args, **kwargs)
        raise ConnectionError("injected transfer commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(h.calls, "_write", fail)
        with pytest.raises(ConnectionError, match="injected"):
            await h.calls.transfer_ready(current.runtime_owner, expected=original)
    assert await h.calls.snapshot(h.binding) == before
    saved = await h.calls.transfer_ready(current.runtime_owner, expected=original)
    assert saved.request == original.request == asset


@pytest.mark.asyncio
async def test_transfer_rejects_changed_expected_fact_and_legacy_position(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    legacy = await h.calls.prepare(h.owner, intent())
    current = await replacement(h, h.task)
    before = await h.calls.snapshot(h.binding)
    changed = original.model_copy(update={"intent": original.intent.model_copy(update={"request_sha256": "c" * 64})})
    for expected in (changed, legacy):
        with pytest.raises(ValueError):
            await h.calls.transfer_ready(current.runtime_owner, expected=expected)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_reservation_transfer_requires_each_immediate_predecessor(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    second = await replacement(h, h.task, begin=False)
    once = await h.calls.transfer_ready(second.runtime_owner, expected=original)
    third = await replacement(h, second)
    with pytest.raises(ValueError):
        await h.calls.transfer_ready(third.runtime_owner, expected=original)
    twice = await h.calls.transfer_ready(third.runtime_owner, expected=once)
    assert len(twice.reservation_transfers) == 2
    assert twice.reservation_transfers[0].predecessor.owner_attempt_id == h.task.attempt_id
    assert twice.reservation_transfers[1].predecessor.owner_attempt_id == second.attempt_id
    assert (await h.calls.snapshot(h.binding)).reserved_calls == 1
    with pytest.raises(ContinuationLeaseLostError):
        await h.calls.transfer_ready(second.runtime_owner, expected=once)


@pytest.mark.asyncio
async def test_diagnosis_retains_original_owner_without_exposing_observer_credentials(model_calls_harness):
    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    original = await h.calls.prepare_initial(h.owner, intent(position_version=1))
    current = await replacement(h, h.task)
    await h.calls.transfer_ready(current.runtime_owner, expected=original)
    report = await h.tasks.diagnose_framework_message(h.task.id, session_id=h.session_id)
    call, = report.model_calls
    assert call.reservation_transfer_count == 1
    assert call.reservation_origin_task_id == h.task.id
    assert call.reservation_origin_attempt_id == h.task.attempt_id
    assert call.reservation_origin_epoch == h.owner.owner_epoch
    assert call.owner_attempt_id == current.attempt_id
    assert "observer_token_sha256" not in report.model_dump_json()
    assert current.lease_token not in report.model_dump_json()
    assert not report.automatic_replay_allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["dto", "langchain"])
async def test_explicit_transfer_allows_one_actual_sdk_send_at_the_unchanged_position(harness, entry):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from src.runtime.execution_context import framework_owner_scope
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_framework_model_dto import dto_request
    from tests.test_framework_model_policy import governed

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), begin_execution=False) as h:
            old = h.owner
            h.models.set_framework_model_call_policy(h.policy)
            model = await h.models.resolve_langchain_chat_model(h.request.model_key)
            execution = SimpleNamespace(thread_id=h.binding.thread_id, checkpoint_ns="model:fixed-task", task_id="fixed-task")
            messages = [HumanMessage(content="fixture request")]

            async def invoke():
                with framework_owner_scope(h.owner.runtime_owner):
                    if entry == "dto":
                        return await h.models.invoke(h.request.model_key, dto_request(h))
                    return await h.policy.invoke(owner=h.owner.runtime_owner, model=model, model_key=h.request.model_key,
                        execution_info=execution, branch_name=None, purpose="agent", messages=messages, tools=[], options={},
                        send=lambda: model.ainvoke(messages))

            with pytest.raises(ContinuationLeaseLostError):
                await invoke()
            before = await h.calls.snapshot(h.binding)
            original, = before.calls
            assert original.phase == "ready" and requests == []
            h.owner = await replacement(h, old)
            with pytest.raises(ValueError, match="prior_attempt"):
                await invoke()
            assert await h.calls.snapshot(h.binding) == before and requests == []
            await h.calls.transfer_ready(h.owner.runtime_owner, expected=original)
            await invoke()
            after = await h.calls.snapshot(h.binding)
            assert len(requests) == after.reserved_calls == 1 and after.actual_tokens == 10
            assert after.policy == before.policy and after.calls[0].intent == original.intent
            assert after.calls[0].owner_attempt_id == h.owner.attempt_id
            with pytest.raises(ValueError, match="already_started"):
                await invoke()
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["invoking", "completed", "unknown"])
async def test_started_reservation_never_transfers_even_to_real_reconciliation_owner(continuation_harness, phase):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

    h = continuation_harness
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    try:
        old = await claim_decision(h)
        await calls.freeze(old.runtime_owner, policy(h))
        original = await calls.prepare_initial(old.runtime_owner, intent(position_version=1))
        await calls.start(old.runtime_owner, original.intent.attempt_id)
        if phase == "completed":
            await calls.complete(old.runtime_owner, original.intent.attempt_id, usage=usage())
        elif phase == "unknown":
            await calls.mark_unknown(old.runtime_owner, original.intent.attempt_id, code="request_failed")
        assert await h.store.renew_turn_execution(h.session_id, old.lease_token, 0)
        assert (await h.tasks.claim(old.id, worker_id="expiry", expected_binding=h.binding)).status == "recovery_required"
        expired = await h.tasks.get(old.id)
        reconciliation = await h.tasks.ensure_framework_reconciliation(old.id)
        current = await h.tasks.claim(reconciliation.id, worker_id="reconciler", expected_binding=h.binding)
        await h.tasks.begin(current, resource_id="reconciler", fencing_token=2)
        before = await calls.snapshot(h.binding)
        for expected in (original, before.calls[0]):
            with pytest.raises(ValueError):
                await calls.transfer_ready(current.runtime_owner, expected=expected)
        assert await calls.snapshot(h.binding) == before
        assert await h.tasks.get(old.id) == expired
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {calls.uploads_table}")
            cur.execute(f"DROP TABLE {calls.calls_table}")
            cur.execute(f"DROP TABLE {calls.budgets_table}")
