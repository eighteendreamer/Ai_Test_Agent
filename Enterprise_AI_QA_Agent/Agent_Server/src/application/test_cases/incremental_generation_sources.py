"""Freeze confirmed scope from saved evidence, never from mutable project previews."""
from __future__ import annotations

import logging
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.schemas.case_generation import (
    IncrementalGenerationModule, IncrementalGenerationScope, fixed_generation_snapshot,
)
from src.schemas.case_management import TestCaseGenerateRequest
from src.schemas.document_impact import DocumentImpactCaseReference

logger = logging.getLogger(__name__)


class IncrementalCaseGenerationSources:
    def __init__(self, *, requirements, impact_tasks, projects, cases, templates, generator):
        self._requirements, self._impact_tasks, self._projects = requirements, impact_tasks, projects
        self._cases, self._templates, self._generator = cases, templates, generator

    async def capture(self, project_id, document_id, request, *, transport_options=None):
        await self._projects.require_active(project_id)
        requirement, review = await self._requirements.read_generation_basis(project_id, document_id,
            request.requirement_id, request.requirement_version_id)
        if requirement.change == "removed" and request.include_new_cases:
            raise ValueError("移除需求只提出停用候选，不能生成新覆盖")
        checkpoint, envelope = await self._impact_tasks.read_input(project_id, document_id, review.source.task_id)
        if envelope.input_sha256 != review.source.input_sha256:
            raise ValueError("增量依据与原影响固定输入不同")
        evidence = envelope.input
        fixed_cases = {bundle.version.id: bundle for bundle in evidence.cases}
        if any(version_id not in fixed_cases for version_id in request.target_case_version_ids):
            raise ValueError("增量目标必须属于该确认决定的固定用例范围")
        selected = [fixed_cases[version_id] for version_id in request.target_case_version_ids]
        if len({bundle.case.id for bundle in selected}) != len(selected):
            raise ValueError("同一用例需明确选择一个更新基线，不能并行更新多个历史版本")
        removed = requirement.change == "removed"
        template = None if removed else await self._templates.get(project_id)
        authoring = None if removed else await self._generator.capture_authoring(request.model_key,
            transport_options=transport_options)
        # No upstream counterpart (checked: generation pipeline, requirement reviews,
        # fixed impact inputs and local synthesis/version governance course): scope
        # modules bind explicit confirmed versions, not inferred document headings.
        modules = []
        targets = [("retirement_candidate" if removed else "update", bundle) for bundle in selected]
        if request.include_new_cases:
            targets.append(("create", None))
        for kind, bundle in targets:
            target = None; latest = None; prepared = None
            if bundle is not None:
                current = await self._cases.get_case(bundle.case.id)
                if current.project_id != project_id:
                    raise ValueError("增量目标用例不属于当前项目")
                latest = current.latest_version
                target = DocumentImpactCaseReference(case=bundle.case, version_id=bundle.version.id,
                    content_hash=bundle.version.content_hash, version_record_sha256=impact_record_hash(bundle.version))
            if not removed:
                links = [link for link in evidence.source_links if bundle and link.case_version_id == bundle.version.id]
                original_ids = {link.evidence_id for link in links if link.evidence_id}
                context = {
                    "generation_template": template.model_dump(mode="json"),
                    "source_refs": [{"source_type": "requirement_version", "source_id": requirement.requirement_id,
                        "version": requirement.id, "label": requirement.title, "metadata": {
                            "content_sha256": requirement.content_sha256, "review_id": review.id,
                            "snapshot_id": review.source.snapshot_id, "input_sha256": review.source.input_sha256}}],
                    "incremental_generation": {"kind": kind, "requirement": requirement.model_dump(mode="json"),
                        "review": review.model_dump(mode="json"), "input_checkpoint": checkpoint.model_dump(mode="json"),
                        "old": evidence.old.model_dump(mode="json"), "new": evidence.new.model_dump(mode="json"),
                        "difference": evidence.difference.model_dump(mode="json"),
                        "target_case": bundle.model_dump(mode="json") if bundle else None,
                        "originals": [item.model_dump(mode="json") for item in evidence.originals if item.id in original_ids],
                        "source_links": [link.model_dump(mode="json") for link in links],
                        "basis_limitations": review.source.basis_limitations,
                        "deployment_status": "unknown"},
                }
                if bundle:
                    context["source_refs"].extend(ref.model_dump(mode="json") for ref in bundle.version.source_refs)
                generation_request = TestCaseGenerateRequest(objective="只为指定已确认需求版本及验证点生成可评审JSON草稿。",
                    mode_key=bundle.case.mode_key if bundle else request.new_case_mode_key, model_key=authoring.model_key,
                    include_knowledge_graph=False, include_history=False, max_cases=1 if bundle else request.max_new_cases)
                prepared = await self._generator.prepare_frozen(request=generation_request, context=context, authoring=authoring)
            modules.append(fixed_generation_snapshot(IncrementalGenerationModule, id=str(uuid4()), kind=kind,
                target=target, expected_latest_version=latest, generation=prepared))
        scope = fixed_generation_snapshot(IncrementalGenerationScope, project_id=project_id, document_id=document_id,
            request=request, requirement=requirement, review=review, input_checkpoint=checkpoint,
            template=template, authoring=authoring, modules=modules, basis_limitations=review.source.basis_limitations)
        logger.info("incremental_generation_scope_captured", extra={"project_id": project_id,
            "requirement_version_id": requirement.id, "scope_sha256": scope.sha256, "module_count": len(scope.modules)})
        return scope

    def parse_module_response(self, module, result):
        fixed = IncrementalGenerationModule.model_validate_json(module.model_dump_json())
        if fixed.kind == "retirement_candidate":
            raise ValueError("停用候选不解析生成响应，也不自动停用用例")
        batch = self._generator.parse_frozen(fixed.generation, result)
        if fixed.target and (len(batch.cases) != 1 or batch.cases[0].case_key != fixed.target.case.case_key):
            raise ValueError("更新候选必须保持原用例身份和case_key，未创建替代用例")
        return batch
