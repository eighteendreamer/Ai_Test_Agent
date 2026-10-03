from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from test_case_planning import SequenceModel, finish, item
from test_run_item_dependencies import setup, finish as complete
from src.application.test_runs.plan_mapping import build_plan_run
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_suites.suite_store import InMemoryTestSuiteStore, PostgresTestSuiteStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.application.test_runs.run_service import TestRunService as RunService
from src.schemas.run_management import RegressionRunCreateRequest
from src.application.test_runs.agent_regression_service import AgentRegressionService
from src.application.test_runs.agent_case_context import build_agent_case_context


LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_RUN_CREATION") != "1",
    reason="requires isolated real PostgreSQL/MySQL/RustFS"))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_agent_regression_keeps_strategy_and_rebuilds_only_needed_execution_chain(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, creator, plan, now, table):
        parent = await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="parent", mode_key="api_testing"))
        initial = await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now)
        for claim in initial:
            await complete(store, claim, now, {"case_output": {"body": {"order.id": "old-parent-id"}}})
        left = (await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now))[0]
        await complete(store, left, now, {"preparations": {"seed": {"status": "passed", "output": {"id": "old-shared-id"}}}})
        right = (await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now))[0]
        failed = await complete(store, right, now, {"case_output": {"verified": False}}, status="failed")
        original = await store.get_run(parent.run.id)
        runs = RunService(store=store, project_service=env.projects, suite_service=None, test_case_service=env.cases)
        runs.set_agent_regression_service(AgentRegressionService(plan_store=env.store,
            plan_run_service=creator, planning_service=env.service, run_store=store))
        regression = await runs.create_regression(parent.run.id, RegressionRunCreateRequest(result_ids=[failed.id]))
        assert regression.run.execution_strategy == "agent"
        assert regression.run.parent_run_id == parent.run.id
        by_key = {entry.plan_item_key: entry for entry in regression.items}
        assert set(by_key) == {"create", "right"}
        snapshot = regression.run.plan_snapshot
        assert snapshot.owners["seed"] == snapshot.owners["cleanup"] == "right"
        assert {entry.key for entry in snapshot.plan.decision.items} == {"create", "seed", "right", "cleanup"}
        assert by_key["right"].regression_source_result_id == failed.id
        assert by_key["right"].dependencies[0].item_id == by_key["create"].id
        assert "old-parent-id" not in snapshot.model_dump_json()
        assert "old-shared-id" not in snapshot.model_dump_json()
        assert await store.get_run(parent.run.id) == original
        claims = await store.claim_items(run_id=regression.run.id, worker_id="regression", limit=20, lease_seconds=60, now=now)
        assert [entry.plan_item_key for entry, _ in claims] == ["create"]
        await complete(store, claims[0], now, {"case_output": {"body": {"order.id": "new-run-id"}}})
        claims = await store.claim_items(run_id=regression.run.id, worker_id="regression", limit=20, lease_seconds=60, now=now)
        assert [entry.plan_item_key for entry, _ in claims] == ["right"]
        context = build_agent_case_context(await store.get_run(regression.run.id), claims[0][0],
            await env.cases.get_version(claims[0][0].case_version_id))
        assert context.steps[0].item.key == "seed"
        assert context.steps[0].inputs["id"].value == "new-run-id"
        result = await complete(store, claims[0], now, {"case_output": {"verified": True},
            "preparations": {"seed": {"status": "passed", "output": {"id": "new-seed-id"}},
                             "cleanup": {"status": "passed", "output": {}}}})
        assert result.regression_source_result_id == failed.id
        assert (await store.get_run(regression.run.id)).run.status == "completed"
        assert not await store.claim_items(run_id=regression.run.id, worker_id="terminal", limit=20, lease_seconds=60, now=now)
        if table:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT payload FROM {table}")
                assert {row["payload"]["run_id"] for row in cur.fetchall()} == {parent.run.id, regression.run.id}


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_agent_regression_new_version_replans_and_keeps_other_selected_old_version(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, creator, plan, now, table):
        parent = await creator.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="version-parent", mode_key="api_testing"))
        initial = await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now)
        for claim in initial:
            await complete(store, claim, now, {"case_output": {"body": {"order.id": "old-parent-id"}}})
        failures = []
        for _ in range(2):
            claim = (await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now))[0]
            failures.append(await complete(store, claim, now,
                {"preparations": {"seed": {"status": "passed", "output": {"id": "old-seed-id"}}}}, status="failed"))
        selected = {entry.key: entry for entry in plan.decision.items if entry.key in {"left", "right"}}
        new_versions = {}
        for key, node in selected.items():
            new_versions[key] = await env.cases.create_version(node.case_id,
                VersionRequest(content_json={"objective": "新输出绑定", "steps": [{"action": "使用新的标识字段"}]},
                    source_refs=[{"source_type": "manual", "source_id": "version-change"}],
                    model_key="manual", prompt_version="manual", skill_versions={"manual": "v1"}))
            await env.cases.submit_review(node.case_id)
            await env.cases.activate(node.case_id)

        def changed(context):
            assert "回归目标" in context["validation_feedback"]["error"]
            assert context["constraints"]["required_case_versions"] == {
                selected["left"].case_id: selected["left"].case_version_id,
                selected["right"].case_id: new_versions["right"].id}
            refs = [e["id"] for e in context["evidence"] if e["kind"] == "case"]
            return finish({"key": "fresh", "kind": "prepare", "instructions": {"operation": "依据新版本创建本次数据"},
                "reason": "新版本需要新标识", "evidence_ids": refs},
                item(context, "item-1", key="left", depends_on=["fresh"],
                    input_bindings={"id": {"source_item": "fresh", "output_path_tokens": ["new.id"]}}),
                item(context, "item-2", key="revised", depends_on=["fresh"],
                    input_bindings={"id": {"source_item": "fresh", "output_path_tokens": ["new.id"]}}))
        model = SequenceModel(finish(), changed)
        env.service._models = model
        runs = RunService(store=store, project_service=env.projects, suite_service=None, test_case_service=env.cases)
        runs.set_agent_regression_service(AgentRegressionService(plan_store=env.store,
            plan_run_service=creator, planning_service=env.service, run_store=store))
        original = await store.get_run(parent.run.id)
        detail = await runs.create_regression(parent.run.id, RegressionRunCreateRequest(
            result_ids=[entry.id for entry in failures], version_overrides={selected["right"].case_id: new_versions["right"].id}))
        assert len(model.requests) == 2
        by_key = {entry.plan_item_key: entry for entry in detail.items}
        assert by_key["left"].case_version_id == selected["left"].case_version_id != new_versions["left"].id
        assert by_key["revised"].case_version_id == new_versions["right"].id
        binding = next(node for node in detail.run.plan_snapshot.plan.decision.items if node.key == "revised").input_bindings["id"]
        assert binding.path_tokens == ["new.id"]
        assert await store.get_run(parent.run.id) == original


