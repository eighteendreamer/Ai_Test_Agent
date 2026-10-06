"""Capture declared values at the original reliable-output transaction boundary."""
from copy import deepcopy
import logging
import json

from src.application.runtime.case_action_binding import _digest, fixed_action_context
from src.application.test_runs.literal_evidence import literal_value
from src.application.testing.verification_service import VerificationService

PROTOCOL = "case-effect-evidence-v1"
INTENT_KEY = "case_effect_evidence_intent"
logger = logging.getLogger(__name__)


def compile_declaration(item, expected_source=None):
    declaration = item.effect_evidence
    if declaration is None:
        return None
    sources = getattr(item, "cleanup_sources", None)
    if declaration.role == "cleanup" and not sources:
        raise ValueError("清理对象证据缺少固定来源范围")
    if declaration.role == "produced" and sources:
        raise ValueError("对象产生声明不能同时充当清理声明")
    checks = []
    source = item.instructions if item.kind == "prepare" else expected_source
    if source is None:
        raise ValueError("正式对象证据缺少完整固定用例")
    for check in declaration.checks:
        try:
            expected = literal_value(source, check.expected_path_tokens)
        except ValueError:
            raise ValueError(f"执行项 {item.key} 的对象证据预期路径不存在于完整固定依据") from None
        checks.append({**check.model_dump(mode="json"), "expected": expected})
    value = {**declaration.model_dump(mode="json"), "checks": checks,
        "source_keys": list(sources) if declaration.role == "cleanup" else [item.key]}
    if declaration.select_when is not None:
        value["select_when"] = []
        for selector in declaration.select_when:
            try:
                expected = literal_value(source, selector.expected_path_tokens)
            except ValueError:
                raise ValueError(f"执行项 {item.key} 的调用匹配预期缺少完整固定依据") from None
            value["select_when"].append({**selector.model_dump(mode="json"), "expected": expected})
    return value


def validate_effect_declarations(decision, protocol, evidence, coverage_protocol=None):
    items = {item.key: item for item in decision.items}
    for item in decision.items:
        if item.effect_evidence is None:
            continue
        if protocol != PROTOCOL:
            raise ValueError("对象证据声明需要启用固定对象证据策略")
        if item.effect_evidence.select_when is not None and coverage_protocol != "case-effect-coverage-v1":
            raise ValueError("调用匹配声明需要固定对象覆盖策略")
        source = None
        if item.kind != "prepare":
            source = next((ref.payload["content"] for ref in evidence if ref.kind == "case"
                and ref.source_id == item.case_id and ref.version == item.case_version_id), None)
        definition = compile_declaration(item, source)
        if definition["role"] == "cleanup":
            for source in definition["source_keys"]:
                producer = items.get(source)
                if (producer is None or producer.effect_evidence is None
                    or producer.effect_evidence.role != "produced"):
                    raise ValueError("清理对象证据缺少来源的固定对象产生声明")
    if coverage_protocol is not None:
        for item in decision.items:
            if getattr(item, "cleanup_sources", None) and (item.effect_evidence is None or item.effect_evidence.role != "cleanup"):
                raise ValueError("对象覆盖的清理来源需要固定清理证据声明")


def effect_intent(bundle, binding):
    context = fixed_action_context(bundle)
    if context is None or context.effect_evidence_protocol is None or binding is None:
        return None
    step = next(step for step in context.steps if step.item.key == binding.item_key)
    definition = compile_declaration(step.item, step.content_json)
    if definition is None:
        return None
    if "select_when" in definition and context.effect_coverage_protocol != "case-effect-coverage-v1":
        raise ValueError("调用匹配意图缺少固定对象覆盖策略")
    value = {"version": PROTOCOL, "binding": binding.model_dump(mode="json"), "definition": definition,
        "max_evidence_bytes": context.effect_evidence_max_bytes}
    return {**value, "sha256": _digest(value)}


