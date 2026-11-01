"""Formal result assets retain original identity, not completion or replay rights."""
from copy import deepcopy
import asyncio
import json
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.schemas.controlled_case_admission import read_admission_checkpoint
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import VerificationResult, VerificationStatus
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred

from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest, submit
from tests.test_controlled_case_result import projected_case
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import prepare, assembly, implementation, full_input
from tests.test_framework_chat_models import _provider, _config, _tool_reply


@pytest.mark.asyncio
async def test_normal_case_freezes_original_asset_before_return_without_faking_message_stage(projected_case):
    p = projected_case
    asset = p.result.result_asset
    assert asset is not None and asset.schema_version == 2
    assert asset.scope == "completed_controlled_case_finalization_input"
    # Persisted JSON normalizes datetimes inside untyped state dictionaries.
    assert asset.runtime_result.model_dump(mode="json") == FrameworkRuntimeResult.from_result(
        p.result.runtime_result).model_dump(mode="json")
    identity = p.s.asset.runtime_preparation.identity
    assert asset.assistant_message_id == identity.assistant_message_id
    assert (asset.run_id, asset.run_item_id, asset.run_item_execution_attempt_id) == (
        p.s.item.run_id, p.s.item.id, p.s.attempt.id)
    assert asset.task_id == p.h.task.id and asset.attempt_id == p.h.task.attempt_id
    assert asset.attempt_id != asset.run_item_execution_attempt_id
    assert asset.projection_at == p.result.runtime_result.events[-1].timestamp
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        assert attempt.checkpoint_version == 2
        original = read_admission_checkpoint(attempt)
        assert original.input_sha256 == p.s.asset.asset_sha256
        assert original.admitted_at == attempt.checkpoint_at
        cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._tasks}_message_stage")
        assert cur.fetchone()["n"] == 0
        for table in (p.s.h.settings.database.postgres_message_table,
                      p.s.h.settings.database.postgres_snapshot_table, p.s.d.runs._result_table):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
    before = database_digest(p.s)
    results = FrameworkRuntimeResults(p.s.h.tasks)
    loaded = await results.load_controlled_case(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert loaded == asset
    again = await results.freeze_controlled_case(p.h.task.runtime_owner, runtime_result=p.result.runtime_result,
        native=p.result.graph_outcome.native_terminal)
    assert again == asset and database_digest(p.s) == before
    assert len(p.requests) == 2 and len(p.effects) == 1
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["assistant", "user_message", "projection_at", "verification"])
async def test_rehashed_retained_asset_cannot_replace_original_finalization_intent(projected_case, drift):
    p = projected_case
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        checkpoint = deepcopy(attempt.checkpoint_payload)
        raw = checkpoint["runtime_result_asset"]
        if drift == "assistant":
            raw["assistant_message_id"] = "foreign-output"
        elif drift == "user_message":
            raw["user_message"] = "foreign formal objective"
        elif drift == "projection_at":
            raw["projection_at"] = "2000-01-01T00:00:00Z"
        else:
            raw["verification_results"] = [VerificationResult(id="foreign-verification", tool_call_id="foreign-call",
                session_id=p.s.session.id, turn_id=p.s.binding.turn_id, tool_key="inspect_source",
                trace_id="fixture", verifier="fixture", created_at=p.result.result_asset.projection_at,
                status=VerificationStatus.passed, summary="not the original verification").model_dump(mode="json")]
        raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
        p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": checkpoint}))
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(
            p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert database_digest(p.s) == before and len(p.effects) == 1 and len(p.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["native", "job", "ledger", "event", "receipt", "lease", "stripped_owner"])
async def test_retained_result_load_still_requires_original_facts_and_both_live_leases(projected_case, drift):
    p = projected_case
    owner, native = deepcopy((p.h.task.runtime_owner, p.result.graph_outcome.native_terminal))
    from src.runtime.controlled_case_results import asset_event_id

    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if drift == "native":
            native.values["messages"][-1].content = "foreign answer"
        elif drift == "job":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_tool_job_table} SET summary='foreign' WHERE session_id=%s", (p.s.session.id,))
        elif drift == "ledger":
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif drift == "event":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,'{{message}}','\"foreign\"') WHERE id=%s",
                (p.result.runtime_result.events[0].id,))
        elif drift == "receipt":
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(p.s.binding),))
        elif drift == "lease":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        else:
            owner = owner.model_copy(update={"controlled_case_lease": None})
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(owner, native=native)
    assert database_digest(p.s) == before and len(p.effects) == 1 and len(p.requests) == 2


