"""Consume frozen inference tasks; retain actual per-evidence model declarations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_checkpoint_io import confirmed_impact_write, store_impact_json
from src.application.documents.impact_reasoning_plan_service import DocumentImpactReasoningPlanService
from src.application.documents.impact_workflow import DocumentImpactWorkflow, discovered_rules, rule_overlaps, case_coverage_issues
from src.schemas.document_impact import DocumentImpactSnapshot
from src.schemas.document_impact_calls import DocumentImpactCallSelection
from src.schemas.document_impact_grouped import (
    DocumentImpactDiscoveryResult, DocumentImpactEvidenceResult, DocumentImpactGroupedContext,
    DocumentImpactGroupedResult,
)
from src.schemas.document_impact_workflow import DocumentImpactUnitResult

logger = logging.getLogger(__name__)


def evidence_results(plan, discovery):
    """One model declaration per member; no group candidate copied onto evidence."""
    result = []
    for task, saved in zip(plan.discovery, discovery):
        outcomes = {(item.unit.kind, item.unit.id): item for item in saved.evidence_outcomes}
        for unit in task.members:
            outcome = outcomes.get((unit.kind, unit.id))
            status = outcome.outcome if outcome is not None else saved.status
            if outcome is None and status in {"analyzed", "evidence_insufficient"}:
                status = "incomplete"
            result.append(DocumentImpactEvidenceResult(unit=unit, status=status, calls=saved.calls,
                final_check=saved.final_check, reason_codes=saved.reason_codes,
                unresolved=saved.unresolved,
                reason=outcome.reason if outcome is not None else "该推理任务没有已核验的逐证据处理声明"))
    return result


class DocumentImpactGroupedWorkflow(DocumentImpactWorkflow):
    # No upstream counterpart (checked: coordinator-v1, immutable reasoning
    # plans, response checks, local Agent/CI course): grouped inference retains
    # explicit evidence declarations and one original proposal provenance.
    def _plans(self):
        return DocumentImpactReasoningPlanService(tasks=self._tasks, store=self._store, objects=self._objects)

    def _case_context(self, seed, checkpoint, discovery, rules):
        return DocumentImpactGroupedContext(protocol=seed.workflow_protocol, stage="case_impacts",
            reasoning_checkpoint=checkpoint, rules=rules, discovery_results=discovery)

    async def _read_result(self, snapshot):
        project, doc, task_id = snapshot.seed.project_id, snapshot.seed.document_id, snapshot.task_id
        seed = await self._store.get_seed(project, doc, task_id)
        self._store.validate_snapshot_metadata(snapshot, seed)
        _, envelope = await self._tasks.read_input(project, doc, task_id)
        manifest_checkpoint, manifest = await self._tasks.read_manifest(project, doc, task_id)
        reasoning_checkpoint, plan = await self._plans().read(project, doc, task_id)
        raw = (await self._objects.read_object_uri(snapshot.storage_uri))["content"]
        if len(raw) != snapshot.size_bytes or hashlib.sha256(raw).hexdigest() != snapshot.sha256:
            raise ValueError("分组影响结果对象大小或哈希不同")
        result = await asyncio.to_thread(DocumentImpactGroupedResult.model_validate_json, raw)
        discovery = result.discovery_tasks
        cases = [item for item in result.units if item.unit.kind == "case"]
        limits = self._limitations(envelope, manifest)
        if (result.task_id != task_id or result.input_sha256 != envelope.input_sha256 or
            result.input_sha256 != snapshot.input_sha256 or result.manifest_sha256 != manifest_checkpoint.sha256 or
            result.manifest_sha256 != snapshot.manifest_sha256 or result.protocol != seed.workflow_protocol or
            result.reasoning_checkpoint != reasoning_checkpoint or len(discovery) != len(plan.discovery) or
            [item.task_id for item in discovery] != [item.id for item in plan.discovery] or
            [item.unit for item in cases] != [item.unit for item in plan.cases] or
            [item.unit for item in result.units] != self._calls.units(manifest) or
            result.units != [*evidence_results(plan, discovery), *cases] or
            result.rules != discovered_rules(discovery) or result.overlaps != rule_overlaps(result.rules) or
            result.basis_limitations != limits or result.status != self._status(result.units, limits) or
            snapshot.status != result.status or snapshot.unit_count != len(result.units) or
            snapshot.analyzed_count != sum(item.status == "analyzed" for item in result.units) or
            snapshot.rule_count != len(result.rules)):
            raise ValueError("分组结果不能替换固定计划、逐证据声明、用例、规则或统计")
        for task, saved in zip(plan.discovery, discovery):
            if saved.unit != task.selection.unit:
                raise ValueError("发现结果不能更换组锚点")
            context = DocumentImpactGroupedContext(protocol=seed.workflow_protocol, stage="discover_rules",
                reasoning_checkpoint=reasoning_checkpoint, discovery_task=task)
            await self._verify_task(project, doc, task_id, saved, context, task.selection)
        context = self._case_context(seed, reasoning_checkpoint, discovery, result.rules)
        for task, saved in zip(plan.cases, cases):
            selection = self._case_selection(task, result.rules) if saved.calls else None
            await self._verify_task(project, doc, task_id, saved, context, selection)
            if saved.status == "analyzed" and case_coverage_issues(saved, result.rules):
                raise ValueError("用例缺少固定规则判断，不能标记完整")
        return result

    async def _verify_task(self, project, doc, task_id, saved, context, initial_selection):
        for index, call_id in enumerate(saved.calls):
            record, payload = await self._calls.read_request(project, doc, task_id, call_id)
            if (record.selection.unit != saved.unit or json.loads(payload.request.messages[-1]["content"]).get(
                "coordination_context") != context.model_dump(mode="json") or
                index == 0 and record.selection != initial_selection):
                raise ValueError("分组实际调用未取得固定完整计划、规则或来源")
        if saved.final_check is None:
            if saved.status in {"analyzed", "evidence_insufficient", "rejected", "ineligible", "incomplete"}:
                raise ValueError("分组处理声明缺少原响应核验")
            return
        checkpoint, assessment = await self._responses.read(project, doc, task_id, saved.final_check.call_id,
            validator_version=saved.final_check.validator_version)
        if checkpoint != saved.final_check or checkpoint.call_id not in saved.calls:
            raise ValueError("分组结果不能替换原响应核验")
        if saved.status in {"analyzed", "evidence_insufficient", "incomplete"} and (
            assessment.status != "unit_finished" or assessment.decision is None or
            saved.candidates != assessment.decision.candidates or
            hasattr(saved, "evidence_outcomes") and saved.evidence_outcomes != assessment.decision.evidence_outcomes):
            raise ValueError("组候选与逐证据处理必须等于实际核验声明")
        if saved.status == "analyzed" and assessment.decision.unit_outcome != "analyzed":
            raise ValueError("不能把依据不足的组响应标记完整")

    def _case_selection(self, task, rules):
        return DocumentImpactCallSelection(unit=task.unit, segment_ids=task.source_segment_ids,
            ranges=self._rule_reads(rules))

    async def execute(self, claim):
        prior = await self._store.get_result_checkpoint(claim.task.project_id, claim.task.document_id, claim.task.id)
        if prior is not None:
            await self._read_result(prior)
            return await confirmed_impact_write(self._store.finish(claim), task_id=claim.task.id, stage="group_result_publish")
        task = await self._tasks.prepare_input(claim)
        if task.status == "failed":
            return task
        await self._tasks.prepare_manifest(claim)
        _, envelope, manifest_checkpoint, manifest = await self._calls.read_fixed_scope(claim)
        reasoning_checkpoint, plan = await self._plans().prepare(claim)
        discovery = []
        for task in plan.discovery:
            output = DocumentImpactDiscoveryResult(task_id=task.id, unit=task.selection.unit, status="unprocessed")
            if task.reason_codes:
                output.reason_codes = task.reason_codes
                discovery.append(output)
                continue
            context = DocumentImpactGroupedContext(protocol=claim.seed.workflow_protocol, stage="discover_rules",
                reasoning_checkpoint=reasoning_checkpoint, discovery_task=task)
            discovery.append(await self._unit(claim, task.selection.unit, context, initial_selection=task.selection,
                output=output, call_key=lambda previous, identity=task.id: f"group:{identity}:{previous or 'initial'}"))
        rules = discovered_rules(discovery)
        context = self._case_context(claim.seed, reasoning_checkpoint, discovery, rules)
        cases = []
        for task in plan.cases:
            try:
                selection = self._case_selection(task, rules)
            except ValueError:
                cases.append(DocumentImpactUnitResult(unit=task.unit, status="unprocessed",
                    reason_codes=["fixed_case_source_selection_limit"]))
                continue
            cases.append(await self._unit(claim, task.unit, context, initial_selection=selection,
                call_key=lambda previous, unit=task.unit: "group-case:" + self._key(unit, previous)))
        units = [*evidence_results(plan, discovery), *cases]
        limits = self._limitations(envelope, manifest)
        result = DocumentImpactGroupedResult(task_id=claim.task.id, input_sha256=envelope.input_sha256,
            manifest_sha256=manifest_checkpoint.sha256, protocol=claim.seed.workflow_protocol,
            reasoning_checkpoint=reasoning_checkpoint, discovery_tasks=discovery, units=units,
            rules=rules, overlaps=rule_overlaps(rules), basis_limitations=limits, status=self._status(units, limits))
        reference = await confirmed_impact_write(store_impact_json(self._objects, claim, result, "grouped-result.json"),
            task_id=claim.task.id, stage="group_result_object")
        snapshot = DocumentImpactSnapshot(id=str(uuid4()), task_id=claim.task.id, seed=claim.seed,
            storage_uri=reference.storage_uri, sha256=reference.sha256, size_bytes=reference.size_bytes,
            input_sha256=result.input_sha256, manifest_sha256=result.manifest_sha256, status=result.status,
            unit_count=len(units), analyzed_count=sum(item.status == "analyzed" for item in units),
            rule_count=len(rules), created_at=datetime.now(timezone.utc))
        await confirmed_impact_write(self._store.save_snapshot_checkpoint(claim, snapshot),
            task_id=claim.task.id, stage="group_result_checkpoint")
        await self._read_result(snapshot)
        saved = await confirmed_impact_write(self._store.finish(claim, message="分组影响候选已保存，业务判断待评审"),
            task_id=claim.task.id, stage="group_result_publish")
        logger.info("document_impact_grouped_result_saved", extra={"task_id": claim.task.id,
            "discovery_tasks": len(discovery), "evidence_units": len(units) - len(cases),
            "case_tasks": len(cases), "coverage_status": result.status, "rule_count": len(rules)})
        return saved
