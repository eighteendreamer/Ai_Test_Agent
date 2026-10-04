"""Serialize controlled valid proposals using the actual request's v4 references."""
from __future__ import annotations

import json


def bound_fixture_response(body, value):
    payload = json.loads(body["messages"][-1]["content"])
    assert value["unit"] == payload["unit"]
    refs = payload["fixed_references"]

    def ref(group, field, identity):
        return next(key for key, item in refs[group].items() if item[field] == identity)

    def case_impact(impact):
        key = ref("cases", "version_id", impact["version_id"])
        fixed = refs["cases"][key]
        citations = []
        for citation in impact["citations"]:
            assert citation["version_id"] == fixed["version_id"]
            assert citation["version_record_sha256"] == fixed["version_record_sha256"]
            citations.append({field: item for field, item in citation.items()
                if field not in {"version_id", "version_record_sha256"}})
        return {**{field: item for field, item in impact.items() if field not in {"version_id", "citations"}},
            "case_ref": key, "citations": citations}

    converted = {field: item for field, item in value.items() if field not in {"unit", "protocol_version", "candidates", "reads"}}
    anchored = payload["analysis_protocol"]["version"] == "impact-call-v5"
    converted["protocol_version"] = "impact-response-v3" if anchored else "impact-response-v2"
    reads = value.get("reads", {})
    converted["reads"] = {
        "segment_refs": [ref("segments", "segment_id", item) for item in reads.get("segment_ids", [])],
        "case_refs": [ref("cases", "version_id", item) for item in reads.get("case_version_ids", [])],
        "difference_refs": [ref("differences", "difference_id", item) for item in reads.get("difference_ids", [])],
        "ranges": [{"source_ref": ref("sources", "source_id", item["source_id"]),
            "start_char": item["start_char"], "end_char": item["end_char"]} for item in reads.get("ranges", [])],
    }
    candidates = []
    case_stage = payload.get("coordination_context", {}).get("stage") == "case_impacts"
    for candidate in value.get("candidates", []):
        impacts = [case_impact(item) for item in candidate["case_impacts"]]
        if case_stage:
            candidates.append({"rule_ref": ref("rules", "rule_id", candidate["key"]), "case_impacts": impacts})
        else:
            citations = []
            for citation in candidate["document_citations"]:
                key = ref("sources", "source_id", citation["source_id"])
                assert citation["text_sha256"] == refs["sources"][key]["text_sha256"]
                fixed = {**{field: item for field, item in citation.items()
                    if field not in {"source_id", "text_sha256"}}, "source_ref": key}
                citations.append(next(name for name, item in payload["fixed_text_evidence"].items() if item == fixed)
                    if anchored else fixed)
            candidates.append({**{field: item for field, item in candidate.items()
                if field not in {"document_citations", "case_impacts"}}, "document_citations": citations, "case_impacts": impacts})
    converted["candidates"] = candidates
    return json.dumps(converted, ensure_ascii=False)
