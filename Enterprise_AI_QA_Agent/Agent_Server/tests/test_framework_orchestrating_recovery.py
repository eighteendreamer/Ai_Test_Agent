"""Recover the pre-graph phase without starting its semantic classification anew."""
from __future__ import annotations

import json

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_message_stages import FrameworkMessageStages
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.infrastructure.redis_task_queue import QueuedTask
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected, run, stage
from tests.test_framework_message_recovery import isolate_recovery_stores, outbox
from tests.test_framework_chat_models import TRANSPORTS, _provider
from tests.test_framework_model_result_import import wire_evidence
from tests.test_framework_semantic_input import wire_semantic
from tests.test_framework_semantic_result_import import semantic_reply


async def pause_orchestration(h, monkeypatch, window):
    task = await selected(h)

    async def stop(*args, **kwargs):
        raise ConnectionError("injected orchestration stop")

    with monkeypatch.context() as patch:
        if window == "ready":
            patch.setattr(h.calls, "start", stop)
        elif window == "unreserved":
            patch.setattr(h.semantic, "enrich_prepared", stop)
        else:
            patch.setattr(FrameworkMessageStages, "freeze_execution", stop)
        with pytest.raises(ConnectionError, match="orchestration stop"):
            await run(h, task)
    assert (await stage(h, task)).phase == "orchestrating"
    before = await h.calls.snapshot(h.binding)
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    assert (await h.tasks.claim(task.id, worker_id="expiry", expected_binding=h.binding)).status == "recovery_required"
    return task, await h.tasks.get(task.id), before


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("window", ["completed", "unreserved", "ready", "skip"])
async def test_orchestrating_successor_uses_original_preparation_and_budget(admission, monkeypatch, window, transport):
    h = admission
    with _provider(transport, first_reply=semantic_reply(transport)) as (url, requests):
        h.models = wire_semantic(h, url, transport)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        if window == "skip":
            h.semantic._enabled = False
        original, terminal, before = await pause_orchestration(h, monkeypatch, window)
        frozen = await stage(h, original)
        successor = await h.tasks.ensure_framework_message_recovery(original.id)
        assert successor is not None
        link = (await stage(h, original)).recoveries[-1]
        assert link.resume_phase == "orchestrating" and link.source_sha256 == frozen.source_sha256

        async def never_reclassify(*args, **kwargs):
            pytest.fail("a recovery must consume the original preparation")

        monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", never_reclassify)
        monkeypatch.setattr(h.semantic, "prepare", never_reclassify)
        h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
        await h.dispatcher.handle_v2(QueuedTask("resume", outbox(h, successor.id)["payload"]), worker_id="recovery")
        assert (await h.tasks.get(successor.id)).status == "completed"
        assert await h.tasks.get(original.id) == terminal
        final = await h.calls.snapshot(h.binding)
        assert final.policy == before.policy and len(requests) == (0 if window == "skip" else 1)
        assert final.reserved_calls == (0 if window == "skip" else 1)
        if window == "completed":
            assert final == before
        elif window == "ready":
            assert final.calls[0].intent == before.calls[0].intent
            assert len(final.calls[0].reservation_transfers) == 1
        assert len(h.runtime.calls) == 1
        from src.runtime.framework_runtime_results import runtime_result_source

        saved = await stage(h, original)
        # Immutable orchestration inputs are unchanged; successful finalization
        # legitimately appends original-result and projection receipts.
        assert runtime_result_source(saved) == frozen.source_manifest
        assert saved.source_manifest["runtime_result_asset"]["source_sha256"] == frozen.source_sha256
        assert "application_projection_commit" in saved.source_manifest
        await h.dispatcher.handle_v2(QueuedTask("duplicate", outbox(h, successor.id)["payload"]), worker_id="duplicate")
        assert len(h.runtime.calls) == 1 and len(requests) == (0 if window == "skip" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["completed", "ready"])
