"""Freeze registered interfaces for read-only planning queries."""
from copy import deepcopy

from src.application.runtime.case_action_binding import _digest
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import PlanningEvidence
from src.modes.api_testing_mode.campaign_state import ApiTestTask

NAVIGATION_PROMPT_VERSION = "case-planning-effect-coverage-v2"


def tool_catalog(contracts, limit, *, navigation=False):
    values = [{"key": key, "name": value["name"]}
        | ({"detail_query": {"kind": "tool", "source_id": key}} if navigation else {})
        for key, value in list(contracts.items())[:limit]]
    result = {"tools": values, "has_more": len(contracts) > limit}
    if navigation:
        result["contract_loaded"] = False
    return result


def capture_tool_contracts():
    contracts = {tool.key: {"key": tool.key, "name": tool.name, "description": tool.description,
        "input_schema": deepcopy(tool.input_schema), "output_schema": deepcopy(tool.output_schema)}
        for tool in ToolRegistry().list()}
    # Actual runtime._execute_dispatched_task returns this native model under
    # task_result. The registered campaign output alone omits this variant.
    contracts["api-test-runner"]["output_variants"] = [{"when": {"worker_action": "execute_task"},
        "wrapper": "task_result", "schema": ApiTestTask.model_json_schema(),
        "basis": "api_testing_mode.runtime._execute_dispatched_task"}]
    return contracts


def query_tool_contracts(contracts, request, limit, *, navigation=False):
    if contracts is None:
        raise ValueError("该固定规划没有工具接口快照")
    if request.kind == "tools":
        keys = [key for key, value in contracts.items() if request.query.lower() in
            (key + " " + value["name"] + " " + value["description"]).lower()]
        page = keys[request.offset:request.offset + limit]
        metadata = {"tools": [{"key": key, "name": contracts[key]["name"]}
            | ({"detail_query": {"kind": "tool", "source_id": key}} if navigation else {}) for key in page],
            "has_more": len(keys) > request.offset + limit, "offset": request.offset}
        if navigation:
            metadata["contract_loaded"] = bool(request.query.strip() and len(keys) == 1 and page)
            if metadata["contract_loaded"]:
                return [_tool_evidence(contracts, page[0])], metadata
        return [], metadata
    if request.source_id not in contracts:
        raise ValueError("固定工具接口中不存在该来源")
    return [_tool_evidence(contracts, request.source_id)], {"has_more": False}


def _tool_evidence(contracts, key):
    payload = deepcopy(contracts[key])
    digest = _digest(payload)
    return PlanningEvidence(id=f"tool:{key}:{digest}", kind="tool",
        source_id=key, version="registered-tool-contract-v1", content_hash=digest, payload=payload)
