"""Freeze registered interfaces for read-only planning queries."""
from copy import deepcopy

from src.application.runtime.case_action_binding import _digest
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import PlanningEvidence
from src.modes.api_testing_mode.campaign_state import ApiTestTask

NAVIGATION_PROMPT_VERSION = "case-planning-effect-coverage-v2"
OPERATION_PROMPT_VERSION = "case-planning-tool-operations-v3"


def tool_catalog(contracts, limit, *, navigation=False, operations=None):
    values = [{"key": key, "name": value["name"]}
        | ({"detail_query": {"kind": "tool", "source_id": key}} if navigation else {})
        for key, value in list(contracts.items())[:limit]]
    result = {"tools": values, "has_more": len(contracts) > limit}
    if operations is not None:
        for value in values:
            value["operations"] = [{"operation": name, "detail_query": {
                "kind": "tool", "source_id": value["key"], "operation": name}}
                for name in operations.get(value["key"], {})]
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


def capture_tool_operations(contracts):
    """Freeze a named native interface; keep the complete descriptor separate."""
    contract = contracts["api-test-runner"]
    variant = next(value for value in contract["output_variants"]
        if value["when"] == {"worker_action": "execute_task"})
    properties = contract["input_schema"]["properties"]
    # The original runtime consumes the same model for task and returns it under
    # task_result; keep every model field and definition once, with explicit refs.
    # No upstream counterpart (checked: fixed tool evidence, native API runtime,
    # Agent trajectory course): named model refs describe this operation view,
    # not a replacement schema for the complete registered tool.
    model = deepcopy(variant["schema"])
    operation = {"scope": "named-operation", "tool_key": contract["key"],
        "tool_name": contract["name"], "operation": "execute_task",
        "parent_contract_hash": _digest(contract), "when": deepcopy(variant["when"]),
        "basis": variant["basis"], "models": {"ApiTestTask": model},
        "input": {"required": ["worker_action"] if "dispatch_ref" in properties else ["worker_action", "task"],
            **({"anyOf": [{"required": ["task"]}, {"required": ["dispatch_ref"]}]} if "dispatch_ref" in properties else {}), "properties": {
            "worker_action": {"const": "execute_task"}, "task": {"model_ref": "ApiTestTask"},
            **({"dispatch_ref": deepcopy(properties["dispatch_ref"])} if "dispatch_ref" in properties else {}),
            "credential_session": deepcopy(properties["credential_session"]),
            "auth_token_field": deepcopy(properties["auth_token_field"]),
            "execution_checkpoint": {"type": "object", "description": "Optional original task execution checkpoint."}}},
        "output": {"returned": {"status": "completed", "ok": "task_result.status == completed",
            "worker_kind": "api_test_task_execution", "task_result": {"model_ref": "ApiTestTask"},
            "idempotency_key": "string", "summary": "string", "credential_session": "object or null",
            **({"credential_ref": "opaque reference (registered Worker)"} if "dispatch_ref" in properties else {})},
            "known_validation_failure": {"status": "failed", "ok": False,
                "errors": ["missing_task_payload", "invalid_task_payload"],
                "task_result_present": False, "HTTP_sent": False}},
        "coverage": "execute_task only; Campaign interface remains available through full tool query"}
    return {contract["key"]: {"execute_task": operation}}


def query_tool_contracts(contracts, request, limit, *, navigation=False, operations=None):
    if contracts is None:
        raise ValueError("该固定规划没有工具接口快照")
    if request.operation is not None:
        if request.source_id not in contracts:
            raise ValueError("固定工具接口中不存在该来源")
        payload = (operations or {}).get(request.source_id, {}).get(request.operation)
        if payload is None:
            raise ValueError("该固定规划没有指定操作契约；完整tool读取不替代操作读取")
        if payload["parent_contract_hash"] != _digest(contracts[request.source_id]):
            raise ValueError("操作契约与固定父工具接口不同")
        payload = deepcopy(payload)
        digest = _digest(payload)
        return [PlanningEvidence(id=f"tool-operation:{request.source_id}:{request.operation}:{digest}", kind="tool",
            source_id=f"{request.source_id}:{request.operation}", version="registered-tool-operation-v1", content_hash=digest, payload=payload)], {
                "has_more": False, "contract_loaded": True, "scope": "named-operation", "operation": request.operation}
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
