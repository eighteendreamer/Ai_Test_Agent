"""Immutable inference task ownership over the complete saved evidence scope."""
from __future__ import annotations

import hashlib
import json
import logging

from src.application.documents.impact_batches import DocumentImpactBatches, DocumentImpactReadBudgetExceeded
from src.application.documents.impact_calls import DocumentImpactCallService
from src.schemas.document_impact_calls import DocumentImpactCallSelection
from src.schemas.document_impact_reasoning import (
    DocumentImpactCaseReasoningTask, DocumentImpactDiscoveryTask, DocumentImpactReasoningPlan,
)

logger = logging.getLogger(__name__)


def _bytes(value):
    # Match impact_batches/_json_bytes and impact_calls/_json for dictionary reads.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class DocumentImpactReasoningPlans:
    # No upstream counterpart (checked: impact manifest, per-unit coordinator,
    # frozen planning workflow and local Agent/CI course): this groups actual
    # evidence reads; it never deduplicates business requirements or model answers.
    @staticmethod
    def selection(members):
        anchor = members[0]
        return DocumentImpactCallSelection(unit=anchor,
            segment_ids=[item.id for item in members[1:] if item.kind == "segment"],
            difference_ids=[item.id for item in members[1:] if item.kind == "difference"])

    @classmethod
    def build(cls, seed, envelope, manifest, manifest_sha256):
        DocumentImpactBatches.validate(seed, envelope, manifest)
        if seed.reasoning_settings is None:
            raise ValueError("历史影响任务未冻结推理策略，请创建新任务；旧依据仍可读")
        policy = seed.reasoning_settings.payload
        units = DocumentImpactCallService.units(manifest)
        discovery, members, reads = [], [], []

        def finish(group, values, *, unavailable=False):
            size = None if unavailable else len(_bytes(
                {"primary": values[0], "additional_fixed_reads": values[1:]}))
            reasons = ["retrieval_bytes_limit"] if unavailable else (
                ["group_read_bytes_limit"] if size > policy.max_group_read_bytes else [])
            selection = cls.selection(group)
            body = {"members": [item.model_dump(mode="json") for item in group],
                "selection": selection.model_dump(mode="json"), "read_bytes": size, "reason_codes": reasons}
            discovery.append(DocumentImpactDiscoveryTask(id=hashlib.sha256(_bytes(body)).hexdigest(), **body))

        for unit in (item for item in units if item.kind != "case"):
            try:
                value = DocumentImpactBatches.read_segment(seed, envelope, manifest, unit.id) if unit.kind == "segment" else (
                    DocumentImpactCallService._difference(seed, envelope, manifest, unit.id))
            except DocumentImpactReadBudgetExceeded:
                if members:
                    finish(members, reads)
                    members, reads = [], []
                finish([unit], [], unavailable=True)
                continue
            candidate = [*reads, value]
            size = len(_bytes({"primary": candidate[0], "additional_fixed_reads": candidate[1:]}))
            if members and (len(members) >= policy.max_group_units or size > policy.max_group_read_bytes):
                finish(members, reads)
                members, reads = [], []
            members.append(unit)
            reads.append(value)
            if len(members) == 1 and len(_bytes(
                {"primary": value, "additional_fixed_reads": []})) > policy.max_group_read_bytes:
                finish(members, reads)
                members, reads = [], []
        if members:
            finish(members, reads)

        cases = []
        for unit in (item for item in units if item.kind == "case"):
            evidence_ids = {link.evidence_id for link in envelope.input.source_links
                if link.case_version_id == unit.id and link.evidence_id is not None}
            source_ids = {source.id for source in manifest.sources
                if any("original:" + identity in source.roles for identity in evidence_ids)}
            cases.append(DocumentImpactCaseReasoningTask(unit=unit,
                source_segment_ids=[segment.id for segment in manifest.segments if segment.source_id in source_ids]))
        plan = DocumentImpactReasoningPlan(input_sha256=envelope.input_sha256, manifest_sha256=manifest_sha256,
            config=seed.reasoning_settings, discovery=discovery, cases=cases)
        if [unit for task in plan.discovery for unit in task.members] != [unit for unit in units if unit.kind != "case"]:
            raise ValueError("推理任务计划不能遗漏、重排或重复固定证据")
        logger.info("document_impact_reasoning_plan_built", extra={"project_id": seed.project_id,
            "evidence_units": sum(len(item.members) for item in discovery), "discovery_tasks": len(discovery),
            "case_tasks": len(cases), "limited_tasks": sum(bool(item.reason_codes) for item in discovery)})
        return plan
