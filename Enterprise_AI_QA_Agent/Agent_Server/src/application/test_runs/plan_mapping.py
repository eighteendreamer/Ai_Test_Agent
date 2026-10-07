"""Map an immutable project plan into the existing suite and RunItem contracts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from graphlib import CycleError, TopologicalSorter
from uuid import NAMESPACE_URL, uuid5

from src.application.test_runs.dependencies import validate_run_dependencies
from src.application.test_runs.preparation_scopes import automatic_cleanup_waits, frozen_preparation_scopes
from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord
from src.schemas.case_planning import CasePlanExecutionSnapshot, CasePlanRecord, CasePlanRunRequest, RegressionPlanSource, case_plan_json
from src.schemas.run_management import RunItemDependency, TestRunItemRecord, TestRunRecord, TestCaseResultRecord
from src.schemas.suite_management import TestSuiteBundle, TestSuiteItemRecord, TestSuiteRecord


def json_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def validate_frozen_cases(plan: CasePlanRecord, cases: dict[str, TestCaseRecord],
                          versions: dict[str, TestCaseVersionRecord]) -> None:
    evidence = {e.id: e for e in plan.evidence}
    for node in plan.decision.items:
        if node.kind == "prepare":
            continue
        case, version = cases.get(node.case_id), versions.get(node.case_version_id)
        frozen = evidence.get(f"case:{node.case_version_id}")
        if case is None or case.project_id != plan.project_id or case.lifecycle_status != "active":
            raise ValueError("计划正式用例不属于项目或已经停用")
        if version is None or version.case_id != case.id or frozen is None or frozen.source_id != case.id:
            raise ValueError("计划固定用例版本或依据不存在")
        if (version.content_hash != frozen.content_hash
            or version.business_content() != frozen.payload.get("content")
            or version.execution_notes != frozen.payload.get("execution_notes")
            or [ref.model_dump(mode="json") for ref in version.source_refs] != frozen.payload.get("source_refs")):
            raise ValueError("计划固定用例内容与原依据不一致")
        # The plan proves this version was enabled at planning time. A newer
        # enabled version must never silently replace the frozen reference.


def source_output_path(snapshot: CasePlanExecutionSnapshot, key: str, path: list[str]) -> list[str]:
    if key in snapshot.preparation_phases:
        return ["preparations", key, "output", *path]
    return ["case_output", *path]


def regression_source_for_plan(plan, request, regression=None):
    frozen = plan.regression
    if frozen is None:
        return regression
    if request.mode_key != frozen.mode_key or request.session_id != frozen.session_id:
        raise ValueError("回归计划启动模式或会话与固定输入不一致")
    targets = {node.case_id: node for node in plan.decision.items if node.kind == "target"} if plan.decision else {}
    if ({case_id: node.case_version_id for case_id, node in targets.items()} != frozen.required_case_versions
        or len(targets) != sum(node.kind == "target" for node in plan.decision.items)):
        raise ValueError("回归计划必须完整保留固定目标及版本")
    expected = RegressionPlanSource(parent_run_id=frozen.parent_run_id, source_plan_id=frozen.source_plan_id,
        source_results={targets[case_id].key: result_id for case_id, result_id in frozen.source_results_by_case.items()},
        version_overrides=frozen.version_overrides)
    if regression is not None and regression != expected:
        raise ValueError("回归计划的失败来源不能在启动时替换")
    return expected


def plan_run_identity(plan, request, kind, key=""):
    return str(uuid5(NAMESPACE_URL, json.dumps(["case-plan-run", plan.project_id,
        request.creation_key, kind, key], ensure_ascii=False)))


def build_plan_run(plan: CasePlanRecord, request: CasePlanRunRequest, *, now: datetime,
                   created_by: str | None = None, regression: RegressionPlanSource | None = None,
                   dependency_mapping_version: str | None = "auto"
                   ) -> tuple[TestSuiteBundle, TestRunRecord, list[TestRunItemRecord]]:
    if plan.status not in {"planned", "blocked"} or plan.decision is None or plan.decision.action != "finish":
        raise ValueError("只有已冻结且包含正式目标的计划可以创建运行")
    regression = regression_source_for_plan(plan, request, regression)
    if dependency_mapping_version == "auto":
        dependency_mapping_version = "case-cleanup-waits-v1" if plan.config_snapshot.policy.consumption_protocol else None
    if dependency_mapping_version not in {None, "case-cleanup-waits-v1"}:
        raise ValueError("不支持的固定计划依赖映射版本")
    waits = automatic_cleanup_waits(plan) if dependency_mapping_version else {}
    nodes = {node.key: node for node in plan.decision.items}
    if len(nodes) != len(plan.decision.items) or not any(n.kind == "target" for n in nodes.values()):
        raise ValueError("计划节点身份重复或没有正式目标")
    for node in nodes.values():
        if any(parent not in nodes or parent == node.key for parent in node.depends_on):
            raise ValueError("计划依赖不存在或引用自身")
    try:
        order = list(TopologicalSorter({key: n.depends_on for key, n in nodes.items()}).static_order())
    except CycleError:
        raise ValueError("计划依赖存在循环") from None
    formal = [key for key in order if nodes[key].kind != "prepare"]
    if regression and (not set(regression.source_results).issubset(formal)
        or len(set(regression.source_results.values())) != len(regression.source_results)
        or {key for key in formal if nodes[key].kind == "target"} != set(regression.source_results)):
        raise ValueError("回归来源结果与正式执行项不一致")
    ancestors: dict[str, set[str]] = {}
    for key in order:
        ancestors[key] = set(nodes[key].depends_on)
        for parent in nodes[key].depends_on:
            ancestors[key].update(ancestors[parent])
        for binding in nodes[key].input_bindings.values():
            if binding.source_item and binding.source_item not in ancestors[key]:
                raise ValueError("计划数据来源必须属于已声明前置")
            if binding.input_key and binding.input_key not in plan.request.runtime_context:
                raise ValueError("计划引用了没有提供的输入")
    # No upstream counterpart (checked: planning_service, run_store,
    # api_testing dependency_planner, Agent轨迹与协议鲁棒性): preparations
    # need an owning formal RunItem; preserving plan edges avoids fake cases.
    owners = {key: key for key in formal}
    phases = {}
    for key in order:
        if nodes[key].kind != "prepare":
            continue
        consumers = [f for f in formal if key in ancestors[f]]
        providers = [f for f in formal if f in ancestors[key]]
        if consumers:
            owners[key], phases[key] = consumers[0], "before"
        elif providers:
            owners[key], phases[key] = providers[-1], "after"
        else:
            raise ValueError("准备项没有关联任何正式用例")

    def identity(kind: str, key: str = "") -> str:
        return plan_run_identity(plan, request, kind, key)

    suite_id, run_id = identity("suite"), identity("run")
    item_ids = {key: identity("item", key) for key in formal}
    snapshot = CasePlanExecutionSnapshot(plan=plan.model_copy(deep=True), run_item_ids=item_ids,
        owners=owners, preparation_phases=phases, regression=regression.model_copy(deep=True) if regression else None,
        preparation_scopes=frozen_preparation_scopes(plan, owners), dependency_mapping_version=dependency_mapping_version)
    dependencies: dict[str, dict[str, RunItemDependency]] = {key: {} for key in formal}

    def edge(owner: str, parent: str, condition: str, preparation: str | None = None) -> RunItemDependency:
        dependency = dependencies[owner].setdefault(parent,
            RunItemDependency(item_id=item_ids[parent], completion_condition=condition))
        if condition == "passed":
            dependency.completion_condition = "passed"
        if preparation and preparation not in dependency.required_preparation_keys:
            dependency.required_preparation_keys.append(preparation)
        return dependency

    for key in order:
        owner = owners[key]
        for parent in nodes[key].depends_on:
            if owners[parent] != owner:
                prepare = parent if nodes[parent].kind == "prepare" else None
                condition = "terminal" if prepare or phases.get(key) == "after" or parent in waits.get(key, []) else "passed"
                edge(owner, owners[parent], condition, prepare)
        for binding in nodes[key].input_bindings.values():
            if binding.source_item and owners[binding.source_item] != owner:
                source = binding.source_item
                prepare = source if nodes[source].kind == "prepare" else None
                condition = "terminal" if prepare or phases.get(key) == "after" else "passed"
                dependency = edge(owner, owners[source], condition, prepare)
                path = source_output_path(snapshot, binding.source_item, binding.path_tokens)
                if binding.required and path not in dependency.required_output_paths:
                    dependency.required_output_paths.append(path)
    suite = TestSuiteBundle(suite=TestSuiteRecord(id=suite_id, project_id=plan.project_id,
        name=plan.request.objective[:240], description=f"固定计划 {plan.id}", created_by=created_by,
        created_at=now, updated_at=now), items=[TestSuiteItemRecord(id=identity("suite-item", key),
        suite_id=suite_id, case_id=nodes[key].case_id, case_version_id=nodes[key].case_version_id,
        position=index) for index, key in enumerate(formal, 1)])
    plan_data = case_plan_json(plan)
    if plan.regression is None:
        # Normal plans created before durable regression have no such field.
        # Keep their creation hash stable so acknowledgement retries still work.
        plan_data.pop("regression", None)
    hash_input = {"plan": plan_data, "request": request.model_dump(mode="json")}
    if dependency_mapping_version:
        hash_input["dependency_mapping_version"] = dependency_mapping_version
    if regression:
        hash_input["regression"] = regression.model_dump(mode="json")
    request_hash = json_digest(hash_input)
    run = TestRunRecord(id=run_id, project_id=plan.project_id, suite_id=suite_id,
        mode_key=request.mode_key, session_id=request.session_id, execution_strategy="agent",
        plan_snapshot=snapshot, creation_key=request.creation_key, creation_hash=request_hash,
        run_kind="regression" if regression else "normal", parent_run_id=regression.parent_run_id if regression else None,
        stats={"total": len(formal), "queued": len(formal)}, created_by=created_by,
        created_at=now, updated_at=now)
    items = [TestRunItemRecord(id=item_ids[key], run_id=run_id, case_id=nodes[key].case_id,
        case_version_id=nodes[key].case_version_id, plan_item_key=key, position=index,
        dependencies=list(dependencies[key].values()), created_at=now, updated_at=now)
        for index, key in enumerate(formal, 1)]
    if regression:
        for entry in items:
            entry.regression_source_result_id = regression.source_results.get(entry.plan_item_key)
    validate_run_dependencies(run, items)
    return suite, run, items


def validate_plan_bundle(suite: TestSuiteBundle, run: TestRunRecord, items: list[TestRunItemRecord]) -> None:
    if run.plan_snapshot is None or not run.creation_key:
        raise ValueError("计划运行没有固定快照或创建请求身份")
    expected = build_plan_run(run.plan_snapshot.plan, CasePlanRunRequest(creation_key=run.creation_key,
        mode_key=run.mode_key, session_id=run.session_id), now=run.created_at, created_by=run.created_by,
        regression=run.plan_snapshot.regression, dependency_mapping_version=run.plan_snapshot.dependency_mapping_version)
    if (suite != expected[0] or run != expected[1] or items != expected[2]):
        raise ValueError("计划映射、套件、运行或条目与固定依据不一致")


def validate_regression_parent(run: TestRunRecord, parent: TestRunRecord | None,
                               results: dict[str, TestCaseResultRecord]) -> None:
    source = run.plan_snapshot.regression if run.plan_snapshot else None
    if source is None:
        return
    if (parent is None or parent.project_id != run.project_id or parent.origin != "native"
        or parent.status not in {"completed", "cancelled"} or parent.execution_strategy != "agent"
        or parent.plan_snapshot is None or parent.plan_snapshot.plan.id != source.source_plan_id):
        raise ValueError("Agent 回归父运行或固定计划不一致")
    nodes = {node.key: node for node in run.plan_snapshot.plan.decision.items}
    selected_case_ids = {nodes[key].case_id for key in source.source_results}
    if not set(source.version_overrides).issubset(selected_case_ids):
        raise ValueError("回归版本替换不属于选定结果")
    parent_nodes = {node.case_id: node for node in parent.plan_snapshot.plan.decision.items if node.kind != "prepare"}
    for key, result_id in source.source_results.items():
        result, node = results.get(result_id), nodes[key]
        original = parent_nodes.get(node.case_id)
        if (result is None or result.run_id != parent.id or result.case_id != node.case_id
            or result.status not in {"failed", "error", "blocked"} or original is None
            or result.run_item_id != parent.plan_snapshot.run_item_ids.get(original.key)
            or result.case_version_id != original.case_version_id
            or node.case_version_id != source.version_overrides.get(node.case_id, result.case_version_id)):
            raise ValueError("Agent 回归来源结果、用例或版本不一致")
