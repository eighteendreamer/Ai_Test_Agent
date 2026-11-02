"""A terminal delivery acknowledges durable facts, not a recreated live owner."""
import pytest
import asyncio
from copy import deepcopy
from dataclasses import replace
from threading import Event

from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_settlement import complete, implementation
from src.runtime.task_deferred import TaskDeferred
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.framework_approval import approval_value_hash
from tests.test_controlled_case_projection_parts import checkpoint


def originals_digest(p):
    """All business facts plus native/ledger/tool originals, no private output."""
    from psycopg import sql
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        state = {"business": database_digest(p.s)}
        tables = [p.s.calls.calls_table, p.s.calls.uploads_table,
            p.s.h.settings.database.postgres_tool_job_table, p.s.h.settings.database.postgres_tool_artifact_table]
        for table in tables:
            cur.execute(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM {table} t")
            state[table] = cur.fetchone()["rows"]
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
            (p.s.h.settings.deep_agents.checkpoint_schema,))
        for row in cur.fetchall():
            cur.execute(sql.SQL("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM {}.{} t").format(
                sql.Identifier(p.s.h.settings.deep_agents.checkpoint_schema), sql.Identifier(row["tablename"])))
            state["native_" + row["tablename"]] = cur.fetchone()["rows"]
        return approval_value_hash(state)


@pytest.mark.asyncio
async def test_reference_delivery_ack_needs_neither_captured_owner_nor_native_object(assessment_case, monkeypatch):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.runtime import controlled_case_settlement as settlements
    p = assessment_case
    await freeze(p)
    receipt = await complete(p)
    raw = delivery(p.s)
    before = originals_digest(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("durable terminal ACK cannot acquire authority, execute or rebuild")
    monkeypatch.setattr(settlements, "assert_runtime_owner", forbidden)
    monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", forbidden)
    monkeypatch.setattr(type(p.s.d.runs), "_complete_locked_item_in_transaction", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "_update_status", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "claim_controlled_case", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "begin", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    # An execution-disabled artifact may still read compatible originals.
    caps = FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=False)
    assert await implementation(p).acknowledge_delivery(raw, capabilities=caps) == receipt
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("incompatible", ["dependency", "runtime"])
async def test_terminal_ack_requires_compatible_reader_artifact_without_execution_gate(assessment_case, incompatible):
    p = assessment_case
    await freeze(p)
    await complete(p)
    caps = FrameworkWorkerCapabilities("a" * 64, True)
    caps = replace(caps, **({"dependency_lock_sha256": "b" * 64} if incompatible == "dependency"
        else {"runtime_version": "future-reader"}))
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred):
        await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=caps)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["identity", "reference", "binding", "input", "old_reader", "future_field",
    "future_version", "receipt_field", "task", "owner", "result", "assessment", "artifact", "native", "audit"])
async def test_delivery_ack_rejects_changed_originals_without_writes_or_replay(assessment_case, changed):
    import json
    p = assessment_case
    await freeze(p)
    receipt = await complete(p)
    raw, caps = delivery(p.s), FrameworkWorkerCapabilities("a" * 64, True)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if changed == "identity":
            raw["trace_id"] = "foreign-trace"
        elif changed == "reference":
            raw["case_execution_reference"]["input_sha256"] = "f" * 64
        elif changed == "binding":
            from src.schemas.runtime_binding import RuntimeBinding
            binding = raw["runtime_binding"]
            binding["checkpoint_namespace"] = "foreign-namespace"
            raw["runtime_binding"] = RuntimeBinding.model_validate({k: v for k, v in binding.items()
                if k != "fingerprint"}).model_dump(mode="json")
        elif changed == "input":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload=input_payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (p.h.task.id,))
        elif changed == "old_reader":
            caps = replace(caps, controlled_case_checkpoint_max_version=5)
        elif changed in {"future_field", "future_version", "receipt_field", "assessment"}:
            attempt = checkpoint(p)
            payload = deepcopy(attempt.checkpoint_payload)
            if changed == "future_field":
                payload["future_ack_field"] = True
            elif changed == "future_version":
                payload["schema_version"] = 7
            elif changed == "receipt_field":
                payload["settlement"]["future_ack_field"] = True
            else:
                payload["assessment"]["completion"]["summary"] = "changed original assessment"
                asset = payload["assessment"]
                asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": payload,
                "checkpoint_version": 7 if changed == "future_version" else 6}))
        elif changed == "task":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET error_code='foreign' WHERE id=%s", (p.h.task.id,))
        elif changed == "owner":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET execution_state='failed' WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif changed == "result":
            cur.execute(f"UPDATE {p.s.d.runs._result_table} SET record=record || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (receipt.result_id,))
        elif changed == "artifact":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_tool_artifact_table} SET content_text='foreign' WHERE tool_job_id=%s", (p.effects[0],))
        elif changed == "native":
            from psycopg import sql
            cur.execute(sql.SQL("UPDATE {}.checkpoints SET metadata=metadata || %s::jsonb WHERE checkpoint_id=%s").format(
                sql.Identifier(p.s.h.settings.deep_agents.checkpoint_schema)),
                (json.dumps({"source": "foreign"}), p.result.graph_outcome.native_terminal.proof.checkpoint_id))
            assert cur.rowcount == 1
        else:
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE session_id=%s AND type='test_run.case_settled'", (p.s.session.id,))
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
        await implementation(p).acknowledge_delivery(raw, capabilities=caps)
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["runtime_result", "assessment", "half_result"])
async def test_delivery_without_atomic_receipt_cannot_ack_or_repair(assessment_case, phase):
    p = assessment_case
    if phase != "runtime_result":
        asset = await freeze(p)
        if phase == "half_result":
            # Reproduce the old, incomplete business kernel boundary in isolated
            # tables. This deliberately has no AgentTask terminal or receipt.
            with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {p.s.d.runs._item_table} WHERE id=%s FOR UPDATE", (p.s.item.id,))
                locked = p.s.d.runs._item_from_value(cur.fetchone()["record"])
                attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
                p.s.d.runs._complete_locked_item_in_transaction(cur, locked, attempt, asset.completion, p.s.now)
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError)):
        await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_concurrent_and_cancelled_ack_remain_read_only(assessment_case, monkeypatch):
    p = assessment_case
    await freeze(p)
    receipt = await complete(p)
    service = implementation(p)
    caps, raw = FrameworkWorkerCapabilities("a" * 64, True), delivery(p.s)
    before = originals_digest(p)
    assert await asyncio.gather(*(service.acknowledge_delivery(raw, capabilities=caps) for _ in range(4))) == [receipt] * 4
    material = service._delivery_original
    entered, release, finished = Event(), Event(), Event()
    def read(*args):
        entered.set()
        assert release.wait(8)
        try:
            return material(*args)
        finally:
            finished.set()
    monkeypatch.setattr(service, "_delivery_original", read)
    waiting = asyncio.create_task(service.acknowledge_delivery(raw, capabilities=caps))
    try:
        assert await asyncio.to_thread(entered.wait, 4)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 8)
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1
    terminal = await p.s.h.tasks.claim_controlled_case(raw, worker_id="cannot-reclaim-terminal",
        framework_capabilities=replace(caps, controlled_case_available=True))
    assert terminal.status == "completed" and terminal.runtime_owner is None
    assert originals_digest(p) == before
