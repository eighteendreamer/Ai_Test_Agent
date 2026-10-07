"""Versioned multi-effect declarations over one original reliable tool return."""
from copy import deepcopy
import json
import logging

from src.application.runtime.case_action_binding import _digest
from src.application.test_runs.literal_evidence import literal_value
from src.application.testing.verification_service import VerificationService
from src.application.runtime.case_effect_expected_sources import document_expected_value
from src.schemas.case_effect_sources import SOURCE_PROTOCOL

PROTOCOL = "case-effect-evidence-v2"
logger = logging.getLogger(__name__)


def compile_relations(item, expected_source, evidence=(), expected_source_protocol=None):
    if getattr(item, "effect_schema", None) not in {2, 3}:
        raise ValueError("多效果证据需要固定v2执行项")
    if item.effect_schema == 3 and expected_source_protocol != SOURCE_PROTOCOL:
        raise ValueError("文档预期执行项缺少固定来源协议")
    source = item.instructions if item.kind == "prepare" else expected_source
    definitions = []
    for declaration in item.effect_declarations or []:
        if source is None:
            raise ValueError("多效果声明缺少完整固定用例依据")
        definition = declaration.model_dump(mode="json")
        for field in ("checks", "select_when"):
            for entry in definition[field]:
                if entry.get("expected_source") is not None:
                    entry["expected"], entry["expected_source_proof"] = document_expected_value(entry["expected_source"], item, evidence)
                    continue
                try:
                    entry["expected"] = literal_value(source, entry["expected_path_tokens"])
                except ValueError:
                    raise ValueError(f"执行项 {item.key} 声明 {declaration.declaration_id} 的固定预期路径不存在") from None
        definitions.append(definition)
    return definitions


def validate_relations(decision, evidence, coverage_protocol, expected_source_protocol=None):
    if coverage_protocol != "case-effect-coverage-v2":
        raise ValueError("多效果声明需要固定v2覆盖协议")
    items = {item.key: item for item in decision.items}
    declarations = {}
    for item in items.values():
        source = next((ref.payload["content"] for ref in evidence if ref.kind == "case"
            and ref.source_id == item.case_id and ref.version == item.case_version_id), None) if item.kind != "prepare" else None
        for definition in compile_relations(item, source, evidence, expected_source_protocol):
            identity = (item.key, definition["declaration_id"])
            if identity in declarations:
                raise ValueError("同一执行项的多效果声明身份重复")
            declarations[identity] = definition
    for item in items.values():
        actual_sources = set()
        for (item_key, _), definition in declarations.items():
            if item_key != item.key:
                continue
            for source in definition["sources"]:
                producer = declarations.get((source["item_key"], source["declaration_id"]))
                if producer is None or producer["role"] != "produced":
                    raise ValueError("多效果清理缺少固定产生声明")
                actual_sources.add(source["item_key"])
        if actual_sources != set(item.cleanup_sources or []):
            raise ValueError("多效果清理声明与固定逻辑来源不一致")


def relation_intent(context, binding):
    step = next(step for step in context.steps if step.item.key == binding.item_key)
    definitions = compile_relations(step.item, step.content_json, context.evidence, context.effect_expected_source_protocol)
    if not definitions:
        return None
    value = {"version": PROTOCOL, "binding": binding.model_dump(mode="json"),
        "definitions": definitions, "max_evidence_bytes": context.effect_evidence_max_bytes}
    if context.effect_expected_source_protocol:
        value["expected_source_protocol"] = context.effect_expected_source_protocol
    return {**value, "sha256": _digest(value)}


def _observe_definition(definition, job, output):
    statuses = []
    for selector in definition["select_when"]:
        try:
            actual = literal_value(job.input_payload, selector["input_path_tokens"])
            statuses.append("matched" if _digest(actual) == _digest(selector["expected"]) else "different")
        except ValueError:
            statuses.append("missing")
    selection = "not_applicable" if "different" in statuses else "selection_unknown" if "missing" in statuses else "matched"
    objects, checks, resolution = [], [], selection
    if selection == "matched":
        resolution = "captured"
        for check in definition["checks"]:
            try:
                actual = literal_value(output, check["output_path_tokens"])
                status = "passed" if VerificationService._matches_assertion(actual, check["expected"], check["operator"]) else "failed"
                observed = {"present": True, "actual": actual, "status": status}
            except ValueError:
                observed = {"present": False, "status": "missing"}
            checks.append({**deepcopy(check), **observed})
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
    result = {"declaration_id": definition["declaration_id"], "role": definition["role"],
        "sources": deepcopy(definition["sources"]), "resolution": resolution,
        "objects": objects, "observed_object_count": len(objects), "checks": checks,
        "checks_passed": bool(checks) and all(check["status"] == "passed" for check in checks),
        "operation_selection": {"version": "case-effect-operation-selection-v1", "status": selection}}
    proofs = [entry["expected_source_proof"] for entry in definition["select_when"] if "expected_source_proof" in entry]
    if proofs:
        result["operation_selection"]["expected_source_proofs"] = deepcopy(proofs)
    return result


def capture_relation_return(job, output, observation):
    intent = job.metadata["case_effect_evidence_intent"]
    if (intent.get("version") != PROTOCOL or intent.get("sha256") != _digest({k:v for k,v in intent.items() if k != "sha256"})
        or intent.get("binding") != job.metadata.get("case_action_binding")):
        raise ValueError("多效果意图与原执行绑定或SHA不同")
    operation = job.metadata.get("case_operation", {})
    if operation.get("decision") != "dispatched" or operation.get("origin_job_id") != job.id:
        raise ValueError("多效果证据只能来自原已领取操作")
    if observation.get("output_hash") != _digest(output):
        raise ValueError("多效果原返回SHA与可靠观察不同")
    entries = [_observe_definition(definition, job, output) for definition in intent["definitions"]]
    # No upstream counterpart (checked: v1 observation, native tool contract and
    # captured mixed formal-case gap): never classify one literal value as two
    # opposite roles without separate returned fields. Keep observed facts.
    for entry in entries:
        conflicts = [other["declaration_id"] for other in entries if other["role"] != entry["role"]
            and any(left["output_path_tokens"] == right["output_path_tokens"]
                for left in entry["objects"] for right in other["objects"])]
        if conflicts:
            entry.update(resolution="role_conflict", checks_passed=False, conflicting_declaration_ids=conflicts)
    value = {"version": PROTOCOL, "origin_job_id": job.id, "operation_id": operation["operation_id"],
        "binding": deepcopy(intent["binding"]), "intent_sha256": intent["sha256"],
        "observation": deepcopy(observation), "declarations": entries}
    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode())
    if size > intent["max_evidence_bytes"]:
        value.update(required_evidence_bytes=size, max_evidence_bytes=intent["max_evidence_bytes"])
        for entry in entries:
            entry.update(resolution="evidence_limit_exceeded", objects=[], checks=[], checks_passed=False)
        logger.warning("case_effect_relation_evidence_limit_exceeded", extra={"tool_job_id": job.id,
            "required_evidence_bytes": size, "max_evidence_bytes": intent["max_evidence_bytes"]})
    logger.info("case_effect_relation_return_captured", extra={"tool_job_id": job.id,
        "run_id": intent["binding"]["run_id"], "declaration_count": len(entries),
        "resolutions": [entry["resolution"] for entry in entries]})
    return {**value, "sha256": _digest(value)}
