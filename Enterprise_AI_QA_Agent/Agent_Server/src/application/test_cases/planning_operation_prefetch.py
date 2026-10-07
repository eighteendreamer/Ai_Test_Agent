"""Read only the operation explicitly chosen in a frozen logical preparation."""
import logging
from pydantic import ValidationError

from src.schemas.case_planning import PlanningQuery

logger = logging.getLogger(__name__)

PREFETCH_PROTOCOL = "case-planning-declared-operation-prefetch-v1"
PREFETCH_PROMPT_VERSION = "case-planning-declared-operation-prefetch-v7"
# Existing EvidencePlanningDecision.queries admits eight reads per batch.
PREFETCH_QUERY_BATCH_SIZE = 8


def declared_operation_queries(decision):
    # No upstream counterpart (checked: captured default logical instructions,
    # fixed operation query service, durable query checkpoints and trajectory
    # course): queue exact declarations; never infer tools from business verbs.
    declared = {}
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