def fixture_plan(context):
    refs = [e["id"] for e in context["evidence"] if e["kind"] == "case"]
    return finish(
        item(context, "item-0", key="create", kind="dependency"),
        {"key": "seed", "kind": "prepare", "instructions": {"operation": "按正式依据准备测试数据", "output": {"id": "实际ID"}},
         "reason": "为两个正式目标提供同一份数据", "evidence_ids": refs[:1], "depends_on": ["create"],
         "input_bindings": {"id": {"source_item": "create", "output_path_tokens": ["body", "order.id"]}}},
        item(context, "item-1", key="left", depends_on=["seed"],
             input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["id"]}}),
        item(context, "item-2", key="right", depends_on=["seed"],
             input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["id"]}}),
        item(context, "item-3", key="independent"),
        {"key": "cleanup", "kind": "prepare", "instructions": {"operation": "删除本次共享数据"},
         "reason": "两个目标均完成消费之后清理", "evidence_ids": refs[:1], "depends_on": ["left", "right"],
         "input_bindings": {"id": {"source_item": "seed", "output_path_tokens": ["id"]}}})


@asynccontextmanager
async def planned(tmp_path, live=False):
    async with setup(tmp_path, live) as (env, store, fixture_run, _, now):
        # Retrieve the formal project from the actual fixture cases.
        project = await env.projects.get(fixture_run.project_id)
        env.service._models = SequenceModel(fixture_plan)
        plan = await env.service.create(project.id, CasePlanningRequest(objective="验证两个目标及独立目标"))
        assert plan.status == "planned", plan.summary
        suite_store = PostgresTestSuiteStore(env.settings) if live else InMemoryTestSuiteStore()
        table = None
        if live:
            table = "up_pr_ob_" + uuid4().hex[:12]
            env.settings.database.postgres_task_outbox_table = table
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
            await PostgresTaskOutbox(env.settings).initialize()
        service = CasePlanRunService(run_store=store, suite_store=suite_store, plan_store=env.store,
            case_service=env.cases, project_service=env.projects,
            outbox_table=table, dispatch_stream="isolated:plan-runs" if table else None)
        try:
            yield env, store, suite_store, service, plan, now, table
        finally:
            if table:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f'DROP TABLE "{table}"')


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_frozen_plan_creates_only_formal_items_and_preserves_preparation_ownership(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        request = CasePlanRunRequest(creation_key="first", mode_key="api_testing")
        detail = await service.create(plan.project_id, plan.id, request)
        assert detail.run.execution_strategy == "agent"
        snapshot = detail.run.plan_snapshot
        assert snapshot.plan == plan
        assert "DO-NOT-INJECT-TEMPLATE" not in snapshot.model_dump_json()
        assert snapshot.owners["seed"] == "left" and snapshot.preparation_phases["seed"] == "before"
        assert snapshot.owners["cleanup"] == "right" and snapshot.preparation_phases["cleanup"] == "after"
        by_key = {i.plan_item_key: i for i in detail.items}
        assert set(by_key) == {"create", "left", "right", "independent"}
        assert len((await suites.get(detail.run.suite_id)).items) == 4
        assert by_key["left"].dependencies[0].required_output_paths == [["case_output", "body", "order.id"]]
        assert by_key["right"].dependencies[0].item_id == by_key["left"].id
        assert by_key["right"].dependencies[0].completion_condition == "terminal"
        assert by_key["right"].dependencies[0].required_preparation_keys == ["seed"]
        assert by_key["right"].dependencies[0].required_output_paths == [["preparations", "seed", "output", "id"]]
        claims = await store.claim_items(run_id=detail.run.id, worker_id="one", limit=20, lease_seconds=60, now=now)
        assert {i.plan_item_key for i, _ in claims} == {"create", "independent"}
        for claim in claims:
            await complete(store, claim, now, {"case_output": {"body": {"order.id": "owned-id"}}})
        claims = await store.claim_items(run_id=detail.run.id, worker_id="two", limit=20, lease_seconds=60, now=now)
        assert [i.plan_item_key for i, _ in claims] == ["left"]
        await complete(store, claims[0], now, {"case_output": {"verified": True},
            "preparations": {"seed": {"status": "passed", "output": {"id": "shared-id"}}}})
        claims = await store.claim_items(run_id=detail.run.id, worker_id="three", limit=20, lease_seconds=60, now=now)
        assert [i.plan_item_key for i, _ in claims] == ["right"]
        await complete(store, claims[0], now, {"case_output": {"verified": True}, "preparations": {"cleanup": {"output": {}}}})
        terminal = await service.create(plan.project_id, plan.id, request)
        assert terminal.run.status == "completed" and len(terminal.results) == 4
        assert terminal.run.plan_snapshot == snapshot
        assert await store.claim_items(run_id=detail.run.id, worker_id="retry", limit=5, lease_seconds=60, now=now) == []
        if table:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT payload FROM {table}")
                rows = cur.fetchall()
                assert len(rows) == 1 and rows[0]["payload"]["run_id"] == detail.run.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_concurrent_retries_create_one_suite_run_and_dispatch_intent(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        request = CasePlanRunRequest(creation_key="concurrent", mode_key="ui_automation")
        details = await asyncio.wait_for(asyncio.gather(*[
            service.create(plan.project_id, plan.id, request) for _ in range(8)]), timeout=20)
        assert len({d.run.id for d in details}) == 1
        assert all(d.run.plan_snapshot == details[0].run.plan_snapshot for d in details)
        with pytest.raises(ValueError, match="创建请求"):
            await service.create(plan.project_id, plan.id, request.model_copy(update={"mode_key": "api_testing"}))
        if table:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS n FROM {table}")
                assert cur.fetchone()["n"] == 1
                cur.execute(f"SELECT count(*) AS n FROM {env.settings.database.postgres_test_run_table}")
                assert cur.fetchone()["n"] == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_RUN_CREATION") != "1", reason="requires actual PostgreSQL rollback")
async def test_suite_run_items_and_outbox_rollback_together(tmp_path, monkeypatch):
    async with planned(tmp_path, True) as (env, store, suites, service, plan, now, table):
        request = CasePlanRunRequest(creation_key="rollback", mode_key="api_testing")
        bundle, candidate, _ = build_plan_run(plan, request, now=now)
        insert = store._insert_run_and_items
        def fail_after_suite(cur, run, items):
            insert(cur, run, items)
            raise RuntimeError("fixture error after actual suite/run/item inserts")
        monkeypatch.setattr(store, "_insert_run_and_items", fail_after_suite)
        with pytest.raises(RuntimeError, match="fixture error"):
            await service.create(plan.project_id, plan.id, request)
        assert await store.get_run(candidate.id) is None
        assert await suites.get(bundle.suite.id) is None
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
        monkeypatch.setattr(store, "_insert_run_and_items", insert)
        assert (await service.create(plan.project_id, plan.id, request)).run.id == candidate.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_new_active_version_never_replaces_frozen_plan_version(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        original = next(n for n in plan.decision.items if n.key == "left")
        created = await env.cases.create_version(original.case_id, VersionRequest(content_json={"objective": "改后的新行为"},
            source_refs=[{"source_type": "manual", "source_id": "new"}], model_key="manual", prompt_version="manual",
            skill_versions={"manual": "v1"}))
        await env.cases.submit_review(original.case_id)
        await env.cases.activate(original.case_id)
        detail = await service.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="frozen", mode_key="ui_automation"))
        frozen = next(i for i in detail.items if i.plan_item_key == "left")
        assert frozen.case_version_id == original.case_version_id
        assert frozen.case_version_id != created.id
        await env.cases.archive(original.case_id)
        with pytest.raises(ValueError, match="停用"):
            await service.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="archived", mode_key="api_testing"))
        replayed = await service.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="frozen", mode_key="ui_automation"))
        assert replayed.run.id == detail.run.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("preparation_status", ["passed", "failed"])
