"""Deterministic source ownership; no inferred project-wide source mapping."""
from src.application.test_cases.planning_sources import json_text
from src.schemas.case_planning_sources import PlanningLinkedSource, PlanningLinkedSources, LINKED_CONTEXT_PROTOCOL
from src.schemas.document_content import DocumentContentBasis


def linked_source_queue(evidence):
    # No upstream counterpart (checked: full case evidence source_refs, durable
    # operation queues and generation provenance): record explicit references
    # outside model rounds; do not manufacture a query decision.
    entries = {}
    for case in evidence:
        if case.kind != "case":continue
        for source in case.payload.get("source_refs", []):
            key = json_text(source)
            if key not in entries:
                entries[key] = PlanningLinkedSource(case_evidence_ids=[], source=source)
            entries[key].case_evidence_ids = list(dict.fromkeys([*entries[key].case_evidence_ids, case.id]))
    return PlanningLinkedSources(entries=list(entries.values()))


def linked_document_bases(progress):
    result = {}
    for entry in progress.entries:
        if entry.source.source_type == "api_doc":result.setdefault(entry.source.source_id, [])
        if isinstance(entry.basis, DocumentContentBasis):
            bases = result.setdefault(entry.source.source_id, [])
            if entry.basis not in bases:bases.append(entry.basis)
    return result


def linked_source_context(progress, protocol):
    if protocol is None:
        return progress.model_dump(mode="json")
    if protocol != LINKED_CONTEXT_PROTOCOL:
        raise ValueError("未识别的关联来源上下文协议")
    # No upstream counterpart (checked: linked source checkpoints, fixed
    # evidence navigation, captured Step requests): expose identity/navigation
    # here; the original basis stays in PG and full content stays in evidence.
    entries = []
    for entry in progress.entries:
        value = entry.model_dump(mode="json", exclude={"basis"})
        basis = entry.basis
        if isinstance(basis, DocumentContentBasis):
            parsed = basis.parse_snapshot
            value["basis"] = {
                "project_id": basis.project_id, "role": basis.role,
                "document_id": basis.version.document_id,
                "version_id": basis.version.id, "version": basis.version.version,
                "asset_id": basis.asset.id, "asset_kind": basis.asset.kind,
                "asset_sha256": basis.asset.sha256,
                "parse_snapshot_id": parsed.id if parsed else None,
                "parse_snapshot_sha256": parsed.sha256 if parsed else None,
                "expected_content_sha256": basis.expected_content_sha256,
                "warnings": list(basis.warnings),
            }
        else:
            value["basis"] = basis.model_dump(mode="json") if basis else None
        entries.append(value)
    return {"protocol": protocol, "entries": entries, "next_index": progress.next_index}


def linked_context_prompt_version(version, protocol):
    if protocol is None:
        return version
    if protocol != LINKED_CONTEXT_PROTOCOL:
        raise ValueError("未识别的关联来源上下文协议")
    return version + "+linked-navigation-v1"


def linked_context_prompts(stages):
    stages = stages.model_copy(deep=True)
    instruction = (
        "linked_source_reads使用固定来源导航协议：basis只提供项目/版本/解析快照/摘要身份、"
        "读取状态和警告；完整恢复快照由系统保存。正文从同一evidence_ids对应的已读evidence读取。"
        "read不能替代has_more的完整性检查；current_linked_source不是当时编写依据。"
        "错误或未读取来源不得假装已读，按原查询补读；不要根据缺少对象存储元数据推断文档内容缺失。"
    )
    stages.logic = instruction + stages.logic
    stages.effects = instruction + stages.effects
    if stages.effect_tasks is not None:
        stages.effect_tasks = {key: instruction + prompt for key, prompt in stages.effect_tasks.items()}
    return stages
