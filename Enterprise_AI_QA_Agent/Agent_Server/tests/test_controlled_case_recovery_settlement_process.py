"""Scoped settlement survives real producer death without graph ownership."""
import asyncio
import json
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_settlement_recovery import recovery_case, ensure
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_recovery_settlement import origin_identity, wire, expire


def settlement_worker(settings_data, raw, original_raw, gate, window):
    from redis import Redis
    from src.core.config import Settings
    from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.schemas.runtime_binding import RuntimeExecutionOwner

    async def work():
        settings = Settings.model_validate(settings_data)
        sessions = PostgresSessionStore(settings)
        tasks = PostgresAgentTaskStore(settings, sessions)
        def forbidden(*args, **kwargs):
            raise AssertionError("settlement process cannot acquire graph authority or execute graph")
        RuntimeExecutionOwner.__init__ = forbidden
        tasks.claim_controlled_case = tasks.begin = tasks.finish = tasks._update_status = forbidden
        service = ControlledCaseSettlementRecovery(tasks)
        caps = FrameworkWorkerCapabilities("a"*64, False, controlled_case_settlement_available=window != "read")
        def pause():
            with Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65) as redis:
                redis.rpush(gate, window)
                assert redis.blpop(gate + ":release", timeout=60), "Settlement kill gate timed out"
        if window == "inside_transaction":
            original = sessions.append_event_in_transaction
            def append_then_pause(*args, **kwargs):
                result = original(*args, **kwargs)
                if args[2].type == "test_run.case_recovery_settled":
                    pause()
                return result
            sessions.append_event_in_transaction = append_then_pause
        elif window == "after_commit":
            original = ControlledCaseRecoverySettlement._transaction
            def commit_then_pause(self, operation, *args, **kwargs):
                receipt = original(self, operation, *args, **kwargs)
                if operation == "complete":
                    pause()
                return receipt
            ControlledCaseRecoverySettlement._transaction = commit_then_pause
        elif window not in {"after_claim", "recover", "read"}:
            raise ValueError("Unknown isolated settlement process window")
        if window != "read":
            lease = await service.claim(raw, worker_id="isolated-process-settlement", capabilities=caps)
            if window == "after_claim":
                pause()
            receipt = await service.complete(lease, capabilities=caps)
            if window != "recover":
                raise AssertionError("Settlement kill gate was not reached")
        else:
            receipt = await service.acknowledge(raw, capabilities=caps)
        assert await service.acknowledge(raw, capabilities=caps) == receipt
        assert await ControlledCaseSettlements(tasks).acknowledge_delivery(original_raw, capabilities=caps) == receipt
        assert (await service.ensure(original_raw, capabilities=caps)).status == "completed"
        with Redis.from_url(settings.database.redis_url, decode_responses=True) as redis:
            redis.rpush(gate, receipt.model_dump_json())
    asyncio.run(work())


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["after_claim", "inside_transaction", "after_commit"])
async def test_killed_successor_keeps_lost_producer_and_only_committed_settlement_is_acknowledged(recovery_case, window):
    from redis.asyncio import Redis

    p = recovery_case
    task = await ensure(p)
    raw, original_raw = wire(p, task.id), delivery(p.s)
    protected = origin_identity(p)
    children = []
    waiting = Redis.from_url(p.s.h.settings.database.redis_url, decode_responses=True, socket_timeout=65)
    gate = p.s.h.prefix + ":settlement-process-kill"
    ctx = multiprocessing.get_context("spawn")
    settings = p.s.h.settings.model_dump(mode="json")
    try:
        producer = ctx.Process(target=settlement_worker, args=(settings, raw, original_raw, gate, window))
        children.append(producer)
        producer.start()
        reached = await waiting.blpop(gate, timeout=40)
        assert reached is not None and reached[1] == window, f"Settlement producer exitcode={producer.exitcode}"
        producer.kill()
        await asyncio.to_thread(producer.join, 10)
        assert not producer.is_alive() and producer.exitcode not in {None, 0}
        committed = window == "after_commit"
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {p.s.h.tasks._tasks} WHERE id=%s", (task.id,))
            current = cur.fetchone()
            assert current["status"] == ("completed" if committed else "claimed")
            assert bool(current["result_payload"]) is committed and current["attempt_no"] == 1
            cur.execute(f"SELECT id FROM {p.s.d.runs._result_table}")
            assert len(cur.fetchall()) == int(committed)
            cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_recovery_settled'")
            assert len(cur.fetchall()) == int(committed)
        assert origin_identity(p) == protected
        if not committed:
            expire(p)
            recovery = ctx.Process(target=settlement_worker, args=(settings, raw, original_raw, gate + ":recover", "recover"))
            children.append(recovery)
            recovery.start()
            recovered = await waiting.blpop(gate + ":recover", timeout=40)
            assert recovered is not None, f"Settlement recovery exitcode={recovery.exitcode}"
            await asyncio.to_thread(recovery.join, 10)
            assert not recovery.is_alive() and recovery.exitcode == 0
        before = originals_digest(p)
        reader = ctx.Process(target=settlement_worker, args=(settings, raw, original_raw, gate + ":read", "read"))
        children.append(reader)
        reader.start()
        observed = await waiting.blpop(gate + ":read", timeout=40)
        assert observed is not None, f"Settlement reader exitcode={reader.exitcode}"
        await asyncio.to_thread(reader.join, 10)
        assert not reader.is_alive() and reader.exitcode == 0
        receipt = json.loads(observed[1])
        item = await p.s.d.runs.get_item(p.s.item.id)
        assert item.result_id == receipt["result_id"]
        assert originals_digest(p) == before and origin_identity(p) == protected
        assert len(p.requests) == 2 and len(p.effects) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 10)
                assert not child.is_alive()
        await waiting.aclose()
