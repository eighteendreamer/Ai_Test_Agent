"""Automatic fixed-scope reading, rule handoff and immutable candidate results."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_checkpoint_io import ImpactCallBudgetExceeded, confirmed_impact_write, store_impact_json
from src.runtime.task_deferred import TaskDeferred
from src.schemas.document_impact import DocumentImpactSnapshot
from src.schemas.document_impact_calls import DocumentImpactCallSelection, DocumentImpactRangeRead
from src.schemas.document_impact_workflow import (
    WORKFLOW_VERSION, DocumentImpactCoordinationContext, DocumentImpactDiscoveredRule,
    DocumentImpactOverlap, DocumentImpactRuleOrigin, DocumentImpactUnitResult,
    DocumentImpactWorkflowProtocol, DocumentImpactWorkflowResult,
)

logger = logging.getLogger(__name__)


def _value_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def capture_impact_workflow_protocol():
    body = {"version": WORKFLOW_VERSION,
        "discovery_instruction": "本阶段从固定原文及差异提出规则变化候选。只处理当前单元，可read补读。"
            "候选引用实际读取的原文；不是需求评审。依据不足保留evidence_insufficient和unresolved。",
        "case_instruction": "本阶段将rules中完整规则候选与当前完整固定用例及其编写来源比较。"
            "逐条返回candidate，key必须为对应rules.id，保留该规则的kind/summary及原文依据，"
            "case_impacts仅判断当前unit.id，每条说明none/minor/major/uncertain并引用当前用例实际字段。"
            "需要原文或来源时read补读。不得遗漏规则后声明analyzed；无法判断保留evidence_insufficient。"
            "discovery_units包含未完成范围时不能宣称完整或无变化。没有规则仍判断本用例来源是否充分。"
            "所有候选仍待评审，不能将模型建议当作部署事实或测试通过。"}
    return DocumentImpactWorkflowProtocol(**body, sha256=_value_hash(body))


def discovered_rules(units):
    """Group exact bodies only. Keep every local proposal's immutable provenance."""
    grouped = {}
    for unit in units:
        if unit.final_check is None or unit.status not in {"analyzed", "evidence_insufficient"}:
            continue
        for candidate in unit.candidates:
            identity = _value_hash(candidate.model_dump(mode="json", exclude={"key"}))
            origin = DocumentImpactRuleOrigin(checkpoint=unit.final_check, local_key=candidate.key)
            if identity not in grouped:
                grouped[identity] = DocumentImpactDiscoveredRule(id=identity, candidate=candidate, origins=[origin])
            else:
                grouped[identity].origins.append(origin)
    return list(grouped.values())


def rule_overlaps(rules):
    groups = {}
    for rule in rules:
        citations = sorted((citation.model_dump(mode="json") for citation in rule.candidate.document_citations),
            key=lambda item: json.dumps(item, sort_keys=True))
        groups.setdefault(_value_hash(citations), []).append(rule.id)
    return [DocumentImpactOverlap(rule_ids=ids) for ids in groups.values() if len(ids) > 1]


def case_coverage_issues(unit, rules):
    expected = {rule.id for rule in rules}
    issues = []
    if {candidate.key for candidate in unit.candidates} != expected:
        issues.append("rule_case_coverage_incomplete")
    for candidate in unit.candidates:
        if [item.version_id for item in candidate.case_impacts] != [unit.unit.id]:
            issues.append("case_scope_mismatch")
    return list(dict.fromkeys(issues))


