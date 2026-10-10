"""Interpret verified provider termination fields without rewriting originals."""
from typing import Any, Literal

from src.schemas.model_config import MODEL_FAILURE_MODES

CompletionStatus = Literal['complete', 'tool_calls', 'truncated', 'incomplete', 'blocked', 'failed', 'unknown']


def model_completion_status(summary: dict[str, Any]) -> CompletionStatus:
    # Counterparts: ProviderClient's finish/stop contract, Responses
    # incomplete_details, Gemini FinishReason, Anthropic stop_reason and
    # the existing receipt parser. Keep unknown vendor values unknown.
    reasons = {str(summary.get(key) or '').strip().lower() for key in ('finish_reason', 'stop_reason')}
    if summary.get('mode') in MODEL_FAILURE_MODES:
        return 'failed'
    if reasons & {'length', 'max_tokens', 'max_output_tokens', 'max_token', 'model_context_window_exceeded'}:
        return 'truncated'
    if reasons & {'incomplete', 'pause_turn'}:
        return 'incomplete'
    if reasons & {'content_filter', 'refusal', 'safety', 'blocklist', 'prohibited_content', 'recitation'}:
        return 'blocked'
    if reasons & {'failed', 'cancelled'}:
        return 'failed'
    if reasons & {'tool_calls', 'tool_use', 'function_call'} or summary.get('tool_call_count') or summary.get('tool_calls'):
        return 'tool_calls'
    if reasons & {'stop', 'end_turn', 'stop_sequence', 'completed'}:
        return 'complete'
    return 'unknown'


def is_model_response_incomplete(summary: dict[str, Any]) -> bool:
    """Reject only known non-completion; missing legacy fields remain unknown."""
    return model_completion_status(summary) in {'truncated', 'incomplete', 'blocked', 'failed'}
