"""Stable field selectors over complete actually delivered fixed case versions."""
from __future__ import annotations

from src.application.documents.impact_sources import impact_record_hash
from src.schemas.case_management import TestCaseBundle


class ImpactCaseEvidenceError(ValueError):
    pass


def fixed_impact_case_evidence(manifest, payloads):
    # No upstream counterpart (checked: fixed T anchors, complete case reads,
    # literal citation checks, local Agent protocol course): model field choices
    # are bound to exact supplied versions without invented paths or values.
    cases = {item.version_id: (f"C{index}", item) for index, item in enumerate(manifest.cases, 1)}
    delivered, result = set(), {}
    for payload in payloads:
        for item in [payload["primary"], *payload["additional_fixed_reads"]]:
            if "case" not in item:
                continue
            try:
                bundle = TestCaseBundle.model_validate(item["case"])
                ref, fixed = cases[bundle.version.id]
                if (bundle.case.id != fixed.case_id or bundle.case.project_id != manifest.project_id or
                    bundle.version.case_id != fixed.case_id or impact_record_hash(bundle.version) != fixed.version_record_sha256):
                    raise ValueError("完整用例与固定版本不同")
            except (KeyError, ValueError, TypeError, RecursionError) as exc:
                raise ImpactCaseEvidenceError("字段引用必须来自实际读取的完整固定用例") from exc
            if bundle.version.id in delivered:
                continue
            delivered.add(bundle.version.id)
            stack = [("execution_notes", [], bundle.version.execution_notes),
                ("content_json", [], bundle.version.content_json)]
            while stack:
                root, path, value = stack.pop()
                result[f"F{len(result)+1}"] = {"case_ref": ref, "root": root, "path": path, "value": value}
                if isinstance(value, dict) and value:
                    stack.extend((root, [*path, key], value[key]) for key in reversed(sorted(value)))
                elif isinstance(value, list) and value:
                    stack.extend((root, [*path, index], value[index]) for index in reversed(range(len(value))))
    return result


def case_evidence_catalog(evidence):
    # Values already exist in the complete JSON/notes; do not duplicate them in
    # the model's selector index. Binding recomputes values from those deliveries.
    return {ref: {key: item for key, item in value.items() if key != "value"}
        for ref, value in evidence.items()}
