"""Retain the original application result before any finalization side effect."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import asyncio
import json
import multiprocessing

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired, message_stage_table
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
from src.schemas.session import VerificationResult, VerificationStatus

from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_application_projection import projection, projection_jobs
from tests.test_framework_message_stages import selected, run, stage


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["worker-result", ""])
async def test_original_runtime_result_is_durable_before_finalizer(projection, monkeypatch, output):
    h = projection
    h.output_text = output
    task = await selected(h)
    observed = {}

    async def stop_before_finalization(**kwargs):
        saved = await stage(h, task)
        asset = saved.source_manifest["runtime_result_asset"]
        observed.update(asset)
        assert asset["runtime_result"]["output_text"] == kwargs["runtime_result"].output_text
        assert asset["runtime_result"]["snapshot"]["id"] == kwargs["runtime_result"].snapshot.id
        assert asset["assistant_message_id"] == kwargs["assistant_message_id"]
        assert asset["task_id"] == task.id
        raise ConnectionError("injected crash before application finalizer")

    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", stop_before_finalization)
    with pytest.raises(ConnectionError, match="before application finalizer"):
        await run(h, task)
    assert observed
    assert h.native_runs == 1 and len(h.runtime.calls) == 1
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))


@pytest_asyncio.fixture
async def frozen_result(projection, monkeypatch):
    h = projection
    h.task = await selected(h)
    h.result_values = {}

    async def pause(**kwargs):
        h.result_values.update(kwargs)
        raise ConnectionError("injected crash before application finalizer")

    with monkeypatch.context() as patch:
        patch.setattr(h.sessions, "_finalize_runtime_result", pause)
        with pytest.raises(ConnectionError, match="before application finalizer"):
            await run(h, h.task)
    h.results = FrameworkRuntimeResults(h.tasks)
    h.asset = await h.results.load(h.task.runtime_owner)
    return h


@pytest.mark.asyncio
async def test_asset_round_trip_restores_every_runtime_result_field_without_running(frozen_result):
    from dataclasses import fields

    h = frozen_result
    before = await h.calls.snapshot(h.binding)
    restored = FrameworkRuntimeResultAsset.model_validate_json(h.asset.model_dump_json())
    original = h.result_values["runtime_result"]
    assert set(type(restored.runtime_result).model_fields) == {field.name for field in fields(original)}
    assert restored.runtime_result == type(restored.runtime_result).from_result(original)
    assert restored.runtime_result.restore() == original
    assert restored == await FrameworkRuntimeResults(h.tasks).load(h.task.runtime_owner)
    assert h.asset.scope == "completed_message_finalization_input"
    assert h.native_runs == 1 and len(h.runtime.calls) == 1
    assert await h.calls.snapshot(h.binding) == before
    assert (await stage(h, h.task)).schema_version == 7
    assert "application_projection_commit" not in (await stage(h, h.task)).source_manifest
    # Asset existence alone does not yet grant a successor or graph replay.
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    terminal = await h.tasks.claim(h.task.id, worker_id="expiry", expected_binding=h.binding)
    assert terminal.status == "recovery_required"
    assert await h.tasks.ensure_framework_message_recovery(h.task.id) is None


@pytest.mark.asyncio
async def test_repeated_freeze_reuses_original_projection_ids_and_time(frozen_result):
    h = frozen_result
    first = h.asset
    repeated = await h.results.freeze(h.task.runtime_owner,
        runtime_result=h.result_values["runtime_result"], assistant_message_id=first.assistant_message_id,
        user_message=first.user_message, verification_results=[])
    assert repeated == first
    assert repeated.event_id("snapshot.saved") == first.event_id("snapshot.saved")
    assert repeated.event_id("turn.completed") != first.event_id("snapshot.saved")
    original = h.result_values["runtime_result"]
    original.state["mutable-caller"] = ["changed after persistence"]
    assert "mutable-caller" not in (await h.results.load(h.task.runtime_owner)).runtime_result.state
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="immutable"):
        await h.results.freeze(h.task.runtime_owner, runtime_result=original,
            assistant_message_id=first.assistant_message_id, user_message=first.user_message, verification_results=[])


@pytest.mark.asyncio
async def test_frozen_verification_and_projection_identities_are_used_once(projection, monkeypatch):
    h = projection
    task = await selected(h)
    calls = []

    def verify(**kwargs):
        calls.append(kwargs)
        return [VerificationResult(id="original-verification", session_id=h.session_id,
            turn_id=h.binding.turn_id, trace_id="original-trace", verifier="fixture",
            status=VerificationStatus.not_run, summary="No assertion means not_run", created_at=datetime.utcnow())]

    monkeypatch.setattr(h.sessions._verification_service, "build_results", verify)
    response = await run(h, task)
    saved = await stage(h, task)
    asset = FrameworkRuntimeResultAsset.model_validate(saved.source_manifest["runtime_result_asset"])
    assert len(calls) == 1
    assert response.output.id == asset.assistant_message_id
    assert response.output.created_at == asset.projection_at
    session = await h.store.get_session(h.session_id)
    assert session.metadata["verification_results"] == [item.model_dump(mode="json") for item in asset.verification_results]
    events = await h.store.list_events(h.session_id)
    for name in ("snapshot.saved", "verification.completed", "assistant.stream.completed",
        "assistant.response_generated", "turn.completed"):
        event, = [item for item in events if item.type == name]
        assert event.id == asset.event_id(name)
        assert event.timestamp == asset.projection_at


@pytest.mark.asyncio
async def test_original_asset_survives_final_transaction_failure(projection, monkeypatch):
    import src.runtime.framework_application_projection as commits

    h = projection
    task = await selected(h)
    original = commits.record_application_commit

    def rollback(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("injected final transaction rollback")

    with monkeypatch.context() as patch:
        patch.setattr(commits, "record_application_commit", rollback)
        with pytest.raises(ConnectionError, match="final transaction rollback"):
            await run(h, task)
    asset = await FrameworkRuntimeResults(h.tasks).load(task.runtime_owner)
    assert asset.runtime_result.output_text == h.output_text
    assert "application_projection_commit" not in (await stage(h, task)).source_manifest
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert await h.store.get_latest_snapshot(h.session_id) is not None
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_freeze_failure_after_stage_sql_rolls_back_asset(projection, monkeypatch):
    import src.runtime.framework_runtime_results as results

    h = projection
    task = await selected(h)
    original = results.postgres_connect

    @contextmanager
    def fail_write(settings):
        with original(settings) as conn:
            class Connection:
                @contextmanager
                def cursor(self):
                    with conn.cursor() as cur:
                        class Cursor:
                            def execute(self, sql, params=None):
                                returned = cur.execute(sql, params)
                                if sql.startswith(f"UPDATE {message_stage_table(settings)} SET record="):
                                    raise ConnectionError("injected asset transaction rollback")
                                return returned

                            def __getattr__(self, name):
                                return getattr(cur, name)
                        yield Cursor()
            yield Connection()

    with monkeypatch.context() as patch:
        patch.setattr(results, "postgres_connect", fail_write)
        with pytest.raises(ConnectionError, match="asset transaction rollback"):
            await run(h, task)
    assert "runtime_result_asset" not in (await stage(h, task)).source_manifest
    assert await h.store.get_latest_snapshot(h.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["output", "digest", "source", "task", "input", "schema"])
async def test_corrupt_or_foreign_asset_cannot_be_read(frozen_result, changed):
    h = frozen_result
    saved = await stage(h, h.task)
    record = saved.model_dump(mode="json")
    asset = record["source_manifest"]["runtime_result_asset"]
    if changed == "output":
        asset["runtime_result"]["output_text"] = "different result"
    elif changed == "digest":
        asset["asset_sha256"] = "0" * 64
    elif changed == "schema":
        record["schema_version"] = 6
    else:
        asset[{"source": "source_sha256", "task": "task_id", "input": "input_sha256"}[changed]] = (
            "foreign-task" if changed == "task" else "0" * 64)
        values = {key: value for key, value in asset.items() if key != "asset_sha256"}
        asset["asset_sha256"] = approval_value_hash(values)
    record["source_sha256"] = approval_value_hash(record["source_manifest"])
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE task_id=%s",
            (json.dumps(record), h.task.id))
    with pytest.raises((ValueError, FrameworkMessageStageRecoveryRequired)):
        await h.results.load(h.task.runtime_owner)


@pytest.mark.asyncio
async def test_expired_owner_cannot_read_or_replace_original_result(frozen_result):
    h = frozen_result
    assert await h.store.renew_turn_execution(h.session_id, h.task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await h.results.load(h.task.runtime_owner)
    with pytest.raises(ContinuationLeaseLostError):
        await h.results.freeze(h.task.runtime_owner, runtime_result=h.result_values["runtime_result"],
            assistant_message_id=h.asset.assistant_message_id, user_message=h.asset.user_message,
            verification_results=[])


@pytest.mark.asyncio
async def test_mutated_in_memory_finalization_asset_is_rejected_before_snapshot(frozen_result):
    h = frozen_result
    h.result_values["projection_asset"].runtime_result.state["changed"] = True
    with framework_owner_scope(h.task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="persisted original"):
            await h.sessions._finalize_runtime_result(**h.result_values)
    assert await h.store.get_latest_snapshot(h.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_finalization", "after_snapshot"])
async def test_official_worker_hard_kill_before_finalization_retains_result_not_commit(admission, window):
    from redis.asyncio import Redis
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from src.infrastructure.redis_task_queue import RedisTaskQueue
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
            gate = h.prefix + ":runtime-result-frozen"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate, window))
            process.start()
            reached = await waiting.blpop(gate, timeout=40)
            marker = "runtime-result-frozen" if window == "before_finalization" else "snapshot-committed"
            assert reached is not None and reached[1] == marker, f"worker exit code: {process.exitcode}"
            active = await h.tasks.get(original.id)
            # A task read intentionally does not hand out a live execution
            # capability. Inspect the durable asset, not an invented owner.
            assert active.status == "running" and active.runtime_owner is None
            before = await stage(h, original)
            asset = FrameworkRuntimeResultAsset.model_validate(before.source_manifest["runtime_result_asset"])
            assert asset.runtime_result.output_text == "fixture answer"
            assert asset.runtime_result.state["framework_messages"]
            assert asset.verification_results == []
            assert before.source_manifest["runtime_result_asset"] == asset.model_dump(mode="json")
            assert "application_projection_commit" not in before.source_manifest
            snapshot = await h.store.get_latest_snapshot(h.session_id)
            if window == "before_finalization":
                assert snapshot is None
                assert "snapshot_projection_commit" not in before.source_manifest
            else:
                from src.runtime.framework_application_projection import _hash

                assert snapshot.id == asset.runtime_result.snapshot.id
                receipt = before.source_manifest["snapshot_projection_commit"]
                assert receipt["runtime_result_asset_sha256"] == asset.asset_sha256
                assert receipt["snapshot_sha256"] == _hash(snapshot.model_dump(mode="python"))
                assert before.schema_version == 8
            assert len((await h.store.get_session(h.session_id)).messages) == 1
            assert len(requests) == 1 and process.is_alive()
            budget = await h.calls.snapshot(h.binding)
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            terminal = await h.tasks.claim(original.id, worker_id="expiry", expected_binding=h.binding)
            assert terminal.status == "recovery_required"
            assert await h.tasks.ensure_framework_message_recovery(original.id) is None
            assert (await stage(h, original)).source_manifest["runtime_result_asset"] == before.source_manifest["runtime_result_asset"]
            assert (await stage(h, original)).source_manifest.get("snapshot_projection_commit") == before.source_manifest.get("snapshot_projection_commit")
            assert await h.calls.snapshot(h.binding) == budget and len(requests) == 1
            assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_native_message_blocks_and_tool_pair_survive_original_asset(projection, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

    h = projection
    original = h.runtime.execute_turn
    native = messages_to_dict([
        HumanMessage(id="original-user", content="read fixture"),
        AIMessage(id="original-ai", content=[{"type": "thinking", "thinking": "fixture", "signature": "synthetic-signature"}],
            tool_calls=[{"id": "original-call", "name": "read_fixture", "args": {}}],
            response_metadata={"fixture": "provider metadata"}),
        ToolMessage(id="original-tool", tool_call_id="original-call", content="fixture result"),
    ])

    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.state["framework_messages"] = native
        return result

    async def pause(**kwargs):
        raise ConnectionError("after native result asset")

    monkeypatch.setattr(h.runtime, "execute_turn", execute)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", pause)
    task = await selected(h)
    with pytest.raises(ConnectionError, match="native result asset"):
        await run(h, task)
    asset = await FrameworkRuntimeResults(h.tasks).load(task.runtime_owner)
    assert asset.runtime_result.restore().state["framework_messages"] == native
    assert native[1]["data"]["content"][0]["signature"] == "synthetic-signature"


@pytest.mark.asyncio
async def test_result_asset_rejects_unserializable_process_values(frozen_result):
    from pydantic_core import PydanticSerializationError

    h = frozen_result
    result = h.result_values["runtime_result"]
    result.state["process_local"] = object()
    with pytest.raises(PydanticSerializationError):
        await h.results.freeze(h.task.runtime_owner, runtime_result=result,
            assistant_message_id=h.asset.assistant_message_id, user_message=h.asset.user_message,
            verification_results=[])
    assert (await h.results.load(h.task.runtime_owner)) == h.asset


@pytest.mark.asyncio
async def test_asset_does_not_promote_pending_interrupted_or_foreign_results(frozen_result):
    from pydantic import ValidationError

    for changed in ("pending", "approval", "interrupted", "control", "snapshot", "binding", "verification"):
        values = frozen_result.asset.model_dump(mode="json")
        result = values["runtime_result"]
        if changed == "pending":
            result["pending_turn"] = {"resume": "not completed"}
        elif changed == "approval":
            result["approvals"] = [{"id": "still waiting"}]
        elif changed == "interrupted":
            result["state"]["termination_reason"] = "interrupted"
        elif changed == "control":
            result["state"]["control_state"] = "waiting_approval"
        elif changed == "snapshot":
            result["snapshot"]["stage"] = "waiting_approval"
        elif changed == "binding":
            result["state"]["context_bundle"]["runtime_binding"]["turn_id"] = "foreign-turn"
        else:
            values["verification_results"] = [VerificationResult(id="foreign-verification",
                session_id="foreign-session", turn_id=frozen_result.binding.turn_id, trace_id="fixture",
                verifier="fixture", status=VerificationStatus.not_run, summary="foreign",
                created_at=datetime.utcnow()).model_dump(mode="json")]
        values["asset_sha256"] = approval_value_hash({k: v for k, v in values.items() if k != "asset_sha256"})
        with pytest.raises(ValidationError):
            FrameworkRuntimeResultAsset.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["model", "tool"])
async def test_execution_fact_drift_after_freeze_stops_finalizer_before_writes(projection_jobs, monkeypatch, changed):
    h = projection_jobs
    h.model_phase = "completed"
    task = await selected(h)
    captured = {}

    async def pause(**kwargs):
        captured.update(kwargs)
        raise ConnectionError("after original result freeze")

    with monkeypatch.context() as patch:
        patch.setattr(h.sessions, "_finalize_runtime_result", pause)
        with pytest.raises(ConnectionError, match="original result freeze"):
            await run(h, task)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "model":
            cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{outcome_code}}','\"changed\"') "
                "WHERE binding_id=%s", (h.binding.binding_id,))
        else:
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET attempt=attempt+1 WHERE id=%s", (h.job.id,))
    with framework_owner_scope(task.runtime_owner):
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="execution facts changed"):
            await h.sessions._finalize_runtime_result(**captured)
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert h.native_runs == 1 and len(h.runtime.calls) == 1


@pytest.mark.asyncio
async def test_application_commit_keeps_original_asset_and_prevents_refreeze(projection, monkeypatch):
    from src.runtime.framework_message_stages import FrameworkMessageStages

    h = projection
    task = await selected(h)
    original_finalize = h.sessions._finalize_runtime_result
    captured = {}

    async def finalize(**kwargs):
        captured.update(kwargs)
        return await original_finalize(**kwargs)

    async def lose_ack(*args, **kwargs):
        raise ConnectionError("after original application commit")

    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", finalize)
    monkeypatch.setattr(FrameworkMessageStages, "projected", lose_ack)
    with pytest.raises(ConnectionError, match="application commit"):
        await run(h, task)
    results = FrameworkRuntimeResults(h.tasks)
    asset = await results.load(task.runtime_owner)
    assert asset == captured["projection_asset"]
    assert "application_projection_commit" in (await stage(h, task)).source_manifest
    assert (await stage(h, task)).schema_version == 8
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="after application commit"):
        await results.freeze(task.runtime_owner, runtime_result=captured["runtime_result"],
            assistant_message_id=asset.assistant_message_id, user_message=asset.user_message,
            verification_results=[])
    assert len([e for e in await h.store.list_events(h.session_id) if e.type == "turn.completed"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["model", "tool", "asset", "asset_removed"])
async def test_final_transaction_rechecks_original_asset_after_async_projection_writes(projection_jobs, monkeypatch, changed):
    h = projection_jobs
    h.model_phase = "completed"
    task = await selected(h)
    save = h.store.save_session_with_events

    async def change_before_transaction(session, events, **kwargs):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            if changed == "model":
                cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{outcome_code}}','\"late-change\"') "
                    "WHERE binding_id=%s", (h.binding.binding_id,))
            elif changed == "tool":
                cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET attempt=attempt+1 WHERE id=%s", (h.job.id,))
            else:
                cur.execute(f"SELECT record FROM {message_stage_table(h.settings)} WHERE task_id=%s", (task.id,))
                record = cur.fetchone()["record"]
                if changed == "asset_removed":
                    record["source_manifest"].pop("runtime_result_asset")
                else:
                    asset = record["source_manifest"]["runtime_result_asset"]
                    asset["runtime_result"]["output_text"] = "late-change"
                    asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
                record["source_sha256"] = approval_value_hash(record["source_manifest"])
                cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=%s::jsonb WHERE task_id=%s",
                    (json.dumps(record), task.id))
        return await save(session, events, **kwargs)

    monkeypatch.setattr(h.store, "save_session_with_events", change_before_transaction)
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await run(h, task)
    # This failure sample may deliberately leave an invalid stage (a snapshot
    # receipt without its asset). Inspect raw storage, not its strict reader.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {message_stage_table(h.settings)} WHERE task_id=%s", (task.id,))
        assert "application_projection_commit" not in cur.fetchone()["record"]["source_manifest"]
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert await h.store.get_latest_snapshot(h.session_id) is not None
    assert not any(e.type == "turn.completed" for e in await h.store.list_events(h.session_id))
    assert h.native_runs == 1 and len(h.runtime.calls) == 1
