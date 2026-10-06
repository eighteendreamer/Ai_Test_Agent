"""Freeze registered interfaces for read-only planning queries."""
from copy import deepcopy

from src.application.runtime.case_action_binding import _digest
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import PlanningEvidence
from src.modes.api_testing_mode.campaign_state import ApiTestTask


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


def query_tool_contracts(contracts, request, limit):
    if contracts is None:
        raise ValueError("该固定规划没有工具接口快照")
    if request.kind == "tools":
        keys = [key for key, value in contracts.items() if request.query.lower() in
            (key + " " + value["name"] + " " + value["description"]).lower()]
        page = keys[request.offset:request.offset + limit]
        return [], {"tools": [{"key": key, "name": contracts[key]["name"]} for key in page],
            "has_more": len(keys) > request.offset + limit, "offset": request.offset}
    if request.source_id not in contracts:
        raise ValueError("固定工具接口中不存在该来源")
    payload = deepcopy(contracts[request.source_id])
    digest = _digest(payload)
    return [PlanningEvidence(id=f"tool:{request.source_id}:{digest}", kind="tool",
        source_id=request.source_id, version="registered-tool-contract-v1",
        content_hash=digest, payload=payload)], {"has_more": False}
