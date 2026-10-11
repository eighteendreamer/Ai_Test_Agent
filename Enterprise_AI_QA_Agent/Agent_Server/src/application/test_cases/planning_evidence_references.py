"""Bind only actual delivered evidence; keep business selection with the model."""
from src.application.runtime.case_action_binding import _digest
from src.schemas.case_planning_stages import EvidencePlanningDecisionV3
from src.schemas.case_planning_references import REFERENCE_PROTOCOL


def evidence_reference_catalog(evidence):
    # No upstream counterpart (checked: impact text anchors/identity bindings,
    # planning fixed evidence, document expected compiler): current-call aliases
    # are a wire adapter, not a new canonical identity or business rule.
    entries, texts = {}, {}
    for index, entry in enumerate(evidence, 1):
        # Case hashes belong to the case version, not its planning wrapper.
        # Scene content_hash belongs to its immutable asset; payload_hash covers
        # the delivered knowledge projection including identities and aliases.
        if entry.kind == "scene":
            asset = {"content": entry.payload.get("content"), "source": entry.payload.get("source")}
            if entry.content_hash != _digest(asset) or entry.payload_hash != _digest(entry.payload):
                raise ValueError("场景原始快照或已读知识投影哈希不一致")
        elif entry.kind != "case" and entry.content_hash != _digest(entry.payload):
            raise ValueError("短引用目录的已读证据哈希不一致")
        ref = f"E{index}"
        entries[ref] = {"evidence_id": entry.id, "kind": entry.kind,
            "source_id": entry.source_id, "version": entry.version, "content_hash": entry.content_hash}
        if entry.kind == "scene":
            entries[ref]["payload_hash"] = entry.payload_hash
        content = entry.payload.get("content")
        if entry.kind != "document" or not isinstance(content, str) or not content:
            continue
        ranges = [(0, len(content))]
        start = 0
        for line in content.splitlines(keepends=True):
            end = start + len(line.rstrip("\r\n"))
            if end > start and (start, end) not in ranges:
                ranges.append((start, end))
            start += len(line)
        for start, end in ranges:
            texts[f"T{len(texts) + 1}"] = {"evidence_ref": ref, "payload_path_tokens": ["content"],
                "char_start": start, "char_end": end, "text": content[start:end]}
    value = {"protocol": REFERENCE_PROTOCOL, "evidence": entries, "texts": texts}
    return {**value, "sha256": _digest(value)}


def bind_evidence_references(decision, catalog, evidence, *, query_decision_type=None):
    if catalog != evidence_reference_catalog(evidence):
        raise ValueError("本次调用短引用目录与固定已读证据不同")

    def entry(ref):
        if ref not in catalog["evidence"]:
            raise ValueError(f"未登记的证据短引用：{ref}")
        return catalog["evidence"][ref]

    value = decision.model_dump(mode="json")
    for binding in value["bindings"]:
        binding["evidence_ids"] = [entry(ref)["evidence_id"] for ref in binding["evidence_ids"]]
        for declaration in binding["effect_declarations"] or []:
            for check in declaration["checks"] + declaration["select_when"]:
                reference = check.get("expected_source")
                if reference is None:
                    continue
                if reference.get("text_ref"):
                    anchor = catalog["texts"].get(reference["text_ref"])
                    if anchor is None:
                        raise ValueError(f"未登记的原文短引用：{reference['text_ref']}")
                    source = entry(anchor["evidence_ref"])
                    normalized = {"payload_path_tokens": anchor["payload_path_tokens"], "excerpt": anchor["text"],
                        "literal": reference["literal"], "encoding": reference["encoding"]}
                else:
                    source = entry(reference["evidence_ref"])
                    normalized = {key: reference[key] for key in
                        ("payload_path_tokens", "excerpt", "literal", "encoding") if reference.get(key) is not None}
                if source["kind"] != "document":
                    raise ValueError("文档预期短引用不能指向用例或工具")
                check["expected_source"] = {**normalized,
                    "evidence_id": source["evidence_id"], "content_hash": source["content_hash"]}
    model = query_decision_type if decision.action == "query" and query_decision_type is not None else EvidencePlanningDecisionV3
    return model.model_validate(value)


def reference_effects_prompt(schema):
    from src.application.test_cases.planning_sources import json_text
    return (
        "只读规划当前项目的执行证据，不执行业务、不报告通过。完整用例/说明/原文和工具契约已经提供；"
        "logical_plan/hash及operation_selections固定，不改用例、项、instructions、依赖或清理关系。"
        "bindings只追加现有item_key的evidence_ids、input_bindings、effect_declarations及具体缺口。"
        "本阶段evidence_ids使用evidence_reference_catalog中的E短引用，正式ID和hash由系统还原。"
        "预期二选一：expected_path_tokens相对正式完整content或prepare.instructions；"
        "expected_source引用文档E及payload_path_tokens，或T原文引用加literal和encoding。"
        "T已提供确切原文，不复制ID/hash/excerpt；literal必须唯一存在，json仅解码标量，text保留字符。"
        "重复原文有歧义时用E+唯一excerpt/literal/encoding或说明缺口，不能猜测。"
        "文档来源的E必须加入该项evidence_ids；不用目录冒充正文。"
        "input_bindings引用外部依赖实际输出或runtime_context顶层input_key。所有路径为字面量键/索引数组，含点号键不拆。"
        "产生项声明produced，清理项声明cleanup；每份有唯一declaration_id、操作输入select_when、object_path_tokens、collection和checks。"
        "select_when匹配真实工具输入，checks比较真实输出与固定预期；根据已读when/input/output及完整models确定包装路径，不能猜。"
        "produced.sources为空；cleanup.sources引用produced的item_key/declaration_id，与固定cleanup_sources一致；可引用同项produced。"
        "重复调用分别保存对象，不能只清理最终输出。无可靠结构先query工具或原文；未知保留unresolved，不删除已有缺口。"
        "has_more可继续读，查询仍使用正式source_id。只返回Schema JSON，资料齐全finish，否则query；不换模式或读取生成模板。\nJSON Schema: "
    ) + json_text(schema)
