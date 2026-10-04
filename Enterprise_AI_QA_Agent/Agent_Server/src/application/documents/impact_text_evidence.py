"""Stable citation selectors derived only from actual fixed text deliveries."""
from __future__ import annotations


class ImpactTextEvidenceError(ValueError):
    pass


def fixed_impact_text_evidence(manifest, payloads):
    # No upstream counterpart (checked: impact range/difference reads, exact
    # citation validators, v4 identity bindings and local Agent protocol course):
    # the model selects supplied text; the system owns the source positions.
    sources = {source.id: (f"S{index}", source) for index, source in enumerate(manifest.sources, 1)}
    identities, result = {}, {}

    def add(source_id, start, end, quote):
        if source_id not in sources:
            raise ImpactTextEvidenceError("文本锚点来源不属于固定清单")
        source_ref, source = sources[source_id]
        if (type(start) is not int or type(end) is not int or not isinstance(quote, str) or
            not 0 <= start <= end <= source.length_chars or end - start != len(quote)):
            raise ImpactTextEvidenceError("文本锚点位置与实际读取长度不同")
        if not quote:
            return
        identity = source_id, start, end
        value = {"source_ref": source_ref, "start_char": start, "end_char": end, "quote": quote}
        if identity in identities:
            if result[identities[identity]] != value:
                raise ImpactTextEvidenceError("同一固定读取范围出现不同原文")
            return
        ref = f"T{len(result) + 1}"
        identities[identity], result[ref] = ref, value

    for payload in payloads:
        for item in [payload["primary"], *payload["additional_fixed_reads"]]:
            if "source" in item and "text" in item:
                source_id, start, end, text = item["source"]["id"], item["start_char"], item["end_char"], item["text"]
                add(source_id, start, end, text)
                for block in item.get("blocks", []):
                    left, right = max(start, block["start_char"]), min(end, block["end_char"])
                    if left < right:
                        add(source_id, left, right, text[left - start:right - start])
            elif "difference" in item and item["kind"] != "normalized":
                for side in ("old", "new"):
                    ranges = [item["difference"][side]] if item["kind"] == "raw" else item["difference"][side + "_ranges"]
                    for value in ranges:
                        add(item[side + "_source_id"], value["start_char"], value["end_char"], value["text"])
    return result
