from __future__ import annotations

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.application.context.memory_runtime_service import MemoryRuntimeService
from tests.test_agent_task_dispatch import harness
from tests.test_framework_application_projection import projection
from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import run, selected, stage
from tests.test_framework_graph_input_asset import expected_graph_stage_version


@pytest_asyncio.fixture
async def turn_memory_projection(projection):
    h = projection
    h.settings.deep_agents.checkpoint_schema = h.prefix + "_checkpoints"
    h.settings.database.postgres_memory_table = h.prefix + "_turn_memory"
    memory = PostgresVectorMemoryStore(h.settings)
    await memory.initialize()
    h.sessions._memory_runtime_service = MemoryRuntimeService(memory)
    try:
        yield h
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {h.settings.database.postgres_memory_table}")


@pytest.mark.asyncio
async def test_framework_turn_memory_plan_has_per_request_execution_receipts(turn_memory_projection):
    h = turn_memory_projection
    original = await selected(h)
    response = await run(h, original)
    saved = await stage(h, original)

    from src.schemas.framework_projection_commit import (
        FrameworkTurnMemoryExecution,
        FrameworkTurnMemoryPlan,
        TURN_MEMORY_EXECUTION_KEY,
    )
    from src.runtime.framework_turn_memory_projection import MEMORY_COLUMNS, _row_hash

    plan = FrameworkTurnMemoryPlan.model_validate(saved.source_manifest["post_commit_memory_plan"])
    execution = FrameworkTurnMemoryExecution.model_validate(saved.source_manifest[TURN_MEMORY_EXECUTION_KEY])
    assert response.session.status.value == "completed"
    assert saved.schema_version == expected_graph_stage_version(saved, minimum=27)
    assert execution.binding_fingerprint == plan.binding_fingerprint
    assert execution.plan_asset_sha256 == plan.asset_sha256
    assert execution.runtime_result_asset_sha256 == plan.runtime_result_asset_sha256
    assert execution.source_snapshot_id == plan.source_snapshot_id
    assert execution.state == "completed"
    receipts = {item.request_id: item for item in execution.row_receipts}
    assert len(receipts) == len(execution.row_receipts) == len(plan.requests)
    assert set(receipts) == {item.request_id for item in plan.requests}
    assert {item.status for item in execution.row_receipts} == {"committed"}

    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {MEMORY_COLUMNS} FROM {h.settings.database.postgres_memory_table} "
            "WHERE session_id=%s ORDER BY id",
            (h.session_id,),
        )
        rows = {str(row["id"]): dict(row) for row in cur.fetchall()}
    assert set(rows) == {item.request_id for item in plan.requests}
    for item in plan.requests:
        receipt = receipts[item.request_id]
        assert receipt.ordinal == item.ordinal
        assert receipt.memory_id == item.request_id
        assert receipt.row_sha256 == _row_hash(rows[receipt.memory_id])
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "memory.persisted"]) == 1
