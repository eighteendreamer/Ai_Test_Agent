"""Read engine request facts, rather than treating console text as proof."""
from __future__ import annotations

import logging
import re
import csv
import io

from .engine_adapter import metric_number

logger = logging.getLogger(__name__)


def correlation_bindings(plan):
    """Existing API-task source identity, applied to ordered performance targets."""
    def index(name, default):
        if name is None:
            return default
        matches = [i for i, target in enumerate(plan.targets) if target.name == name]
        if len(matches) != 1:
            raise ValueError(f"关联目标必须唯一存在: {name}")
        return matches[0]

    bindings = []
    for i, correlation in enumerate(plan.correlations):
        if not plan.targets or not correlation.extract or not correlation.from_path:
            raise ValueError("响应关联必须提供目标、变量名和JSON路径")
        source = index(correlation.from_target, 0)
        destination = None
        if correlation.into_header:
            destination = index(correlation.into_target, source + 1)
            if not source < destination < len(plan.targets):
                raise ValueError("关联header消费目标必须在来源目标之后")
        elif correlation.into_target is not None or correlation.header_prefix is not None:
            raise ValueError("关联消费目标或header前缀需要into_header")
        bindings.append({"index": i, "source": source, "destination": destination,
            "correlation": correlation})
    return bindings