def capture_returned_evidence(job, output, observation):
    intent = job.metadata.get(INTENT_KEY)
    if intent is None:
        return None
    if (intent.get("version") != PROTOCOL or intent.get("sha256") != _digest({k:v for k,v in intent.items() if k != "sha256"})
        or intent.get("binding") != job.metadata.get("case_action_binding")):
        raise ValueError("原对象证据意图与固定工具绑定或SHA不同")
    operation = job.metadata.get("case_operation", {})
    if operation.get("decision") != "dispatched" or operation.get("origin_job_id") != job.id:
        raise ValueError("对象证据只能来自原已领取逻辑操作，不能来自别名")
    if observation.get("output_hash") != _digest(output):
        raise ValueError("对象证据原返回SHA与可靠观察不同")
    definition = intent["definition"]
    selection = None
    if "select_when" in definition:
        # No upstream counterpart (checked: original effect observation, literal
        # input paths and cleanup research): fixed input equality distinguishes
        # operations; missing input is unknown, never a guessed exclusion.
        statuses = []
        for selector in definition["select_when"]:
            try:
                actual = literal_value(job.input_payload, selector["input_path_tokens"])
                statuses.append("matched" if _digest(actual) == _digest(selector["expected"]) else "different")
            except ValueError:
                statuses.append("missing")
        selection = ("not_applicable" if "different" in statuses else
            "selection_unknown" if "missing" in statuses else "matched")
    checks = []
    for check in definition["checks"]:
        try:
            actual = literal_value(output, check["output_path_tokens"])
            status = "passed" if VerificationService._matches_assertion(actual, check["expected"], check["operator"]) else "failed"
            resolved = {"present": True, "actual": actual, "status": status}
        except ValueError:
            resolved = {"present": False, "status": "missing"}
        checks.append({**deepcopy(check), **resolved})
    objects, resolution = [], "captured"
    try:
        value = literal_value(output, definition["object_path_tokens"])
        if definition["collection"] and not isinstance(value, list):
            resolution = "collection_shape_mismatch"
        else:
            values = value if definition["collection"] else [value]
            objects = [{"value": obj, "value_sha256": _digest(obj),
                "output_path_tokens": definition["object_path_tokens"] + ([str(index)] if definition["collection"] else [])}
                for index, obj in enumerate(values)]
    except ValueError:
        resolution = "object_path_missing"
    if selection in {"not_applicable", "selection_unknown"}:
        objects, checks, resolution = [], [], selection
    value = {"version": PROTOCOL, "origin_job_id": job.id, "operation_id": operation["operation_id"],
        "binding": deepcopy(intent["binding"]), "intent_sha256": intent["sha256"],
        "role": definition["role"], "source_keys": definition["source_keys"],
        "observation": deepcopy(observation), "resolution": resolution, "objects": objects, "checks": checks,
        "observed_object_count": len(objects),
        "checks_passed": bool(checks) and all(check["status"] == "passed" for check in checks)}
    if selection is not None:
        value["operation_selection"] = {"version": "case-effect-operation-selection-v1", "status": selection}
    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    if size > intent["max_evidence_bytes"]:
        value["resolution"] = "evidence_limit_exceeded"
        value["objects"], value["checks"], value["checks_passed"] = [], [], False
        value["required_evidence_bytes"] = size
        value["max_evidence_bytes"] = intent["max_evidence_bytes"]
        logger.warning("case_effect_evidence_limit_exceeded", extra={"tool_job_id": job.id,
            "required_evidence_bytes": size, "max_evidence_bytes": intent["max_evidence_bytes"]})
    logger.info("case_effect_returned_evidence_captured", extra={"tool_job_id": job.id,
        "run_id": intent["binding"]["run_id"], "role": definition["role"], "object_count": len(value["objects"]),
        "resolution": value["resolution"], "checks_passed": value["checks_passed"]})
    return {**value, "sha256": _digest(value)}
