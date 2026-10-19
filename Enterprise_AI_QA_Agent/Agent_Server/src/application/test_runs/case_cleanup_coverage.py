"""Project all original observations onto explicit frozen cleanup scope."""
from copy import deepcopy
import json

from src.application.runtime.case_action_binding import _digest
from src.infrastructure.storage_utils import ensure_utc_datetime

PROTOCOL = "case-effect-coverage-v1"


def cleanup_coverage(context, inventory):
    if context.effect_coverage_protocol == "case-effect-coverage-v2":
        from src.application.test_runs.case_relation_coverage import relation_cleanup_coverage
        return relation_cleanup_coverage(context, inventory)
    if context.effect_coverage_protocol != PROTOCOL:
        raise ValueError("对象覆盖缺少固定协议")
    # No upstream counterpart (checked: assessment, original effect store and
    # real cleanup failures): coverage reads every origin, never receipt outputs.
    for row in inventory:
        binding = row["binding"]
        if any(binding.get(k) != getattr(context, k) for k in ("project_id", "run_id", "plan_id")):
            raise ValueError("对象覆盖来源不属于本次项目、运行或固定计划")
    cleanups = {}
    for step in context.steps:
        item = step.item
        sources = getattr(item, "cleanup_sources", None)
        if not sources:
            continue
        objects, unknown, excluded = [], [], []
        for source in sources:
            scope = (context.preparation_scopes or {}).get(source)
            if scope is None or item.key not in scope.cleanup_keys:
                raise ValueError("清理项不属于固定来源范围")
            rows = [r for r in inventory if r["binding"]["item_key"] == source]
            if not rows:
                unknown.append({"source_key": source, "reason": "no_original_source_operations"})
            applicable = False
            for row in rows:
                if row["binding"]["run_item_id"] != context.effect_owner_item_ids[scope.source_owner_key]:
                    raise ValueError("原对象来源与固定拥有者不同")
                event = row["returned_evidence"]
                ref = {"source_key": source, "origin_job_id": row["origin_job_id"]}
                if event is not None and event["resolution"] in {"not_applicable", "not_executed"}:
                    excluded.append(ref if event["resolution"] == "not_applicable" else {
                        **ref, "reason": "not_executed", "non_execution": deepcopy(event["observation"]["non_execution"])})
                    continue
                applicable = True
                if (event is None or event["resolution"] != "captured" or not event["checks_passed"]
                    or event["role"] != "produced" or event["source_keys"] != [source]):
                    unknown.append({**ref, "reason": "source_without_confirmed_object_evidence",
                        "resolution": event["resolution"] if event else row["evidence_state"]})
                    continue
                for obj in event["objects"]:
                    objects.append({**ref, **deepcopy(obj), "observation": deepcopy(event["observation"]),
                        "source_evidence_sha256": event["sha256"]})
            if rows and not applicable:
                unknown.append({"source_key": source, "reason": "no_applicable_source_operations"})
        candidates = [r for r in inventory if r["binding"]["item_key"] == item.key]
        confirmed, failed = [], []
        for row in candidates:
            if row["binding"]["run_item_id"] != context.run_item_id:
                raise ValueError("清理证据与当前拥有者不同")
            event = row["returned_evidence"]
            ref = {"origin_job_id": row["origin_job_id"]}
            if event is not None and event["resolution"] in {"not_applicable", "not_executed"}:
                excluded.append(ref if event["resolution"] == "not_applicable" else {
                    **ref, "reason": "not_executed", "non_execution": deepcopy(event["observation"]["non_execution"])})
            elif event is None or event["resolution"] != "captured":
                unknown.append({**ref, "reason": "cleanup_without_reliable_objects"})
            elif event["role"] != "cleanup" or event["source_keys"] != list(sources):
                raise ValueError("原清理声明与固定来源不同")
            elif not event["checks_passed"]:
                failed.append({**ref, "evidence_sha256": event["sha256"], "checks": deepcopy(event["checks"])})
            else:
                confirmed.append(event)
        covered, uncovered = [], []
        for obj in objects:
            matches = [{"origin_job_id": e["origin_job_id"], "evidence_sha256": e["sha256"],
                "output_path_tokens": value["output_path_tokens"]} for e in confirmed
                if obj["source_key"] in e["source_keys"]
                and ensure_utc_datetime(e["observation"]["observed_at"]) >= ensure_utc_datetime(obj["observation"]["observed_at"])
                for value in e["objects"] if value["value_sha256"] == obj["value_sha256"]]
            (covered if matches else uncovered).append({**obj, "cleanup_evidence": matches})
        status = "failed" if failed else "blocked" if unknown or uncovered else "passed"
        cleanups[item.key] = {"status": status, "source_keys": list(sources), "covered": covered,
            "uncovered": uncovered, "unknown": unknown, "excluded_operations": excluded,
            "failed_cleanup_checks": failed, "known_object_count": len(objects),
            "covered_object_count": len(covered), "scope": "declared_original_object_evidence",
            "consumption_completion_verified": False}
    result = {"version": PROTOCOL, "project_id": context.project_id, "run_id": context.run_id,
        "plan_id": context.plan_id, "inventory_sha256": _digest(inventory),
        "original_operation_count": len(inventory), "cleanups": cleanups,
        "consumption_completion_verified": False, "complete_inventory_available": True}
    size = len(json.dumps({"inventory": inventory, "coverage": result}, ensure_ascii=False,
        sort_keys=True, allow_nan=False, separators=(",", ":")).encode())
    if size > context.effect_coverage_max_bytes:
        result["complete_inventory_available"] = False
        result["required_bytes"], result["max_bytes"] = size, context.effect_coverage_max_bytes
        result["cleanups"] = {key: {"status": "blocked", "reason": "inventory_limit_exceeded",
            "source_keys": value["source_keys"], "known_object_count": value["known_object_count"],
            "consumption_completion_verified": False} for key, value in cleanups.items()}
    return result
