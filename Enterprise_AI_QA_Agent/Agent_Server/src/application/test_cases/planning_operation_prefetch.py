"""Read only the operation explicitly chosen in a frozen logical preparation."""
import logging
from pydantic import ValidationError

from src.schemas.case_planning import PlanningQuery

logger = logging.getLogger(__name__)

PREFETCH_PROTOCOL = "case-planning-declared-operation-prefetch-v1"
PREFETCH_PROMPT_VERSION = "case-planning-declared-operation-prefetch-v7"
ITEM_PREFETCH_PROTOCOL = "case-planning-item-operation-prefetch-v2"
ITEM_PREFETCH_PROMPT_VERSION = "case-planning-item-operation-prefetch-v9"
# Existing EvidencePlanningDecision.queries admits eight reads per batch.
PREFETCH_QUERY_BATCH_SIZE = 8


def validate_operation_selections(decision, selections, evidence):
    items = {item.key: item for item in decision.items}
    available = {entry.id: entry for entry in evidence}
    for entry in selections:
        item = items.get(entry.item_key)
        if item is None:
            raise ValueError("操作选择引用了不存在的执行项")
        if any(ref not in item.evidence_ids or ref not in available or available[ref].kind == "tool"
               for ref in entry.evidence_ids):
            raise ValueError("操作选择必须引用该执行项已经读取的业务依据")


def declared_operation_queries(decision, selections=None):
    # No upstream counterpart (checked: captured default logical instructions,
    # fixed operation query service, durable query checkpoints and trajectory
    # course): queue exact declarations; never infer tools from business verbs.
    declared = {}
    if selections is not None:
        # No upstream counterpart (checked: original formal-only default plan,
        # v1 preparation reads and fixed tool query service): explicit metadata
        # reads contracts for any item without rewriting its business JSON.
        for selection in selections:
            key = (selection.tool_key, selection.operation)
            entry = declared.setdefault(key, {"item_keys": [], "evidence_ids": []})
            entry["item_keys"] = list(dict.fromkeys([*entry["item_keys"], selection.item_key]))
            entry["evidence_ids"] = list(dict.fromkeys([*entry["evidence_ids"], *selection.evidence_ids]))
        queries = [PlanningQuery(kind="tool", source_id=tool, operation=operation) for tool, operation in declared]
        return queries, [{"query": query.model_dump(mode="json"), **entry}
                         for query, entry in zip(queries, declared.values())]
    for item in decision.items:
        if item.kind != "prepare":
            continue
        tool, operation = item.instructions.get("tool"), item.instructions.get("operation")
        if not isinstance(tool, str) or not tool or not isinstance(operation, str) or not operation:
            continue
        declared.setdefault((tool, operation), []).append(item.key)
    queries, origins = [], []
    for (tool, operation), keys in declared.items():
        try:
            query = PlanningQuery(kind="tool", source_id=tool, operation=operation)
        except ValidationError:
            logger.warning("case_planning_operation_prefetch_unaddressable", extra={"item_keys": keys,
                "tool_name_chars": len(tool), "operation_name_chars": len(operation)})
            continue
        queries.append(query)
        origins.append({"query": query.model_dump(mode="json"), "item_keys": keys})
    return queries, origins
