"""Compile deterministic document values without changing historical case JSON."""
from copy import deepcopy
import json
import logging

from src.application.runtime.case_action_binding import _digest
from src.application.test_runs.literal_evidence import literal_value

logger = logging.getLogger(__name__)


def referenced_expected_value(reference, payload, proof, *, item_key, label):
    """Shared literal extraction; identity checks stay with each source reader."""
    value = literal_value(payload, reference["payload_path_tokens"])
    if reference.get("excerpt") is not None:
        excerpt, literal = reference["excerpt"], reference["literal"]
        if not isinstance(value, str) or value.count(excerpt) != 1 or excerpt.count(literal) != 1:
            raise ValueError(f"执行项 {item_key} 的原文摘录或字面值不存在或有歧义")
        start = value.index(excerpt) + excerpt.index(literal)
        proof.update(excerpt=excerpt, literal=literal, encoding=reference["encoding"],
            char_start=start, char_end=start+len(literal))
        if reference["payload_path_tokens"] == ["content"]:
            proof["line"] = payload.get("start_line", 1) + value[:start].count("\n")
        if reference["encoding"] == "text":
            value = literal
        else:
            # Existing document source contract: decode a selected literal,
            # never infer a rule or repair malformed JSON.
            try:
                value = json.loads(literal, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
            except ValueError:
                raise ValueError(f"执行项 {item_key} 的{label}字面值不是合法JSON标量") from None
            if isinstance(value, (dict, list)):
                raise ValueError(f"执行项 {item_key} 的原文字面值不是标量")
    proof["expected_sha256"] = _digest(value)
    return deepcopy(value), proof


def document_expected_value(reference, item, evidence):
    matches = [entry for entry in evidence if entry.id == reference["evidence_id"]]
    if len(matches) != 1 or reference["evidence_id"] not in item.evidence_ids:
        raise ValueError(f"执行项 {item.key} 的文档预期没有唯一已读来源")
    source = matches[0]
    if (source.kind != "document" or source.content_hash != reference["content_hash"]
        or source.content_hash != _digest(source.payload)):
        raise ValueError(f"执行项 {item.key} 的固定文档身份或内容哈希不一致")
    proof = {"evidence_id": source.id, "source_id": source.source_id, "version": source.version,
        "content_hash": source.content_hash, "payload_path_tokens": reference["payload_path_tokens"],
        "source_uri": source.payload.get("source_uri"), "content_asset_id": source.payload.get("content_asset_id"),
        "parse_snapshot": deepcopy(source.payload.get("parse_snapshot"))}
    value, proof = referenced_expected_value(reference, source.payload, proof, item_key=item.key, label="文档")
    logger.info("case_effect_document_expected_compiled", extra={"item_key": item.key,
        "evidence_id": source.id, "document_id": source.source_id, "version_id": source.version,
        "content_hash": source.content_hash, "value_type": type(value).__name__,
        "text_reference": reference.get("excerpt") is not None})
    return deepcopy(value), proof
