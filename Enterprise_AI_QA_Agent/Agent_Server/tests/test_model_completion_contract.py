"""Known provider limits must not turn into plans, drafts, or passing receipts."""
import json
from pathlib import Path

import pytest

from src.application.model_clients.completion import model_completion_status, is_model_response_incomplete
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.schemas.model_config import ModelInvocationResult
from test_agent_case_assessment import receipt

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('transport', ['openai_chat_completions', 'openai_responses',
    'anthropic_messages', 'google_gemini_generate_content', 'langchain_chat'])
@pytest.mark.parametrize('streaming', [False, True])
def test_actual_http_sample_limits_cannot_become_passing_receipts(transport, streaming):
    path = ROOT / 'docs/upgrade_2026-10-03/p0-model-completion-before2' / (
        f'{transport}-{"stream" if streaming else "native"}-limited/result.json')
    captured = json.loads(path.read_bytes())['response']
    summary = captured['response_summary']
    assert model_completion_status(summary) == 'truncated'
    response = ModelInvocationResult(text=json.dumps(receipt()), response_summary=summary)
    with pytest.raises(ValueError, match='模型输出未完整结束'):
        AgentCaseAssessmentService.parse_response(response)
    assert response.response_summary == summary  # Do not rewrite legacy facts.


@pytest.mark.parametrize('summary,status', [
    ({'stop_reason': 'model_context_window_exceeded'}, 'truncated'),
    ({'stop_reason': 'pause_turn'}, 'incomplete'),
    ({'finish_reason': 'incomplete'}, 'incomplete'),
    ({'finish_reason': 'SAFETY'}, 'blocked'),
    ({'finish_reason': 'content_filter'}, 'blocked'),
    ({'stop_reason': 'refusal'}, 'blocked'),
    ({'finish_reason': 'failed'}, 'failed'),
    ({'finish_reason': 'cancelled'}, 'failed'),
    ({'mode': 'http_error', 'finish_reason': 'stop'}, 'failed'),
    ({'finish_reason': 'tool_calls'}, 'tool_calls'),
    ({'stop_reason': 'tool_use'}, 'tool_calls'),
    ({'finish_reason': 'STOP', 'tool_call_count': 1}, 'tool_calls'),
    ({'finish_reason': 'STOP', 'tool_calls': [{'name': 'fixture'}]}, 'tool_calls'),
    ({'stop_reason': 'end_turn'}, 'complete'),
    ({'stop_reason': 'stop_sequence'}, 'complete'),
    ({'finish_reason': 'completed'}, 'complete'),
    ({'finish_reason': 'future_vendor_reason'}, 'unknown'),
    ({}, 'unknown'),
    ({'completion_status': 'complete', 'finish_reason': 'MAX_TOKENS'}, 'truncated'),
    ({'finish_reason': 'stop', 'stop_reason': 'max_tokens'}, 'truncated'),
])
def test_verified_meanings_and_unknown_legacy_fields(summary, status):
    before = json.dumps(summary, sort_keys=True)
    assert model_completion_status(summary) == status
    assert is_model_response_incomplete(summary) == (status in {'truncated', 'incomplete', 'blocked', 'failed'})
    assert json.dumps(summary, sort_keys=True) == before
