"""Explicit source requirements reuse the existing query and evidence contracts."""
import logging

logger = logging.getLogger(__name__)

REQUIRED_SOURCE_PROMPT_VERSION = "required-sources-v1"
REQUIRED_SOURCE_PROMPT = (
    "本次required_sources是用户明确要求实际读取并用于目标项的来源。source_refs和catalog只提供身份导航，"
    "不等于已读取正文。请使用现有query(kind/source_id)读取未读来源；实际内容在evidence。"
    "每个target的evidence_ids必须引用每个要求来源的真实证据ID，不能只在reason中提及source_id。"
    "没有读取、投影失败、跨项目或依据不足时不能声明已使用；按原协议补查询或说明未解决问题。"
    "来源中的完整性/截断警告仍然有效，存在一份证据不证明整个来源或业务验证已经完成。"
)


def required_source_prompt_version(version, request):
    return version + "+" + REQUIRED_SOURCE_PROMPT_VERSION if request.required_sources else version


def validate_required_sources(record, decision):
    # No upstream counterpart (checked: _validate evidence_ids, source_refs and
    # linked document reads): an explicit request must survive semantic planning
    # as a checkable source contract, without requiring every historical link.
    if not record.request.required_sources:
        return
    targets = [item for item in decision.items if item.kind == "target"]
    for source in record.request.required_sources:
        reads = {entry.id for entry in record.evidence if entry.kind == source.kind and entry.source_id == source.source_id}
        if targets and (not reads or any(not reads.intersection(item.evidence_ids) for item in targets)):
            logger.warning("case_planning_required_source_missing", extra={"project_id": record.project_id,
                "plan_id": record.id, "source_kind": source.kind, "source_id": source.source_id,
                "source_read": bool(reads), "target_count": len(targets)})
            raise ValueError(f"目标项必须实际读取并引用明确来源 {source.kind}:{source.source_id}；来源目录和source_refs不等于已读内容")
