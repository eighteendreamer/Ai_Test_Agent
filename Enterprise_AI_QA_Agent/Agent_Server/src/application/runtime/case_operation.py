"""Logical operations are independent of provider calls and do not imply effects."""
from copy import deepcopy
from dataclasses import dataclass
import logging
import re

from src.application.runtime.case_action_binding import (
    ARGUMENT, OPERATION_PROTOCOL, CaseOperationSelection, CaseConsumptionSelection, _digest, business_arguments,
)
from src.schemas.http_observation import HttpResponseObservation
from src.schemas.case_execution import (
    AgentCaseExecutionContext,
    CaseOperationVerificationDeclaration,
)

RECEIPT_KEY = "case_operation"
VERIFICATION_KEY = "case_operation_verification"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaseOperationRecoveryRequired:
    origin_job_id: str


def _verification_source(step) -> dict:
    if isinstance(step.content_json, dict):
        return step.content_json
    item = step.item
    instructions = getattr(item, "instructions", None)
    return instructions if isinstance(instructions, dict) else {}


def declared_operation_verification(
    context: AgentCaseExecutionContext,
    *,
    item_key: str,
    tool_key: str,
    operation_key: str,
    input_payload: dict,
) -> CaseOperationVerificationDeclaration | None:
    """Return the exact frozen read declaration for a call, if one exists.

    A declaration is optional. When present, malformed or mismatching data is
    an explicit contract error rather than a reason to guess a recovery URL.
    """
    step = next((entry for entry in context.steps if entry.item.key == item_key), None)
    if step is None:
        raise ValueError("查证操作引用的固定执行项不存在")
    raw = _verification_source(step).get("operation_verifications", [])
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("固定用例 operation_verifications 必须是数组")
    declarations = [CaseOperationVerificationDeclaration.model_validate(value) for value in raw]
    matches = [entry for entry in declarations if entry.read_operation_key == operation_key]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("固定用例查证操作键不唯一")
    declaration = matches[0]
    if declaration.tool_key != tool_key:
        raise ValueError("查证操作工具与固定用例声明不同")
    if str(input_payload.get("worker_action") or "") != declaration.operation:
        raise ValueError("查证操作名称与固定用例声明不同")
    task = input_payload.get("task")
    if not isinstance(task, dict):
        raise ValueError("查证操作缺少固定工具任务")
    method = str(task.get("method") or "").upper()
    if method != declaration.method:
        raise ValueError("查证操作方法与固定用例声明不同")
    if "request_body" in task and task.get("request_body") is not None:
        raise ValueError("只读查证操作不能携带请求体")
    path = str(task.get("full_url") or task.get("path") or "")
    template = re.escape(declaration.path_template)
    template = re.sub(r"\\\{[^{}]+\\\}", r"[^/?#]+", template)
    if re.fullmatch(template, path) is None:
        raise ValueError("查证操作路径与固定用例声明不同")
    return declaration


def operation_verification_receipt(*, declaration, origin, read_identity, read_job_id, action_sent):
    value = {
        "version": "case-operation-verification-v1",
        "declaration_id": declaration.declaration_id,
        "origin_job_id": origin["origin_job_id"],
        "origin_operation_id": origin["operation_id"],
        "origin_operation_key": declaration.origin_operation_key,
        "read_job_id": read_job_id,
        "read_operation_id": read_identity["operation_id"],
        "read_operation_key": declaration.read_operation_key,
        "action_sent": action_sent,
        "decision": "dispatched",
        "effect_present_statuses": list(declaration.effect_present_statuses),
        "effect_absent_statuses": list(declaration.effect_absent_statuses),
        "observation": None,
    }
    return {**value, "sha256": _digest(value)}


