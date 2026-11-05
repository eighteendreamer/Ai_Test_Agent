"""Approval results enter the sole Session finalizer with their real lineage."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import json
from threading import Event
from types import SimpleNamespace

import pytest

from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_result_reader import ControlledCaseResultReader
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_finalization_projection import original_messages
from src.runtime.framework_application_projection import FrameworkProjectionWrite, _hash
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal
from tests.test_controlled_case_approval_claim import wire, implementation as continuations
from tests.test_controlled_case_approval_progress import implementation as progress, retain
from tests.test_controlled_case_approval_repark import implementation as reparks, prepared, repark
from tests.test_controlled_case_approval_result import terminal_result, ready, result_caps
from tests.test_controlled_case_approval_runtime import owned, ports, expire_lease
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_successor import submit_a
from tests.test_controlled_case_approval_wait import facts, implementation as waits
from tests.test_controlled_case_projection_parts import checkpoint, observation_services


def reader(t):
    return ControlledCaseResultReader(t.p.s.h.tasks, approval_continuations=t.service,
        capabilities=replace(t.caps, controlled_case_approval_finalization_available=True))


def write(t):
    asset = t.outcome.result_asset
    return FrameworkProjectionWrite(owner=t.a.runtime_owner, snapshot_id=asset.runtime_result.snapshot.id,
        snapshot_sha256=_hash(asset.runtime_result.snapshot.graph_state), output_message_id=asset.assistant_message_id,
        runtime_result_asset_sha256=asset.asset_sha256, controlled_case_native=t.outcome.graph_outcome.native_terminal,
        controlled_case_reader=reader(t))


async def finalize(t):
    with framework_owner_scope(t.a.runtime_owner):
        return await t.p.s.h.sessions.finalize_controlled_case_result(t.a.runtime_owner,
            native=t.outcome.graph_outcome.native_terminal, controlled_case_reader=reader(t))


def require_inherited_validators(value):
    from src.schemas.framework_approval import approval_value_hash

    required = {"require_original", "require_original_approval", "require_part_identities"}
    if value.schema_version == 11:
        required.add("require_application_identity")
    assert required.issubset(type(value).__pydantic_decorators__.model_validators)
    # A changed lineage first invalidates the immutable asset's source hash;
    # the shared result validator correctly rejects it before later validators.
    for kind, message in (("parts", "projection part"), ("lineage", "original admission"), ("source", "original admission")):
        raw = value.model_dump(mode="json")
        if kind == "parts":
            raw["projection_receipts"]["snapshot_projection_commit"]["runtime_result_asset_sha256"] = "0" * 64
        elif kind == "lineage":
            raw["decision_receipt"]["decision_id"] = "foreign-original-decision"
        else:
            asset = raw["runtime_result_asset"]
            asset["source_sha256"] = "0" * 64
            asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
        with pytest.raises(ValueError, match=message):
            type(value).model_validate(raw)


@pytest.mark.asyncio
async def test_approval_result_uses_same_finalizer_and_preserves_all_original_lineage(terminal_result, monkeypatch):
    from src.application.testing.verification_service import VerificationService

    t, p = terminal_result, terminal_result.p
    original = deepcopy(checkpoint(p).checkpoint_payload)
    tasks = await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(t.b.id)
    async with observation_services(p) as (_, memories, _):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()

        def forbidden(*args, **kwargs):
            raise AssertionError("Retained Verification or committed finalization must not be rebuilt")

        monkeypatch.setattr(VerificationService, "build_results", forbidden)
        response = await finalize(t)
        asset, saved = t.outcome.result_asset, checkpoint(p)
        session = await p.s.h.store.get_session(p.s.session.id)
        assert response.output.id == asset.assistant_message_id and response.output.content == asset.runtime_result.output_text
        assert session.status.value == "completed" and not session.metadata.get("pending_turn")
        assert {m.id for m in session.messages} == {m.id for m in original_messages(asset)}
        assert all(m.role.value != "user" for m in session.messages)
        assert saved.checkpoint_version == 11
        for key in ("admission", "approval_history", "approval_admission", "decision_receipt", "runtime_result_asset", "retained_at"):
            assert saved.checkpoint_payload[key] == original[key]
        parts = saved.checkpoint_payload["projection_receipts"]
        proof = parts["application_projection_commit"]
        assert (proof["task_id"], proof["attempt_id"], proof["owner_epoch"]) == (
            t.a.id, t.a.attempt_id, t.a.runtime_owner.owner_epoch)
        assert proof["scope"] == "session_messages_completion_events"
        assert parts["snapshot_projection_commit"] and parts["event_projection_commits"]
        assert parts["observation_projection_asset"] and parts["observation_projection_commits"]
        assert parts["post_commit_memory_execution"]["state"] == "completed"
        from src.schemas.controlled_case_admission import ControlledCaseApprovalApplicationCheckpoint

        require_inherited_validators(ControlledCaseApprovalApplicationCheckpoint.model_validate(saved.checkpoint_payload))
        assert (await p.s.h.tasks.get(t.a.id)).status == "running" and (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
        assert tasks == (await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(t.b.id))
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._tasks}_message_stage")
            assert cur.fetchone()["n"] == 0
        assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=t.caps) == t.root
        assert await progress(p).read(wire(p, t.b.id), capabilities=t.caps) == t.parent
        await reparks(p).acknowledge_delivery(wire(p, t.b.id), capabilities=t.caps)
        before = facts(p)
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
        monkeypatch.setattr(memories, "execute_turn_memory_plan", forbidden)
        again = await finalize(t)
        assert again.output.id == response.output.id and again.output.content == response.output.content
        assert checkpoint(p) == saved and facts(p) == before
        assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("a_decision", ["approved", "denied"])
@pytest.mark.parametrize("b_decision", ["approved", "denied"])
async def test_runtime_calls_the_same_finalizer_after_real_serial_decisions(pending_formal, a_decision, b_decision):
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime

    p = pending_formal
    root, b, _, parent = await prepared(p, decision=b_decision, queued=False)
    await repark(p, b)
    a = await submit_a(p, root, a_decision)
    service = continuations(p)
    caps = replace(result_caps(), controlled_case_approval_finalization_available=True)
    a = await service.claim(wire(p, a.id), worker_id="session-final-a", capabilities=caps)
    _, worker = ports(p, caps=caps, continuation=service)
    runtime = ControlledCaseRuntime(runtime_service=p.bridge.runtime, session_service=p.s.h.sessions)
    outcome = await runtime.execute(a, executor=worker, resource_id="session-final-a", fencing_token=3)
    assert outcome.conversation_response.output.id == outcome.result_asset.assistant_message_id
    assert outcome.assessment_asset is outcome.settlement_receipt is None
    saved = checkpoint(p)
    assert saved.checkpoint_version == 11
    assert saved.checkpoint_payload["approval_history"]["progress_assets"] == [parent.model_dump(mode="json")]
    assert saved.checkpoint_payload["admission"]["task_id"] == p.h.task.id
    assert saved.checkpoint_payload["runtime_result_asset"]["task_id"] == a.id
    assert (await p.s.h.tasks.get(a.id)).status == "running" and (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert len(p.requests) == 2 and len(p.effects) == (a_decision == "approved") + (b_decision == "approved")


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}], indirect=True)
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_single_schema7_parent_enters_existing_session_finalizer(pending_formal, decision):
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime

    p = pending_formal
    root, task = await owned(p, decision, path="a.py")
    caps = replace(result_caps(), controlled_case_approval_finalization_available=True)
    _, worker = ports(p, caps=caps)
    outcome = await ControlledCaseRuntime(runtime_service=p.bridge.runtime, session_service=p.s.h.sessions).execute(
        task, executor=worker, resource_id="single-session-final", fencing_token=2)
    assert outcome.conversation_response.session.status.value == "completed"
    saved = checkpoint(p)
    assert saved.checkpoint_version == 11
    assert saved.checkpoint_payload["approval_history"]["state_asset"] == root.model_dump(mode="json")
    assert saved.checkpoint_payload["approval_history"]["schema_version"] == 7
    assert len(p.requests) == 2 and len(p.effects) == (decision == "approved")
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"third_tool": True}], indirect=True)
async def test_session_application_keeps_all_serial_predecessors_and_their_acks(pending_formal):
    p = pending_formal
    root, b, _, b_asset = await prepared(p, queued=False)
    await repark(p, b)
    a = await submit_a(p, root, "denied")
    service, caps = continuations(p), result_caps()
    a = await service.claim(wire(p, a.id), worker_id="application-middle-a", capabilities=caps)
    runtime, worker = ports(p, caps=caps, continuation=service)
    middle = await runtime.execute(a, executor=worker, resource_id="application-middle-a", fencing_token=3)
    a_asset = await retain(p, a, middle, caps=caps)
    await repark(p, a, caps=replace(caps, controlled_case_approval_repark_available=True))
    c = await submissions(p).submit(wire(p, p.h.task.id), root.state["tool_results"][2]["approval_id"],
        ApprovalDecisionRequest(decision="approved"), capabilities=caps)
    c = await service.claim(wire(p, c.id), worker_id="application-last-c", capabilities=caps)
    outcome = await runtime.execute(c, executor=worker, resource_id="application-last-c", fencing_token=4)
    t = SimpleNamespace(p=p, a=c, service=service, caps=caps, outcome=outcome)
    await finalize(t)
    saved = checkpoint(p)
    assert saved.checkpoint_version == 11 and saved.checkpoint_payload["approval_history"]["progress_assets"] == [
        b_asset.model_dump(mode="json"), a_asset.model_dump(mode="json")]
    assert await progress(p).read(wire(p, b.id), capabilities=caps) == b_asset
    assert await progress(p).read(wire(p, a.id), capabilities=caps) == a_asset
    await reparks(p).acknowledge_delivery(wire(p, b.id), capabilities=caps)
    await reparks(p).acknowledge_delivery(wire(p, a.id), capabilities=caps)
    assert len(p.requests) == 2 and [item[0]["path"] for item in p.effects] == ["b.py", "c.py"]


@pytest.mark.asyncio
async def test_partial_snapshot_is_schema10_and_reuses_original_snapshot_receipt(terminal_result):
    from src.schemas.controlled_case_admission import ControlledCaseApprovalProjectionCheckpoint

    t, p = terminal_result, terminal_result.p
    original = deepcopy(checkpoint(p).checkpoint_payload)
    snapshot = await p.s.h.store.save_snapshot(p.s.session.id, t.outcome.result_asset.runtime_result.snapshot,
        turn_lease_token=t.a.lease_token, framework_projection=write(t))
    saved = checkpoint(p)
    assert saved.checkpoint_version == 10
    parsed = ControlledCaseApprovalProjectionCheckpoint.model_validate(saved.checkpoint_payload)
    require_inherited_validators(parsed)
    assert parsed.projection_receipts.snapshot_projection_commit.snapshot_id == snapshot.id
    for key in ("admission", "approval_history", "approval_admission", "decision_receipt", "runtime_result_asset"):
        assert saved.checkpoint_payload[key] == original[key]
    assert await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner,
        native=t.outcome.graph_outcome.native_terminal, reader=reader(t)) == t.outcome.result_asset
    before = facts(p)
    assert await p.s.h.store.save_snapshot(p.s.session.id, t.outcome.result_asset.runtime_result.snapshot,
        turn_lease_token=t.a.lease_token, framework_projection=write(t)) == snapshot
    assert facts(p) == before
    await finalize(t)
    assert checkpoint(p).checkpoint_version == 11
    assert checkpoint(p).checkpoint_payload["projection_receipts"]["snapshot_projection_commit"] == (
        saved.checkpoint_payload["projection_receipts"]["snapshot_projection_commit"])


def test_finalization_is_default_off_separate_from_result_and_format_support():
    caps = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    assert caps.controlled_case_checkpoint_max_version == 11
    assert not caps.controlled_case_approval_finalization_available and not caps.controlled_case_approval_result_available


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["max9", "max10", "missing_result"])
async def test_finalization_preflight_defers_before_begin_delivery_and_model(pending_formal, change):
    t = await ready(pending_formal)
    caps = replace(t.caps, controlled_case_approval_finalization_available=True,
        **({"controlled_case_approval_result_available": False} if change == "missing_result"
            else {"controlled_case_checkpoint_max_version": 9 if change == "max9" else 10}))
    runtime, worker = ports(t.p, caps=caps, continuation=t.service)
    before = facts(t.p)
    with pytest.raises(TaskDeferred, match="case_.*unavailable"):
        await runtime.execute(t.a, executor=worker, resource_id="no-incompatible-finalization", fencing_token=3)
    assert facts(t.p) == before and (await t.p.s.h.tasks.get(t.a.id)).status == "claimed"
    assert len(t.p.requests) == 1 and t.p.effects == []


@pytest.mark.asyncio
async def test_installed_session_service_does_not_implicitly_enable_finalization(pending_formal, monkeypatch):
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime

    t = await ready(pending_formal)
    def forbidden(*args, **kwargs):
        raise AssertionError("Session injection does not enable an approval finalization writer")
    monkeypatch.setattr(t.p.s.h.sessions, "finalize_controlled_case_result", forbidden)
    outcome = await ControlledCaseRuntime(runtime_service=t.p.bridge.runtime,
        session_service=t.p.s.h.sessions).execute(t.a, executor=t.worker, resource_id="default-off-finalizer", fencing_token=3)
    assert outcome.result_asset is not None and outcome.conversation_response is None
    assert checkpoint(t.p).checkpoint_version == 9 and len(t.p.requests) == 2 and len(t.p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_port", "disabled", "max10"])
async def test_manual_finalization_requires_explicit_reader_writer_and_codec(terminal_result, change):
    t, p = terminal_result, terminal_result.p
    port = None if change == "missing_port" else ControlledCaseResultReader(p.s.h.tasks,
        approval_continuations=t.service, capabilities=(t.caps if change == "disabled"
            else replace(t.caps, controlled_case_approval_finalization_available=True, controlled_case_checkpoint_max_version=10)))
    before = facts(p)
    with framework_owner_scope(t.a.runtime_owner):
        with pytest.raises(TaskDeferred, match="case_.*unavailable"):
            await p.s.h.sessions.finalize_controlled_case_result(t.a.runtime_owner,
                native=t.outcome.graph_outcome.native_terminal, controlled_case_reader=port)
    assert facts(p) == before and checkpoint(p).checkpoint_version == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["scope", "native", "missing_native", "session", "owner", "formal"])
async def test_finalization_keeps_captured_owner_native_and_all_live_lease_requirements(terminal_result, change):
    t, p = terminal_result, terminal_result.p
    native = deepcopy(t.outcome.graph_outcome.native_terminal)
    if change == "native":
        native.values["messages"][-1].content = "foreign terminal"
    if change in {"session", "owner", "formal"}:
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            expire_lease(p, cur, change)
    before = facts(p)
    with framework_owner_scope(None if change == "scope" else t.a.runtime_owner):
        with pytest.raises((ValueError, RuntimeError)):
            await p.s.h.sessions.finalize_controlled_case_result(t.a.runtime_owner,
                native=None if change == "missing_native" else native, controlled_case_reader=reader(t))
    assert facts(p) == before and checkpoint(p).checkpoint_version == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "ack_loss", "cancel_waiter"])
async def test_application_failure_uses_original_parts_or_commit_ack_not_reexecution(terminal_result, monkeypatch, failure):
    import src.runtime.framework_application_projection as commits

    t, p = terminal_result, terminal_result.p
    record, save = commits.record_application_commit, p.s.h.store._save_session_with_events_sync
    entered, release, finished = Event(), Event(), Event()
    def record_part(*args, **kwargs):
        value = record(*args, **kwargs)
        if failure == "rollback":
            raise RuntimeError("isolated approval application rollback")
        if failure == "cancel_waiter":
            entered.set()
            assert release.wait(60)
        return value
    def save_part(*args, **kwargs):
        try:
            value = save(*args, **kwargs)
            if failure == "ack_loss":
                raise RuntimeError("isolated approval application ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_application_commit", record_part)
        patch.setattr(p.s.h.store, "_save_session_with_events_sync", save_part)
        operation = asyncio.create_task(finalize(t))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 180)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 60)
        else:
            with pytest.raises(RuntimeError, match="isolated approval application"):
                await operation
    committed = failure != "rollback"
    saved = checkpoint(p)
    assert saved.checkpoint_version == (11 if committed else 10)
    assert ("application_projection_commit" in saved.checkpoint_payload["projection_receipts"]) == committed
    session = await p.s.h.store.get_session(p.s.session.id)
    assert any(m.id == t.outcome.result_asset.assistant_message_id for m in session.messages) == committed
    if committed:
        def forbidden(*args, **kwargs):
            raise AssertionError("Committed approval application must only ACK")
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    await finalize(t)
    assert checkpoint(p).checkpoint_version == 11 and len(p.requests) == 2 and len(p.effects) == 1
    assert (await p.s.h.tasks.get(t.a.id)).status == "running" and (await p.s.d.runs.get_run(p.s.item.run_id)).results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_final_application_rows_crossing_any_lease_roll_back_without_losing_parts(terminal_result, monkeypatch, lease):
    import src.runtime.framework_projection_source as source

    t, p = terminal_result, terminal_result.p
    save = source.save_projection_manifest
    def expire_before_proof(cur, settings, original, manifest, **kwargs):
        if "application_projection_commit" in manifest:
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")
        return save(cur, settings, original, manifest, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(source, "save_projection_manifest", expire_before_proof)
        with pytest.raises(ContinuationLeaseLostError):
            await finalize(t)
    saved = checkpoint(p)
    assert saved.checkpoint_version == 10 and "application_projection_commit" not in saved.checkpoint_payload["projection_receipts"]
    session = await p.s.h.store.get_session(p.s.session.id)
    assert not any(m.id == t.outcome.result_asset.assistant_message_id for m in session.messages)
    await finalize(t)
    assert checkpoint(p).checkpoint_version == 11 and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["native", "decision", "parent_return", "message", "snapshot", "completion", "verification", "ledger"])
async def test_committed_approval_original_drift_is_not_refinalized_or_overwritten(terminal_result, monkeypatch, drift):
    t, p = terminal_result, terminal_result.p
    await finalize(t)
    asset = t.outcome.result_asset
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "native":
            t.outcome.graph_outcome.native_terminal.values["messages"][-1].content = "foreign native"
        elif drift == "decision":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET applied_at=NULL WHERE approval_id=%s", (t.a.reference_id,))
        elif drift == "parent_return":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=NULL WHERE id=%s", (t.b.id,))
        elif drift == "message":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_message_table} SET content='foreign' WHERE id=%s", (asset.assistant_message_id,))
        elif drift == "snapshot":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_snapshot_table} SET version=version+1 WHERE id=%s", (asset.runtime_result.snapshot.id,))
        elif drift == "completion":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s",
                (asset.event_id("turn.completed"),))
        elif drift == "verification":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_session_table} SET metadata=metadata || %s::jsonb WHERE id=%s",
                (json.dumps({"verification_results": [{"id": "foreign", "turn_id": p.s.binding.turn_id}]}), p.s.session.id))
        else:
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s",
                (p.s.binding.binding_id,))
    before = facts(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("Drifted committed application must not run finalization")
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    with pytest.raises((ValueError, RuntimeError)):
        await finalize(t)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_approval_application_rebase_preserves_concurrent_message_and_preferences(terminal_result, monkeypatch):
    from src.schemas.session import ChatMessage, MessageRole

    t, p = terminal_result, terminal_result.p
    save = p.s.h.store.save_snapshot
    future = ChatMessage(id="approval-next-turn", role=MessageRole.user, content="future input",
        created_at=datetime.now(timezone.utc), metadata={"turn_id": "future-turn"})
    async def save_then_update(*args, **kwargs):
        value = await save(*args, **kwargs)
        session = await p.s.h.store.get_session(p.s.session.id)
        session.metadata["concurrent_preference"] = {"preserve": True}
        session.messages.append(future)
        await p.s.h.store.save_session(session, turn_lease_token=t.a.lease_token)
        return value
    monkeypatch.setattr(p.s.h.store, "save_snapshot", save_then_update)
    await finalize(t)
    session = await p.s.h.store.get_session(p.s.session.id)
    assert session.metadata["concurrent_preference"] == {"preserve": True}
    assert any(m.id == future.id and m.content == future.content for m in session.messages)
    assert len([m for m in session.messages if m.id == t.outcome.result_asset.assistant_message_id]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_derived_memory_failure_or_missing_rows_do_not_authorize_application_replay(terminal_result, monkeypatch, failure):
    import src.runtime.framework_turn_memory_projection as memory_parts

    t, p = terminal_result, terminal_result.p
    async with observation_services(p) as (_, memories, _):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()
        record = memory_parts.record_memory_row_projection
        def fail(*args, **kwargs):
            record(*args, **kwargs)
            raise RuntimeError("isolated approval derived Memory row rollback")
        with monkeypatch.context() as patch:
            if failure:
                patch.setattr(memory_parts, "record_memory_row_projection", fail)
            await finalize(t)
        saved = checkpoint(p)
        execution = saved.checkpoint_payload["projection_receipts"]["post_commit_memory_execution"]
        if failure:
            assert execution is None
            events = [e for e in await p.s.h.store.list_events(p.s.session.id) if e.type == "session.post_commit_effect_failed"]
            assert len(events) == 1 and events[0].payload["status"] == "unknown"
        else:
            ids = [r["memory_id"] for r in execution["row_receipts"]]
            assert ids
            with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {p.h.settings.database.postgres_memory_table} WHERE id=ANY(%s)", (ids,))
        before = facts(p)
        def forbidden(*args, **kwargs):
            raise AssertionError("Application ACK cannot replay or repair derived Memory")
        monkeypatch.setattr(memories, "execute_turn_memory_plan", forbidden)
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
        await finalize(t)
        assert checkpoint(p) == saved and facts(p) == before


@pytest.mark.asyncio
async def test_inconsistent_empty_memory_policy_cannot_commit_or_discard_original_parts(terminal_result, monkeypatch):
    from src.application.context.memory_runtime_service import MemoryRuntimeService

    t, p = terminal_result, terminal_result.p
    async with observation_services(p) as (_, memories, _):
        p.s.h.sessions._memory_runtime_service = memories
        with monkeypatch.context() as patch:
            patch.setattr(MemoryRuntimeService, "build_turn_memory_requests", staticmethod(lambda **kwargs: []))
            with pytest.raises(ValueError, match="requested flag differs from its frozen requests"):
                await finalize(t)
        saved = checkpoint(p)
        assert saved.checkpoint_version == 10 and "application_projection_commit" not in saved.checkpoint_payload["projection_receipts"]
        session = await p.s.h.store.get_session(p.s.session.id)
        assert not any(m.id == t.outcome.result_asset.assistant_message_id for m in session.messages)
        # This test performs two full finalization attempts outside a Worker.
        # Model its existing paired heartbeat at the retry boundary, with the
        # actual configured duration; it cannot revive an expired owner.
        assert await p.s.h.tasks.renew_framework_execution(t.a, p.h.settings.orchestration.turn_execution_lease_seconds)
        await finalize(t)
        parts = checkpoint(p).checkpoint_payload["projection_receipts"]
        assert parts["post_commit_memory_plan"]["requests"]
        assert parts["post_commit_memory_execution"]["state"] == "completed"
        assert parts["snapshot_projection_commit"] == saved.checkpoint_payload["projection_receipts"]["snapshot_projection_commit"]
