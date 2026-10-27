"""Regression for parallel tool results whose task IDs invert task-path order."""
from __future__ import annotations

import pytest
from langgraph.channels import DeltaChannel
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import StateGraph, START, END

from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import (
    checkpoint_harness, config, start, DeltaState, append_batches,
)


def test_pinned_read_shape_sorts_paths_then_task_ids_then_numeric_indexes():
    from src.application.deep_agents.path_ordered_postgres_saver import PathOrderedPostgresSaver

    saver = PathOrderedPostgresSaver(None)
    rows = []
    expected = ["input", "index-2", "index-10", "later-task", "later-path"]
    for task_id, path, index, value in [
        ("a", "z", 0, "later-path"), ("z", "", 0, "input"),
        ("a", "a", 10, "index-10"), ("z", "a", 0, "later-task"),
        ("a", "a", 2, "index-2"),
    ]:
        kind, content = saver.serde.dumps_typed(value)
        rows.append((task_id.encode(), b"events", kind.encode(), content, path.encode(), str(index).encode()))
    assert [write[2] for write in saver._load_writes(rows)] == expected
    assert "cw.task_path::bytea, cw.idx::text::bytea" in saver.SELECT_SQL
    assert saver._load_writes([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("namespace", ["", "nested:delta"])
async def test_delta_history_preserves_task_paths_not_hashed_ids(checkpoint_harness, bound, namespace):
    h = checkpoint_harness
    await start(h)
    selected = config(h, namespace if bound else h.binding.checkpoint_namespace + namespace)
    owner = h.owner.runtime_owner if bound else None
    async with h.provider.open(owner=owner) as saver:
        root = empty_checkpoint()
        root["channel_values"] = {"events": []}
        root["channel_versions"] = {"events": 1}
        root_config = await saver.aput(selected, root, {"source": "loop", "step": 0}, {"events": 1})
        # Live apply_writes sorts by path. UUID/hash order is deliberately inverse.
        await saver.aput_writes(root_config, [("events", ["second"])], "a-task", "~__pregel_push, 1")
        await saver.aput_writes(root_config, [("events", ["first"])], "z-task", "~__pregel_push, 0")
        terminal = empty_checkpoint()
        terminal["channel_versions"] = {"events": 2}
        terminal_config = await saver.aput(root_config, terminal, {"source": "loop", "step": 1}, {"events": 2})
        # Writes at the target are pending, not part of its materialized value.
        await saver.aput_writes(terminal_config, [("events", ["not-applied"])], "target-task", "")
        history = await saver.aget_delta_channel_history(config=terminal_config, channels=["events"])
        assert [write[2] for write in history["events"]["writes"]] == [["first"], ["second"]]
        assert [write[0] for write in (await saver.aget_tuple(root_config)).pending_writes] == ["z-task", "a-task"]
        listed = [item async for item in saver.alist(selected)]
        persisted_root = next(item for item in listed if item.config == root_config)
        assert [write[0] for write in persisted_root.pending_writes] == ["z-task", "a-task"]

    await h.provider.close()
    async with h.provider.open(owner=owner) as saver:
        repeated = await saver.aget_delta_channel_history(config=terminal_config, channels=["events"])
        assert repeated == history
        # Materialization remains the official DeltaChannel reducer, not app replay.
        channel = DeltaChannel(append_batches, list).from_checkpoint(history["events"]["seed"])
        channel.replay_writes(history["events"]["writes"])
        assert channel.get() == ["first", "second"]


@pytest.mark.asyncio
async def test_parallel_graph_return_equals_reloaded_state_and_continuation(checkpoint_harness):
    h = checkpoint_harness
    await start(h)
    builder = StateGraph(DeltaState)
    builder.add_node("a-first", lambda state: {"events": ["first"]})
    builder.add_node("z-second", lambda state: {"events": ["second"]})
    for name in ("a-first", "z-second"):
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        graph = builder.compile(checkpointer=saver)
        returned = await graph.ainvoke({"events": ["input"]}, config(h), durability="sync")
        assert returned["events"] == ["input", "first", "second"]
        assert (await graph.aget_state(config(h))).values == returned
    await h.provider.close()
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        graph = builder.compile(checkpointer=saver)
        assert (await graph.aget_state(config(h))).values == returned
        continued = await graph.ainvoke({"events": ["next"]}, config(h), durability="sync")
        assert continued["events"] == returned["events"] + ["next", "first", "second"]
        assert (await graph.aget_state(config(h))).values == continued


@pytest.mark.asyncio
async def test_delta_walk_keeps_snapshot_seed_and_excludes_sibling_branch(checkpoint_harness):
    from langgraph.checkpoint.serde.types import _DeltaSnapshot

    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        root = empty_checkpoint()
        root["channel_values"] = {"events": _DeltaSnapshot(["snapshot"])}
        root["channel_versions"] = {"events": 1}
        root_config = await saver.aput(config(h), root, {"source": "loop", "step": 0}, {"events": 1})
        await saver.aput_writes(root_config, [("events", ["on-path"])], "root-task", "root")
        branch = empty_checkpoint()
        branch["channel_versions"] = {"events": 2}
        branch_config = await saver.aput(root_config, branch, {"source": "loop", "step": 1}, {"events": 2})
        await saver.aput_writes(branch_config, [("events", ["sibling-only"])], "sibling-task", "sibling")
        target = empty_checkpoint()
        target["channel_versions"] = {"events": 3}
        target_config = await saver.aput(root_config, target, {"source": "loop", "step": 1}, {"events": 3})
        history = await saver.aget_delta_channel_history(config=target_config, channels=["events"])
        assert history["events"]["seed"] == _DeltaSnapshot(["snapshot"])
        assert [write[2] for write in history["events"]["writes"]] == [["on-path"]]
        channel = DeltaChannel(append_batches, list).from_checkpoint(history["events"]["seed"])
        channel.replay_writes(history["events"]["writes"])
        assert channel.get() == ["snapshot", "on-path"]
