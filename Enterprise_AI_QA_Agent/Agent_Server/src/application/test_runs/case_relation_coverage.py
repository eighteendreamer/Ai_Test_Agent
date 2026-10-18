"""Project all original multi-effect observations onto fixed source relations."""
from copy import deepcopy
import json

from src.application.runtime.case_action_binding import _digest
from src.infrastructure.storage_utils import ensure_utc_datetime

PROTOCOL = "case-effect-coverage-v2"


def _declaration(row, declaration_id):
    event = row["returned_evidence"]
    if event is None:
        return None
    if event["version"] != "case-effect-evidence-v2":
        raise ValueError("多效果覆盖的原返回协议不同")
    entries = [entry for entry in event["declarations"] if entry["declaration_id"] == declaration_id]
    if len(entries) > 1:
        raise ValueError("原返回的多效果声明身份重复")
    return entries[0] if entries else None


def _consumption_closure_proofs(context, item, rows):
    if context.consumption_protocol != "case-consumption-phase-v1":
        return []
    scopes = [scope for scope in (context.preparation_scopes or {}).values()
        if item.key in scope.cleanup_keys]
    expected_sources = {scope.source_key for scope in scopes}
    expected_consumers = {key for scope in scopes for key in scope.consumer_keys}
    proofs = {}
    for row in rows:
        if row["binding"].get("item_key") != item.key:
            continue
        proof = row.get("consumption_closure")
        if proof is None:
            continue
        consumers = proof.get("consumers")
        if (proof.get("version") != "case-consumption-phase-v1"
            or proof.get("scope") != "explicit_completed_consumption_phase"
            or proof.get("run_item_completed") is not False
            or set(proof.get("source_keys", [])) != expected_sources
            or not isinstance(consumers, list)):
            raise ValueError("消费关闭证明与固定清理范围不同")
        consumer_keys = {entry.get("item_key") for entry in consumers if isinstance(entry, dict)}
        if consumer_keys != expected_consumers:
            raise ValueError("消费关闭证明没有覆盖固定消费者集合")
        for entry in consumers:
            if entry.get("run_item_id") != context.effect_owner_item_ids.get(entry["item_key"]):
                raise ValueError("消费关闭证明的消费者拥有者与固定运行不同")
            if entry["item_key"] == context.target_key:
                if not isinstance(entry.get("closure_sha256"), str):
                    raise ValueError("当前目标的消费关闭证明缺少关闭摘要")
            elif (entry.get("status") not in {"passed", "failed", "error", "blocked", "skipped", "cancelled"}
                or not entry.get("result_id") or not entry.get("result_sha256")):
                raise ValueError("共享消费者的终态结果证明不完整")
        closure_sha256 = proof.get("closure_sha256")
        if not isinstance(closure_sha256, str) or len(closure_sha256) != 64:
            raise ValueError("消费关闭证明缺少有效关闭摘要")
        proofs[closure_sha256] = deepcopy(proof)
    return list(proofs.values())


