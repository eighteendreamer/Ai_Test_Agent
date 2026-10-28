"""Reuse real original outputs at the missing event-declaration kill windows."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_event_completion import completion_event_id
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from tests.test_framework_message_result_reconstruction import message_asset_gap, read_original as read_message
from tests.test_framework_approval_result_reconstruction import approval_asset_gap
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_framework_approval_graph_input import prepared_approval_runtime
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_model_admission import admission
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness


def remove_fixture_seal(h, *, terminal_events):
    # Only fixture-owned rows, with the same original inputs/oracle as the real
    # after_graph_return/after_graph_events hard-kill samples. Do not manufacture
    # replacement callback rows or change the original native checkpoint.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        ids = [completion_event_id(h.binding)]
        if not terminal_events:
            ids.extend(item.id for item in h.oracle.events if item.type in {"graph.plan_built", "runtime.turn_completed"})
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE session_id=%s AND id=ANY(%s)",
            (h.session_id, ids))


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["none", "tools", "planning", "api-tester"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_message_reuses_complete_original_projection_without_writes(message_asset_gap, terminal_events):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    events = await h.store.list_events(h.session_id)
    budget = await h.calls.snapshot(h.binding)
    stage = h.stages.get(h.initial_task_id, h.session_id)
    effects, requests = deepcopy(h.effects), list(h.adapter_requests)
    result = await read_message(h)
    assert result.completion is None, "Read-only derivation must not fabricate an original completion declaration"
    assert result.checkpoint_event_prefix is not None
    # These fixtures deliberately run without a model-call policy: retain the
    # historical v1 prefix/absent budget, never invent an empty budget object.
    assert not budget.calls and result.checkpoint_event_prefix.schema_version == 1
    assert result.checkpoint_event_prefix.root_model_budget_material is None
    assert "root_model_budget_material" not in result.checkpoint_event_prefix.model_dump(mode="json")
    assert result.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert result.verification_results == h.verification_oracle
    assert await read_message(h) == result
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert h.effects == effects and h.adapter_requests == requests
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "future", "legacy_budget", "prefix", "terminal", "extra", "budget", "producer"])
async def test_unsealed_message_rejects_untrusted_or_changed_originals_without_repair(message_asset_gap, monkeypatch, changed):
    from src.runtime import framework_approval_result_reader as reader

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=True)
    collect = reader.collect_native_terminal

    async def read_then_change(*args, **kwargs):
        native = await collect(*args, **kwargs)
        if changed in {"missing", "future", "legacy_budget"}:
            prefix = deepcopy(native.checkpoint_event_prefix)
            if changed == "missing":
                prefix = None
            else:
                prefix["schema_version"] = 99 if changed == "future" else 1
                if changed == "legacy_budget":
                    # v1 may be read, but cannot smuggle budget fields even
                    # when its caller recomputes the outer self-digest.
                    prefix["root_model_budget_material"] = {"legacy": True}
                prefix["prefix_sha256"] = approval_value_hash({key: value for key, value in prefix.items()
                    if key != "prefix_sha256"})
            return replace(native, checkpoint_event_prefix=prefix)
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            table = h.settings.database.postgres_event_table
            if changed in {"prefix", "terminal"}:
                event_id = h.oracle.events[0 if changed == "prefix" else -1].id
                cur.execute(f"UPDATE {table} SET payload=payload || %s::jsonb WHERE id=%s",
                    ('{"output_length":999}', event_id))
            elif changed == "extra":
                cur.execute(f"INSERT INTO {table}(id,session_id,type,timestamp,payload) "
                    f"SELECT %s,session_id,type,timestamp,payload FROM {table} WHERE id=%s",
                    (str(uuid4()), h.oracle.events[-1].id))
            elif changed == "budget":
                cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy, '{{max_calls}}', '99') WHERE binding_id=%s",
                    (h.binding.binding_id,))
            elif changed == "producer":
                cur.execute(f"UPDATE {h.settings.database.postgres_agent_attempt_table} SET worker_id='foreign-reader' WHERE id=%s",
                    (h.owner.attempt_id,))
        return native

    monkeypatch.setattr(reader, "collect_native_terminal", read_then_change)
    with pytest.raises((ValueError, FrameworkMessageStageRecoveryRequired)):
        await read_message(h)
    events = await h.store.list_events(h.session_id)
    assert not any(item.id == completion_event_id(h.binding) for item in events)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    assert await h.tasks.get(h.owner.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["planning"], indirect=True)
async def test_unsealed_result_refuses_completed_row_with_missing_original_plan(message_asset_gap):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=True)
    plan, = [item for item in h.oracle.events if item.type == "graph.plan_built"]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (plan.id,))
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="conflicting or unbound"):
        await read_message(h)
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_approval_uses_same_original_projection_without_sealing(approval_asset_gap, terminal_events):
    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    events = await h.store.list_events(h.session_id)
    stage = h.stages.get(h.initial_task_id, h.session_id)
    budget = await h.calls.snapshot(h.binding)
    effects, requests = deepcopy(h.effects), list(h.adapter_requests)
    result = await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks)
    assert result.completion is None and result.checkpoint_event_prefix is not None
    assert result.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert result.verification_results == h.verification_oracle
    assert await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks) == result
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert h.effects == effects and h.adapter_requests == requests
