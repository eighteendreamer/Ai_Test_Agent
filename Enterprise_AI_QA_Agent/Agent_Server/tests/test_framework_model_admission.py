from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.schemas.framework_model_call import FrameworkModelBudget
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@pytest_asyncio.fixture
async def admission(harness):
    h = harness
    h.calls = PostgresFrameworkModelCalls(h.settings)
    await h.calls.initialize()
    try:
        h.binding = make_binding(h.session_id, f"turn-{uuid4().hex}")
        h.command = make_command(h, h.binding)
        h.payload, h.message, h.key = make_payload(), make_message(h, h.binding), uuid4().hex
        h.budget = FrameworkModelBudget(max_calls=8, max_tokens=100_000,
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            model_config_fingerprints={"primary": h.binding.model_config_fingerprint})
        yield h
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {h.calls.uploads_table}")
            cur.execute(f"DROP TABLE {h.calls.calls_table}")
            cur.execute(f"DROP TABLE {h.calls.budgets_table}")


async def submit(h, budget=None):
    return await h.tasks.submit_v2(h.command, h.payload, h.message, idempotency_key=h.key,
        model_budget=budget or h.budget)


def counts(h):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        result = {}
        for name, table in {
            "tasks": h.settings.database.postgres_agent_task_table,
            "owners": f"{h.settings.database.postgres_agent_task_table}_runtime",
            "outbox": h.settings.database.postgres_task_outbox_table,
            "budgets": h.calls.budgets_table,
            "messages": h.settings.database.postgres_message_table,
        }.items():
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            result[name] = cur.fetchone()["n"]
        return result


def model_configuration(**changes):
    from src.schemas.model_config import ModelConfigRecord

    values = dict(key="primary", name="Controlled model configuration", provider="openai",
        transport="openai_chat_completions", model_id="fixture-model", api_base_url="https://models.invalid/v1",
        is_active=True, temperature=.25, api_key="synthetic-private-material",
        extra_headers={"X-Test-Private": "synthetic-private-material"})
    values.update(changes)
    return ModelConfigRecord(**values)


def snapshot_budget(h):
    from src.schemas.model_config import ModelInvocationParameters

    configuration = model_configuration()
    snapshot = ModelInvocationParameters.from_configuration(configuration)
    h.binding = h.binding.model_copy(update={"model_config_fingerprint": snapshot.fingerprint})
    h.command = make_command(h, h.binding)
    h.budget = FrameworkModelBudget(**{**h.budget.model_dump(), "schema_version": 2,
        "model_config_fingerprints": {configuration.key: snapshot.fingerprint},
        "model_configurations": {configuration.key: snapshot}})
    return configuration, snapshot


@pytest.mark.parametrize("transport,provider", [("openai_chat_completions", "openai"),
    ("openai_responses", "openai"), ("anthropic_messages", "anthropic"),
    ("google_gemini_generate_content", "google")])
def test_model_configuration_snapshot_reuses_the_existing_nonsecret_projection(transport, provider):
    import hashlib
    from src.application.models.framework_model_call_policy import canonical_model_input, model_config_fingerprint
    from src.schemas.model_config import ModelInvocationParameters, model_invocation_parameters

    configuration = model_configuration(transport=transport, provider=provider)
    expected = model_invocation_parameters(configuration)
    snapshot = ModelInvocationParameters.from_configuration(configuration)
    assert snapshot.model_dump(mode="json") == expected
    assert snapshot.fingerprint == model_config_fingerprint(configuration)
    assert snapshot.fingerprint == hashlib.sha256(canonical_model_input(expected)).hexdigest()
    assert "synthetic-private-material" not in snapshot.model_dump_json()
    assert "models.invalid" not in snapshot.model_dump_json()
    configuration.temperature = .75
    configuration.capabilities.vision = True
    assert snapshot.model_dump(mode="json") == expected


@pytest.mark.parametrize("transport", ["openai_chat_completions", "openai_responses",
    "anthropic_messages", "google_gemini_generate_content"])
