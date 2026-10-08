"""Create fresh Agent regression plans through the existing plan/run transaction."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.schemas.case_planning import CasePlanRunRequest, CasePlanningRequest, RegressionPlanSource, RegressionPlanningInput
from src.application.test_cases.planning_required_sources import validate_required_sources

logger = logging.getLogger(__name__)


class AgentRegressionService:
    def __init__(self, *, plan_store, plan_run_service, planning_service, run_store, planning_task_service=None):
        self._plans = plan_store
        self._creator = plan_run_service
        self._planner = planning_service
        self._runs = run_store
        self._planning_tasks = planning_task_service

    async def create(self, parent, candidates, payload, *, session_id=None, created_by=None):
        snapshot, selected, versions, context = await self._inputs(parent, candidates, payload)
        replanned = bool(payload.version_overrides)
        if replanned:
            plan = await self._replan(parent, versions, context)
        else:
            plan = self._subset(snapshot, selected, context)
            if plan is None:
                # Structural slicing cannot decide how to clean data produced by
                # an omitted branch. Ask the existing planner with fixed targets.
                plan = await self._replan(parent, versions, context)
                replanned = True
            else:
                plan = await self._plans.save(plan)

        targets = {node.case_id: node for node in plan.decision.items if node.kind == "target"}
        source_results = {targets[candidate.case_id].key: candidate.result_id for candidate in candidates}
        source = RegressionPlanSource(parent_run_id=parent.id, source_plan_id=snapshot.plan.id,
            source_results=source_results, version_overrides=payload.version_overrides)
        detail = await self._creator.create(parent.project_id, plan.id,
            CasePlanRunRequest(creation_key=str(uuid4()), mode_key=parent.mode_key, session_id=session_id),
            created_by=created_by, regression=source)
        logger.info("agent_regression_plan_created", extra={"project_id": parent.project_id,
            "parent_run_id": parent.id, "run_id": detail.run.id, "plan_id": plan.id,
            "target_count": len(selected), "formal_item_count": len(detail.items),
            "replanned": replanned})
        return detail

    async def _inputs(self, parent, candidates, payload):
        snapshot = parent.plan_snapshot
        if snapshot is None or snapshot.plan.project_id != parent.project_id:
            raise ValueError("Agent 回归父运行缺少项目所属固定计划")
        nodes = {entry.key: entry for entry in snapshot.plan.decision.items}
        selected = {}
        versions = {}
        for candidate in candidates:
            key = next((key for key, item_id in snapshot.run_item_ids.items() if item_id == candidate.run_item_id), None)
            node = nodes.get(key)
            if (node is None or node.kind == "prepare" or node.case_id != candidate.case_id
                or node.case_version_id != candidate.case_version_id):
                raise ValueError("回归结果与父运行固定用例不一致")
            selected[key] = candidate.result_id
            versions[node.case_id] = payload.version_overrides.get(node.case_id, node.case_version_id)

        context = {"parent_run_id": parent.id, "source_plan_id": snapshot.plan.id,
                   "selected_result_ids": list(selected.values()),
                   "previous_plan": snapshot.plan.decision.model_dump(mode="json")}
        context["failures"] = []
        for candidate in candidates:
            result = await self._runs.get_result(candidate.result_id)
            if result is None or result.run_id != parent.id:
                raise ValueError("回归来源失败结果已不可读取，未创建测试运行")
            context["failures"].append({"result_id": result.id, "case_id": result.case_id,
                "case_version_id": result.case_version_id, "status": result.status,
                "summary": result.summary, "error_message": result.error_message,
                "verification_results": result.actual.get("verification_results", [])})
        return snapshot, selected, versions, context

    async def find_submission(self, parent, request):
        if self._planning_tasks is None:
            raise RuntimeError("回归持久规划服务尚未配置")
        return await self._planning_tasks.find_regression_submission(parent.project_id, parent.id, request)

    async def submit(self, parent, candidates, request, *, session_id=None):
        snapshot, selected, versions, context = await self._inputs(parent, candidates, request)
        source = RegressionPlanningInput(parent_run_id=parent.id, source_plan_id=snapshot.plan.id,
            source_results_by_case={candidate.case_id: candidate.result_id for candidate in candidates},
            required_case_versions=versions, version_overrides=dict(request.version_overrides),
            mode_key=parent.mode_key, session_id=session_id,
            submission=request.model_dump(mode="json", exclude={"creation_key"}), context=context)
        fixed_plan = None if request.version_overrides else self._subset(snapshot, selected, context)
        planning_request = fixed_plan.request if fixed_plan is not None else CasePlanningRequest(objective=snapshot.plan.request.objective,
            candidate_case_ids=list(versions), model_key=snapshot.plan.model_key,
            runtime_context=snapshot.plan.request.runtime_context, environment=snapshot.plan.request.environment,
            required_sources=snapshot.plan.request.required_sources)
        return await self._planning_tasks.submit_regression(parent.project_id, request, source,
            planning_request=planning_request, prepared_plan=fixed_plan)

    async def _replan(self, parent, versions, context):
        original = parent.plan_snapshot.plan
        request = CasePlanningRequest(objective=original.request.objective,
            candidate_case_ids=list(versions), model_key=original.model_key,
            runtime_context=original.request.runtime_context, environment=original.request.environment,
            required_sources=original.request.required_sources)
        plan = await self._planner.create(parent.project_id, request,
            fixed_case_versions=versions, planning_context=context)
        if plan.status not in {"planned", "blocked"} or plan.decision is None:
            raise ValueError(f"回归重新规划未完成（计划 {plan.id}）：{plan.summary}")
        return plan

    @staticmethod
    def _subset(snapshot, selected, context):
        # No upstream counterpart (checked: create_regression, plan_mapping,
        # Agent轨迹与协议鲁棒性): keep the selected nodes' declared ancestors;
        # only trim cleanup ordering edges, never instructions or data sources.
        original = snapshot.plan
        nodes = {entry.key: entry for entry in original.decision.items}
        keep = set(selected)
        pending = list(keep)
        while pending:
            for parent in nodes[pending.pop()].depends_on:
                if parent not in keep:
                    keep.add(parent)
                    pending.append(parent)
        for key, phase in snapshot.preparation_phases.items():
            if phase != "after" or not set(nodes[key].depends_on).intersection(keep):
                continue
            keep.add(key)
        for key in keep:
            if any(binding.source_item and binding.source_item not in keep
                   for binding in nodes[key].input_bindings.values()):
                return None
            if any(source not in keep for source in getattr(nodes[key], "cleanup_sources", None) or []):
                # A sliced plan cannot silently rewrite the declared cleanup
                # operation or invent omitted sources; use the existing replan.
                return None
        items = []
        for entry in original.decision.items:
            if entry.key not in keep:
                continue
            node = entry.model_copy(deep=True)
            if node.kind != "prepare":
                node.kind = "target" if node.key in selected else "dependency"
            node.depends_on = [key for key in node.depends_on if key in keep]
            items.append(node)
        decision = original.decision.model_copy(deep=True, update={"items": items,
            "summary": "仅回归选定失败用例及必要前置、准备和清理",
            "unresolved": [gap for gap in original.decision.unresolved if gap.item_key is None or gap.item_key in keep]})
        refs = {ref for node in items for ref in node.evidence_ids}
        refs.update(f"case:{node.case_version_id}" for node in items if node.kind != "prepare")
        # Selected targets live in the frozen decision/provenance, rather than
        # expanding the original request's bounded candidate list or objective.
        request = original.request.model_copy(deep=True, update={"candidate_case_ids": []})
        scoped = original.model_copy(deep=True, update={"id": str(uuid4()), "request": request,
            "created_at": datetime.now(timezone.utc), "decision": decision, "summary": decision.summary, "regression": None,
            "status": "blocked" if decision.unresolved else "planned",
            "evidence": [e.model_copy(deep=True) for e in original.evidence if e.id in refs],
            "rounds": [{"kind": "regression_scope", **context,
                        "retained_item_keys": [node.key for node in items]}]})
        try:
            validate_required_sources(scoped, decision)
        except ValueError:
            # A dependency promoted to a regression target may not yet cite
            # the original target's required sources; use the existing replan.
            return None
        return scoped
