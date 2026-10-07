"""One result contract for the execution prompt and evidence-only assessment."""
from __future__ import annotations

import json

from src.schemas.case_execution import AgentCaseExecutionReceipt


RECEIPT_PROMPT_VERSION = "case-execution-receipt-v3"


def case_result_contract() -> str:
    return (
        "Return only compact JSON matching the following schema. Account for every requested step "
        "(all owned steps unless requested_item_keys explicitly limits this read-only assessment), "
        "including preparation and cleanup; never claim passed without checks. Cover every "
        "expected result in the complete case. expected_path_tokens are literal keys/indexes "
        "into that item's expected_source in the assessment input (the complete fixed case JSON "
        "or preparation instructions). In the execution context it is content_json for a formal "
        "case and item.instructions for a preparation. This expected source is NEVER the tool "
        "output. Do not invent expected status/fields that are absent from expected_source. observations and "
        "outputs reference actual call_id and literal output_path_tokens into persisted tool "
        "output, never input examples or invented actual values. output_path_tokens is relative "
        "to the manifest entry's output value: do not prefix it with output or the call ID. "
        "For example an output {\"body\": {\"literal.key\": false}} is referenced by "
        "[\"body\", \"literal.key\"]. A present null can be checked; a missing field cannot. "
        "[] means the entire value. outputs maps field names to references; output_source "
        "optionally copies a complete object instead. For nested objects or arrays composed "
        "from multiple calls, use output_structure: a reference node has kind=reference and "
        "source={call_id,output_path_tokens}; an object node has kind=object and fields mapping "
        "literal keys to nodes; an array node has kind=array and items listing nodes in order. "
        "Every leaf must reference an actual persisted tool value, never contain a literal actual "
        "value. The server resolves the leaves; declare structure that matches the fixed plan's "
        "consumer paths. Choose only one of outputs, output_source or output_structure. "
        "Use deterministic operators when the fixed expected value is directly comparable. "
        "For natural-language business expectations, use semantic with verdict and a reason "
        "grounded in all necessary observations; identify incomplete evidence as not_run. "
        "These semantic decisions remain AI judgments. State blocked/skipped/error and the "
        "concrete reason for unfinished steps; successful preparation does not prove the target.\n"
        + json.dumps(AgentCaseExecutionReceipt.model_json_schema(), ensure_ascii=False)
    )