def test_model_configuration_snapshot_preserves_request_profile_and_is_detached(transport):
    from src.application.models.framework_model_call_policy import model_config_fingerprint
    from src.schemas.model_config import ModelInvocationParameters, model_invocation_parameters
    from src.schemas.model_request_options import ModelRequestProfile

    configuration = model_configuration(transport=transport,
        request_profile=ModelRequestProfile(output_formats=["json_schema"]))
    expected = model_invocation_parameters(configuration)
    snapshot = ModelInvocationParameters.from_configuration(configuration)
    assert snapshot.model_dump(mode="json") == expected
    assert snapshot.fingerprint == model_config_fingerprint(configuration)
    assert ModelInvocationParameters.model_validate_json(snapshot.model_dump_json()) == snapshot
    configuration.request_profile.revision += 1
    configuration.request_profile.output_formats.clear()
    assert snapshot.model_dump(mode="json") == expected


@pytest.mark.parametrize("change", ["missing_mapping", "missing_hash", "wrong_hash", "missing_profile",
    "profile_transport", "capabilities_field", "profile_field", "token_limits", "nan"])
def test_model_configuration_snapshot_rejects_changed_or_unknown_semantic_evidence(change):
    from pydantic import ValidationError
    from src.schemas.model_config import ModelInvocationParameters, model_invocation_parameters
    from src.schemas.model_request_options import ModelRequestProfile

    wire = model_invocation_parameters(model_configuration(request_profile=ModelRequestProfile(
        output_formats=["json_schema"], chat_token_parameter="max_completion_tokens")))
    if change == "missing_mapping":
        wire.pop("request_mapping_version")
    elif change == "missing_hash":
        wire.pop("request_profile_hash")
    elif change == "wrong_hash":
        wire["request_profile"]["revision"] += 1
    elif change == "missing_profile":
        wire.pop("request_profile")
    elif change == "profile_transport":
        wire["transport"] = "anthropic_messages"
    elif change == "capabilities_field":
        wire["capabilities"]["future_capability"] = True
    elif change == "profile_field":
        wire["request_profile"]["future_field"] = True
    elif change == "token_limits":
        wire["max_tokens"] += 1
    else:
        wire["temperature"] = float("nan")
    with pytest.raises(ValidationError):
        ModelInvocationParameters.model_validate(wire)


def test_historical_budget_wire_does_not_acquire_configuration_snapshots():
    budget = FrameworkModelBudget(max_calls=8, max_tokens=1000,
        deadline_at=datetime(2030, 1, 1, tzinfo=timezone.utc), model_config_fingerprints={"primary": "historical-fingerprint"})
    assert set(budget.model_dump()) == {"schema_version", "max_calls", "max_tokens", "deadline_at", "model_config_fingerprints"}
    assert FrameworkModelBudget.model_validate_json(budget.model_dump_json()) == budget
    assert budget.root_configuration("historical-fingerprint") is None


def test_snapshot_budget_selects_only_the_root_binding_fingerprint_without_new_wire_fields():
    from src.schemas.model_config import ModelInvocationParameters

    snapshots = {key: ModelInvocationParameters.from_configuration(model_configuration(key=key))
        for key in ("summary", "primary")}
    budget = FrameworkModelBudget(schema_version=2, max_calls=8, max_tokens=1000,
        deadline_at=datetime(2030, 1, 1, tzinfo=timezone.utc), model_configurations=snapshots,
        model_config_fingerprints={key: value.fingerprint for key, value in snapshots.items()})
    before = budget.model_dump_json()
    assert budget.root_configuration(snapshots["primary"].fingerprint) == snapshots["primary"]
    assert budget.model_dump_json() == before and "root_model_key" not in before
    with pytest.raises(ValueError, match="root model"):
        budget.root_configuration("absent-fingerprint")