class DocumentImpactWorkflow:
    # No upstream counterpart (checked: impact calls/checks, planning_workflow,
    # shared document snapshot/Worker and local Agent course): this new domain
    # joins exact, provenance-bearing rule candidates to complete case versions.
    def __init__(self, *, tasks, store, calls, responses, objects):
        self._tasks, self._store, self._calls = tasks, store, calls
        self._responses, self._objects = responses, objects

    @staticmethod
    def _key(unit, predecessor=None):
        identity = hashlib.sha256(unit.id.encode("utf-8")).hexdigest()
        return f"workflow:{unit.kind}:{identity}:{predecessor or 'initial'}"

    @staticmethod
    def _rule_reads(rules):
        ranges = {}
        for rule in rules:
            for citation in rule.candidate.document_citations:
                ranges.setdefault(citation.source_id, []).append((citation.start_char, citation.end_char))
        result = []
        for source_id, values in ranges.items():
            merged = []
            for start, end in sorted(values):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            result += [DocumentImpactRangeRead(source_id=source_id, start_char=start, end_char=end) for start, end in merged]
        return result

    async def _unit(self, claim, unit, context):
        output = DocumentImpactUnitResult(unit=unit, status="unprocessed")
        try:
            selection = DocumentImpactCallSelection(unit=unit,
                ranges=self._rule_reads(context.rules) if context.stage == "case_impacts" else [])
            while True:
                # Existing requests/responses/checks remain readable after the deadline.
                # prepare/invoke/assess enforce budgets only for new work.
                record = await self._calls.prepare(claim, call_key=self._key(unit, selection.previous_call_id),
                    selection=selection, coordination_context=context)
                output.calls.append(record.id)
                record = await self._calls.invoke(claim, record.id)
                if record.phase == "invoking":
                    # Same-attempt competing invocation is live, not an unknown or failed result.
                    raise TaskDeferred("影响单元仍由当前attempt其他执行协程调用")
                if record.phase != "response_saved":
                    output.status = "unknown"
                    output.reason_codes.append(record.outcome_code or record.phase)
                    return output
                checkpoint, assessment = await self._responses.assess(claim, record.id)
                output.final_check = checkpoint
                if assessment.status in {"rejected", "ineligible"}:
                    output.status = assessment.status
                    output.reason_codes.extend(issue.code for issue in assessment.issues)
                    if assessment.status == "ineligible":
                        output.reason_codes.append(f"response_{record.response_disposition}")
                    return output
                decision = assessment.decision
                if assessment.status == "read_requested":
                    selection = DocumentImpactCallSelection(unit=unit, previous_call_id=record.id,
                        **decision.reads.model_dump(mode="python"))
                    continue
                output.status = decision.unit_outcome
                output.candidates, output.unresolved = decision.candidates, decision.unresolved
                if context.stage == "case_impacts":
                    output.reason_codes += case_coverage_issues(output, context.rules)
                    if output.reason_codes and output.status == "analyzed":
                        output.status = "incomplete"
                return output
        except (ValueError, KeyError, TimeoutError) as exc:
            # Validation errors may embed source text; retain exact saved inputs, not exception text.
            output.status = "unprocessed"
            reason = f"call_budget_{exc.kind}" if isinstance(exc, ImpactCallBudgetExceeded) else (
                "task_deadline" if isinstance(exc, TimeoutError) else "fixed_call_rejected")
            output.reason_codes.append(reason)
            logger.warning("document_impact_workflow_unit_unprocessed", extra={"task_id": claim.task.id,
                "unit_kind": unit.kind, "unit_id": unit.id, "error_type": type(exc).__name__})
            return output

    @staticmethod
    def _limitations(envelope, manifest):
        limits = [f"source_{source.status}:{source.id}" for source in manifest.sources if source.status != "complete"]
        limits += [f"source_warning:{source.id}:{warning.code}" for source in manifest.sources for warning in source.warnings]
        limits += [f"case_source_{gap.status}:{gap.case_version_id}:{gap.source_index}" for gap in manifest.source_gaps]
        limits += [f"difference_warning:{warning.code}" for warning in envelope.input.difference.warnings]
        return limits

    @staticmethod
    def _status(units, limitations):
        return "partial" if limitations or any(item.status != "analyzed" or item.unresolved or item.reason_codes for item in units) else "complete"

    async def _read_result(self, snapshot):
        project, doc, task_id = snapshot.seed.project_id, snapshot.seed.document_id, snapshot.task_id
        fixed_seed = await self._store.get_seed(project, doc, task_id)
        self._store.validate_snapshot_metadata(snapshot, fixed_seed)
        _, envelope = await self._tasks.read_input(project, doc, task_id)
        checkpoint, manifest = await self._tasks.read_manifest(project, doc, task_id)
        raw = (await self._objects.read_object_uri(snapshot.storage_uri))["content"]
        if len(raw) != snapshot.size_bytes or hashlib.sha256(raw).hexdigest() != snapshot.sha256:
            raise ValueError("影响工作流结果对象大小或哈希不同")
        result = await asyncio.to_thread(DocumentImpactWorkflowResult.model_validate_json, raw)
        discovery = [item for item in result.units if item.unit.kind != "case"]
        if (result.task_id != task_id or result.input_sha256 != envelope.input_sha256 or
            snapshot.input_sha256 != result.input_sha256 or result.manifest_sha256 != checkpoint.sha256 or
            snapshot.manifest_sha256 != result.manifest_sha256 or result.protocol != snapshot.seed.workflow_protocol or
            [item.unit for item in result.units] != self._calls.units(manifest) or
            result.rules != discovered_rules(discovery) or result.overlaps != rule_overlaps(result.rules) or
            result.basis_limitations != self._limitations(envelope, manifest) or
            result.status != self._status(result.units, result.basis_limitations) or snapshot.status != result.status or
            snapshot.unit_count != len(result.units) or snapshot.analyzed_count != sum(item.status == "analyzed" for item in result.units) or
            snapshot.rule_count != len(result.rules)):
            raise ValueError("影响工作流结果不能替换固定范围、规则或统计")
        for item in result.units:
            expected_context = DocumentImpactCoordinationContext(protocol=result.protocol,
                stage="case_impacts" if item.unit.kind == "case" else "discover_rules",
                rules=result.rules if item.unit.kind == "case" else [],
                discovery_units=discovery if item.unit.kind == "case" else [])
            for call_id in item.calls:
                record, payload = await self._calls.read_request(project, doc, task_id, call_id)
                if record.selection.unit != item.unit or json.loads(payload.request.messages[-1]["content"]).get(
                    "coordination_context") != expected_context.model_dump(mode="json"):
                    raise ValueError("影响结果中的单元实际调用未获得固定规则与阶段依据")
            if item.final_check is not None:
                saved, assessment = await self._responses.read(project, doc, task_id, item.final_check.call_id)
                if saved != item.final_check or saved.call_id not in item.calls:
                    raise ValueError("影响单元不能替换原响应核验来源")
                if item.status in {"analyzed", "evidence_insufficient", "incomplete"} and (
                    assessment.decision is None or item.candidates != assessment.decision.candidates):
                    raise ValueError("影响单元候选与已保存回执不同")
                if item.status == "analyzed" and (assessment.status != "unit_finished" or assessment.decision.unit_outcome != "analyzed"):
                    raise ValueError("影响单元不能将未完成响应标记已分析")
            elif item.status in {"analyzed", "evidence_insufficient", "rejected", "ineligible", "incomplete"}:
                raise ValueError("影响单元分析状态缺少原响应核验")
            if item.unit.kind == "case" and item.status == "analyzed" and case_coverage_issues(item, result.rules):
                raise ValueError("影响用例缺少固定规则判断，不能标记完整")
        return result

    async def read_result(self, project_id, document_id, task_id):
        await self._tasks.get(project_id, document_id, task_id)
        snapshot = await self._store.get_result_checkpoint(project_id, document_id, task_id)
        if snapshot is None:
            raise KeyError("该影响任务尚未保存全范围结果")
        return snapshot, await self._read_result(snapshot)

    async def execute(self, claim):
        protocol = claim.seed.workflow_protocol
        if protocol is None:
            return await self._store.finish_failed(claim, error_code="impact_workflow_protocol_missing",
                message="历史影响任务没有固定自动协调协议，请创建新任务；原调用仍可查询")
        DocumentImpactWorkflowProtocol.model_validate(protocol.model_dump(mode="json"))
        prior = await self._store.get_result_checkpoint(claim.task.project_id, claim.task.document_id, claim.task.id)
        if prior is not None:
            await self._read_result(prior)
            return await confirmed_impact_write(self._store.finish(claim), task_id=claim.task.id, stage="result_publish")
        task = await self._tasks.prepare_input(claim)
        if task.status == "failed":
            return task
        await self._tasks.prepare_manifest(claim)
        _, envelope, checkpoint, manifest = await self._calls.read_fixed_scope(claim)
        discovery_context = DocumentImpactCoordinationContext(protocol=protocol, stage="discover_rules")
        discovery = []
        units = self._calls.units(manifest)
        for unit in units:
            if unit.kind != "case":
                discovery.append(await self._unit(claim, unit, discovery_context))
        rules = discovered_rules(discovery)
        case_context = DocumentImpactCoordinationContext(protocol=protocol, stage="case_impacts", rules=rules,
            discovery_units=discovery)
        results = [*discovery]
        for unit in units:
            if unit.kind == "case":
                results.append(await self._unit(claim, unit, case_context))
        limits = self._limitations(envelope, manifest)
        result = DocumentImpactWorkflowResult(task_id=claim.task.id, input_sha256=envelope.input_sha256,
            manifest_sha256=checkpoint.sha256, protocol=protocol, units=results, rules=rules,
            overlaps=rule_overlaps(rules), basis_limitations=limits, status=self._status(results, limits))
        reference = await confirmed_impact_write(store_impact_json(self._objects, claim, result, "workflow-result.json"),
            task_id=claim.task.id, stage="workflow_result_object")
        snapshot = DocumentImpactSnapshot(id=str(uuid4()), task_id=claim.task.id, seed=claim.seed,
            storage_uri=reference.storage_uri, sha256=reference.sha256, size_bytes=reference.size_bytes,
            input_sha256=result.input_sha256, manifest_sha256=result.manifest_sha256, status=result.status,
            unit_count=len(results), analyzed_count=sum(item.status == "analyzed" for item in results),
            rule_count=len(rules), created_at=datetime.now(timezone.utc))
        await confirmed_impact_write(self._store.save_snapshot_checkpoint(claim, snapshot),
            task_id=claim.task.id, stage="workflow_result_checkpoint")
        # Verify durable evidence before publication; unknown commits never grant dispatch permission.
        await self._read_result(snapshot)
        saved = await confirmed_impact_write(self._store.finish(claim,
            message="影响候选结果已保存，业务判断待评审" if result.status == "complete" else "影响分析存在未完成范围或依据不足，结果已保存待评审"),
            task_id=claim.task.id, stage="workflow_result_publish")
        logger.info("document_impact_workflow_result_saved", extra={"task_id": claim.task.id,
            "snapshot_id": snapshot.id, "coverage_status": result.status, "unit_count": len(results), "rule_count": len(rules)})
        return saved
