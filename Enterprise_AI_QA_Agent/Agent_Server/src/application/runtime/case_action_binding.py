"""Bind calls to owned frozen execution items, without changing business arguments."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.case_execution import AgentCaseExecutionContext, CaseConsumptionClosure
from src.schemas.scene_receipt import SceneConsumptionClosure, consumption_closure_type


PROTOCOL = "case-action-binding-v1"
ARGUMENT = "_case_action"
ADMISSION_PROTOCOL = "case-action-admission-v1"
OPERATION_PROTOCOL = "case-operation-v1"
CONSUMPTION_PROTOCOL = "case-consumption-phase-v1"


class CaseActionClaimUnavailable(RuntimeError):
    """A claim is not safely repeatable; preserve its original durable state."""


def action_admission_enabled(bundle: dict[str, Any]) -> bool:
    raw = bundle.get("case_execution")
    return isinstance(raw, dict) and raw.get("action_admission_protocol") == ADMISSION_PROTOCOL


class CaseActionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_key: str = Field(min_length=1, max_length=80)


class CaseOperationSelection(CaseActionSelection):
    operation_key: str = Field(min_length=1, max_length=160)


class CaseConsumptionSelection(CaseOperationSelection):
    consumption_closure: CaseConsumptionClosure | None = None


class SceneConsumptionSelection(CaseOperationSelection):
    consumption_closure: SceneConsumptionClosure | None = None


class CaseActionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["case-action-binding-v1"] = PROTOCOL
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    attempt_id: str
    session_id: str
    turn_id: str
    item_key: str
    phase: Literal["before", "case", "after"]
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_action_admission_receipt(job, binding, *, required=False):
    receipt = job.metadata.get("case_action_admission")
    if receipt is None:
        if required and job.started_at is not None:
            raise ValueError("已领取的新策略动作缺少原确认回执")
        return None  # Permission-denied calls can end before domain admission.
    if (not isinstance(receipt, dict) or receipt.get("version") != ADMISSION_PROTOCOL or
        receipt.get("tool_job_id") != job.id or receipt.get("binding") != binding.model_dump(mode="json") or
        receipt.get("sha256") != _digest({k:v for k,v in receipt.items() if k != "sha256"})):
        raise ValueError("原动作确认回执身份或SHA不同")
    if receipt.get("decision") == "explicit_invalid" and job.started_at is not None:
        raise ValueError("明确拒绝的原动作不能带业务领取时间")
    return receipt


def fixed_action_context(bundle: dict[str, Any]) -> AgentCaseExecutionContext | None:
    raw = bundle.get("case_execution")
    if not isinstance(raw, dict) or raw.get("action_binding_protocol") is None:
        return None
    if not bundle.get("case_execution_managed"):
        raise ValueError("执行项关联缺少服务器管理的固定运行身份")
    context = AgentCaseExecutionContext.model_validate(raw)
    if any(bundle.get(key) != getattr(context, key) for key in ("project_id", "run_id", "run_item_id")):
        raise ValueError("执行项关联与固定项目、运行或条目不一致")
    if len({step.item.key for step in context.steps}) != len(context.steps):
        raise ValueError("固定执行项不能重复")
    return context


def action_input_schema(schema: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    context = fixed_action_context(bundle)
    if context is None:
        return schema
    # No upstream counterpart (checked: Registry tools, graph executor,
    # DeepAgent governance and local trajectory course): frozen arbitrary JSON
    # needs explicit item ownership; HTTP methods and provider IDs cannot supply it.
    result = deepcopy(schema or {"type": "object", "properties": {}})
    properties = result.setdefault("properties", {})
    if ARGUMENT in properties:
        raise ValueError("工具业务字段与执行项关联字段冲突")
    properties[ARGUMENT] = {"type": "object", "additionalProperties": False,
        "properties": {"item_key": {"type": "string", "enum": [step.item.key for step in context.steps]}},
        "required": ["item_key"],
        "description": "Select the owned frozen execution item for this call. Its phase is server-derived."}
    if context.operation_protocol is not None:
        properties[ARGUMENT]["properties"]["operation_key"] = {
            "type":"string", "minLength":1, "maxLength":160,
            "description":"Stable logical operation key within the owned item. Preserve this key and all inputs "
                "when recovering an operation, including a new provider call ID. Use a different key for a "
                "deliberate new operation or read refresh. This is not a business endpoint idempotency key."}
        properties[ARGUMENT]["required"].append("operation_key")
    if context.consumption_protocol is not None:
        closure_schema = consumption_closure_type(context.scene_receipt_protocol).model_json_schema()
        definitions = closure_schema.pop("$defs", {})
        if set(definitions) & set(result.get("$defs", {})):
            raise ValueError("消费回执Schema与原工具定义冲突")
        result.setdefault("$defs", {}).update(definitions)
        properties[ARGUMENT]["properties"]["consumption_closure"] = closure_schema
        properties[ARGUMENT]["description"] += (
            " Before cleanup, attach consumption_closure with a reason and an evidence-only receipt for "
            "the current formal target's completed consumption phase. Failure is allowed if observed; "
            "unfinished or unknown actions cannot close. Closure forbids new ordinary actions in this owned "
            "phase but allows declared cleanup. It does not complete the case or run. Subsequent cleanup "
            "calls reuse the saved closure and need not repeat it.")
    result["required"] = list(dict.fromkeys([*result.get("required", []), ARGUMENT]))
    return result


def bind_case_action(*, bundle: dict[str, Any], arguments: dict[str, Any],
    session_id: str, turn_id: str) -> CaseActionBinding | None:
    context = fixed_action_context(bundle)
    if context is None:
        return None
    selection_type = ((SceneConsumptionSelection if context.scene_receipt_protocol else CaseConsumptionSelection) if context.consumption_protocol is not None else
        CaseOperationSelection if context.operation_protocol is not None else CaseActionSelection)
    selection = selection_type.model_validate(arguments.get(ARGUMENT))
    step = next((step for step in context.steps if step.item.key == selection.item_key), None)
    if step is None:
        raise ValueError("工具调用选择的执行项不属于当前固定条目")
    attempt = bundle.get("attempt_id")
    if not isinstance(attempt, str) or not attempt or not session_id or not turn_id:
        raise ValueError("执行项关联缺少原执行attempt、会话或turn身份")
    return CaseActionBinding(project_id=context.project_id, run_id=context.run_id,
        run_item_id=context.run_item_id, plan_id=context.plan_id, attempt_id=attempt,
        session_id=session_id, turn_id=turn_id, item_key=step.item.key, phase=step.phase,
        context_sha256=_digest(context.execution_json()), wire_arguments_sha256=_digest(arguments))


def business_arguments(arguments: dict[str, Any], binding: CaseActionBinding | None) -> dict[str, Any]:
    if binding is None:
        return arguments
    return deepcopy({key: value for key, value in arguments.items() if key != ARGUMENT})