async def test_orchestrating_diagnosis_requires_admission_and_never_authorizes_replay(admission, monkeypatch, window):
    h = admission
    with _provider("openai_chat_completions", first_reply=semantic_reply("openai_chat_completions")) as (url, requests):
        h.models = wire_semantic(h, url)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        original, terminal, before = await pause_orchestration(h, monkeypatch, window)
        report = await h.tasks.diagnose_framework_message(original.id, session_id=h.session_id)
        assert report.stage.phase == "orchestrating"
        assert "stage_recovery_requires_admission" in report.issues
        assert "stage_recovery_not_implemented" not in report.issues
        assert not report.automatic_replay_allowed
        assert await h.tasks.get(original.id) == terminal and await h.calls.snapshot(h.binding) == before
        assert len(await h.tasks.list_session(h.session_id)) == 1 and h.runtime.calls == []
        assert len(requests) == (1 if window == "completed" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["release", "input", "settlement", "schema"])
async def test_published_prepared_proof_is_rechecked_before_creating_an_attempt(admission, monkeypatch, changed):
    from src.runtime import framework_orchestration_artifact as artifacts
    from src.schemas.framework_approval import approval_value_hash

    h = admission
    with _provider("openai_chat_completions", first_reply=semantic_reply("openai_chat_completions")) as (url, requests):
        h.models = wire_semantic(h, url)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        original, terminal, _ = await pause_orchestration(h, monkeypatch, "completed")
        successor = await h.tasks.ensure_framework_message_recovery(original.id)
        assert successor is not None
        record = (await stage(h, original)).model_dump(mode="json")
        if changed == "release":
            different = artifacts.orchestration_artifact().model_copy(update={"python_version": "different-release"})
            monkeypatch.setattr(artifacts, "orchestration_artifact", lambda: different)
        else:
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                if changed == "settlement":
                    cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{settled_response_id}}',%s::jsonb) WHERE binding_id=%s",
                        (json.dumps("different-response-asset"), h.binding.binding_id))
                else:
                    if changed == "schema":
                        record["schema_version"] = 3
                    else:
                        record["source_manifest"]["semantic_input"]["request"]["system_prompt"] += " changed"
                        record["source_sha256"] = approval_value_hash(record["source_manifest"])
                    cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                        (json.dumps(record), original.id))
        rejected = await h.tasks.claim(successor.id, worker_id="changed-proof", expected_binding=h.binding)
        assert rejected.status == "recovery_required" and rejected.attempt_id is None
        assert rejected.error_code == "framework_message_recovery_changed"
        assert await h.tasks.get(original.id) == terminal and len(requests) == 1 and h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["completed", "unreserved", "ready", "skip"])
async def test_prepared_recovery_publication_failure_leaves_no_partial_successor(admission, monkeypatch, window):
    h = admission
    with _provider("openai_chat_completions", first_reply=semantic_reply("openai_chat_completions")) as (url, requests):
        h.models = wire_semantic(h, url)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        if window == "skip":
            h.semantic._enabled = False
        original, terminal, before = await pause_orchestration(h, monkeypatch, window)
        saved = await stage(h, original)
        event = h.tasks._event

        def fail(cur, row, event_type, payload):
            if event_type == "framework.message_recover_queued":
                raise ConnectionError("injected prepared recovery publication failure")
            return event(cur, row, event_type, payload)

        with monkeypatch.context() as patch:
            patch.setattr(h.tasks, "_event", fail)
            with pytest.raises(ConnectionError, match="publication failure"):
                await h.tasks.ensure_framework_message_recovery(original.id)
        assert await stage(h, original) == saved and await h.tasks.get(original.id) == terminal
        assert await h.calls.snapshot(h.binding) == before
        assert len(await h.tasks.list_session(h.session_id)) == 1
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {h.settings.database.postgres_task_outbox_table}")
            assert cur.fetchone()["n"] == 1
        successor = await h.tasks.ensure_framework_message_recovery(original.id)
        assert successor is not None and (await stage(h, original)).recoveries[-1].task_id == successor.id
        assert len(requests) == (1 if window == "completed" else 0)


@pytest.mark.asyncio
async def test_sent_semantic_failure_remains_unknown_and_never_gets_a_resending_successor(admission, monkeypatch):
    h = admission
    with _provider("openai_chat_completions", status=503) as (url, requests):
        wire_semantic(h, url)
        original, terminal, before = await pause_orchestration(h, monkeypatch, "completed")
        assert len(requests) == 1 and before.calls[0].phase == "unknown"
        assert await h.tasks.ensure_framework_message_recovery(original.id) is None
        assert await h.tasks.get(original.id) == terminal
        assert await h.calls.snapshot(h.binding) == before and h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing_artifact", "artifact", "missing_input", "request", "position", "owner", "unbound"])