def classify_operation_verification(receipt: dict, output: dict, *, job_status: str) -> dict:
    """Classify one explicit read using only its persisted HTTP response.

    This intentionally has no default success or failure status. A frozen case
    must declare both semantics when it needs a determinate effect result.
    """
    observation = {
        "version": "case-operation-verification-observation-v1",
        "status": "inconclusive",
        "response_status": None,
        "reason": "",
    }
    if job_status != "completed":
        observation["reason"] = "read_operation_not_completed"
        return observation
    task_result = output.get("task_result") if isinstance(output, dict) else None
    if not isinstance(task_result, dict):
        observation["reason"] = "read_response_missing"
        return observation
    response_status = task_result.get("response_status")
    if isinstance(response_status, bool) or not isinstance(response_status, int):
        observation["reason"] = "read_response_status_missing"
        return observation
    observation["response_status"] = response_status
    present = set(receipt.get("effect_present_statuses") or [])
    absent = set(receipt.get("effect_absent_statuses") or [])
    if response_status in present:
        observation["status"] = "effect_present"
        observation["reason"] = "response_status_declared_present"
    elif response_status in absent:
        observation["status"] = "effect_absent"
        observation["reason"] = "response_status_declared_absent"
    else:
        observation["reason"] = "response_status_not_declared"
    return observation


def save_operation_verification_observation(receipt: dict, output: dict, *, job_status: str) -> dict:
    """Return a tamper-evident verification receipt with its read observation."""
    value = {key: deepcopy(item) for key, item in receipt.items() if key != "sha256"}
    value["observation"] = classify_operation_verification(receipt, output, job_status=job_status)
    return {**value, "sha256": _digest(value)}


def operation_identity(job, binding):
    raw = job.input_payload.get(ARGUMENT)
    selection_type = CaseConsumptionSelection if isinstance(raw, dict) and "consumption_closure" in raw else CaseOperationSelection
    selection = selection_type.model_validate(raw)
    scope = {"protocol":OPERATION_PROTOCOL, "operation_key":selection.operation_key,
        **{key:getattr(binding,key) for key in (
            "project_id", "run_id", "run_item_id", "plan_id", "attempt_id", "item_key", "context_sha256")}}
    return {"operation_id":_digest(scope), "scope":scope,
        "request_sha256":_digest({"tool_key":job.tool_key,
            "arguments":business_arguments(job.input_payload,binding)})}


def operation_receipt(identity, *, origin_job_id, decision, action_sent, output=None, origin_output=None):
    value = {"version":OPERATION_PROTOCOL, **identity, "origin_job_id":origin_job_id,
        "decision":decision, "action_sent":action_sent,"execution_claimed":decision == "dispatched"}
    if output is not None:
        value["replayed_output_sha256"] = _digest(output)
    if origin_output is not None:
        value["origin_output_sha256"] = _digest(origin_output)
    return {**value,"sha256":_digest(value)}


def replay_output(origin):
    output = deepcopy(origin.output_payload)
    # Only server-owned API task/campaign checkpoints are protocol locations.
    # Never search arbitrary response bodies for a key with the same spelling.
    def mark(checkpoint):
        if not isinstance(checkpoint,dict):
            return
        locations = [checkpoint,checkpoint.get("completed_request")]
        for location in locations:
            if not isinstance(location,dict) or "http_observation_v1" not in location:
                continue
            try:
                observation = HttpResponseObservation.model_validate(location["http_observation_v1"])
            except ValueError:
                # Preserve invalid original evidence; it cannot prove a network
                # observation, but must not block recovery of a returned output.
                logger.warning("case_operation_http_origin_unavailable",extra={"origin_job_id":origin.id})
                continue
            location["http_observation_v1"] = observation.model_copy(
                update={"acquisition":"completed_request"}).model_dump(mode="json")
    if origin.tool_key == "api-test-runner":
        task = output.get("task_result")
        if isinstance(task,dict):
            mark(task.get("execution_checkpoint"))
        mark(output.get("execution_checkpoint"))
        state = output.get("api_testing_state")
        if isinstance(state,dict):
            mark(state.get("execution_checkpoint"))
            campaign = state.get("campaign")
            if isinstance(campaign,dict):
                for task in campaign.get("tasks",[]):
                    if isinstance(task,dict):
                        mark(task.get("execution_checkpoint"))
    output["case_operation_replay"] = {"origin_job_id":origin.id,
        "origin_output_sha256":_digest(origin.output_payload), "action_sent":False}
    return output
