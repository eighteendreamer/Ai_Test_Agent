"""Boundary regressions use the actual captured shared Agent plan."""
from pathlib import Path
import json

import pytest

from src.application.test_cases.planning_service import CasePlanningService
from src.application.test_runs.preparation_scopes import normalize_preparation_scopes, frozen_preparation_scopes
from src.application.test_runs.agent_regression_service import AgentRegressionService
from src.schemas.case_planning import CasePlanExecutionSnapshot, CasePlanningPolicy


@pytest.fixture
def captured():
    root = Path(__file__).resolve().parents[2]
    path = root / "docs/upgrade_2026-10-03/p4-shared-effect-wire/first-consumer-only-left-passed-finished/verification.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["actual_run_detail"]["run"]["plan_snapshot"]
    return raw, CasePlanExecutionSnapshot.model_validate(raw)


def declared(snapshot):
    plan = snapshot.plan.model_copy(deep=True)
    policy = plan.config_snapshot.policy.model_dump(mode="json")
    policy["preparation_scope_protocol"] = "preparation-scope-v1"
    plan.config_snapshot.policy = CasePlanningPolicy.model_validate(policy)
    next(n for n in plan.decision.items if n.key == "cleanup").cleanup_sources = ["seed"]
    return plan


def test_actual_legacy_snapshot_and_prompt_keep_original_wire(captured):
    raw, snapshot = captured
    assert snapshot.model_dump(mode="json") == raw
    assert "preparation_scopes" not in snapshot.model_dump(mode="json")
    assert "cleanup_sources" not in CasePlanningService._system_prompt()
    assert "cleanup_sources" in CasePlanningService._system_prompt(preparation_scopes=True)


def test_actual_plan_normalizes_before_ownership_and_requires_frozen_dependencies(captured):
    _, snapshot = captured
    plan = declared(snapshot)
    with pytest.raises(ValueError, match="固定清理范围"):
        frozen_preparation_scopes(plan, snapshot.owners)
    normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    cleanup = next(n for n in plan.decision.items if n.key == "cleanup")
    assert cleanup.depends_on == ["left", "seed", "right"]
    before = plan.model_dump(mode="json")
    normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    assert plan.model_dump(mode="json") == before
    scope = frozen_preparation_scopes(plan, {**snapshot.owners, "cleanup": "right"})["seed"]
    assert scope.consumer_keys == ["left", "right"] and scope.source_owner_key == "left"
    assert scope.cleanup_keys == ["cleanup"]


@pytest.mark.parametrize("source,reason", [(["seed", "seed"], "重复"), (["missing"], "不存在"),
    (["cleanup"], "自身")])
def test_invalid_sources_from_actual_plan_are_not_frozen(captured, source, reason):
    _, snapshot = captured
    plan = declared(snapshot)
    next(n for n in plan.decision.items if n.key == "cleanup").cleanup_sources = source
    before = plan.model_dump(mode="json")
    with pytest.raises(ValueError, match=reason):
        normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    assert plan.model_dump(mode="json") == before


def test_actual_plan_cleanup_before_a_remaining_consumer_is_a_cycle(captured):
    _, snapshot = captured
    plan = declared(snapshot)
    next(n for n in plan.decision.items if n.key == "right").depends_on.append("cleanup")
    before = plan.model_dump(mode="json")
    with pytest.raises(ValueError, match="循环"):
        normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    assert plan.model_dump(mode="json") == before


def test_formal_source_can_declare_its_own_cleanup(captured):
    _, snapshot = captured
    plan = declared(snapshot)
    next(n for n in plan.decision.items if n.key == "cleanup").cleanup_sources = ["left"]
    normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    scope = frozen_preparation_scopes(plan, snapshot.owners)["left"]
    assert scope.consumer_keys == ["left"] and scope.source_owner_key == "left"


def test_scope_policy_is_required_and_no_declaration_does_not_infer_cleanup(captured):
    _, snapshot = captured
    with pytest.raises(ValueError, match="固定逻辑操作"):
        CasePlanningPolicy(preparation_scope_protocol="preparation-scope-v1")
    plan = declared(snapshot)
    with pytest.raises(ValueError, match="启用"):
        normalize_preparation_scopes(plan.decision, None)
    next(n for n in plan.decision.items if n.key == "cleanup").cleanup_sources = None
    normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    assert next(n for n in plan.decision.items if n.key == "cleanup").depends_on == ["left"]
    assert frozen_preparation_scopes(plan, snapshot.owners) == {}


def test_regression_cannot_slice_away_a_declared_cleanup_source(captured):
    _, snapshot = captured
    plan = declared(snapshot)
    next(n for n in plan.decision.items if n.key == "cleanup").cleanup_sources = ["seed", "left"]
    normalize_preparation_scopes(plan.decision, "preparation-scope-v1")
    snapshot.plan = plan
    assert AgentRegressionService._subset(snapshot, {"right": "controlled-scope-result"}, {}) is None
