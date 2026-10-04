"""Exact call-local references over immutable evidence; no guessed ID repairs."""
from __future__ import annotations

import json

from src.schemas.document_impact_bound_responses import BoundRuleCaseAssessment, BoundRuleCandidate
from src.schemas.document_impact_responses import DocumentImpactDecision
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext
from src.application.documents.impact_text_evidence import fixed_impact_text_evidence, ImpactTextEvidenceError


class ImpactFixedReferenceError(ValueError):
    pass


def fixed_impact_references(manifest, context=None):
    # No upstream counterpart (checked: impact batches/calls, planning evidence
    # IDs and local Agent protocol course): these short selectors replace model
    # copying of system identities; canonical hashes and retrieval stay unchanged.
    sources = {source.id: f"S{index}" for index, source in enumerate(manifest.sources, 1)}
    return {
        "sources": {sources[item.id]: {"source_id": item.id, "text_sha256": item.text_sha256}
            for item in manifest.sources},
        "segments": {f"P{index}": {"segment_id": item.id, "source_ref": sources[item.source_id]}
            for index, item in enumerate(manifest.segments, 1)},
        "cases": {f"C{index}": {"version_id": item.version_id, "version_record_sha256": item.version_record_sha256}
            for index, item in enumerate(manifest.cases, 1)},
        "differences": {f"D{index}": {"difference_id": f"{item.kind}:{item.index}"}
            for index, item in enumerate(manifest.differences, 1)},
        "rules": {f"R{index}": {"rule_id": item.id}
            for index, item in enumerate(context.rules if context else [], 1)},
    }


def impact_primary_reference(unit, references):
    group, field = {"segment": ("segments", "segment_id"), "case": ("cases", "version_id"),
        "difference": ("differences", "difference_id")}[unit.kind]
    return next(ref for ref, item in references[group].items() if item[field] == unit.id)


def bind_impact_decision(decision, seed, manifest, payload):
    current = json.loads(payload.request.messages[-1]["content"])
    context = DocumentImpactCoordinationContext.model_validate(current["coordination_context"]) if (
        "coordination_context" in current) else None
    references = fixed_impact_references(manifest, context)
    if context and (context.protocol != seed.workflow_protocol or
        ((payload.selection.unit.kind == "case") != (context.stage == "case_impacts"))):
        raise ImpactFixedReferenceError("固定引用不能替换协调阶段或协议")
    delivered, text_evidence = [], {}
    anchored = seed.response_protocol is not None and seed.response_protocol.version == "impact-call-v5"
    for message in payload.request.messages:
        if message.get("role") == "user":
            value = json.loads(message["content"])
            if (value.get("fixed_references") != references or
                value.get("primary_ref") != impact_primary_reference(payload.selection.unit, references)):
                raise ImpactFixedReferenceError("固定引用目录不能替换原范围或当前单元")
            if anchored:
                delivered.append(value)
                try:
                    text_evidence = fixed_impact_text_evidence(manifest, delivered)
                except ImpactTextEvidenceError as exc:
                    raise ImpactFixedReferenceError("文本锚点目录与实际固定读取不同") from exc
                if value.get("fixed_text_evidence") != text_evidence:
                    raise ImpactFixedReferenceError("文本锚点目录不能替换实际已读范围")

    def lookup(group, ref):
        try:
            return references[group][ref]
        except KeyError as exc:
            raise ImpactFixedReferenceError("模型引用没有对应的固定依据") from exc

    def text_citation(citation):
        if anchored:
            if citation not in text_evidence:
                raise ImpactFixedReferenceError("引用没有对应的实际读取文本锚点")
            citation = text_evidence[citation]
            source = lookup("sources", citation["source_ref"])
            return {**{key: value for key, value in citation.items() if key != "source_ref"}, **source}
        source = lookup("sources", citation.source_ref)
        return {**citation.model_dump(exclude={"source_ref"}), **source}

    def case_impact(impact):
        fixed = lookup("cases", impact.case_ref)
        return {**impact.model_dump(exclude={"case_ref", "citations"}), "version_id": fixed["version_id"],
            "citations": [{**citation.model_dump(), **fixed} for citation in impact.citations]}

    candidates = []
    case_stage = context is not None and context.stage == "case_impacts"
    for candidate in decision.candidates:
        if case_stage:
            if not isinstance(candidate, BoundRuleCaseAssessment):
                raise ImpactFixedReferenceError("用例阶段只能判断已有固定规则")
            identity = lookup("rules", candidate.rule_ref)["rule_id"]
            rule = next(item for item in context.rules if item.id == identity)
            converted = rule.candidate.model_dump(exclude={"key", "case_impacts"})
            converted.update(key=identity, case_impacts=[case_impact(item) for item in candidate.case_impacts])
        else:
            if not isinstance(candidate, BoundRuleCandidate):
                raise ImpactFixedReferenceError("规则发现阶段不能使用不存在的规则引用")
            converted = candidate.model_dump(exclude={"document_citations", "case_impacts"})
            converted.update(document_citations=[text_citation(item) for item in candidate.document_citations],
                case_impacts=[case_impact(item) for item in candidate.case_impacts])
        candidates.append(converted)
    reads = decision.reads
    # Canonical validators still reject duplicate reads, unfinished actions,
    # duplicate candidate/case keys and invalid literal paths after exact binding.
    return DocumentImpactDecision(protocol_version="impact-response-v1", unit=payload.selection.unit,
        action=decision.action, summary=decision.summary, unit_outcome=decision.unit_outcome,
        candidates=candidates, unresolved=decision.unresolved, reads={
            "segment_ids": [lookup("segments", ref)["segment_id"] for ref in reads.segment_refs],
            "case_version_ids": [lookup("cases", ref)["version_id"] for ref in reads.case_refs],
            "difference_ids": [lookup("differences", ref)["difference_id"] for ref in reads.difference_refs],
            "ranges": [{**item.model_dump(exclude={"source_ref"}),
                "source_id": lookup("sources", item.source_ref)["source_id"]} for item in reads.ranges]})
