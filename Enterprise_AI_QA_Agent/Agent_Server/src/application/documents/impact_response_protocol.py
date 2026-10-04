"""Typed impact decisions and factual citation checks, never semantic approval."""
from __future__ import annotations

import importlib.metadata
import hashlib
import json
import re
import sys

from src.application.documents.impact_batches import DocumentImpactBatches
from src.schemas.document_impact_responses import (
    DocumentImpactDecision, DocumentImpactResponseAssessment, DocumentImpactResponseIssue,
    DocumentImpactResponseProtocolSnapshot,
)


def capture_impact_response_protocol():
    value = {"version": "impact-call-v3", "scope": "one declared unit only",
        "response_schema": DocumentImpactDecision.model_json_schema(),
        "instruction": "Read primary and fixed evidence. Request more fixed ranges when cross-section rules are needed. "
            "Propose candidates with exact source slices and literal case JSON paths; report uncertain/missing evidence. "
            "Return one impact-response-v1 JSON object. For action=read request fixed reads, with no final candidates or outcome. "
            "For action=finish state the unit_outcome, candidates and unresolved evidence. Cite only exact text actually supplied "
            "in primary/additional_fixed_reads or earlier user messages; normalized diff offsets are not original offsets. "
            "Case impacts must cite a complete version actually supplied in this call history. Catalog metadata is not text evidence. "
            "Use fixed_case_catalog and fixed_difference_catalog identities to request additional fixed evidence. "
            "Copy version_record_sha256 from fixed_case_catalog for case citations; it identifies the complete version record "
            "and is different from content_hash. Never invent or calculate this hash. Catalog hashes do not replace reading "
            "the complete case. Copy text_sha256 from the supplied source and quote its exact original slice. "
            "Applicability is only a suggestion; deployment remains unknown and all candidates require review. "
            "Do not claim complete project coverage from this unit. Do not execute or change official records."}
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return DocumentImpactResponseProtocolSnapshot(**value, sha256=digest)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("影响响应JSON包含重复键")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("影响响应JSON包含非有限数字")


def parse_impact_decision(text):
    """Keep existing fenced/prose compatibility without rescuing broken objects."""
    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)
    stripped = text.strip()
    try:
        if stripped.startswith(("{", "[")):
            values = [decoder.decode(stripped)]
        else:
            blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", stripped, re.I)
            if blocks:
                # Fenced decisions must be complete objects; do not extract nested JSON.
                values = [decoder.decode(block.strip()) for block in blocks]
                outside = re.sub(r"```(?:json)?\s*[\s\S]*?```", "", stripped, flags=re.I)
                if "{" in outside or "[" in outside:
                    raise ValueError("影响响应包含围栏外的歧义JSON")
            else:
                start = stripped.find("{")
                if start < 0:
                    raise ValueError("影响响应缺少JSON对象")
                value, end = decoder.raw_decode(stripped, start)
                if "{" in stripped[end:] or "[" in stripped[end:]:
                    raise ValueError("影响响应包含多个JSON对象")
                values = [value]
        if len(values) != 1 or not isinstance(values[0], dict):
            raise ValueError("影响响应需要且只能有一个JSON对象")
        return DocumentImpactDecision.model_validate(values[0])
    except (ValueError, TypeError, RecursionError) as exc:
        # Pydantic/JSON errors can quote input data. The original response is already saved.
        raise ValueError("影响响应未返回唯一且符合协议的完整JSON对象") from exc


def _delivered(request):
    """Extract actual fixed text/cases from saved user payloads, including prior rounds."""
    ranges, cases = {}, set()

    def add(identity, start, end):
        ranges.setdefault(identity, []).append((start, end))

    for message in request.messages:
        if message.get("role") != "user":
            continue
        payload = json.loads(message["content"])
        for item in [payload["primary"], *payload["additional_fixed_reads"]]:
            if "source" in item and "text" in item:
                add(item["source"]["id"], item["start_char"], item["end_char"])
            elif "case" in item:
                cases.add(item["case"]["version"]["id"])
            elif "difference" in item and item["kind"] != "normalized":
                diff = item["difference"]
                for side in ("old", "new"):
                    entries = [diff[side]] if item["kind"] == "raw" else diff[side + "_ranges"]
                    for entry in entries:
                        add(item[side + "_source_id"], entry["start_char"], entry["end_char"])
    return ranges, cases


