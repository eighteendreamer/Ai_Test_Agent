"""Final application facts must match the original result, not merely be hashed."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import multiprocessing

import pytest
import pytest_asyncio

from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.schemas.session import ChatMessage, MessageRole, VerificationResult, VerificationStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_application_projection import projection
from tests.test_framework_runtime_result_asset import frozen_result
from tests.test_framework_message_stages import stage, selected, run
from tests.test_framework_snapshot_projection import stop_after_snapshot


@pytest.mark.asyncio
async def test_finalizer_does_not_sign_a_different_message_under_original_identity(frozen_result):
    h = frozen_result
    latest = await h.store.get_session(h.session_id)
    latest.messages.append(ChatMessage(id=h.asset.assistant_message_id, role=MessageRole.assistant,
        content="not the original answer", created_at=h.asset.projection_at,
        metadata={"turn_id": h.binding.turn_id}))
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Mm]essage"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest
    actual = await h.store.get_session(h.session_id)
    assert next(item for item in actual.messages if item.id == h.asset.assistant_message_id).content == "not the original answer"
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))


@pytest_asyncio.fixture
async def finalization_result(projection, monkeypatch, request):
    h = projection
    options = getattr(request, "param", {})
    if options.get("user_whitespace"):
        h.payload = h.payload.model_copy(update={"content": "  " + h.payload.content + "\n"})
    if options.get("empty"):
        h.output_text = ""
    execute = h.runtime.execute_turn
    async def with_tool(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result.tool_messages = [ChatMessage(id="original-tool-" + h.prefix, role=MessageRole.tool,
            content="original tool result", created_at=datetime.utcnow(),
            metadata={"turn_id": h.binding.turn_id, "tool_call_id": "original-call", "context_eligible": False})]
        if options.get("hidden"):
            result.tool_messages[0].metadata["persist_transcript"] = False
        return result
    def verify(**kwargs):
        return [VerificationResult(id="original-verification-" + h.prefix, session_id=h.session_id,
            turn_id=h.binding.turn_id, trace_id="original-trace", verifier="fixture",
            status=VerificationStatus.not_run, summary="No assertion means not_run", created_at=datetime.utcnow())]
    async def pause(**kwargs):
        h.result_values = kwargs
        raise ConnectionError("frozen finalization input")
    with monkeypatch.context() as patch:
        patch.setattr(h.runtime, "execute_turn", with_tool)
        patch.setattr(h.sessions._verification_service, "build_results", verify)
        patch.setattr(h.sessions, "_finalize_runtime_result", pause)
        h.task = await selected(h)
        with pytest.raises(ConnectionError, match="frozen finalization input"):
            await run(h, h.task)
    h.asset = await FrameworkRuntimeResults(h.tasks).load(h.task.runtime_owner)
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["summary", "status", "created_at", "duplicate"])
async def test_finalizer_does_not_overwrite_conflicting_original_verification(finalization_result, changed):
    h = finalization_result
    original = h.asset.verification_results[0]
    value = original.model_dump(mode="python")
    if changed == "summary":
        value["summary"] = "not the original evidence"
    elif changed == "status":
        value["status"] = "passed"
    elif changed == "created_at":
        value["created_at"] += timedelta(seconds=1)
    latest = await h.store.get_session(h.session_id)
    latest.metadata["verification_results"] = [value, value] if changed == "duplicate" else [value]
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    before = (await h.store.get_session(h.session_id)).metadata["verification_results"]
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Vv]erification"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert (await h.store.get_session(h.session_id)).metadata["verification_results"] == before
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["tool", "assistant"])
@pytest.mark.parametrize("changed", ["content", "role", "created_at", "metadata"])
async def test_existing_original_message_is_never_overwritten(finalization_result, source, changed):
    from src.runtime.framework_finalization_projection import original_messages

    h = finalization_result
    original = original_messages(h.asset)[0 if source == "tool" else -1]
    value = original.model_copy(deep=True)
    if changed == "content":
        value.content = "not the original"
    elif changed == "role":
        value.role = MessageRole.system
    elif changed == "created_at":
        value.created_at += timedelta(seconds=1)
    else:
        value.metadata["extra"] = "changed"
    latest = await h.store.get_session(h.session_id)
    latest.messages.append(value)
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    before = await h.store.get_session(h.session_id)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Mm]essage"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    actual = await h.store.get_session(h.session_id)
    assert next(item for item in actual.messages if item.id == value.id) == next(item for item in before.messages if item.id == value.id)
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_partial_finalization_reuses_original_verification_and_message_intents(finalization_result, monkeypatch):
    from src.runtime.framework_finalization_projection import original_messages

    h = finalization_result
    await stop_after_snapshot(h, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("Do not rebuild frozen verification")
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    unrelated = VerificationResult(id="prior-turn-verification", session_id=h.session_id, turn_id="prior-turn",
        trace_id="prior-trace", verifier="fixture", status=VerificationStatus.failed,
        summary="Preserve the previous result", created_at=datetime.utcnow())
    latest = await h.store.get_session(h.session_id)
    latest.metadata["verification_results"] = [unrelated.model_dump(mode="python")]
    latest.metadata["unrelated-preference"] = "keep"
    latest.messages.extend(original_messages(h.asset))
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["verification_results"] == [unrelated.model_dump(mode="json"),
        *[item.model_dump(mode="json") for item in h.asset.verification_results]]
    assert saved.metadata["unrelated-preference"] == "keep"
    for item in original_messages(h.asset):
        assert [message for message in saved.messages if message.id == item.id] == [item]
    assert h.native_runs == 1 and len(h.runtime.calls) == 1
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "verification.completed"]) == 1


@pytest.mark.asyncio
async def test_message_collision_after_precheck_rolls_back_instead_of_overwriting(finalization_result, monkeypatch):
    import src.runtime.framework_finalization_projection as projections

    h = finalization_result
    prepare = projections.prepare_finalization_projection
    def collide(cur, settings, write, candidate):
        prepared = prepare(cur, settings, write, candidate)
        cur.execute(f"INSERT INTO {settings.database.postgres_message_table} "
            "(id,session_id,role,content,created_at,metadata) VALUES (%s,%s,'assistant','foreign',%s,%s::jsonb)",
            (h.asset.assistant_message_id, h.session_id, h.asset.projection_at, json.dumps({"turn_id": h.binding.turn_id})))
        return prepared
    monkeypatch.setattr(projections, "prepare_finalization_projection", collide)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Mm]essage"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    saved = await h.store.get_session(h.session_id)
    assert len(saved.messages) == 1 and not saved.metadata.get("verification_results")
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing_verification", "verification_summary", "missing_message", "output_identity"])
async def test_transaction_rechecks_candidate_against_original_input(finalization_result, monkeypatch, changed):
    h = finalization_result
    save = h.store.save_session_with_events
    async def tamper(session, events, **kwargs):
        if changed == "missing_verification":
            session.metadata["verification_results"] = []
        elif changed == "verification_summary":
            session.metadata["verification_results"][0]["summary"] = "not original"
        elif changed == "missing_message":
            session.messages = [item for item in session.messages if item.id != h.asset.assistant_message_id]
        else:
            from dataclasses import replace
            kwargs["framework_projection"] = replace(kwargs["framework_projection"], output_message_id="not-original")
        return await save(session, events, **kwargs)
    monkeypatch.setattr(h.store, "save_session_with_events", tamper)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["message", "verification", "user"])
async def test_actual_rows_are_rechecked_after_insert_before_application_receipt(finalization_result, monkeypatch, changed):
    h = finalization_result
    save = h.store._save_session_in_transaction
    def tamper(cur, session, *args, **kwargs):
        result = save(cur, session, *args, **kwargs)
        if changed in {"message", "user"}:
            identity = h.asset.assistant_message_id if changed == "message" else h.task.user_message_id
            cur.execute(f"UPDATE {h.settings.database.postgres_message_table} SET content='not original' WHERE id=%s", (identity,))
        else:
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata="
                "jsonb_set(metadata,'{verification_results,0,summary}','\"not original\"'::jsonb) WHERE id=%s", (h.session_id,))
        return result
    monkeypatch.setattr(h.store, "_save_session_in_transaction", tamper)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired):
            await h.sessions._finalize_runtime_result(**h.result_values)
    actual = await h.store.get_session(h.session_id)
    assert len(actual.messages) == 1 and actual.messages[0].content == h.asset.user_message
    assert not actual.metadata.get("verification_results")
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_concurrent_input_and_unrelated_message_are_preserved(finalization_result, monkeypatch):
    h = finalization_result
    save = h.store.save_session_with_events
    later = ChatMessage(id="later-user-input", role=MessageRole.user, content="Keep this queued input",
        created_at=datetime.utcnow(), metadata={"turn_id": "later-turn", "delivery_status": "queued"})
    inserted = False
    async def conflict(session, events, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            latest = await h.store.get_session(h.session_id)
            latest.messages.append(later)
            latest.metadata["other-preference"] = "keep"
            await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
        return await save(session, events, **kwargs)
    monkeypatch.setattr(h.store, "save_session_with_events", conflict)
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    actual = await h.store.get_session(h.session_id)
    assert actual.metadata["other-preference"] == "keep"
    assert [message for message in actual.messages if message.id == later.id] == [later]
    assert len(actual.metadata["verification_results"]) == 1
    assert len({message.id for message in actual.messages}) == len(actual.messages)


@pytest.mark.asyncio
async def test_legacy_message_updates_remain_editable(finalization_result):
    h = finalization_result
    latest = await h.store.get_session(h.session_id)
    latest.messages[0].content = "edited through legacy save"
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    assert (await h.store.get_session(h.session_id)).messages[0].content == "edited through legacy save"


@pytest.mark.asyncio
async def test_message_identity_owned_by_other_session_cannot_be_stolen(finalization_result):
    from src.runtime.framework_finalization_projection import original_messages

    h = finalization_result
    latest = await h.store.get_session(h.session_id)
    foreign = deepcopy(latest)
    foreign.id, foreign.state_revision = h.session_id + "-foreign", 0
    foreign.metadata, foreign.messages = {}, [original_messages(h.asset)[-1]]
    await h.store.save_session(foreign)
    before = await h.store.get_session(foreign.id)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Mm]essage"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_session(foreign.id) == before
    assert len((await h.store.get_session(h.session_id)).messages) == 1


@pytest.mark.asyncio
async def test_changed_admitted_input_is_not_signed_as_original_result(finalization_result):
    h = finalization_result
    latest = await h.store.get_session(h.session_id)
    latest.messages[0].content = "not the admitted input"
    await h.store.save_session(latest, turn_lease_token=h.task.lease_token)
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="[Mm]essage"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest


@pytest.mark.asyncio
async def test_ack_only_reader_does_not_acquire_result_write_authority(finalization_result):
    from src.runtime.framework_application_projection import load_committed_projection

    h = finalization_result
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    assert (await h.tasks.claim(h.task.id, worker_id="read-expiry", expected_binding=h.binding)).status == "recovery_required"
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    task = await h.tasks.claim(successor.id, worker_id="ack-reader", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="ack-read", fencing_token=9)
    original = await stage(h, task)
    assert original.recoveries[-1].resume_phase == "runtime_submitted"
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="owner"):
        await FrameworkRuntimeResults(h.tasks).load(task.runtime_owner)
    proof, snapshot, assistant = await load_committed_projection(h.settings, task.runtime_owner)
    assert proof.snapshot_id == snapshot.id == h.asset.runtime_result.snapshot.id
    assert assistant.id == h.asset.assistant_message_id
    assert await stage(h, task) == original


@pytest.mark.asyncio
async def test_committed_verification_and_tool_messages_allow_ack_only_successor(finalization_result):
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_framework_message_recovery import outbox

    h = finalization_result
    with framework_owner_scope(h.task.runtime_owner):
        await h.sessions._finalize_runtime_result(**h.result_values)
    before = await h.store.get_session(h.session_id)
    assert len(before.messages) == 3 and len(before.metadata["verification_results"]) == 1
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    assert (await h.tasks.claim(h.task.id, worker_id="expiry", expected_binding=h.binding)).status == "recovery_required"
    terminal = await h.tasks.get(h.task.id)
    successor = await h.tasks.ensure_framework_message_recovery(h.task.id)
    assert successor is not None
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
    await h.dispatcher.handle_v2(QueuedTask("finalization-ack", outbox(h, successor.id)["payload"]),
        worker_id="finalization-ack-worker")
    assert (await h.tasks.get(successor.id)).status == "completed"
    assert await h.tasks.get(h.task.id) == terminal
    actual = await h.store.get_session(h.session_id)
    assert actual.metadata["verification_results"] == before.metadata["verification_results"]
    for message in before.messages:
        saved = next(item for item in actual.messages if item.id == message.id)
        assert saved.content == message.content and saved.created_at == message.created_at and saved.role == message.role
    assert len(actual.messages) == len(before.messages)
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finalization_result", [{"hidden": True}, {"empty": True}], indirect=True)
async def test_context_only_tools_and_empty_output_keep_existing_persistence_semantics(finalization_result):
    h = finalization_result
    with framework_owner_scope(h.task.runtime_owner):
        response = await h.sessions._finalize_runtime_result(**h.result_values)
    actual = await h.store.get_session(h.session_id)
    assert len(actual.messages) == 2
    if h.output_text:
        assert not any(item.role == MessageRole.tool for item in actual.messages)
        assert response.output.id == h.asset.assistant_message_id
    else:
        assert not any(item.role == MessageRole.assistant for item in actual.messages)
        assert response.output.id == h.asset.runtime_result.snapshot.id and response.output.content == ""
    assert actual.metadata["verification_results"][0]["status"] == "not_run"


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_final_session", "inside_final_transaction"])
async def test_official_worker_kill_does_not_publish_uncommitted_finalization(admission, window):
    from redis.asyncio import Redis
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
    from tests.test_framework_application_projection import worker_waiting_after_application_commit
    from tests.test_framework_chat_models import _config, _provider
    from tests.test_framework_model_admission import submit
    from tests.test_runtime_binding_persistence import make_command

    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 30
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=45)
    process = None
    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            original = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="projection-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":finalization"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate, window))
            process.start()
            reached = await waiting.blpop(gate, timeout=40)
            marker = "finalization-prepared" if window == "before_final_session" else "finalization-uncommitted"
            assert reached is not None and reached[1] == marker, f"worker exit code: {process.exitcode}"
            before = await stage(h, original)
            asset = FrameworkRuntimeResultAsset.model_validate(before.source_manifest["runtime_result_asset"])
            assert len(asset.runtime_result.tool_messages) == len(asset.verification_results) == 1
            assert asset.verification_results[0].status == VerificationStatus.not_run
            assert "application_projection_commit" not in before.source_manifest
            snapshot = await h.store.get_latest_snapshot(h.session_id)
            assert snapshot.id == asset.runtime_result.snapshot.id
            current = await h.store.get_session(h.session_id)
            assert len(current.messages) == 1 and not current.metadata.get("verification_results")
            assert len(requests) == 1 and process.is_alive()
            budget = await h.calls.snapshot(h.binding)
            active = await h.tasks.get(original.id)
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            terminal = await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
            assert terminal.status == "recovery_required"
            successor = await h.tasks.ensure_framework_message_recovery(original.id)
            assert successor is not None
            after = await stage(h, original)
            assert after.source_manifest == before.source_manifest
            assert await h.calls.snapshot(h.binding) == budget and len(requests) == 1
            actual = await h.store.get_session(h.session_id)
            assert len(actual.messages) == 1 and not actual.metadata.get("verification_results")
            events = await h.store.list_events(h.session_id)
            assert not any(e.type == "turn.completed" for e in events)
            event, = [e for e in events if e.type == "verification.completed"]
            assert event.id == asset.event_id("verification.completed")
            from tests.test_framework_partial_projection_recovery import complete_in_new_process

            terminal_record = await h.tasks.get(original.id)
            await complete_in_new_process(h, successor)
            assert await h.tasks.get(original.id) == terminal_record
            assert (await h.tasks.get(successor.id)).status == "completed"
            saved = await h.store.get_session(h.session_id)
            assert len(saved.messages) == 3
            assert saved.metadata["verification_results"] == [item.model_dump(mode="json") for item in asset.verification_results]
            assert await h.calls.snapshot(h.binding) == budget and len(requests) == 1
            assert len([e for e in await h.store.list_events(h.session_id) if e.type == "verification.completed"]) == 1
            assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")
