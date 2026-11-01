"""Formal originals enter the sole Session finalizer, never a test verdict."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import json
from threading import Event
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_application_projection import COMMIT_KEY, _hash
from src.runtime.framework_finalization_projection import original_messages
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_result import projected_case
from tests.test_controlled_case_projection_parts import checkpoint, observation_services
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare, implementation
from tests.test_framework_chat_models import TRANSPORTS, _provider, _config, _tool_reply


async def finalize(p):
    with framework_owner_scope(p.h.task.runtime_owner):
        return await p.s.h.sessions.finalize_controlled_case_result(
            p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)


@pytest.mark.asyncio
async def test_formal_session_finalization_commits_original_parts_without_inventing_user_or_verdict(projected_case, monkeypatch):
    p = projected_case
    async with observation_services(p) as (store, memories, build):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()
        response = await finalize(p)
        asset = p.result.result_asset
        assert response.output.id == asset.assistant_message_id and response.output.content == asset.runtime_result.output_text
        session = await p.s.h.store.get_session(p.s.session.id)
        assert session.status.value == "completed"
        assert {m.id for m in session.messages} == {m.id for m in original_messages(asset)}
        assert all(m.role.value != "user" for m in session.messages)
        saved = checkpoint(p)
        assert saved.checkpoint_version == 4
        assert saved.checkpoint_payload["runtime_result_asset"] == asset.model_dump(mode="json")
        parts = saved.checkpoint_payload["projection_receipts"]
        proof = parts[COMMIT_KEY]
        assert proof["scope"] == "session_messages_completion_events"
        assert proof["output_message_id"] == asset.assistant_message_id
        assert parts["post_commit_memory_execution"]["state"] == "completed"
        assert parts["post_commit_memory_plan"]["requests"]
        assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
        assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            for table in (f"{p.s.h.tasks._tasks}_message_stage", p.s.d.runs._result_table):
                cur.execute(f"SELECT count(*) AS n FROM {table}")
                assert cur.fetchone()["n"] == 0
        before = database_digest(p.s)
        def forbidden(*args, **kwargs):
            raise AssertionError("committed case must only read/ACK, not finalize or replay derived Memory")
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
        monkeypatch.setattr(memories, "execute_turn_memory_plan", forbidden)
        again = await finalize(p)
        assert again.output.id == response.output.id and again.output.content == response.output.content
        assert checkpoint(p) == saved and database_digest(p.s) == before
        assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_normal_formal_runtime_calls_the_same_finalizer_inside_original_owner(formal_case, tmp_path, transport):
    from src.runtime.execution_context import current_framework_owner

    s = formal_case
    reply = _tool_reply(transport)
    # Same two-call fixture rule as test_controlled_case_graph: the terminal
    # Responses reply has a constant ID, so its first reply needs a fresh ID.
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_formal_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config(transport, url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                outcome = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime,
                    session_service=s.h.sessions).execute(h.task, executor=executor(s, h),
                        resource_id="isolated-formal-session-finalizer", fencing_token=1)
                assert outcome.conversation_response.output.id == outcome.result_asset.assistant_message_id
                assert (await s.h.store.get_session(s.session.id)).status.value == "completed"
                assert current_framework_owner() is None and len(requests) == 2 and len(effects) == 1
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                with pytest.raises(ValueError, match="finalizer"):
                    await s.h.tasks.finish(h.task, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["before_final", "rollback", "ack", "cancel_waiter"])
async def test_formal_application_transaction_failure_preserves_real_commit_and_never_replays_graph(
    projected_case, monkeypatch, caplog, failure,
):
    import src.runtime.framework_application_projection as commits

    p = projected_case
    record, save = commits.record_application_commit, p.s.h.store._save_session_with_events_sync
    entered, release, finished = Event(), Event(), Event()
    def record_part(*args, **kwargs):
        value = record(*args, **kwargs)
        if failure == "rollback":
            raise RuntimeError("isolated application transaction rollback")
        if failure == "cancel_waiter":
            entered.set()
            assert release.wait(30), "application transaction not released"
        return value
    def save_part(*args, **kwargs):
        try:
            if failure == "before_final":
                raise RuntimeError("isolated application precommit failure")
            value = save(*args, **kwargs)
            if failure == "ack":
                raise RuntimeError("isolated committed application ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_application_commit", record_part)
        patch.setattr(p.s.h.store, "_save_session_with_events_sync", save_part)
        operation = asyncio.create_task(finalize(p))
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
    saved = checkpoint(p)
    assert saved.checkpoint_version == (4 if committed else 3)
    session = await p.s.h.store.get_session(p.s.session.id)
    assert bool(session.messages) == committed
    assert any(getattr(r, "recovery_reason", None) == "application_commit_receipt_unknown"
        and getattr(r, "automatic_replay", None) is False for r in caplog.records)
    if committed:
        def forbidden(*args, **kwargs):
            raise AssertionError("committed application must not be finalized twice")
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    await finalize(p)
    assert checkpoint(p).checkpoint_version == 4
    assert len(p.requests) == 2 and len(p.effects) == 1
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["message", "snapshot", "completion_event", "verification", "ledger"])
async def test_committed_formal_application_original_drift_is_not_overwritten_or_refinalized(projected_case, drift):
    p = projected_case
    await finalize(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        settings = p.s.h.settings
        if drift == "message":
            cur.execute(f"UPDATE {settings.database.postgres_message_table} SET content='foreign' WHERE id=%s",
                (p.result.result_asset.assistant_message_id,))
        elif drift == "snapshot":
            cur.execute(f"UPDATE {settings.database.postgres_snapshot_table} SET created_at=created_at+interval '1 second' WHERE id=%s",
                (p.result.result_asset.runtime_result.snapshot.id,))
        elif drift == "completion_event":
            cur.execute(f"UPDATE {settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s",
                (p.result.result_asset.event_id("turn.completed"),))
        elif drift == "verification":
            # A foreign result for this Turn must not be adopted into the signed original result.
            cur.execute(f"UPDATE {settings.database.postgres_session_table} SET metadata=metadata || %s::jsonb WHERE id=%s",
                (json.dumps({"verification_results": [{"id": "foreign", "turn_id": p.s.binding.turn_id}]}), p.s.session.id))
        else:
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s",
                (p.s.binding.binding_id,))
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await finalize(p)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_formal_commit_ack_does_not_require_derived_memory_rows_to_still_exist(projected_case, monkeypatch):
    p = projected_case
    async with observation_services(p) as (store, memories, build):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()
        await finalize(p)
        saved = checkpoint(p)
        ids = [r["memory_id"] for r in saved.checkpoint_payload["projection_receipts"]["post_commit_memory_execution"]["row_receipts"]]
        assert ids
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_memory_table} WHERE id=ANY(%s)", (ids,))
        def forbidden(*args, **kwargs):
            raise AssertionError("ACK cannot repair/rebuild derived Memory")
        monkeypatch.setattr(memories, "execute_turn_memory_plan", forbidden)
        await finalize(p)
        assert checkpoint(p) == saved
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {p.s.h.settings.database.postgres_memory_table} WHERE id=ANY(%s)", (ids,))
            assert cur.fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_formal_application_rebase_preserves_concurrent_preferences_and_other_turn_messages(projected_case, monkeypatch):
    from src.schemas.session import ChatMessage, MessageRole

    p = projected_case
    save = p.s.h.store.save_snapshot
    concurrent = ChatMessage(id="queued-next-turn", role=MessageRole.user, content="future queued input",
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": "future-turn"})
    async def save_then_update(*args, **kwargs):
        value = await save(*args, **kwargs)
        session = await p.s.h.store.get_session(p.s.session.id)
        session.metadata["concurrent_preference"] = {"preserve": True}
        session.messages.append(concurrent)
        await p.s.h.store.save_session(session, turn_lease_token=p.h.task.lease_token)
        return value
    monkeypatch.setattr(p.s.h.store, "save_snapshot", save_then_update)
    await finalize(p)
    session = await p.s.h.store.get_session(p.s.session.id)
    assert session.metadata["concurrent_preference"] == {"preserve": True}
    assert any(m.id == concurrent.id and m.content == concurrent.content for m in session.messages)
    assert len([m for m in session.messages if m.id == p.result.result_asset.assistant_message_id]) == 1


@pytest_asyncio.fixture
async def verified_case(formal_case, tmp_path):
    from src.schemas.agent import ToolDescriptor

    s = formal_case
    descriptor = ToolDescriptor(key="api-test-runner", name="Synthetic assertion", category="testing",
        description="Fixture only, no actual API request.", permission_level="safe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = descriptor.key
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async def prepare_input(models):
            prepared = assembly(s, tmp_path / "assembly", models)
            prepared.parts.tool_registry._replace_descriptor(descriptor.key, **descriptor.model_dump(exclude={"key"}))
            prepared.parts.agent_registry.resolve_for_message("").supported_tools.append(descriptor.key)
            prepared.request.context["requested_tool_keys"] = [descriptor.key]
            return await prepare(s, prepared)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                bridge.runtime._tool_registry._replace_descriptor(descriptor.key, **descriptor.model_dump(exclude={"key"}))
                async def handle(arguments, context):
                    effects.append(context.tool_job_id)
                    return {"status": "completed", "summary": "fixture failed assertion",
                        "checks": [{"name": "fixture-only", "passed": False}]}
                bridge.runtime._tool_runtime_service._handlers[descriptor.key] = handle
                result = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-formal-verification-finalizer", fencing_token=1)
                yield SimpleNamespace(s=s, h=h, result=result, requests=requests, effects=effects)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "summary", "duplicate"])
async def test_formal_finalizer_uses_retained_nonempty_verification_and_never_turns_it_into_case_pass(
    verified_case, monkeypatch, drift,
):
    from src.application.testing.verification_service import VerificationService

    p = verified_case
    asset = p.result.result_asset
    verification, = asset.verification_results
    assert verification.status.value == "failed"
    if drift:
        value = verification.model_dump(mode="python")
        if drift == "summary":
            value["summary"] = "foreign"
        latest = await p.s.h.store.get_session(p.s.session.id)
        latest.metadata["verification_results"] = [value, value] if drift == "duplicate" else [value]
        await p.s.h.store.save_session(latest, turn_lease_token=p.h.task.lease_token)
        before = (await p.s.h.store.get_session(p.s.session.id)).metadata["verification_results"]
    def forbidden(*args, **kwargs):
        raise AssertionError("formal Verification builder must not run again")
    monkeypatch.setattr(VerificationService, "build_results", forbidden)
    if drift:
        with pytest.raises(RuntimeError, match="[Vv]erification"):
            await finalize(p)
        assert (await p.s.h.store.get_session(p.s.session.id)).metadata["verification_results"] == before
        assert COMMIT_KEY not in checkpoint(p).checkpoint_payload["projection_receipts"]
    else:
        await finalize(p)
        actual, = (await p.s.h.store.get_session(p.s.session.id)).metadata["verification_results"]
        assert _hash(actual) == _hash(verification.model_dump(mode="python"))
        assert actual["status"] == "failed" and checkpoint(p).checkpoint_version == 4
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_reader", "future_field", "future_version"])
async def test_formal_application_checkpoint_compatibility_defers_without_reclaim_mutations(projected_case, change):
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred
    from tests.test_controlled_case_execution_lease import delivery

    p = projected_case
    await finalize(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        if change == "future_field":
            raw = deepcopy(attempt.checkpoint_payload)
            raw["projection_receipts"]["future_receipt"] = {}
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": raw}))
        elif change == "future_version":
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_version": 5}))
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format"):
        await p.s.h.tasks.claim_controlled_case(delivery(p.s), worker_id="incompatible-application-reader",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True,
                controlled_case_checkpoint_max_version=3 if change == "old_reader" else 4))
    assert database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assistant", "tool"])
async def test_original_formal_message_conflict_is_never_overwritten_by_finalization(projected_case, kind):
    p = projected_case
    original = next(m for m in original_messages(p.result.result_asset) if m.role.value == kind)
    changed = original.model_copy(update={"content": "foreign prior row"}, deep=True)
    session = await p.s.h.store.get_session(p.s.session.id)
    session.messages.append(changed)
    await p.s.h.store.save_session(session, turn_lease_token=p.h.task.lease_token)
    with pytest.raises(RuntimeError, match="[Mm]essage"):
        await finalize(p)
    session = await p.s.h.store.get_session(p.s.session.id)
    assert len(session.messages) == 1 and session.messages[0].content == changed.content
    assert COMMIT_KEY not in checkpoint(p).checkpoint_payload["projection_receipts"]
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["scope", "missing_native", "native", "lease"])
async def test_formal_finalization_requires_original_native_and_current_live_dual_owner(projected_case, drift):
    p = projected_case
    native = deepcopy(p.result.graph_outcome.native_terminal)
    if drift == "native":
        native.values["messages"][-1].content = "foreign"
    if drift == "lease":
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
    before = database_digest(p.s)
    with framework_owner_scope(None if drift == "scope" else p.h.task.runtime_owner):
        with pytest.raises((RuntimeError, ValueError)):
            await p.s.h.sessions.finalize_controlled_case_result(p.h.task.runtime_owner,
                native=None if drift == "missing_native" else native)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 2


@pytest.mark.asyncio
async def test_dual_lease_is_rechecked_after_final_application_rows_and_before_commit_receipt(projected_case, monkeypatch):
    import src.runtime.framework_projection_source as source

    p = projected_case
    save = source.save_projection_manifest
    def expire_before_proof(cur, settings, original, manifest, **kwargs):
        if COMMIT_KEY in manifest:
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        return save(cur, settings, original, manifest, **kwargs)
    monkeypatch.setattr(source, "save_projection_manifest", expire_before_proof)
    with pytest.raises(RuntimeError):
        await finalize(p)
    assert checkpoint(p).checkpoint_version == 3
    session = await p.s.h.store.get_session(p.s.session.id)
    assert not session.messages and session.status.value != "completed"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None


@pytest.mark.asyncio
async def test_actual_snapshot_version_is_signed_without_rewriting_formal_original_intent(projected_case):
    p = projected_case
    intended = p.result.result_asset.runtime_result.snapshot
    await p.s.h.store.save_snapshot(p.s.session.id, intended.model_copy(update={"id": "occupied-version", "stage": "fixture"}),
        turn_lease_token=p.h.task.lease_token)
    await finalize(p)
    parts = checkpoint(p).checkpoint_payload["projection_receipts"]
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id,session_id,version,created_at,graph_state,stage FROM {p.s.h.settings.database.postgres_snapshot_table} WHERE id=%s", (intended.id,))
        row = dict(cur.fetchone())
    assert row["version"] > intended.version
    assert parts[COMMIT_KEY]["snapshot_sha256"] == _hash(row)
    assert checkpoint(p).checkpoint_payload["runtime_result_asset"] == p.result.result_asset.model_dump(mode="json")


@pytest.mark.asyncio
async def test_derived_memory_transaction_failure_does_not_undo_session_or_authorize_ack_replay(projected_case, monkeypatch):
    import src.runtime.framework_turn_memory_projection as memory_parts

    p = projected_case
    async with observation_services(p) as (store, memories, build):
        p.s.h.sessions._memory_runtime_service = memories
        p.s.h.sessions._observation_runtime_service = ObservationRuntimeService()
        record = memory_parts.record_memory_row_projection
        def fail(*args, **kwargs):
            record(*args, **kwargs)
            raise RuntimeError("isolated derived Memory row rollback after application commit")
        with monkeypatch.context() as patch:
            patch.setattr(memory_parts, "record_memory_row_projection", fail)
            response = await finalize(p)
        assert response.session.status.value == "completed"
        saved = checkpoint(p)
        parts = saved.checkpoint_payload["projection_receipts"]
        assert saved.checkpoint_version == 4 and parts["post_commit_memory_execution"] is None
        events = [e for e in await p.s.h.store.list_events(p.s.session.id) if e.type == "session.post_commit_effect_failed"]
        assert len(events) == 1 and events[0].payload["effect_kind"] == "turn_memory"
        assert events[0].payload["status"] == "unknown"
        def forbidden(*args, **kwargs):
            raise AssertionError("committed application ACK must not replay unknown derived effects")
        monkeypatch.setattr(memories, "execute_turn_memory_plan", forbidden)
        monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
        await finalize(p)
        assert checkpoint(p) == saved
        assert len([e for e in await p.s.h.store.list_events(p.s.session.id) if e.type == "session.post_commit_effect_failed"]) == 1