def _contains(ranges, start, end):
    cursor = start
    for left, right in sorted(ranges):
        if left > cursor:
            break
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return False


class DocumentImpactResponseProtocol:
    # No upstream counterpart (checked: PlanningDecision/_parse, impact_batches,
    # local Agent protocol course): this impact protocol uses existing JSON,
    # fixed offsets/hashes and complete case paths, without business rule gates.
    @staticmethod
    def assess(seed, envelope, manifest, record, payload, response):
        DocumentImpactBatches.validate(seed, envelope, manifest)
        limitations = [f"source_{source.status}:{source.id}" for source in manifest.sources if source.status != "complete"]
        limitations += [f"source_warning:{source.id}:{warning.code}" for source in manifest.sources for warning in source.warnings]
        limitations += [f"case_source_{gap.status}:{gap.case_version_id}:{gap.source_index}" for gap in manifest.source_gaps]
        limitations += [f"difference_warning:{warning.code}" for warning in envelope.input.difference.warnings]
        assessment = DocumentImpactResponseAssessment(runtime_versions={"python": sys.version.split()[0],
            "pydantic": importlib.metadata.version("pydantic")}, task_id=record.task_id, call_id=record.id,
            request_sha256=record.request.sha256, response_sha256=record.response.sha256,
            manifest_sha256=payload.manifest_sha256, status="ineligible", decision=None,
            issues=[DocumentImpactResponseIssue(code="response_not_eligible", message="该回执不满足完整文本分析条件")],
            basis_limitations=limitations)
        if record.response_disposition != "received":
            return assessment
        issues = []
        try:
            decision = parse_impact_decision(response.text)
        except ValueError:
            assessment.status = "rejected"
            assessment.issues = [DocumentImpactResponseIssue(code="invalid_protocol", message="响应JSON不完整、歧义或不符合固定协议")]
            return assessment
        assessment.decision = decision
        if decision.unit != payload.selection.unit:
            issues.append(DocumentImpactResponseIssue(code="unit_mismatch", message="响应不能更换原调用的固定分析单元"))
        if decision.action == "read":
            try:
                reads = decision.reads
                for identity in reads.segment_ids:
                    DocumentImpactBatches.read_segment(seed, envelope, manifest, identity)
                for identity in reads.case_version_ids:
                    DocumentImpactBatches.read_case(seed, envelope, manifest, identity)
                for identity in reads.difference_ids:
                    kind, _, number = identity.partition(":")
                    if not number.isdecimal() or str(int(number)) != number:
                        raise ValueError("无效差异身份")
                    DocumentImpactBatches.read_difference(seed, envelope, manifest, kind, int(number))
                for item in reads.ranges:
                    DocumentImpactBatches.read_range(seed, envelope, manifest, item.source_id, item.start_char, item.end_char)
            except ValueError:
                issues.append(DocumentImpactResponseIssue(code="invalid_fixed_read", message="请求补读不属于固定范围或超过既有读取限制"))
        else:
            delivered_ranges, delivered_cases = _delivered(payload.request)
            for candidate in decision.candidates:
                for citation in candidate.document_citations:
                    try:
                        DocumentImpactBatches.validate_text_citation(seed, envelope, manifest, citation)
                    except ValueError:
                        issues.append(DocumentImpactResponseIssue(code="invalid_text_citation", message="原文引用与固定正文身份、切片或哈希不符"))
                        continue
                    if not _contains(delivered_ranges.get(citation.source_id, []), citation.start_char, citation.end_char):
                        issues.append(DocumentImpactResponseIssue(code="unread_text_citation", message="引用原文不在该调用实际提供范围，需要先补读"))
                for impact in candidate.case_impacts:
                    if impact.version_id not in delivered_cases:
                        issues.append(DocumentImpactResponseIssue(code="unread_case", message="受影响用例未向该调用提供完整固定版本"))
                    for citation in impact.citations:
                        try:
                            DocumentImpactBatches.validate_case_citation(seed, envelope, manifest, citation)
                        except ValueError:
                            issues.append(DocumentImpactResponseIssue(code="invalid_case_citation", message="用例引用与固定版本、字面路径或实际值不符"))
        assessment.status = "rejected" if issues else "read_requested" if decision.action == "read" else "unit_finished"
        assessment.issues = issues
        return DocumentImpactResponseAssessment.model_validate(assessment.model_dump())
