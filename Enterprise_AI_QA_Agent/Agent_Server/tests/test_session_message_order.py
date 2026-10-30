"""Durable ordering, using the equal-clock failure recorded in progress section 134."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.context.framework_context_service import FrameworkContextService
from src.infrastructure.postgres_runtime import postgres_connect
from src.api.routes.settings import _session_import_bundle
from tests.test_framework_context_transcript import context_case, covered_asset
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_runtime_binding_persistence import make_binding
from tests.test_session_event_delivery import stores


async def captured_history(writer, session_id):
    case, request, _ = context_case()
    # Actual seven-row clock from c134-context-tuple-probe-v2.log, not a
    # manufactured chronological spacing that would hide the reported defect.
    captured_at = datetime.fromisoformat("2026-10-30T14:14:55.405576").replace(tzinfo=timezone.utc)
    for message in case.messages:
        message.created_at = captured_at
    session = await writer.get_session(session_id)
    session.messages = deepcopy(case.messages)
    binding = make_binding(session_id, request.turn_id, runtime_version="v2.1.0")
    request = request.model_copy(update={"session_id": session_id, "runtime_binding": binding})
    session.metadata["framework_transcript_asset"] = covered_asset(session, binding)
    await writer.save_session(session)
    return request


def advance_physical_versions(settings, session_id):
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        for _ in range(100):
            cur.execute(f"UPDATE {settings.database.postgres_message_table} SET metadata=metadata "
                "WHERE session_id=%s AND id=%s", (session_id, "old-user"))
            assert cur.rowcount == 1


@pytest.mark.asyncio
async def test_captured_equal_clock_history_survives_physical_row_versions(stores):
    writer, reader, session_id, settings = stores
    request = await captured_history(writer, session_id)
    before = await reader.get_session(session_id)
    original = FrameworkContextService().prepare(before, request)
    if hasattr(writer, "_settings"):
        advance_physical_versions(settings, session_id)
    else:
        await writer.save_session(deepcopy(before))
    after = await reader.get_session(session_id)
    assert {item.id: item for item in before.messages} == {item.id: item for item in after.messages}
    assert [item.id for item in after.messages] == [item.id for item in before.messages]
    assert FrameworkContextService().prepare(after, request) == original


@pytest.mark.asyncio
async def test_new_backup_declares_order_and_restore_preserves_unknown_legacy_order(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    exported, = await reader.bulk_export()
    assert exported["message_order"] == {"schema_version": 1, "unsequenced_message_ids": []}
    restored = deepcopy(exported)
    restored["id"] = uuid4().hex
    restored["metadata"].pop("framework_transcript_asset")
    restored.pop("message_order")
    for message in restored["messages"]:
        message["id"] = restored["id"] + ":" + message["id"]
    imported = _session_import_bundle(restored)
    assert imported.session.unsequenced_message_ids == frozenset(item.id for item in imported.session.messages)
    assert await writer.import_session_bundles([imported]) == (1, 0)
    actual = await reader.get_session(restored["id"])
    assert actual.unsequenced_message_ids == imported.session.unsequenced_message_ids
    binding = make_binding(actual.id, "current-turn", runtime_version="v2.1.0")
    _, request, _ = context_case()
    request = request.model_copy(update={"session_id": actual.id, "runtime_binding": binding,
        "user_message_id": actual.id + ":current-user"})
    with pytest.raises(ValueError, match="message order requires explicit migration"):
        FrameworkContextService().prepare(actual, request)
    assert (await reader.get_session(actual.id)).metadata == actual.metadata
    reexported = next(item for item in await reader.bulk_export() if item["id"] == actual.id)
    assert set(reexported["message_order"]["unsequenced_message_ids"]) == actual.unsequenced_message_ids


@pytest.mark.asyncio
async def test_known_backup_restores_equal_clock_order_without_changing_message_fields(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    exported, = await reader.bulk_export()
    exported["id"] = uuid4().hex
    exported["metadata"].pop("framework_transcript_asset")
    for message in exported["messages"]:
        message["id"] = exported["id"] + ":" + message["id"]
    bundle = _session_import_bundle(exported)
    assert bundle.session.unsequenced_message_ids == frozenset()
    assert await writer.import_session_bundles([bundle]) == (1, 0)
    actual = await reader.get_session(exported["id"])
    assert actual.unsequenced_message_ids == frozenset()
    assert actual.messages == bundle.session.messages


@pytest.mark.asyncio
@pytest.mark.parametrize("stores", ["postgres"], indirect=True)
async def test_initializer_preserves_unsequenced_legacy_rows_instead_of_backfilling_physical_order(stores):
    writer, reader, session_id, settings = stores
    request = await captured_history(writer, session_id)
    before = await reader.get_session(session_id)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"ALTER TABLE {settings.database.postgres_message_table} DROP COLUMN message_sequence")
    await writer.initialize()
    await writer.initialize()
    actual = await reader.get_session(session_id)
    assert {item.id: item for item in actual.messages} == {item.id: item for item in before.messages}
    assert actual.unsequenced_message_ids == frozenset(item.id for item in actual.messages)
    with pytest.raises(ValueError, match="message order requires explicit migration"):
        FrameworkContextService().prepare(actual, request)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS inferred FROM {settings.database.postgres_message_table} "
            "WHERE message_sequence IS NOT NULL")
        assert cur.fetchone()["inferred"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stores", ["postgres"], indirect=True)
async def test_message_sequence_is_immutable_through_upsert_and_reinitialization(stores):
    writer, reader, session_id, settings = stores
    request = await captured_history(writer, session_id)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id,message_sequence FROM {settings.database.postgres_message_table} WHERE session_id=%s",
            (session_id,))
        original = {row["id"]: row["message_sequence"] for row in cur.fetchall()}
    session = await reader.get_session(session_id)
    captured_ids = [item.id for item in session.messages]
    assert all(value is not None for value in original.values())
    assert [original[identity] for identity in captured_ids] == sorted(original.values())
    for item in session.messages:
        item.metadata["message_sequence"] = -12345
    session.messages.reverse()
    await writer.save_session(session)
    await writer.initialize()
    advance_physical_versions(settings, session_id)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id,message_sequence FROM {settings.database.postgres_message_table} WHERE session_id=%s",
            (session_id,))
        assert {row["id"]: row["message_sequence"] for row in cur.fetchall()} == original
    actual = await reader.get_session(session_id)
    assert [item.id for item in actual.messages] == captured_ids
    assert not actual.unsequenced_message_ids
    assert all("message_sequence" not in item.model_dump(exclude={"metadata"}) for item in actual.messages)
    FrameworkContextService().prepare(actual, request)


@pytest.mark.asyncio
async def test_unique_clock_legacy_messages_need_no_guessed_tie_order(stores):
    writer, reader, session_id, settings = stores
    request = await captured_history(writer, session_id)
    session = await reader.get_session(session_id)
    session.metadata.pop("framework_transcript_asset")
    # The ambiguous seven-row sample remains in the root regression. This is
    # the separate valid legacy boundary: only unique historical clocks.
    session.messages = session.messages[:4]
    for index, item in enumerate(session.messages):
        item.created_at += timedelta(seconds=index)
    session.unsequenced_message_ids = frozenset(item.id for item in session.messages)
    from src.runtime.session_message_order import require_context_message_order

    require_context_message_order(session)
    assert FrameworkContextService().prepare(session, request).preparation["covered_message_ids"] == [
        "old-user", "old-answer", "legacy-user", "current-user"]


@pytest.mark.asyncio
async def test_hidden_and_queued_unknown_rows_do_not_block_proven_context(stores):
    writer, reader, session_id, settings = stores
    request = await captured_history(writer, session_id)
    session = await reader.get_session(session_id)
    session.unsequenced_message_ids = frozenset({"future-user", "hidden-error"})
    next(item for item in session.messages if item.id == "future-user").metadata["delivery_status"] = "queued"
    assert FrameworkContextService().prepare(session, request).preparation["covered_message_ids"] == [
        "old-user", "old-answer", "legacy-user", "legacy-answer", "current-user"]


@pytest.mark.parametrize("value", [
    {"schema_version": True, "unsequenced_message_ids": []},
    {"schema_version": 2, "unsequenced_message_ids": []},
    {"schema_version": 1, "unsequenced_message_ids": ["foreign"]},
    {"schema_version": 1, "unsequenced_message_ids": ["old-user", "old-user"]},
    {"schema_version": 1, "unsequenced_message_ids": "old-user"},
    {"schema_version": 1, "unsequenced_message_ids": [], "trusted": True},
])
def test_backup_order_rejects_unrecognized_provenance(value):
    from src.runtime.session_message_order import parse_message_order_backup

    case, _, _ = context_case()
    with pytest.raises(ValueError):
        parse_message_order_backup(value, case.messages)


@pytest.mark.asyncio
async def test_legacy_unknown_survives_ordinary_saves_status_append_and_read_only_reload(stores, monkeypatch):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    exported, = await reader.bulk_export()
    exported["id"] = uuid4().hex
    exported.pop("message_order")
    for item in exported["messages"]:
        item["id"] = exported["id"] + ":" + item["id"]
    bundle = _session_import_bundle(exported)
    await writer.import_session_bundles([bundle])
    session = await reader.get_session(bundle.session.id)
    unknown = session.unsequenced_message_ids
    original = {item.id: deepcopy(item) for item in session.messages}
    # Neither a stale caller omitting provenance nor a normal status append
    # constitutes the explicit migration approved by the user.
    session.unsequenced_message_ids = frozenset()
    session.messages.reverse()
    await writer.save_session(session)
    new = session.messages[0].model_copy(deep=True, update={"id": uuid4().hex})
    await writer.update_status_and_append_messages_and_events(session.id, status=None, messages=[new], events=[])

    def forbidden(*args, **kwargs):
        raise AssertionError("A transcript read must not initialize or migrate message order")

    monkeypatch.setattr(type(reader), "initialize", forbidden)
    actual = await reader.get_session(session.id)
    assert actual.unsequenced_message_ids == unknown
    assert {item.id: item for item in actual.messages if item.id in original} == original
    reexported = next(item for item in await reader.bulk_export() if item["id"] == session.id)
    assert set(reexported["message_order"]["unsequenced_message_ids"]) == unknown
    assert new.id not in unknown and new.id in {item.id for item in actual.messages}


@pytest.mark.asyncio
async def test_ordinary_save_cannot_replace_existing_message_order_provenance(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    session = await reader.get_session(session_id)
    session.unsequenced_message_ids = frozenset(item.id for item in session.messages)
    await writer.save_session(session)
    assert (await reader.get_session(session_id)).unsequenced_message_ids == frozenset()


@pytest.mark.asyncio
async def test_equal_clock_stale_append_merge_claim_and_export_share_persistent_order(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    first, second = await reader.get_session(session_id), await reader.get_session(session_id)
    expected = [item.id for item in first.messages]
    for session, identity in [(first, "append-first"), (second, "append-second")]:
        session.messages.append(session.messages[0].model_copy(deep=True, update={"id": identity}))
        await writer.save_session(session)
    expected.extend(["append-first", "append-second"])
    if hasattr(writer, "_settings"):
        advance_physical_versions(settings, session_id)
    actual = await reader.get_session(session_id)
    claimed = await writer.claim_turn_execution(session_id, "claim-order", "test-owner", "order-token", 60)
    assert [item.id for item in actual.messages] == expected
    assert [item.id for item in claimed.messages] == expected
    assert actual.unsequenced_message_ids == claimed.unsequenced_message_ids == frozenset()
    exported, = await reader.bulk_export()
    assert [item["id"] for item in exported["messages"]] == expected


@pytest.mark.asyncio
async def test_concurrent_atomic_appends_keep_each_batch_order_and_stable_reload(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    before = await reader.get_session(session_id)
    batches = [[before.messages[0].model_copy(deep=True, update={"id": f"{batch}-{index}"})
        for index in range(2)] for batch in ("a", "b")]
    assert all(await asyncio.gather(*(writer.update_status_and_append_messages_and_events(
        session_id, status=None, messages=batch, events=[]) for batch in batches)))
    ids = [item.id for item in (await reader.get_session(session_id)).messages]
    assert ids[:len(before.messages)] == [item.id for item in before.messages]
    tail = ids[len(before.messages):]
    assert tail in [["a-0", "a-1", "b-0", "b-1"], ["b-0", "b-1", "a-0", "a-1"]]
    if hasattr(writer, "_settings"):
        advance_physical_versions(settings, session_id)
    assert [item.id for item in (await reader.get_session(session_id)).messages] == ids
    exported, = await reader.bulk_export()
    assert [item["id"] for item in exported["messages"]] == ids


@pytest.mark.asyncio
async def test_explicit_migration_requires_full_evidence_order_and_then_unlocks_context(stores):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    session = await reader.get_session(session_id)
    ordered = [item.id for item in reversed(session.messages)]
    if hasattr(writer, "_settings"):
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {settings.database.postgres_message_table} SET message_sequence=NULL WHERE session_id=%s",
                (session_id,))
    else:
        writer._sessions[session_id].unsequenced_message_ids = frozenset(ordered)
    with pytest.raises(ValueError, match="does not match"):
        await writer.migrate_message_order(session_id, ordered[:-1])
    await writer.migrate_message_order(session_id, ordered)
    actual = await reader.get_session(session_id)
    assert actual.unsequenced_message_ids == frozenset()
    assert [item.id for item in actual.messages] == ordered


@pytest.mark.asyncio
@pytest.mark.parametrize("stores", ["postgres"], indirect=True)
async def test_rolled_back_write_does_not_publish_order_or_reuse_consumed_sequence(stores, monkeypatch):
    writer, reader, session_id, settings = stores
    await captured_history(writer, session_id)
    before = await reader.get_session(session_id)
    consumed = []

    def fail(cur, *args, **kwargs):
        cur.execute(f"SELECT message_sequence FROM {settings.database.postgres_message_table} WHERE id='rollback-new'")
        consumed.append(cur.fetchone()["message_sequence"])
        raise RuntimeError("injected rollback after message insert")

    from src.schemas.session import ExecutionEvent

    pending = deepcopy(before)
    pending.messages.append(before.messages[0].model_copy(deep=True, update={"id": "rollback-new"}))
    event = ExecutionEvent(id="rollback-event", session_id=session_id, type="test.rollback",
        timestamp=before.messages[0].created_at, payload={})
    with monkeypatch.context() as patch:
        patch.setattr(writer, "append_event_in_transaction", fail)
        with pytest.raises(RuntimeError, match="injected rollback"):
            await writer.save_session_with_events(pending, [event])
    assert consumed and consumed[0] is not None
    assert (await reader.get_session(session_id)).messages == before.messages
    assert await reader.list_events(session_id) == []
    await writer.initialize()
    current = await reader.get_session(session_id)
    current.messages.append(before.messages[0].model_copy(deep=True, update={"id": "committed-new"}))
    await writer.save_session(current)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT message_sequence FROM {settings.database.postgres_message_table} WHERE id='committed-new'")
        assert cur.fetchone()["message_sequence"] > consumed[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_version", ["v2.1.0", "v2.2.0", "v2.3.0"])
async def test_new_context_rejects_ambiguous_legacy_before_orchestration_without_advancing_stage(
    harness, monkeypatch, runtime_version,
):
    from tests.test_runtime_binding_persistence import make_command, make_message, make_payload

    h = harness
    await captured_history(h.store, h.session_id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET message_sequence=NULL WHERE session_id=%s",
            (h.session_id,))
    binding = make_binding(h.session_id, uuid4().hex, runtime_version=runtime_version)
    admitted = make_message(h, binding)
    task = await h.tasks.submit_v2(make_command(h, binding), make_payload(), admitted, idempotency_key=uuid4().hex)
    owner = await h.tasks.claim(task.id, worker_id="order-test", expected_binding=binding)
    await h.tasks.begin(owner, resource_id="order-test", fencing_token=1)
    stage = await h.tasks.get_framework_message_stage(task.id, session_id=h.session_id)
    before = await h.store.get_session(h.session_id)
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append("unexpected execution")
        raise AssertionError("Unknown history reached orchestration or runtime")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises(ValueError, match="message order requires explicit migration"):
            await h.sessions.execute_claimed_message(owner, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert calls == [] and h.runtime.calls == []
    assert await h.tasks.get_framework_message_stage(task.id, session_id=h.session_id) == stage
    after = await h.store.get_session(h.session_id)
    assert {item.id: item for item in after.messages} == {item.id: item for item in before.messages}
    assert after.unsequenced_message_ids == before.unsequenced_message_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["orchestrating", "orchestrated"])
async def test_frozen_recovery_does_not_readmit_or_reorder_unknown_history(admission, monkeypatch, phase):
    from src.runtime.framework_message_stages import FrameworkMessageStages
    from src.runtime.session_message_order import require_context_message_order
    from tests.test_framework_message_stages import run, selected, stage
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.deep_agents.checkpoint_schema = h.prefix + "_order_checkpoints"
    h.settings.database.postgres_tool_job_table = h.prefix + "_order_jobs"
    h.binding = h.binding.model_copy(update={"runtime_version": "v2.1.0"})
    h.command = make_command(h, h.binding)
    history, _, _ = context_case()
    session = await h.store.get_session(h.session_id)
    session.messages = history.messages[:2]
    for item in session.messages:
        item.created_at = h.message.created_at
    await h.store.save_session(session)
    original = await selected(h)

    async def stop(*args, **kwargs):
        raise ConnectionError("injected stop after original preparation")

    with monkeypatch.context() as patch:
        patch.setattr(FrameworkMessageStages,
            "freeze_execution" if phase == "orchestrating" else "submit_runtime", stop)
        with pytest.raises(ConnectionError, match="original preparation"):
            await run(h, original)
    frozen = await stage(h, original)
    assert frozen.phase == phase and h.runtime.calls == []
    before = await h.store.get_session(h.session_id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET message_sequence=NULL WHERE session_id=%s",
            (h.session_id,))
    current = await h.store.get_session(h.session_id)
    assert current.messages == before.messages
    with pytest.raises(ValueError, match="explicit migration"):
        require_context_message_order(current)
    assert await h.store.renew_turn_execution(h.session_id, original.lease_token, 0)
    assert (await h.tasks.claim(original.id, worker_id="order-expiry", expected_binding=h.binding)).status == "recovery_required"
    # As in frozen_before_runtime, compare persisted snapshots, not the
    # claim operation's pre-UPDATE timestamp projection.
    terminal = await h.tasks.get(original.id)
    successor = await h.tasks.ensure_framework_message_recovery(original.id)
    owner = await h.tasks.claim(successor.id, worker_id="order-recovery", expected_binding=h.binding)
    await h.tasks.begin(owner, resource_id="order-test", fencing_token=2)
    observed = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("Frozen recovery must not classify its input again")

    async def runtime_boundary(session, request, **kwargs):
        observed.append(request.model_dump(mode="json"))
        raise ConnectionError("observed original runtime boundary")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", runtime_boundary)
    with pytest.raises(ConnectionError, match="observed original runtime boundary"):
        await run(h, owner)
    assert len(observed) == 1 and h.runtime.calls == []
    if phase == "orchestrated":
        assert observed[0] == frozen.execution_request
    assert await h.tasks.get(original.id) == terminal
    after = await h.store.get_session(h.session_id)
    assert [item.id for item in after.messages] == [item.id for item in current.messages]
    assert after.unsequenced_message_ids == current.unsequenced_message_ids


@pytest.mark.parametrize("value", [
    {"schema_version": True, "unsequenced_message_ids": []},
    {"schema_version": 2, "unsequenced_message_ids": []},
    {"schema_version": 1, "unsequenced_message_ids": ["foreign"]},
    {"schema_version": 1, "unsequenced_message_ids": ["message-1", "message-1"]},
    {"schema_version": 1, "unsequenced_message_ids": "message-1"},
    {"schema_version": 1, "unsequenced_message_ids": [], "trusted": True},
])
def test_http_import_skips_invalid_order_without_writing_any_children(value):
    from src.runtime.store import InMemorySessionStore
    from tests.test_settings_data_import import _client, _exported_session, _import

    source, store = _exported_session(), InMemorySessionStore()
    source["message_order"] = value
    with _client(store) as client:
        response = _import(client, [source])
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 0 and response.json()["skipped_count"] == 1
    assert asyncio.run(store.get_session(source["id"])) is None
    assert asyncio.run(store.list_events(source["id"])) == []
    assert asyncio.run(store.list_snapshots(source["id"])) == []
    assert asyncio.run(store.list_approvals(source["id"])) == []
