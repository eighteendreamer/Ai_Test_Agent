"""Lost producer originals can be read without execution or settlement authority."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

import pytest

from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_execution_lease import claim, delivery
from tests.test_controlled_case_lease_reconciliation import expire
from tests.test_controlled_case_projection_parts import checkpoint
from tests.test_controlled_case_settlement import implementation, complete
from tests.test_controlled_case_settlement_ack import originals_digest


async def lost(p):
    asset = await freeze(p)
    now = datetime.now(timezone.utc)
    expire(p.s, now)
    assert await p.s.d.runs.recover_expired(p.s.item.run_id, now) == 0
    assert await p.s.h.store.renew_turn_execution(p.s.session.id, p.h.task.lease_token, 0)
    task = await claim(p.s)
    assert task.status == "recovery_required" and task.error_code == "execution_owner_expired"
    assert task.attempt_id == p.h.task.attempt_id and task.attempt_no == 1
    return asset


@pytest.mark.asyncio
async def test_expired_assessment_is_read_from_delivery_without_owner_reconstruction_or_writes(assessment_case, monkeypatch, caplog):
    p = assessment_case
    asset = await lost(p)
    before = originals_digest(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("expired original reads must not acquire authority, write or reevaluate")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", forbidden)
    for name in ("claim", "claim_controlled_case", "begin", "finish", "renew_framework_execution", "_update_status"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    for name in ("_write_item", "_write_attempt"):
        monkeypatch.setattr(type(p.s.d.runs), name, forbidden)
    monkeypatch.setattr(p.s.h.store, "append_event_in_transaction", forbidden)
    caplog.set_level("INFO")
    service = implementation(p)
    # The compatible read-only artifact deliberately has all execution disabled.
    caps = FrameworkWorkerCapabilities("a" * 64, False)
    result = await service.read_expired_assessment(delivery(p.s), capabilities=caps)
    assert result == asset and result.scope == "controlled_case_assessment_preparation_only"
    assert p.h.task.lease_token not in result.model_dump_json() and p.s.item.lease_token not in result.model_dump_json()
    assert await service.read_expired_assessment(delivery(p.s), capabilities=caps) == asset
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1
    assert any(r.message == "controlled_case_expired_assessment_read"
        and r.automatic_replay is False for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"receipt": kind} for kind in ["false_actual", "empty_checks"]], indirect=True)
async def test_expired_read_preserves_original_failed_or_blocked_assessment(assessment_case):
    p = assessment_case
    asset = await lost(p)
    before = originals_digest(p)
    result = await implementation(p).read_expired_assessment(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, False))
    assert result == asset and result.completion.status in {"failed", "blocked"}
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["application", "running", "explicit_reconciliation", "completed"])
async def test_only_expired_retained_assessment_has_this_read_scope(assessment_case, phase):
    p = assessment_case
    if phase != "application":
        await freeze(p)
    if phase == "explicit_reconciliation":
        await p.s.h.tasks.finish(p.h.task, status="recovery_required", error_code="explicit_reconciliation")
    elif phase == "completed":
        await complete(p)
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError)):
        await implementation(p).read_expired_assessment(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, False))
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["reference", "identity", "input", "old_reader", "dependency", "runtime", "future_field",
    "future_version", "task", "attempt", "owner", "case_identity", "checkpoint_identity", "assessment", "evidence",
    "native", "application_snapshot", "application_message", "assessment_audit", "retention_audit", "model"])
async def test_expired_read_rejects_incompatible_or_changed_originals_without_repair(assessment_case, changed):
    p = assessment_case
    await lost(p)
    raw, caps = delivery(p.s), FrameworkWorkerCapabilities("a" * 64, False)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if changed == "reference":
            raw["case_execution_reference"]["input_sha256"] = "f" * 64
        elif changed == "identity":
            raw["trace_id"] = "different-trace"
        elif changed == "input":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload=input_payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (p.h.task.id,))
        elif changed in {"old_reader", "dependency", "runtime"}:
            values = {"old_reader": {"controlled_case_checkpoint_max_version": 4},
                "dependency": {"dependency_lock_sha256": "b" * 64}, "runtime": {"runtime_version": "future-reader"}}
            caps = replace(caps, **values[changed])
        elif changed in {"future_field", "future_version", "assessment", "checkpoint_identity"}:
            attempt = checkpoint(p)
            payload = deepcopy(attempt.checkpoint_payload)
            if changed == "future_field":
                payload["unrecognized_field"] = True
            elif changed == "future_version":
                payload["schema_version"] = 99
            elif changed == "checkpoint_identity":
                payload["admission"]["run_item_execution_attempt_id"] = "foreign-attempt"
            else:
                payload["assessment"]["completion"]["summary"] = "changed original assessment"
                asset = payload["assessment"]
                asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": payload,
                "checkpoint_version": 99 if changed == "future_version" else 5}))
        elif changed in {"task", "attempt"}:
            table = p.s.h.tasks._tasks if changed == "task" else p.s.h.tasks._attempts
            identity = p.h.task.id if changed == "task" else p.h.task.attempt_id
            cur.execute(f"UPDATE {table} SET error_code='not_expiry' WHERE id=%s", (identity,))
        elif changed == "owner":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET owner_epoch=owner_epoch+1 WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif changed == "case_identity":
            item = p.s.d.runs._lock_item(cur, p.s.item.id)
            p.s.d.runs._write_item(cur, item.model_copy(update={"attempt_no": item.attempt_no+1}))
        elif changed == "evidence":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_tool_artifact_table} SET content_text='foreign' WHERE tool_job_id=%s", (p.effects[0],))
        elif changed == "native":
            from psycopg import sql
            cur.execute(sql.SQL("UPDATE {}.checkpoints SET metadata=metadata || '{{\"source\":\"foreign\"}}'::jsonb WHERE checkpoint_id=%s").format(
                sql.Identifier(p.s.h.settings.deep_agents.checkpoint_schema)), (p.result.graph_outcome.native_terminal.proof.checkpoint_id,))
            assert cur.rowcount == 1
        elif changed == "application_snapshot":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_snapshot_table} SET graph_state=graph_state || '{{\"foreign\":true}}'::jsonb WHERE id=%s",
                (p.result.result_asset.runtime_result.snapshot.id,))
        elif changed == "application_message":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_message_table} SET content='foreign' WHERE id=%s", (p.result.result_asset.assistant_message_id,))
            assert cur.rowcount == 1
        elif changed in {"assessment_audit", "retention_audit"}:
            kind = "test_run.case_assessment_prepared" if changed == "assessment_audit" else "test_run.case_result_prepared"
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE session_id=%s AND type=%s", (p.s.session.id, kind))
            assert cur.rowcount == 1
        else:
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"'::jsonb) WHERE binding_id=%s", (p.s.binding.binding_id,))
            assert cur.rowcount > 0
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
        await implementation(p).read_expired_assessment(raw, capabilities=caps)
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_expired_read_does_not_make_the_asset_a_settlement_or_execution_permit(assessment_case):
    p = assessment_case
    asset = await lost(p)
    before = originals_digest(p)
    service = implementation(p)
    assert await service.read_expired_assessment(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, False)) == asset
    with pytest.raises((RuntimeError, ValueError, ContinuationLeaseLostError)):
        await complete(p)
    with pytest.raises(RuntimeError, match="settlement is missing"):
        await service.acknowledge_delivery(delivery(p.s), capabilities=FrameworkWorkerCapabilities("a" * 64, False))
    assert (await claim(p.s)).status == "recovery_required"
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_concurrent_expired_reads_and_cancelled_waiter_leave_originals_unchanged(assessment_case, monkeypatch, caplog):
    p = assessment_case
    asset = await lost(p)
    service, caps, raw = implementation(p), FrameworkWorkerCapabilities("a" * 64, False), delivery(p.s)
    before = originals_digest(p)
    assert await asyncio.gather(*(service.read_expired_assessment(raw, capabilities=caps) for _ in range(4))) == [asset]*4
    entered, release, finished = Event(), Event(), Event()
    original = service._expired_assessment_original
    def read(*args):
        entered.set()
        try:
            assert release.wait(10)
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(service, "_expired_assessment_original", read)
    waiting = asyncio.create_task(service.read_expired_assessment(raw, capabilities=caps))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1
    assert any(getattr(r, "recovery_reason", None) == "expired_assessment_originals_unsettled"
        and r.automatic_replay is False for r in caplog.records)