@pytest.mark.parametrize("change", ["missing", "identity", "fingerprint", "legacy_version", "secret_field"])
def test_snapshot_budget_rejects_missing_or_changed_model_configuration_evidence(change):
    from pydantic import ValidationError
    from src.schemas.model_config import ModelInvocationParameters

    snapshot = ModelInvocationParameters.from_configuration(model_configuration())
    wire = dict(schema_version=2, max_calls=8, max_tokens=1000,
        deadline_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        model_config_fingerprints={"primary": snapshot.fingerprint},
        model_configurations={"primary": snapshot.model_dump(mode="json")})
    if change == "missing":
        wire.pop("model_configurations")
    elif change == "identity":
        wire["model_configurations"]["primary"]["key"] = "different-model"
    elif change == "fingerprint":
        wire["model_configurations"]["primary"]["temperature"] = .75
    elif change == "legacy_version":
        wire["schema_version"] = 1
    else:
        wire["model_configurations"]["primary"]["api_key"] = "synthetic-private-material"
    with pytest.raises(ValidationError):
        FrameworkModelBudget.model_validate(wire)


@pytest.mark.asyncio
async def test_snapshot_budget_is_committed_with_binding_and_cannot_be_replaced(admission):
    h = admission
    configuration, snapshot = snapshot_budget(h)
    task = await submit(h)
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
    saved = await h.calls.snapshot(h.binding)
    assert saved.policy == h.budget and saved.policy.model_configurations["primary"] == snapshot
    configuration.temperature = .75
    assert (await h.calls.snapshot(h.binding)).policy.model_configurations["primary"] == snapshot
    assert (await submit(h)).id == task.id
    with pytest.raises(AgentSubmissionConflict):
        await submit(h, h.budget.model_copy(update={"max_calls": 12}))
    assert await h.calls.snapshot(h.binding) == saved


@pytest.mark.asyncio
async def test_snapshot_budget_admission_failure_keeps_no_partial_configuration_or_owner(admission, monkeypatch):
    h = admission
    snapshot_budget(h)
    original = h.tasks._submit_message_in_transaction

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("injected snapshot admission failure")

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_submit_message_in_transaction", fail)
        with pytest.raises(ConnectionError, match="injected snapshot admission failure"):
            await submit(h)
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["fingerprint", "nested_copy", "missing_model", "new_field"])
async def test_snapshot_budget_model_copy_cannot_bypass_atomic_admission_validation(admission, change):
    from pydantic import ValidationError

    h = admission
    _, snapshot = snapshot_budget(h)
    if change == "fingerprint":
        forged = h.budget.model_copy(update={"model_config_fingerprints": {"primary": "c" * 64}})
    elif change == "nested_copy":
        forged = h.budget.model_copy(update={"model_configurations": {
            "primary": snapshot.model_copy(update={"api_base_url_hash": "invalid"})}})
    elif change == "missing_model":
        forged = h.budget.model_copy(update={"model_configurations": {}})
    else:
        wire = snapshot.model_dump()
        wire["capabilities"] = {**wire["capabilities"], "future_capability": True}
        forged = h.budget.model_copy(update={"model_configurations": {"primary": wire}})
    if change == "new_field":
        with pytest.warns(UserWarning, match="Pydantic serializer warnings"), pytest.raises(ValidationError):
            await submit(h, forged)
    else:
        with pytest.raises(ValidationError):
            await submit(h, forged)
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)


@pytest.mark.asyncio
async def test_snapshot_budget_original_result_material_keeps_the_semantic_configuration(admission):
    from src.application.models.framework_model_budget_projection import capture_model_budget_material, read_model_budget_material

    h = admission
    _, snapshot = snapshot_budget(h)
    await submit(h)
    original = await h.calls.snapshot(h.binding)
    material = capture_model_budget_material(h.binding, original)
    restored = read_model_budget_material(h.binding, material)
    assert restored == original and restored.policy.model_configurations["primary"] == snapshot
    assert "synthetic-private-material" not in str(material)
    restored.policy.model_configurations.clear()
    assert (await h.calls.snapshot(h.binding)) == original


