"""Build execution data from a frozen plan and persisted results, never templates."""
from __future__ import annotations

from copy import deepcopy
from graphlib import TopologicalSorter
import logging

from src.application.test_runs.dependencies import dependencies_ready, output_at_path
from src.application.test_runs.environment_admission import fixed_environment_admission
from src.application.test_runs.plan_mapping import source_output_path
from src.application.test_runs.preparation_scopes import frozen_preparation_scopes
from src.runtime.postgres_business_planning import fixed_planning_environment
from src.schemas.case_execution import AgentCaseExecutionContext, BoundCaseInput, CaseExecutionStep
from src.schemas.case_management import TestCaseVersionRecord
from src.schemas.run_management import TestRunDetail, TestRunItemRecord


logger = logging.getLogger(__name__)


def build_agent_case_context(
    detail: TestRunDetail,
    item: TestRunItemRecord,
    version: TestCaseVersionRecord,
) -> AgentCaseExecutionContext:
    run = detail.run
    snapshot = run.plan_snapshot
    if run.execution_strategy != "agent" or snapshot is None:
        raise ValueError("运行没有固定的 Agent 执行依据")
    plan = snapshot.plan
    if snapshot.preparation_scopes != frozen_preparation_scopes(plan, snapshot.owners):
        raise ValueError("固定准备消费范围与原计划不一致")
    if plan.project_id != run.project_id or item.run_id != run.id or plan.decision is None:
        raise ValueError("固定计划与当前项目或运行引用不一致")
    nodes = {node.key: node for node in plan.decision.items}
    target = nodes.get(item.plan_item_key)
    stored_items = {entry.id: entry for entry in detail.items}
    stored = stored_items.get(item.id)
    if (target is None or target.kind == "prepare" or stored is None
        or snapshot.run_item_ids.get(target.key) != item.id
        or target.case_id != item.case_id or target.case_version_id != item.case_version_id
        or stored.run_id != run.id or stored.plan_item_key != target.key
        or stored.case_id != item.case_id or stored.case_version_id != item.case_version_id
        or version.id != item.case_version_id or version.case_id != item.case_id):
        raise ValueError("正式用例、版本与固定执行项引用不一致")
    frozen = next((e for e in plan.evidence if e.id == f'case:{version.id}'), None)
    if (frozen is None or frozen.source_id != item.case_id or frozen.content_hash != version.content_hash
        or frozen.payload.get("content") != version.business_content()
        or frozen.payload.get("execution_notes") != version.execution_notes
        or frozen.payload.get("source_refs") != [ref.model_dump(mode="json") for ref in version.source_refs]):
        raise ValueError("固定用例内容与执行版本引用不一致")
    results = {result.id: result for result in detail.results}
    if not dependencies_ready(stored, stored_items, results):
        raise ValueError("当前执行项的实际依赖尚未满足")
    owned = {key for key, owner in snapshot.owners.items() if owner == target.key}
    steps = []
    for key in TopologicalSorter({key: node.depends_on for key, node in nodes.items()}).static_order():
        if key not in owned:
            continue
        node = nodes[key]
        inputs = {}
        for name, binding in node.input_bindings.items():
            resolved, value, source_item_id, result_id, path = False, None, None, None, None
            if binding.input_key:
                value = plan.request.runtime_context.get(binding.input_key)
                resolved = binding.input_key in plan.request.runtime_context and value is not None
            elif binding.source_item not in owned:
                source_item_id = snapshot.run_item_ids[snapshot.owners[binding.source_item]]
                source_item = stored_items.get(source_item_id)
                result = results.get(source_item.result_id or "") if source_item else None
                path = source_output_path(snapshot, binding.source_item, binding.path_tokens)
                if (result is not None and source_item.run_id == run.id and result.run_id == run.id
                    and result.run_item_id == source_item_id and result.status == source_item.status
                    and result.case_id == source_item.case_id and result.case_version_id == source_item.case_version_id):
                    value = output_at_path(result.actual, path)
                    resolved, result_id = value is not None, result.id
            if binding.required and not resolved and binding.source_item not in owned:
                raise ValueError(f'执行项 {key} 的必要输入 {name} 没有实际来源')
            inputs[name] = BoundCaseInput(binding=binding.model_copy(deep=True), resolved=resolved,
                value=deepcopy(value), run_item_id=source_item_id, result_id=result_id, actual_path=path)
        steps.append(CaseExecutionStep(item=node.model_copy(deep=True),
            phase=snapshot.preparation_phases.get(key, "case"), inputs=inputs,
            content_json=deepcopy(version.business_content()) if key == target.key else None,
            execution_notes=version.execution_notes if key == target.key else "",
            source_refs=[ref.model_dump(mode="json") for ref in version.source_refs] if key == target.key else []))
    evidence_ids = {ref for key in owned for ref in nodes[key].evidence_ids}
    evidence_ids.add(frozen.id)
    evidence = [e.model_copy(deep=True) for e in plan.evidence if e.id in evidence_ids]
    # Complete formal content is already present in its execution step. Keep
    # source identity here, without repeating the same content in the prompt.
    for entry in evidence:
        if entry.id == frozen.id:
            entry.payload = {key: value for key, value in entry.payload.items()
                if key not in {"content", "execution_notes"}}
    fixed = None
    if plan.environment is not None:
        fixed = fixed_planning_environment(run.project_id, plan.request.environment, plan.environment)
    elif plan.request.environment is not None:
        raise ValueError("Agent所选环境缺少固定规划依据")
    attempt = next((entry for entry in detail.attempts if entry.run_item_id == item.id
        and entry.attempt_no == item.attempt_no and entry.lease_token == item.lease_token),None)
    admission = fixed_environment_admission(run,item,attempt,detail.attempts) if attempt else None
    context = AgentCaseExecutionContext(project_id=run.project_id, run_id=run.id, run_item_id=item.id,
        plan_id=plan.id, objective=plan.request.objective, target_key=target.key, steps=steps,
        action_binding_protocol=plan.config_snapshot.policy.action_binding_protocol,
        action_admission_protocol=plan.config_snapshot.policy.action_admission_protocol,
        operation_protocol=plan.config_snapshot.policy.operation_protocol,
        preparation_scope_protocol=plan.config_snapshot.policy.preparation_scope_protocol,
        preparation_scopes=deepcopy(snapshot.preparation_scopes),
        runtime_inputs=deepcopy(plan.request.runtime_context), business_environment=fixed,environment_admission=admission,
        evidence=evidence,
        unresolved=[gap.model_copy(deep=True) for gap in plan.decision.unresolved if gap.item_key is None or gap.item_key in owned])
    logger.info("agent_case_context_built", extra={"project_id": run.project_id, "run_id": run.id,
        "run_item_id": item.id, "plan_id": plan.id, "step_count": len(steps),
        "resolved_input_count": sum(inp.resolved for step in steps for inp in step.inputs.values())})
    return context
