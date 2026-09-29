from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest

from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.smoke_testing_mode.catalog_store import SmokePlanVersionConflictError
from src.modes.smoke_testing_mode.contracts import SmokeExecutionPlan
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _drop_tables

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not LivePostgresTestConfig().run_live_postgres_tests,
        reason="set RUN_LIVE_POSTGRES_TESTS=1 to use the local PostgreSQL instance",
    ),
]


class _StoredArtifacts:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def store_uploaded_bytes(self, *, content: bytes, filename: str, object_prefix: str, **kwargs):
        key = f"{object_prefix}/{filename}"
        self.objects[key] = content
        return {"uri": f"rustfs://test/{key}"}


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="smoke-version-session",
        turn_id="smoke-version-turn",
        trace_id="smoke-version-trace",
        user_message="Review the smoke test plan",
        normalized_input="Review the smoke test plan",
        context_bundle={},
    )


async def _invoke(runtime: ToolRuntimeService, arguments: dict):
    tool = ToolRegistry().get("smoke-suite-runner")
    return await runtime.execute(
        tool,
        ModelToolCall(id=uuid4().hex, name=tool.key, arguments=arguments),
        _context(),
    )


async def test_stale_smoke_revision_cannot_replace_an_existing_version(monkeypatch):
    suffix = uuid4().hex[:10]
    settings = Settings()
    runtime = ToolRuntimeService(settings=settings)
    smoke = runtime._smoke_testing_mode_runtime
    catalog = smoke._plan_store._catalog_store
    catalog.plan_table = f"live_smoke_plan_{suffix}"
    catalog.version_table = f"live_smoke_version_{suffix}"
    catalog.run_table = f"live_smoke_run_{suffix}"
    catalog.regression_table = f"live_smoke_regression_{suffix}"
    catalog.binding_table = f"live_smoke_binding_{suffix}"
    tables = [catalog.binding_table, catalog.regression_table, catalog.run_table, catalog.version_table, catalog.plan_table]
    artifacts = _StoredArtifacts()
    smoke._plan_store._artifact_storage_service = artifacts

    async def resolve_project(**kwargs):
        return "reviewed-project", [], []

    async def resolve_sources(**kwargs):
        return {"warnings": [], "sources": [], "project_cases": [], "api_documents": []}

    monkeypatch.setattr(smoke._project_resolver, "resolve", resolve_project)
    monkeypatch.setattr(smoke._source_resolver, "resolve", resolve_sources)
    try:
        draft = await _invoke(runtime, {"action": "draft_plan", "target_url": "http://127.0.0.1:8801"})
        assert draft.output["phase"] == "awaiting_user_confirmation"
        assert draft.output["storage_warnings"] == []
        stale_plan = deepcopy(draft.output["plan"])
        plan_id = draft.output["plan_id"]

        first = await _invoke(runtime, {
            "action": "revise_plan",
            "plan_id": plan_id,
            "plan_version": 1,
            "user_revision": "First review: keep the health check",
        })
        assert first.output["plan_version"] == 2
        assert first.output["storage_warnings"] == []
        version_two_uri = first.output["plan"]["rustfs_uris"]["plan.v2.json"]
        version_two_key = version_two_uri.removeprefix("rustfs://test/")
        first_snapshot = artifacts.objects[version_two_key]

        second = await _invoke(runtime, {
            "action": "revise_plan",
            "plan_id": plan_id,
            "plan_version": 1,
            "plan": deepcopy(stale_plan),
            "user_revision": "Second stale review: replace the first revision",
        })

        assert second.output["status"] == "failed"
        assert second.output["error"] == "inline_plan_not_allowed"
        stale_by_id = await _invoke(runtime, {
            "action": "revise_plan", "plan_id": plan_id, "plan_version": 1,
            "user_revision": "Third stale review",
        })
        assert stale_by_id.output["error"] == "plan_version_conflict"
        with pytest.raises(SmokePlanVersionConflictError):
            await catalog.save_plan_version(
                SmokeExecutionPlan.model_validate(first.output["plan"]),
                plan_uri="rustfs://test/attempted-replacement.json",
                plan_md_uri="rustfs://test/attempted-replacement.md",
                user_revision="Direct stale replacement",
            )
        with pytest.raises(SmokePlanVersionConflictError):
            await catalog.save_plan(SmokeExecutionPlan.model_validate(stale_plan))
        assert artifacts.objects[version_two_key] == first_snapshot
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT current_version, metadata FROM {catalog.plan_table} WHERE plan_id = %s", (plan_id,))
            plan_row = cur.fetchone()
            cur.execute(f"SELECT user_revision FROM {catalog.version_table} WHERE plan_id = %s AND version = 2", (plan_id,))
            saved_revision = cur.fetchone()["user_revision"]
        assert plan_row["current_version"] == 2
        assert saved_revision == "First review: keep the health check"
        assert plan_row["metadata"]["revisions"][-1]["user_revision"] == saved_revision

        frozen = SmokeExecutionPlan.model_validate(first.output["plan"])
        approved_one, warnings = await smoke._plan_store.save_approved_plan(frozen.model_copy(deep=True))
        assert warnings == []
        first_approval_uri = approved_one.rustfs_uris["approved_plan_uri"]
        first_approval_key = first_approval_uri.removeprefix("rustfs://test/")
        first_approval_bytes = artifacts.objects[first_approval_key]
        approved_two, warnings = await smoke._plan_store.save_approved_plan(frozen.model_copy(deep=True))
        assert warnings == []
        assert approved_two.rustfs_uris["approved_plan_uri"] != first_approval_uri
        assert artifacts.objects[first_approval_key] == first_approval_bytes
        changed = frozen.model_copy(deep=True)
        changed.title = "Changed after freezing the same version"
        with pytest.raises(SmokePlanVersionConflictError):
            await smoke._plan_store.save_approved_plan(changed)
        assert artifacts.objects[first_approval_key] == first_approval_bytes

        revised_after_approval = await _invoke(runtime, {
            "action": "revise_plan", "plan_id": plan_id, "plan_version": 2,
            "user_revision": "Review a new version after the approved run",
        })
        assert revised_after_approval.output["plan_version"] == 3
        assert revised_after_approval.output["approved_plan_uri"] == ""

        parallel_draft = await _invoke(runtime, {"action": "draft_plan", "target_url": "http://127.0.0.1:8802"})
        base = SmokeExecutionPlan.model_validate(parallel_draft.output["plan"])
        left = base.model_copy(deep=True)
        left.version = 2
        left.title = "Concurrent revision A"
        right = base.model_copy(deep=True)
        right.version = 2
        right.title = "Concurrent revision B"
        outcomes = await asyncio.gather(
            smoke._plan_store.save_plan_version(left, user_revision="A"),
            smoke._plan_store.save_plan_version(right, user_revision="B"),
            return_exceptions=True,
        )
        assert sum(isinstance(outcome, SmokePlanVersionConflictError) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, tuple) for outcome in outcomes) == 1
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT current_version, metadata FROM {catalog.plan_table} WHERE plan_id = %s",
                (base.plan_id,),
            )
            parallel_row = cur.fetchone()
            cur.execute(
                f"SELECT plan_uri FROM {catalog.version_table} WHERE plan_id = %s AND version = 2",
                (base.plan_id,),
            )
            parallel_uri = cur.fetchone()["plan_uri"]
        assert parallel_row["current_version"] == 2
        assert parallel_row["metadata"]["title"] in {"Concurrent revision A", "Concurrent revision B"}
        assert json.loads(artifacts.objects[parallel_uri.removeprefix("rustfs://test/")])["title"] == parallel_row["metadata"]["title"]
    finally:
        _drop_tables(settings, tables)