@pytest.mark.asyncio
async def test_initial_submission_commits_frozen_budget_before_any_worker_claim(admission):
    h = admission
    task = await submit(h)
    snapshot = await h.calls.snapshot(h.binding)
    assert snapshot.policy == h.budget and snapshot.reserved_calls == 0
    assert task.status == "queued" and task.attempt_no == 0
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
async def test_submission_retry_cannot_change_deadline_or_limits(admission):
    h = admission
    task = await submit(h)
    assert (await submit(h)).id == task.id
    before = await h.calls.snapshot(h.binding)
    for change in ({"max_calls": 100}, {"max_tokens": 1_000_000},
        {"deadline_at": h.budget.deadline_at + timedelta(hours=1)}):
        with pytest.raises(AgentSubmissionConflict):
            await submit(h, h.budget.model_copy(update=change))
    assert await h.calls.snapshot(h.binding) == before
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["budget", "outbox"])
async def test_admission_failure_rolls_back_budget_binding_input_and_outbox(admission, monkeypatch, window):
    h = admission
    if window == "budget":
        original = PostgresFrameworkModelCalls.freeze_submission

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError("injected budget admission failure")

        target, name = PostgresFrameworkModelCalls, "freeze_submission"
    else:
        original = h.tasks._submit_message_in_transaction

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError("injected outbox admission failure")

        target, name = h.tasks, "_submit_message_in_transaction"
    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises(ConnectionError, match="injected"):
            await submit(h)
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
    await submit(h)
    assert (await h.calls.snapshot(h.binding)).policy == h.budget


@pytest.mark.asyncio
async def test_concurrent_conflicting_admissions_freeze_only_winning_budget(admission):
    h = admission
    changed = h.budget.model_copy(update={"max_calls": 12})
    results = await asyncio.gather(submit(h), submit(h, changed), return_exceptions=True)
    assert sum(isinstance(item, AgentSubmissionConflict) for item in results) == 1
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    assert (await h.calls.snapshot(h.binding)).policy in (h.budget, changed)
    assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)


@pytest.mark.asyncio
async def test_budget_for_a_different_root_model_is_rejected_atomically(admission):
    h = admission
    wrong = h.budget.model_copy(update={"model_config_fingerprints": {"other": "wrong-fingerprint"}})
    with pytest.raises(AgentSubmissionConflict, match="bound root model"):
        await submit(h, wrong)
    assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)


@pytest.mark.asyncio
async def test_started_historical_turn_cannot_receive_fresh_budget_from_admission(admission):
    h = admission
    task = await h.tasks.submit_v2(h.command, h.payload, h.message, idempotency_key=h.key)
    await h.tasks.claim(task.id, worker_id="historical", expected_binding=h.binding)
    with pytest.raises(ValueError, match="budget"):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            h.calls.freeze_submission(cur, h.binding, h.budget)
    assert counts(h)["budgets"] == 0


@pytest.mark.asyncio
async def test_worker_defers_budgetless_graph_task_without_claim_or_execution(admission):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    h = admission
    task = await h.tasks.submit_v2(h.command, h.payload, h.message, idempotency_key=h.key)
    dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_model_calls=h.calls, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    with pytest.raises(TaskDeferred, match="model_budget_unavailable"):
        await dispatcher.handle_v2(QueuedTask("budgetless", h.command.model_dump(mode="json")), worker_id="guarded")
    saved = await h.tasks.get(task.id)
    assert saved.status == "queued" and saved.attempt_no == 0 and h.runtime.calls == []


@pytest.mark.asyncio
async def test_worker_reads_same_admitted_budget_without_refreezing(admission):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    h = admission
    task = await submit(h)
    dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
        framework_model_calls=h.calls, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    observed = []

    async def execute(selected):
        observed.append((selected.id, await h.calls.frozen_policy(selected.runtime_binding)))

    dispatcher._execute = execute
    await dispatcher.handle_v2(QueuedTask("admitted", h.command.model_dump(mode="json")), worker_id="guarded")
    assert observed == [(task.id, h.budget)]
    assert (await h.calls.snapshot(h.binding)).policy == h.budget


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status", [("message", "completed"), ("framework_reconcile", "claimed")])
async def test_budget_gate_preserves_terminal_delivery_and_deterministic_reconciliation(operation, status):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_agent_v2_worker_contract import _Settings, _Tasks, binding, command, stored_task

    selected = binding()
    saved = stored_task(selected)
    saved.operation, saved.status = operation, status
    tasks = _Tasks(saved)
    executions, reconciliations = [], []

    class Calls:
        async def frozen_policy(self, _binding):
            pytest.fail("No model budget is needed to inspect terminal delivery or reconcile durable facts")

    async def execute(task):
        executions.append(task.id)

    async def reconcile(task_id):
        reconciliations.append(task_id)

    tasks.ensure_framework_reconciliation = reconcile
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None, framework_model_calls=Calls(),
        framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    dispatcher._execute = execute
    wire = command(selected).model_copy(update={"operation": operation}).model_dump(mode="json")
    await dispatcher.handle_v2(QueuedTask("old-record", wire), worker_id="compatibility")
    expected = [] if status == "completed" else [saved.id]
    assert executions == reconciliations == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_version", [1, 2])
