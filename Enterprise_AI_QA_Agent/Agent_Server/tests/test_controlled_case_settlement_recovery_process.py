"""Real admission transaction/ACK losses; not settlement claim or production routing."""
import asyncio
import json
import multiprocessing

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.controlled_case_settlement_recovery import settlement_recovery_task_id
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_settlement_recovery import recovery_case, producer_facts, ensure


def admission_worker(settings_data, raw, gate, window):
    from redis import Redis
    from src.core.config import Settings
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
            raise AssertionError("admission process may not acquire graph execution authority")
        RuntimeExecutionOwner.__init__ = forbidden
        tasks.claim_controlled_case = tasks.begin = tasks._update_status = forbidden
        service = ControlledCaseSettlementRecovery(tasks)
        def pause():
            with Redis.from_url(settings.database.redis_url, decode_responses=True, socket_timeout=65) as redis:
                redis.rpush(gate, window)
                assert redis.blpop(gate + ":release", timeout=60), "Admission kill gate timed out"
        if window == "inside_transaction":
            original = sessions.append_event_in_transaction
            def append_then_pause(*args, **kwargs):
                result = original(*args, **kwargs)
                if args[2].type == "test_run.case_settlement_admitted":
                    pause()
                return result
            sessions.append_event_in_transaction = append_then_pause
        elif window == "after_commit":
            original = service._ensure
            def commit_then_pause(*args):
                receipt = original(*args)  # connection context committed
                pause()
                return receipt
            service._ensure = commit_then_pause
        elif window != "read":
            raise ValueError("Unknown isolated admission window")
        caps = FrameworkWorkerCapabilities("a"*64, False, controlled_case_settlement_admission_available=window != "read")
        receipt = await service.ensure(raw, capabilities=caps)
        if window != "read":
            raise AssertionError("Admission kill gate was not reached")
        assert await service.ensure(raw, capabilities=caps) == receipt
        with Redis.from_url(settings.database.redis_url, decode_responses=True) as redis:
            redis.rpush(gate, receipt.model_dump_json())
    asyncio.run(work())


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["inside_transaction", "after_commit"])
async def test_hard_killed_admission_is_atomic_and_fresh_reader_only_reuses_its_receipt(recovery_case, window):
    from redis.asyncio import Redis

    p = recovery_case
    process, reader = None, None
    # Match the existing process harness: the lock-gate wait must not inherit
    # Redis's five-second socket timeout while a fresh interpreter imports.
    waiting = Redis.from_url(p.s.h.settings.database.redis_url, decode_responses=True, socket_timeout=65)
    gate = p.s.h.prefix + ":settlement-admission-kill"
    protected, all_before = producer_facts(p), originals_digest(p)
    ctx = multiprocessing.get_context("spawn")
    try:
        process = ctx.Process(target=admission_worker, args=(p.s.h.settings.model_dump(mode="json"), delivery(p.s), gate, window))
        process.start()
        reached = await waiting.blpop(gate, timeout=40)
        assert reached is not None and reached[1] == window, f"Admission worker exitcode={process.exitcode}"
        process.kill()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode not in {None, 0}
        identity = settlement_recovery_task_id(p.h.task.id, p.h.task.attempt_id)
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {p.s.h.tasks._tasks} WHERE id=%s", (identity,))
            assert len(cur.fetchall()) == int(window == "after_commit")
            cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (identity,))
            assert len(cur.fetchall()) == int(window == "after_commit")
            cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_settlement_admitted'")
            assert len(cur.fetchall()) == int(window == "after_commit")
        if window == "inside_transaction":
            assert originals_digest(p) == all_before
        assert producer_facts(p) == protected
        task = await ensure(p)
        before_read = originals_digest(p)
        reader = ctx.Process(target=admission_worker, args=(p.s.h.settings.model_dump(mode="json"), delivery(p.s), gate + ":read", "read"))
        reader.start()
        observed = await waiting.blpop(gate + ":read", timeout=40)
        assert observed is not None, f"Admission reader exitcode={reader.exitcode}"
        await asyncio.to_thread(reader.join, 10)
        assert not reader.is_alive() and reader.exitcode == 0
        assert json.loads(observed[1]) == task.model_dump(mode="json")
        assert originals_digest(p) == before_read and producer_facts(p) == protected
        assert task.operation == "case_settle" and task.attempt_id is None and task.status == "queued"
        assert len(p.requests) == 2 and len(p.effects) == 1
    finally:
        for child in (process, reader):
            if child is not None and child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 10)
                assert not child.is_alive()
        await waiting.aclose()
