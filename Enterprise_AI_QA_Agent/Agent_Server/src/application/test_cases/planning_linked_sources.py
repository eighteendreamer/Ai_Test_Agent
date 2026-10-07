"""Deterministic source ownership; no inferred project-wide source mapping."""
from src.application.test_cases.planning_sources import json_text
from src.schemas.case_planning_sources import PlanningLinkedSource, PlanningLinkedSources


def linked_source_queue(evidence):
    # No upstream counterpart (checked: full case evidence source_refs, durable
    # operation queues and generation provenance): record explicit references
    # outside model rounds; do not manufacture a query decision.
    entries = {}
    for case in evidence:
        if case.kind != "case":continue
        for source in case.payload.get("source_refs", []):
            key = json_text(source)
            if key not in entries:
                entries[key] = PlanningLinkedSource(case_evidence_ids=[], source=source)
            entries[key].case_evidence_ids = list(dict.fromkeys([*entries[key].case_evidence_ids, case.id]))
    return PlanningLinkedSources(entries=list(entries.values()))


def linked_document_bases(progress):
    result = {}
    for entry in progress.entries:
        if entry.source.source_type == "api_doc":result.setdefault(entry.source.source_id, [])
        if entry.basis is not None:
            bases = result.setdefault(entry.source.source_id, [])
            if entry.basis not in bases:bases.append(entry.basis)
    return result