def request_validation(data, plan):
    result = {"status": "not_requested", "targets": [], "correlations": [],
        "checked_status": [], "extracted": {}, "missing_checks": [], "failed_checks": []}
    if plan.engine not in {"k6", "jmeter"}:
        result["status"] = "not_observed"
        result["extracted"] = {name: False for name in plan.smoke.expect_extract}
        return result
    if not plan.targets and not plan.correlations and not plan.smoke.expect_extract:
        return result
    if plan.engine == "jmeter":
        jtl = data.get("__jtl") if isinstance(data, dict) else None
        if not isinstance(jtl, str) or not jtl.strip():
            result["status"] = "not_observed"
            result["extracted"] = {name: False for name in plan.smoke.expect_extract}
            result["missing_checks"].append("jmeter_jtl")
            logger.warning("performance_jmeter_observation_unavailable", extra={"reason": "missing_jtl"})
            return result
        rows = list(csv.DictReader(io.StringIO(jtl)))
        bindings = correlation_bindings(plan)
        by_index = {i: [] for i in range(len(plan.targets))}
        for row in rows:
            label = str(row.get("label") or "")
            for i, target in enumerate(plan.targets):
                if label == target.name:
                    by_index[i].append(row)
        expected = set(str(status) for status in plan.smoke.expect_status)
        for i, target in enumerate(plan.targets):
            target_rows = by_index[i]
            statuses = sorted({int(row["responseCode"]) for row in target_rows if str(row.get("responseCode", "")).isdigit()})
            passes = sum(1 for row in target_rows if str(row.get("responseCode")) in expected)
            fails = len(target_rows) - passes
            if not target_rows:
                result["missing_checks"].append(f"status:{target.name}")
            elif fails:
                result["failed_checks"].append(f"qa_status_{i}")
            result["checked_status"].extend(statuses)
            result["targets"].append({"index": i, "name": target.name,
                "expected_status": plan.smoke.expect_status, "observed_status": statuses,
                "checks": [{"name": f"qa_status_{i}", "passes": passes, "fails": fails,
                    "passed": not fails}]})
        by_name = {}

        def jtl_check(name, target_rows):
            values = [row.get(name) for row in target_rows]
            passes = sum(value == "true" for value in values)
            fails = sum(value == "false" for value in values)
            if not values or passes + fails != len(values):
                result["missing_checks"].append(name)
            if fails:
                result["failed_checks"].append(name)
            return {"name": name, "passes": passes, "fails": fails,
                "passed": bool(values) and passes == len(values)}

        for binding in bindings:
            i = binding["index"]
            source_rows = by_index[binding["source"]]
            destination_rows = by_index[binding["destination"]] if binding["destination"] is not None else []
            extraction = jtl_check(f"qa_extract_{i}", source_rows)
            extract_passed = extraction["passed"]
            binding_passed = True
            detail = {"index": i, "name": binding["correlation"].extract,
                "from_target": plan.targets[binding["source"]].name, "from_path": binding["correlation"].from_path,
                "extraction": extraction}
            if binding["destination"] is not None:
                dependency = jtl_check(f"qa_dependency_{i}", destination_rows)
                binding_passed = dependency["passed"]
                detail.update({"into_target": plan.targets[binding["destination"]].name,
                    "into_header": binding["correlation"].into_header,
                    "binding": dependency})
            result["correlations"].append(detail)
            by_name.setdefault(binding["correlation"].extract, []).append(extract_passed and binding_passed)
        for name in plan.smoke.expect_extract:
            result["extracted"][name] = bool(by_name.get(name)) and all(by_name[name])
            if name not in by_name:
                result["missing_checks"].append(f"extract:{name}")
        result["checked_status"] = sorted(set(result["checked_status"]))
        result["status"] = "failed" if result["failed_checks"] else "missing" if result["missing_checks"] else "passed"
        logger.info("performance_jmeter_requests_validated", extra={"validation_status": result["status"],
            "target_count": len(result["targets"]), "correlation_count": len(bindings),
            "missing_checks": result["missing_checks"], "failed_checks": result["failed_checks"]})
        return result

    checks = {}

    def collect(group):
        if not isinstance(group, dict):
            return
        entries = group.get("checks", {})
        entries = list(entries.values()) if isinstance(entries, dict) else entries if isinstance(entries, list) else []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("name", "")).startswith("qa_"):
                checks[str(entry["name"])] = entry
        groups = group.get("groups", {})
        children = groups.values() if isinstance(groups, dict) else groups if isinstance(groups, list) else []
        for child in children:
            collect(child)

    collect(data.get("root_group", {}) if isinstance(data, dict) else {})

    def observation(name):
        raw = checks.get(name, {})
        try:
            passed, failed = metric_number(raw, "passes"), metric_number(raw, "fails")
            if passed is None or failed is None or passed < 0 or failed < 0 or passed % 1 or failed % 1 or passed + failed == 0:
                raise ValueError("check计数缺失或非法")
        except (ValueError, TypeError) as exc:
            logger.warning("performance_check_unavailable", extra={"check_name": name, "error": str(exc)})
            result["missing_checks"].append(name)
            return {"name": name, "passes": None, "fails": None, "passed": None}
        if failed:
            result["failed_checks"].append(name)
        return {"name": name, "passes": int(passed), "fails": int(failed), "passed": failed == 0}

    for i, target in enumerate(plan.targets):
        names = [name for name in checks if re.fullmatch(rf"qa_status_{i}_[0-9]+", name)]
        observations = [observation(name) for name in names] if names else [observation(f"qa_status_{i}")]
        statuses = sorted({int(name.rsplit("_", 1)[1]) for name in names})
        result["checked_status"].extend(statuses)
        result["targets"].append({"index": i, "name": target.name,
            "expected_status": plan.smoke.expect_status, "observed_status": statuses, "checks": observations})
    try:
        bindings = correlation_bindings(plan)
    except ValueError as exc:
        result["missing_checks"].append(str(exc))
        bindings = []
    by_name = {}
    for binding in bindings:
        i, correlation = binding["index"], binding["correlation"]
        extracted = observation(f"qa_extract_{i}")
        by_name.setdefault(correlation.extract, []).append(extracted["passed"])
        detail = {"index": i, "name": correlation.extract, "from_target": plan.targets[binding["source"]].name,
            "from_path": correlation.from_path, "extraction": extracted}
        if binding["destination"] is not None:
            detail.update({"into_target": plan.targets[binding["destination"]].name,
                "into_header": correlation.into_header, "binding": observation(f"qa_dependency_{i}")})
        result["correlations"].append(detail)
    for name in plan.smoke.expect_extract:
        result["extracted"][name] = bool(by_name.get(name)) and all(v is True for v in by_name[name])
        if name not in by_name:
            result["missing_checks"].append(f"extract:{name}")
    result["checked_status"] = sorted(set(result["checked_status"]))
    result["status"] = "failed" if result["failed_checks"] else "missing" if result["missing_checks"] else "passed"
    return result