@pytest.mark.asyncio
async def test_retained_asset_reentry_does_not_rebuild_verifications_or_publish_completion(projected_case, monkeypatch):
    p = projected_case
    from src.application.testing.verification_service import VerificationService
    from src.runtime.controlled_case_results import asset_event_id

    def forbidden(*args, **kwargs):
        raise AssertionError("retained Verification must not be rebuilt")
    monkeypatch.setattr(VerificationService, "build_results", forbidden)
    results = FrameworkRuntimeResults(p.s.h.tasks)
    before = database_digest(p.s)
    first, second = await asyncio.gather(
        results.load_controlled_case(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal),
        results.freeze_controlled_case(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal,
            runtime_result=p.result.runtime_result))
    assert first == second == p.result.result_asset and database_digest(p.s) == before
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {p.s.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(p.s.binding),))
        assert cur.fetchone()["n"] == 1
    with pytest.raises(ValueError, match="finalizer"):
        await p.s.h.tasks.finish(p.h.task, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["receipt_write", "post_commit_ack", "cancel_waiter"])
async def test_result_retention_failure_preserves_real_commit_and_never_reexecutes(formal_case, tmp_path, monkeypatch, failure):
    from src.runtime.controlled_case_results import ControlledCaseResults, asset_event_id

    s, captured = formal_case, []
    entered, release, finished = Event(), Event(), Event()
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                real_freeze = FrameworkRuntimeResults.freeze_controlled_case
                real_material = ControlledCaseResults._material
                real_append = s.h.tasks._sessions.append_event_in_transaction
                async def freeze(self, owner, **kwargs):
                    captured.append((deepcopy(owner), deepcopy(kwargs)))
                    return await real_freeze(self, owner, **kwargs)
                def append(*args, **kwargs):
                    event = args[2]
                    if event.type == "test_run.case_result_prepared" and failure == "receipt_write":
                        raise RuntimeError("isolated retention receipt SQL failure")
                    value = real_append(*args, **kwargs)
                    if event.type == "test_run.case_result_prepared" and failure == "cancel_waiter":
                        entered.set()
                        assert release.wait(30), "test did not release original DB thread"
                    return value
                def material(self, *args):
                    try:
                        value = real_material(self, *args)
                        if failure == "post_commit_ack":
                            raise RuntimeError("isolated committed asset ACK loss")
                        return value
                    finally:
                        finished.set()
                monkeypatch.setattr(FrameworkRuntimeResults, "freeze_controlled_case", freeze)
                monkeypatch.setattr(ControlledCaseResults, "_material", material)
                monkeypatch.setattr(s.h.tasks._sessions, "append_event_in_transaction", append)
                runtime = implementation().ControlledCaseRuntime(runtime_service=bridge.runtime)
                operation = asyncio.create_task(runtime.execute(h.task, executor=executor(s, h),
                    resource_id="isolated-retention-failure", fencing_token=1))
                if failure == "cancel_waiter":
                    try:
                        assert await asyncio.to_thread(entered.wait, 30), "original DB transaction did not enter"
                        operation.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await operation
                    finally:
                        release.set()
                        assert await asyncio.to_thread(finished.wait, 30), "original DB thread did not finish"
                else:
                    with pytest.raises(RuntimeError):
                        await operation
                assert len(requests) == 2 and len(effects) == 1
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    attempt = s.d.runs._get_attempt_by_id(cur, s.attempt.id)
                    assert attempt.checkpoint_version == (1 if failure == "receipt_write" else 2)
                    cur.execute(f"SELECT id FROM {s.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(s.binding),))
                    assert (cur.fetchone() is not None) == (failure != "receipt_write")
                monkeypatch.setattr(FrameworkRuntimeResults, "freeze_controlled_case", real_freeze)
                monkeypatch.setattr(ControlledCaseResults, "_material", real_material)
                monkeypatch.setattr(s.h.tasks._sessions, "append_event_in_transaction", real_append)
                owner, kwargs = captured[0]
                asset = await FrameworkRuntimeResults(s.h.tasks).freeze_controlled_case(owner, **kwargs)
                assert asset.runtime_result.output_text == "fixture answer"
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                with pytest.raises((ValueError, RuntimeError)):
                    await runtime.execute(h.task, executor=executor(s, h), resource_id="isolated-retention-failure", fencing_token=1)
                assert len(requests) == 2 and len(effects) == 1


@pytest.mark.asyncio
async def test_older_input_reader_defers_before_claim_or_input_replacement(full_input):
    s, asset = full_input.s, full_input.asset
    s.asset = asset
    s.command = s.command.model_copy(update={"case_execution_input": asset})
    await submit(s)
    before = database_digest(s)
    with pytest.raises(TaskDeferred, match="case_.*format"):
        await s.h.tasks.claim_controlled_case(delivery(s), worker_id="incompatible-case-reader",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True,
                controlled_case_input_max_version=3))
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_reader", "future_field", "future_version"])
async def test_retained_checkpoint_format_is_checked_before_reclaim_writes(projected_case, change):
    p = projected_case
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        if change == "future_field":
            raw = deepcopy(attempt.checkpoint_payload)
            raw["future_receipt_field"] = True
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": raw}))
        elif change == "future_version":
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_version": 3}))
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format"):
        await p.s.h.tasks.claim_controlled_case(delivery(p.s), worker_id="incompatible-checkpoint-reader",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True,
                controlled_case_checkpoint_max_version=1 if change == "old_reader" else 2))
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_nonempty_verification_uses_original_preparation_and_is_retained_without_rebuilding(formal_case, tmp_path, monkeypatch):
    from src.schemas.agent import ToolDescriptor
    from src.schemas.framework_verification_projection import VerificationProjectionIdentity
    from src.application.testing.verification_service import VerificationService

    s = formal_case
    descriptor = ToolDescriptor(key="api-test-runner", name="Fixture API runner", category="execution",
        description="Synthetic assertion output only, no external API call", permission_level="safe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = descriptor.key
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async def prepare_input(models):
            p = assembly(s, tmp_path / "assembly", models)
            # Fixture-local synthetic descriptor, not the real mode Runner.
            # The registry already owns this static key; dynamic override is forbidden.
            p.parts.tool_registry._replace_descriptor(descriptor.key, **descriptor.model_dump(exclude={"key"}))
            agent = p.parts.agent_registry.resolve_for_message("")
            agent.supported_tools.append(descriptor.key)
            p.request.context["requested_tool_keys"] = [descriptor.key]
            return await prepare(s, p)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                bridge.runtime._tool_registry._replace_descriptor(descriptor.key, **descriptor.model_dump(exclude={"key"}))
                async def handle(arguments, context):
                    effects.append(context.tool_job_id)
                    return {"status": "completed", "summary": "synthetic assertion result",
                        "checks": [{"name": "fixture-only", "passed": False}]}
                bridge.runtime._tool_runtime_service._handlers[descriptor.key] = handle
                outcome = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-nonempty-verification", fencing_token=1)
                asset = outcome.result_asset
                verification, = asset.verification_results
                assert verification.status == VerificationStatus.failed
                record, = outcome.runtime_result.state["tool_results"]
                identity = VerificationProjectionIdentity.for_record(s.asset.verification_preparation,
                    record, outcome.runtime_result.state["context_bundle"])
                assert verification.id == identity.result_id and verification.created_at == identity.created_at
                assert verification.evidence[0].source_id == effects[0]
                def forbidden(*args, **kwargs):
                    raise AssertionError("retained nonempty Verification must not be rebuilt")
                monkeypatch.setattr(VerificationService, "build_results", forbidden)
                loaded = await FrameworkRuntimeResults(s.h.tasks).load_controlled_case(
                    h.task.runtime_owner, native=outcome.graph_outcome.native_terminal)
                assert loaded == asset and len(requests) == 2 and len(effects) == 1
                assert (await s.d.runs.get_item(s.item.id)).result_id is None


@pytest.mark.asyncio
async def test_old_projection_input_does_not_silently_gain_result_retention(formal_case, tmp_path):
    from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async def prepare_input(models):
            raw = (await prepare(s, assembly(s, tmp_path / "assembly", models))).model_dump(mode="json")
            raw["schema_version"] = 3
            raw.pop("verification_preparation")
            raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
            return parse_controlled_case_task_input(raw)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            async with business_bridge(h, [], approval_required=False) as bridge:
                outcome = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-v3-projection", fencing_token=1)
                assert outcome.runtime_result is not None and outcome.result_asset is None
                with pytest.raises(RuntimeError, match="retention/Verification"):
                    await FrameworkRuntimeResults(s.h.tasks).freeze_controlled_case(h.task.runtime_owner,
                        runtime_result=outcome.runtime_result, native=outcome.graph_outcome.native_terminal)
                assert len(requests) == 1
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    assert s.d.runs._get_attempt_by_id(cur, s.attempt.id).checkpoint_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["missing", "context", "time", "trace"])
async def test_rehashed_input_still_requires_original_verification_preparation(full_input, drift):
    from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

    s, raw = full_input.s, full_input.asset.model_dump(mode="json")
    before = database_digest(s)
    preparation = raw["verification_preparation"]
    context = raw["runtime_preparation"]["initial_state"]["context_bundle"]
    if drift == "missing":
        raw.pop("verification_preparation")
    elif drift == "context":
        context.pop("framework_verification_preparation")
    else:
        preparation["reserved_at" if drift == "time" else "trace_id"] = (
            "2000-01-01T00:00:00Z" if drift == "time" else "foreign-trace")
        preparation["sha256"] = approval_value_hash({k: v for k, v in preparation.items() if k != "sha256"})
        context["framework_verification_preparation"] = deepcopy(preparation)
    raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
    with pytest.raises(ValueError):
        parse_controlled_case_task_input(raw)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_supplied_result_cannot_replace_retained_original_projection(projected_case):
    p = projected_case
    altered = deepcopy(p.result.runtime_result)
    altered.output_text = "not the original result"
    before = database_digest(p.s)
    with pytest.raises(RuntimeError, match="deterministic original projection"):
        await FrameworkRuntimeResults(p.s.h.tasks).freeze_controlled_case(p.h.task.runtime_owner,
            runtime_result=altered, native=p.result.graph_outcome.native_terminal)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1