async def test_formal_failure_does_not_invalidate_a_successful_shared_preparation(tmp_path, live, preparation_status):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        detail = await service.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key="separate-results", mode_key="api_testing"))
        initial = await store.claim_items(run_id=detail.run.id, worker_id="initial", limit=20, lease_seconds=60, now=now)
        for claim in initial:
            await complete(store, claim, now, {"case_output": {"body": {"order.id": "actual-id"}}})
        first = await store.claim_items(run_id=detail.run.id, worker_id="consumer-a", limit=20, lease_seconds=60, now=now)
        assert [i.plan_item_key for i, _ in first] == ["left"]
        await complete(store, first[0], now, {"case_output": {"assertion": "failed"},
            "preparations": {"seed": {"status": preparation_status, "output": {"id": "actual-id"}}}}, status="failed")
        second = await store.claim_items(run_id=detail.run.id, worker_id="consumer-b", limit=20, lease_seconds=60, now=now)
        if preparation_status == "passed":
            assert [i.plan_item_key for i, _ in second] == ["right"]
            await complete(store, second[0], now)
        else:
            assert not second
            result = next(r for r in (await store.get_run(detail.run.id)).results if r.status == "blocked")
            assert any(f["reason"] == "preparation_unsuccessful" for f in result.actual["dependency_failures"])
        saved = await store.get_run(detail.run.id)
        assert saved.run.stats.failed == 1 and saved.run.status == "completed"
        assert next(i for i in saved.items if i.plan_item_key == "independent").status == "passed"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_RUN_CREATION") != "1", reason="requires actual transaction race checks")
