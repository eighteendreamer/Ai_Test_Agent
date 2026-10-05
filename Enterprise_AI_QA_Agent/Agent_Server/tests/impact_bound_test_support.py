"""Serialize controlled valid proposals using the actual request's v4 references."""
from __future__ import annotations

import json


def bound_fixture_response(body, value):
    payload = json.loads(body["messages"][-1]["content"])
    assert value["unit"] == payload["unit"]
    refs = payload["fixed_references"]
    case_anchored = payload["analysis_protocol"]["version"] == "impact-call-v7"

    def ref(group, field, identity):
        return next(key for key, item in refs[group].items() if item[field] == identity)

    def case_impact(impact):
        key = ref("cases", "version_id", impact["version_id"])
        fixed = refs["cases"][key]
        citations = []
        for citation in impact["citations"]:
            assert citation["version_id"] == fixed["version_id"]
            assert citation["version_record_sha256"] == fixed["version_record_sha256"]
            if case_anchored:
                selected = next(name for name, item in payload["fixed_case_evidence"].items()
                    if item == {"case_ref": key, "root": citation["root"], "path": citation["path"]})
                supplied = []
                for message in body["messages"]:
                    if message["role"] != "user": continue
                    delivered = json.loads(message["content"])
                    supplied.extend(item["case"]["version"] for item in
                        [delivered["primary"], *delivered["additional_fixed_reads"]] if "case" in item)
                actual = next(item for item in supplied if item["id"] == citation["version_id"])[citation["root"]]
                for token in citation["path"]: actual = actual[token]
                assert json.dumps(actual, sort_keys=True) == json.dumps(citation["value"], sort_keys=True)
                citations.append(selected)
            else:
                citations.append({field: item for field, item in citation.items()
                    if field not in {"version_id", "version_record_sha256"}})
        return {**{field: item for field, item in impact.items() if field not in {"version_id", "citations"}},
            "case_ref": key, "citations": citations}

    converted = {field: item for field, item in value.items() if field not in {"unit", "protocol_version", "candidates", "reads"}}
    grouped = payload["analysis_protocol"]["version"] in {"impact-call-v6", "impact-call-v7"}
    anchored = payload["analysis_protocol"]["version"] in {"impact-call-v5", "impact-call-v6", "impact-call-v7"}
    converted["protocol_version"] = "impact-response-v5" if case_anchored else (
        "impact-response-v4" if grouped else "impact-response-v3" if anchored else "impact-response-v2")
    if grouped:
        converted["evidence_outcomes"] = [{"member_ref": ref(
            "segments" if item["unit"]["kind"] == "segment" else "differences",
            "segment_id" if item["unit"]["kind"] == "segment" else "difference_id", item["unit"]["id"]),
            "outcome": item["outcome"], "reason": item["reason"]} for item in value.get("evidence_outcomes", [])]
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