def relation_cleanup_coverage(context, inventory):
    if context.effect_coverage_protocol != PROTOCOL:
        raise ValueError("多效果覆盖缺少固定协议")
    for row in inventory:
        if any(row["binding"].get(key) != getattr(context, key) for key in ("project_id", "run_id", "plan_id")):
            raise ValueError("多效果原操作不属于固定项目、运行或计划")
    cleanups = {}
    for step in context.steps:
        item = step.item
        declarations = [entry for entry in item.effect_declarations or [] if entry.role == "cleanup"]
        if not declarations:
            continue
        objects, covered, uncovered, unknown, excluded, failed = [], [], [], [], [], []
        sources = {(source.item_key, source.declaration_id): source.model_dump(mode="json")
            for declaration in declarations for source in declaration.sources}
        confirmed = {identity: [] for identity in sources}
        for identity, source in sources.items():
            scope = (context.preparation_scopes or {}).get(source["item_key"])
            if scope is None or item.key not in scope.cleanup_keys:
                raise ValueError("多效果清理不属于原固定来源范围")
            source_rows = [row for row in inventory if row["binding"]["item_key"] == source["item_key"]]
            applicable = False
            for row in source_rows:
                if row["binding"]["run_item_id"] != context.effect_owner_item_ids[scope.source_owner_key]:
                    raise ValueError("多效果产生证据与固定拥有者不同")
                entry = _declaration(row, source["declaration_id"])
                ref = {"source": deepcopy(source), "origin_job_id": row["origin_job_id"]}
                if entry is not None and entry["resolution"] == "not_applicable":
                    excluded.append(ref)
                    continue
                applicable = True
                if (entry is None or entry["resolution"] != "captured" or not entry["checks_passed"]
                    or entry["role"] != "produced" or entry["sources"]):
                    unknown.append({**ref, "reason": "source_without_confirmed_object_evidence",
                        "resolution": entry["resolution"] if entry else row["evidence_state"]})
                    continue
                envelope = row["returned_evidence"]
                objects.extend({**ref, **deepcopy(obj), "observation": deepcopy(envelope["observation"]),
                    "source_evidence_sha256": envelope["sha256"]} for obj in entry["objects"])
            if not applicable:
                unknown.append({"source": deepcopy(source), "reason": "no_applicable_source_operations"})
        for declaration in declarations:
            declared_sources = [source.model_dump(mode="json") for source in declaration.sources]
            for row in (row for row in inventory if row["binding"]["item_key"] == item.key):
                if row["binding"]["run_item_id"] != context.run_item_id:
                    raise ValueError("多效果清理证据与当前拥有者不同")
                entry = _declaration(row, declaration.declaration_id)
                ref = {"origin_job_id": row["origin_job_id"], "cleanup_declaration_id": declaration.declaration_id}
                if entry is not None and entry["resolution"] == "not_applicable":
                    excluded.append(ref)
                elif entry is None or entry["resolution"] != "captured":
                    unknown.append({**ref, "reason": "cleanup_without_reliable_objects"})
                elif entry["role"] != "cleanup" or entry["sources"] != declared_sources:
                    raise ValueError("多效果原清理声明与固定来源不同")
                elif not entry["checks_passed"]:
                    failed.append({**ref, "evidence_sha256": row["returned_evidence"]["sha256"], "checks": deepcopy(entry["checks"])})
                else:
                    for source in declaration.sources:
                        confirmed[(source.item_key, source.declaration_id)].append((row["returned_evidence"], entry))
        for obj in objects:
            source = obj["source"]
            matches = [{"origin_job_id": event["origin_job_id"], "evidence_sha256": event["sha256"],
                "declaration_id": entry["declaration_id"], "output_path_tokens": value["output_path_tokens"]}
                for event, entry in confirmed[(source["item_key"], source["declaration_id"])]
                if ensure_utc_datetime(event["observation"]["observed_at"]) >= ensure_utc_datetime(obj["observation"]["observed_at"])
                for value in entry["objects"] if value["value_sha256"] == obj["value_sha256"]]
            (covered if matches else uncovered).append({**obj, "cleanup_evidence": matches})
        status = "failed" if failed else "blocked" if unknown or uncovered else "passed"
        consumption_closures = _consumption_closure_proofs(context, item, inventory)
        cleanups[item.key] = {"status": status, "source_keys": list(item.cleanup_sources or []),
            "covered": covered, "uncovered": uncovered, "unknown": unknown, "excluded_operations": excluded,
            "failed_cleanup_checks": failed, "known_object_count": len(objects), "covered_object_count": len(covered),
            "scope": "declared_original_object_evidence", "consumption_completion_verified": bool(consumption_closures),
            "consumption_closures": consumption_closures}
    result = {"version": PROTOCOL, "project_id": context.project_id, "run_id": context.run_id, "plan_id": context.plan_id,
        "inventory_sha256": _digest(inventory), "original_operation_count": len(inventory), "cleanups": cleanups,
        "consumption_completion_verified": bool(cleanups) and all(
            value["consumption_completion_verified"] for value in cleanups.values()),
        "complete_inventory_available": True}
    size = len(json.dumps({"inventory": inventory, "coverage": result}, ensure_ascii=False,
        sort_keys=True, allow_nan=False, separators=(",", ":")).encode())
    if size > context.effect_coverage_max_bytes:
        result.update(complete_inventory_available=False, required_bytes=size, max_bytes=context.effect_coverage_max_bytes)
        result["cleanups"] = {key: {"status": "blocked", "reason": "inventory_limit_exceeded",
            "source_keys": value["source_keys"], "known_object_count": value["known_object_count"],
            "consumption_completion_verified": False} for key, value in cleanups.items()}
    return result