async def test_archival_between_service_read_and_commit_is_rechecked_in_transaction(tmp_path, monkeypatch):
    async with planned(tmp_path, True) as (env, store, suites, service, plan, now, table):
        create = store.create_planned_run
        target = next(n for n in plan.decision.items if n.key == "left")
        async def archive_before_commit(*args, **kwargs):
            await env.cases.archive(target.case_id)
            return await create(*args, **kwargs)
        monkeypatch.setattr(store, "create_planned_run", archive_before_commit)
        request = CasePlanRunRequest(creation_key="race", mode_key="api_testing")
        bundle, run, _ = build_plan_run(plan, request, now=now)
        with pytest.raises(ValueError, match="停用"):
            await service.create(plan.project_id, plan.id, request)
        assert await store.get_run(run.id) is None and await suites.get(bundle.suite.id) is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_RUN_CREATION") != "1", reason="requires actual Outbox insertion failure")
async def test_outbox_write_failure_rolls_back_already_inserted_suite_and_run(tmp_path):
    async with planned(tmp_path, True) as (env, store, suites, service, plan, now, table):
        request = CasePlanRunRequest(creation_key="outbox-failure", mode_key="api_testing")
        bundle, run, _ = build_plan_run(plan, request, now=now)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT injected_failure CHECK (stream <> 'isolated:plan-runs')")
        from psycopg.errors import CheckViolation
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError) as failure:
            await service.create(plan.project_id, plan.id, request)
        assert isinstance(failure.value.orig, CheckViolation)
        assert await store.get_run(run.id) is None and await suites.get(bundle.suite.id) is None
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT injected_failure")
        assert (await service.create(plan.project_id, plan.id, request)).run.id == run.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_other_project_or_changed_mapping_cannot_create_a_run(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        from src.schemas.project import ProjectCreateRequest
        other = await env.projects.create(ProjectCreateRequest(project_key="other-run", name="其他项目"))
        request = CasePlanRunRequest(creation_key="scope", mode_key="api_testing")
        with pytest.raises(KeyError, match="固定计划不存在"):
            await service.create(other.id, plan.id, request)
        bundle, run, items = build_plan_run(plan, request, now=now)
        run.plan_snapshot.owners["seed"] = "right"
        with pytest.raises(ValueError, match="固定依据不一致"):
            await store.create_planned_run(bundle, run, items, suite_store=suites,
                outbox_table=table, event_key="not-published", stream="isolated:plan-runs" if table else None, payload={})
        assert await store.get_run(run.id) is None and await suites.get(bundle.suite.id) is None


def test_agent_plan_is_not_sent_to_the_legacy_mode_parameter_compiler():
    from test_case_execution_adapter import _fixture
    from src.application.test_runs.case_execution import CaseExecutionAdapter, CaseExecutionBlockedError
    case, version, run, item_record = _fixture()
    run.execution_strategy = "agent"
    def unexpected_resolver(key):
        raise AssertionError("new Agent strategy must not compile mode parameters")
    with pytest.raises(CaseExecutionBlockedError, match="Agent execution"):
        CaseExecutionAdapter(tool_resolver=unexpected_resolver).build_invocation(
            case=case, version=version, run=run, item=item_record)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_PLAN_RUN_CREATION") != "1", reason="requires actual committed records")
async def test_lost_response_after_commit_replays_the_same_run_without_dispatching_again(tmp_path, monkeypatch):
    async with planned(tmp_path, True) as (env, store, suites, service, plan, now, table):
        create = store.create_planned_run
        async def lose_committed_response(*args, **kwargs):
            await create(*args, **kwargs)
            raise RuntimeError("fixture response lost after committed transaction")
        monkeypatch.setattr(store, "create_planned_run", lose_committed_response)
        request = CasePlanRunRequest(creation_key="lost-response", mode_key="api_testing")
        with pytest.raises(RuntimeError, match="response lost"):
            await service.create(plan.project_id, plan.id, request)
        bundle, expected, _ = build_plan_run(plan, request, now=now)
        assert await store.get_run(expected.id) is not None and await suites.get(bundle.suite.id) is not None
        recovered = await service.create(plan.project_id, plan.id, request)
        assert recovered.run.id == expected.id
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 1
