"""One result contract for the execution prompt and evidence-only assessment."""
from __future__ import annotations

import json

from src.schemas.case_execution import AgentCaseExecutionReceipt
from src.schemas.scene_receipt import case_receipt_type, SCENE_RECEIPT_PROMPT_VERSION


RECEIPT_PROMPT_VERSION = "case-execution-receipt-v6"


def receipt_prompt_version(protocol=None):
    case_receipt_type(protocol)
    return SCENE_RECEIPT_PROMPT_VERSION if protocol else RECEIPT_PROMPT_VERSION


def case_result_contract(protocol=None) -> str:
    if protocol:
        return ("Use schema_version=2. Each check selects exactly one expected basis: expected_path_tokens "
            "into its own complete frozen case/preparation, or expected_source pointing to an already-read "
            "scene in this item's evidence_ids, with exact evidence_id/content_hash/payload_path_tokens. "
            "Scene paths reference content.steps[index].expected or content.assertions[index].expected; "
            "never take recorded actual responses, aliases or organized/not_executed as expected results. "
            "The server verifies the frozen scene asset and projection, then resolves the expectation. "
            "Scene expectations supplement the formal case; still cover every case expectation. "
            "For a textual expectation, semantic may cite the full string; excerpt/literal/encoding may "
            "select an unambiguous scalar. JSON only decodes the selected literal, never infers a rule. "
            "Observations still require actual owned persisted tool output; knowledge availability is not a pass.\n"
            + case_result_contract().replace(json.dumps(AgentCaseExecutionReceipt.model_json_schema(),ensure_ascii=False),
                json.dumps(case_receipt_type(protocol).model_json_schema(),ensure_ascii=False)))
    return (
        "Return only compact JSON matching the following schema. The top-level steps array is "
        "one receipt entry per requested execution item, keyed by that item's exact item_key "
        "(all owned items unless requested_item_keys explicitly limits this read-only assessment). "
        "Never create one receipt entry for each content_json.steps business step, and never "
        "repeat an item_key: if one fixed execution item contains several business steps, combine "
        "all of its checks and outputs under that single item_key entry. Include preparation and "
        "cleanup items when they are requested; never claim passed without checks. Cover every "
        "expected result in the complete case. expected_path_tokens are literal keys/indexes "
        "into that item's expected_source in the assessment input (the complete fixed case JSON "
        "or preparation instructions). In the execution context it is content_json for a formal "
        "case and item.instructions for a preparation. This expected source is NEVER the tool "
        "output. IMPORTANT path-root rule: expected_source is already the root value supplied to "
        "the assessment. For a formal case it is the content_json object itself, so do not prefix "
        "expected_path_tokens with content_json, execution_items, plan_item, or any wrapper field. "
        "For example, if expected_source is {\"期望\":{\"cleanup_code\":200}}, the path is "
        "[\"期望\",\"cleanup_code\"], never [\"content_json\",\"期望\",\"cleanup_code\"]. "
        "For a preparation it is the instructions value itself and likewise has no instructions "
        "prefix. The server rejects paths that do not exist under this exact root. "
        "Do not invent expected status/fields that are absent from expected_source. observations and "
        "outputs must use the exact call_id from the tool message's output_reference (or the "
        "assessment manifest). Never replace it with tool names, ordinal labels or job_id. "
        "An output_reference of null means this call has no settled persisted evidence; "
        "do not cite its diagnostic output as observations or outputs. If an expectation "
        "could not be tested, report the step blocked/skipped/error with its concrete reason "
        "and checks=[] rather than inventing an observation for a not_run check. "
        "A tool message is a model-facing projection: download links, projection fields and "
        "digest truncation markers are not necessarily persisted output values. "
        "Evidence availability alone does not prove any expectation. "
        "All path token arrays contain strings only, including array indexes: index 0 is \"0\", "
        "never the JSON number 0. This applies to both expected_path_tokens and output_path_tokens. "
        "observations and outputs reference actual call_id and literal output_path_tokens into persisted tool "
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
        "For every operator other than semantic, observations must contain exactly one reference "
        "to the actual value being compared. Do not append an action-success record or a second "
        "value to that scalar comparison; report other expectations as separate checks. "
        "For natural-language business expectations, use semantic with verdict and a reason "
        "grounded in all necessary observations; identify incomplete evidence as not_run. "
        "These semantic decisions remain AI judgments. State blocked/skipped/error and the "
        "concrete reason for unfinished steps; successful preparation does not prove the target.\n"
        + json.dumps(AgentCaseExecutionReceipt.model_json_schema(), ensure_ascii=False)
    )
