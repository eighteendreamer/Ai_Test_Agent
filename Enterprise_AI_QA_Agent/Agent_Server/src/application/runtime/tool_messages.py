"""One model-facing tool result contract for execution and approval resume."""
from __future__ import annotations

import json
from typing import Any

from src.infrastructure.storage_utils import make_json_safe
from src.schemas.tool_runtime import ToolExecutionRecord


def build_tool_message(record: ToolExecutionRecord) -> dict[str, Any]:
    # Same reference root as AgentCaseAssessmentService.evidence_manifest. A
    # pre-execution denial or an unfinished job is not settled tool evidence.
    settled = bool(record.job_id) and record.status in {
        "completed", "partial", "failed", "denied",
    }
    payload = {
        "call_id": record.call_id,
        "job_id": record.job_id,
        "output_reference": (
            {"call_id": record.call_id, "output_path_tokens": []} if settled else None
        ),
        "status": record.status,
        "summary": record.summary,
        "output": record.output,
        "content_provenance": "tool_output",
        "instruction_authority": "none",
    }
    return {
        "role": "tool",
        "tool_call_id": record.call_id,
        "name": record.tool_key,
        "content": json.dumps(make_json_safe(payload), ensure_ascii=False),
    }