async def test_orchestrating_phase_cannot_override_missing_or_changed_frozen_proof(admission, monkeypatch, changed):
    from src.schemas.framework_approval import approval_value_hash

    h = admission
    with _provider("openai_chat_completions", first_reply=semantic_reply("openai_chat_completions")) as (url, requests):
        h.models = wire_semantic(h, url)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        original, terminal, _ = await pause_orchestration(h, monkeypatch, "completed")
        record = (await stage(h, original)).model_dump(mode="json")
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            if changed in {"position", "owner", "unbound"}:
                path, value = ({"position": (["intent", "logical_call_id"], "foreign-position"),
                    "owner": (["owner_task_id"], "foreign-owner"), "unbound": (["settled_response_id"], None)}[changed])
                cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,%s,%s::jsonb) WHERE binding_id=%s",
                    (path, json.dumps(value), h.binding.binding_id))
            else:
                manifest = record["source_manifest"]
                if changed == "missing_artifact":
                    manifest.pop("orchestration_artifact")
                elif changed == "missing_input":
                    manifest.pop("semantic_input")
                elif changed == "artifact":
                    manifest["orchestration_artifact"]["python_version"] = "different-application"
                else:
                    manifest["semantic_input"]["request"]["system_prompt"] += " changed"
                record["source_sha256"] = approval_value_hash(manifest)
                cur.execute(f"UPDATE {h.tasks._tasks}_message_stage SET record=%s::jsonb WHERE task_id=%s",
                    (json.dumps(record), original.id))
        assert await h.tasks.ensure_framework_message_recovery(original.id) is None
        assert await h.tasks.get(original.id) == terminal
        assert len(requests) == 1 and h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["completed", "ready"])
async def test_replacement_can_lose_its_owner_again_without_restarting_semantic_input(admission, monkeypatch, window):
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.runtime.store import ContinuationLeaseLostError

    h = admission
    with _provider("openai_chat_completions", first_reply=semantic_reply("openai_chat_completions")) as (url, requests):
        h.models = wire_semantic(h, url)
        h.policy = h.models._framework_model_call_policy
        wire_evidence(h, monkeypatch)
        original, terminal, _ = await pause_orchestration(h, monkeypatch, window)
        first = await h.tasks.ensure_framework_message_recovery(original.id)
        first = await h.tasks.claim(first.id, worker_id="first", expected_binding=h.binding)
        await h.tasks.begin(first, resource_id="first", fencing_token=2)
        stages = FrameworkMessageStages(h.tasks)
        await stages.resume_orchestrating(first.runtime_owner, modes=h.sessions._mode_registry.list())
        calls = PostgresFrameworkModelCalls(h.settings)
        if window == "ready":
            await calls.transfer_ready(first.runtime_owner, expected=(await calls.snapshot(h.binding)).calls[0])
        assert await h.store.renew_turn_execution(h.session_id, first.lease_token, 0)
        await h.tasks.claim(first.id, worker_id="expiry", expected_binding=h.binding)
        first_terminal = await h.tasks.get(first.id)
        successor = await h.tasks.ensure_framework_message_recovery(first.id)
        assert successor is not None
        with pytest.raises(ContinuationLeaseLostError):
            await stages.resume_orchestrating(first.runtime_owner, modes=h.sessions._mode_registry.list())
        h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True))
        await h.dispatcher.handle_v2(QueuedTask("second", outbox(h, successor.id)["payload"]), worker_id="second")
        assert (await h.tasks.get(successor.id)).status == "completed"
        assert await h.tasks.get(first.id) == first_terminal and await h.tasks.get(original.id) == terminal
        assert len(requests) == 1 and len(h.runtime.calls) == 1
        final = await calls.snapshot(h.binding)
        assert len(final.calls) == 1
        assert len(final.calls[0].reservation_transfers) == (2 if window == "ready" else 0)
