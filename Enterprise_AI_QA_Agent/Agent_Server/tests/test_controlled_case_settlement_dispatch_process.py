"""Real Dispatcher/Redis Worker kill windows, not just the internal service."""
import asyncio
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_settlement_recovery import recovery_case, ensure, successor_row
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_recovery_settlement import origin_identity, wire, expire
from tests.test_controlled_case_settlement_dispatch import queue_for


def dispatch_worker(settings_data, prefix, gate, window):
    from redis import Redis as SyncRedis
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from tests.test_agent_task_dispatch import build_services, Runtime

    async def work():
        settings = Settings.model_validate(settings_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65)
        resources = RedisResourceLeaseManager(settings.database.redis_url, client=redis,
            key_prefix=prefix, cleanup_stream=prefix + ":cleanup")
        await resources.connect()
        store, tasks, sessions, dispatcher = build_services(settings, Runtime(), resources)
        def forbidden(*args, **kwargs):
            raise AssertionError("scoped Worker cannot adopt graph owner or execute pipeline")
        RuntimeExecutionOwner.__init__ = forbidden
        tasks.claim = tasks.begin = tasks.finish = tasks._update_status = forbidden
        sessions._finalize_runtime_result = sessions._runtime_service.request_interrupt = forbidden
        service = ControlledCaseSettlementRecovery(tasks)
        dispatcher._controlled_case_settlements = service
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a"*64, False,
            controlled_case_settlement_available=window != "read"))
        queue = RedisTaskQueue(settings.database.redis_url,
            stream=settings.orchestration.agent_task_v2_stream, group="scoped-settlement", client=redis)
        await queue.connect()
        async def pause():
            await redis.rpush(gate, window)
            assert await redis.blpop(gate + ":release", timeout=60), "Dispatcher kill gate timed out"
        if window == "after_claim":
            claim = service.claim
            async def claimed_pause(*args, **kwargs):
                result = await claim(*args, **kwargs)
                await pause()
                return result
            service.claim = claimed_pause
        elif window == "inside_transaction":
            append = store.append_event_in_transaction
            def event_pause(*args, **kwargs):
                result = append(*args, **kwargs)
                if args[2].type == "test_run.case_recovery_settled":
                    with SyncRedis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65) as sync:
                        sync.rpush(gate, window)
                        assert sync.blpop(gate + ":release", timeout=60), "Transaction kill gate timed out"
                return result
            store.append_event_in_transaction = event_pause
        elif window == "before_ack":
            ack = queue.ack_owned
            async def ack_pause(*args, **kwargs):
                await pause()
                return await ack(*args, **kwargs)
            queue.ack_owned = ack_pause
        elif window not in {"recover", "read"}:
            raise ValueError("Unknown isolated Dispatcher kill window")
        try:
            processed = await RedisTaskWorker(queue, consumer="isolated-dispatch-" + window,
                handler=lambda task: dispatcher.handle_v2(task, worker_id="isolated-dispatch-" + window),
                block_ms=0, reclaim_idle_ms=300, task_timeout_seconds=90).run_once()
            assert processed == 1, "Isolated Dispatcher did not ACK its committed receipt"
            await redis.rpush(gate, str(processed))
        finally:
            await redis.aclose()
    asyncio.run(work())


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["after_claim", "inside_transaction", "before_ack"])
async def test_killed_real_worker_retains_pending_delivery_and_only_recovers_scoped_settlement(recovery_case, window):
    from redis.asyncio import Redis

    p = recovery_case
    task = await ensure(p)
    raw = wire(p, task.id)
    queue = await queue_for(p, raw)
    protected = origin_identity(p)
    children = []
    waiting = Redis.from_url(p.s.h.settings.database.redis_url, decode_responses=True, socket_timeout=65)
    gate = p.s.h.prefix + ":dispatch-process-kill"
    ctx = multiprocessing.get_context("spawn")
    settings = p.s.h.settings.model_copy(deep=True)
    settings.orchestration.resource_default_lease_seconds = 2
    p.s.h.resources._cleanup_stream = p.s.h.prefix + ":cleanup"
    def start(mode):
        child = ctx.Process(target=dispatch_worker,
            args=(settings.model_dump(mode="json"), p.s.h.prefix, gate + ":" + mode, mode))
        children.append(child)
        child.start()
        return child
    try:
        producer = start(window)
        reached = await waiting.blpop(gate + ":" + window, timeout=40)
        assert reached is not None and reached[1] == window, f"Dispatcher producer exitcode={producer.exitcode}"
        producer.kill()
        await asyncio.to_thread(producer.join, 10)
        assert not producer.is_alive() and producer.exitcode not in {None, 0}
        committed = window == "before_ack"
        current = successor_row(p, task.id)
        assert current["status"] == ("completed" if committed else "claimed")
        assert bool(current["result_payload"]) is committed and current["attempt_no"] == 1
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await p.s.h.redis.xlen(queue.stream) == 1
        assert origin_identity(p) == protected
        # Actual Redis expiration/reaper releases the unbound logical slot;
        # do not fabricate retirement with another process's lease credentials.
        async with asyncio.timeout(8):
            while await p.s.h.resources.list_active():
                await asyncio.sleep(.1)
        await p.s.h.resources.reap_expired()
        assert await p.s.h.resources.usage_snapshot() == {}
        if not committed:
            expire(p)
            await asyncio.sleep(.35)
            recovery = start("recover")
            done = await waiting.blpop(gate + ":recover", timeout=40)
            assert done is not None and done[1] == "1", f"Dispatcher recovery exitcode={recovery.exitcode}"
            await asyncio.to_thread(recovery.join, 10)
            assert not recovery.is_alive() and recovery.exitcode == 0
            assert successor_row(p, task.id)["attempt_no"] == 2
            # Re-deliver the original envelope, not a rewritten retry command.
            await queue.enqueue(raw)
        before = originals_digest(p)
        await asyncio.sleep(.35)
        reader = start("read")
        done = await waiting.blpop(gate + ":read", timeout=40)
        assert done is not None and done[1] == "1", f"Dispatcher reader exitcode={reader.exitcode}"
        await asyncio.to_thread(reader.join, 10)
        assert not reader.is_alive() and reader.exitcode == 0
        assert originals_digest(p) == before and origin_identity(p) == protected
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
        assert await p.s.h.resources.list_active() == []
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {p.s.d.runs._result_table}")
            assert len(cur.fetchall()) == 1
            cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_recovery_settled'")
            assert len(cur.fetchall()) == 1
        assert len(p.requests) == 2 and len(p.effects) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 10)
                assert not child.is_alive()
        await waiting.aclose()