async def test_admitted_worker_runs_official_model_under_budget_and_keeps_legacy_adapter(admission, budget_version):
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeResult
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.application.runtime.runtime_service import RuntimeService
    from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.redis_task_worker import RedisTaskWorker
    from tests.test_deep_agent_runtime_adapter import _runtime_request, _runtime_session
    from tests.test_framework_chat_models import _config, _provider

    h = admission
    h.settings.orchestration.agent_task_v2_stream = h.prefix + ":agent:v2"
    h.settings.deep_agents.checkpoint_schema = "admitted_model_" + uuid4().hex
    provider = DeepAgentCheckpointProvider(h.settings)
    legacy_requests = []

    class LegacyAdapter:
        async def execute(self, request):
            legacy_requests.append(request.turn_id)
            return DeepAgentRuntimeResult(output_text="legacy fixture", messages=[
                {"role": "assistant", "content": "legacy fixture"}])

    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)

            class Registry:
                def get_runtime_config(self, key):
                    assert key == config.key
                    return config

            models = ModelRuntimeService(Registry(), h.settings)
            owned = DeepAgentRuntimeAdapter(model_resolver=models.resolve_langchain_chat_model,
                checkpointer_factory=provider.open, model_call_policy=FrameworkModelCallPolicy(
                    calls=h.calls, model_config_resolver=models.get_model_config))
            runtime = RuntimeService(graph=None, model_runtime_service=models, tool_runtime_service=object(),
                tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(), deep_agent_enabled=True,
                deep_agent_runtime_adapter=LegacyAdapter(), deep_agent_bound_runtime_adapter=owned)
            h.sessions._runtime_service = runtime
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {
                config.key: h.binding.model_config_fingerprint}})
            if budget_version == 2:
                from src.schemas.model_config import ModelInvocationParameters

                h.budget = FrameworkModelBudget(**{**h.budget.model_dump(), "schema_version": 2,
                    "model_configurations": {config.key: ModelInvocationParameters.from_configuration(config)}})
            task = await submit(h)
            dispatcher = AgentTaskDispatcher(h.settings, h.tasks, h.sessions, h.resources,
                framework_model_calls=h.calls, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
            queue = RedisTaskQueue(h.settings.database.redis_url,
                stream=h.settings.orchestration.agent_task_v2_stream, group="admission-test", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            errors = []

            async def handle(item):
                try:
                    await dispatcher.handle_v2(item, worker_id="admitted-sdk-worker")
                except Exception as exc:
                    errors.append(exc)
                    raise

            worker = RedisTaskWorker(queue, consumer="admitted-sdk-worker", handler=handle, block_ms=0)
            assert await worker.run_once() == 1
            if errors:
                raise errors[0]
            assert (await h.tasks.get(task.id)).status == "completed"
            assert len(requests) == 1 and legacy_requests == []
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.policy == h.budget and snapshot.actual_tokens == 10
            assert snapshot.reserved_calls == 1 and snapshot.calls[0].phase == "completed"
            await dispatcher.handle_v2(QueuedTask("duplicate", h.command.model_dump(mode="json")), worker_id="duplicate")
            legacy_request = _runtime_request(h.session_id, "legacy-turn").model_copy(update={"model_key": config.key})
            result = await runtime.execute_turn(_runtime_session(h.session_id), legacy_request)
            assert result.output_text == "legacy fixture" and legacy_requests == ["legacy-turn"]
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot
    finally:
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_per_call_approval_owners_continue_same_admitted_root_budget(projection_harness):
    import json
    from types import SimpleNamespace

    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.runtime.execution_context import framework_owner_scope
    from src.schemas.session import ExecutionRequest, SessionStatus
    from tests.test_framework_approval_runtime import build_runtime
    from tests.test_framework_approval_continuation import claim_decision
    from tests.test_framework_chat_models import _config, _provider, _tool_reply

    h = projection_harness
    h.calls = PostgresFrameworkModelCalls(h.settings)
    await h.calls.initialize()
    try:
        await h.tasks.finish(h.owner, status="completed", result_status="completed")
        session = await h.store.get_session(h.session_id)
        session.status = SessionStatus.idle
        await h.store.save_session(session)
        reply = _tool_reply("openai_chat_completions")
        reply["choices"][0]["message"]["tool_calls"] = [
            {"id": key, "type": "function", "function": {"name": "inspect_value",
                "arguments": json.dumps({"value": key})}} for key in ("A", "B")]
        with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
            config = _config("openai_chat_completions", url)

            class Registry:
                def get_runtime_config(self, key):
                    assert key == config.key
                    return config

            models = ModelRuntimeService(Registry(), h.settings)
            h.binding = make_binding(h.session_id, "approved-model-" + uuid4().hex,
                model_config_fingerprint=model_config_fingerprint(config))
            budget = FrameworkModelBudget(max_calls=2, max_tokens=100_000,
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                model_config_fingerprints={config.key: h.binding.model_config_fingerprint})
            submitted = await h.tasks.submit_v2(make_command(h, h.binding), make_payload(), make_message(h, h.binding),
                idempotency_key=uuid4().hex, model_budget=budget)
            h.owner = await h.tasks.claim(submitted.id, worker_id="initial-sdk", expected_binding=h.binding)
            await h.tasks.begin(h.owner, resource_id="initial-sdk", fencing_token=3)
            h.effects = []
            build_runtime(h)
            h.runtime._model_runtime_service = models
            h.runtime._deep_agent_bound_runtime_adapter = DeepAgentRuntimeAdapter(
                model_resolver=models.resolve_langchain_chat_model, checkpointer_factory=h.provider.open,
                model_call_policy=FrameworkModelCallPolicy(calls=h.calls, model_config_resolver=models.get_model_config))
            request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
                trace_id="approved-sdk", user_message="Inspect A/B", normalized_input="Inspect A/B",
                mode_key="default", agent_key="coordinator", model_key=config.key,
                runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
            with framework_owner_scope(h.owner.runtime_owner):
                first = await h.runtime.execute_turn(await h.store.get_session(h.session_id), request)
            assert len(first.approvals) == 2 and len(requests) == 1
            h.proposals = {item["metadata"]["call_id"]: SimpleNamespace(approval=SimpleNamespace(id=item["id"]))
                for item in first.approvals}

            def persist(metadata, status):
                metadata["pending_turn"] = first.pending_turn
                metadata["control"] = {"control_state": "waiting_approval"}

            await h.store.update_status_and_mutate_metadata(h.session_id, status=None, mutate=persist,
                turn_lease_token=h.owner.lease_token)
            await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
            h.sessions._runtime_service = h.runtime
            monitor = asyncio.create_task(asyncio.Event().wait())
            try:
                b = await claim_decision(h)
                partial = await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
                assert partial.session.status.value == "waiting_approval" and len(requests) == 1
                assert [value for value, _ in h.effects] == ["B"]
                assert (await h.calls.snapshot(h.binding)).reserved_calls == 1
                await h.tasks.finish(b, status="completed", result_status="waiting_approval")
                a = await claim_decision(h, key="A", decision="denied")
                final = await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            snapshot = await h.calls.snapshot(h.binding)
            assert final.output.content == "fixture answer" and len(requests) == 2
            assert [value for value, _ in h.effects] == ["B"]
            assert snapshot.policy == budget and snapshot.reserved_calls == 2 and snapshot.actual_tokens == 20
            assert len({call.owner_epoch for call in snapshot.calls}) == 2
            assert all(call.phase == "completed" for call in snapshot.calls)
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {h.calls.uploads_table}")
            cur.execute(f"DROP TABLE {h.calls.calls_table}")
            cur.execute(f"DROP TABLE {h.calls.budgets_table}")
