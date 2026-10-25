"""Preserve native cumulative usage at the official Anthropic stream boundary."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages.ai import subtract_usage


logger = logging.getLogger(__name__)


@dataclass
class _StreamUsage:
    raw: dict[str, Any] = field(default_factory=dict)
    emitted: dict[str, Any] | None = None


_stream_usage: ContextVar[_StreamUsage | None] = ContextVar("anthropic_framework_stream_usage", default=None)


class UsagePreservingChatAnthropic(ChatAnthropic):
    """Retain the official protocol, tools, blocks and client implementations.

    No upstream counterpart (checked: ChatAnthropic 1.7.1 event mapper,
    AnthropicMessagesClient.stream, Claude streaming documentation): the pinned
    mapper discards message_start usage, but the documented final delta may
    omit input/cache fields. Merge observed facts before its normal conversion.
    https://platform.claude.com/docs/en/build-with-claude/streaming
    """

    def _stream(self, *args, **kwargs):
        state = _StreamUsage()
        stream = super()._stream(*args, **kwargs)
        try:
            while True:
                token = _stream_usage.set(state)
                try:
                    chunk = next(stream)
                except StopIteration:
                    return
                finally:
                    _stream_usage.reset(token)
                yield chunk
        finally:
            stream.close()

    async def _astream(self, *args, **kwargs):
        state = _StreamUsage()
        stream = super()._astream(*args, **kwargs)
        try:
            while True:
                # Limit ContextVar tokens to one advancement. A yielded stream
                # can be closed by another task during cancellation cleanup.
                token = _stream_usage.set(state)
                try:
                    chunk = await anext(stream)
                except StopAsyncIteration:
                    return
                finally:
                    _stream_usage.reset(token)
                yield chunk
        finally:
            await stream.aclose()

    def _make_message_chunk_from_anthropic_event(self, event, **kwargs):
        state = _stream_usage.get()
        if state is not None and kwargs.get("stream_usage", True):
            if event.type == "message_start":
                state.raw = event.message.usage.model_dump(exclude_none=True)
            elif event.type == "message_delta":
                # Omitted/None fields preserve earlier facts; explicit zero is
                # a new observation, matching the existing native client.
                state.raw.update(event.usage.model_dump(exclude_none=True))
                event = event.model_copy(update={"usage": event.usage.model_copy(update=state.raw)})
        chunk, block = super()._make_message_chunk_from_anthropic_event(event, **kwargs)
        if state is not None and event.type == "message_delta" and chunk is not None and chunk.usage_metadata:
            cumulative = dict(chunk.usage_metadata)
            if not event.delta.stop_reason:
                # Input/cache counts may be revised later. Emit those at the
                # terminal boundary; intermediate output counts are cumulative.
                cumulative = {"input_tokens": 0, "output_tokens": cumulative["output_tokens"],
                    "total_tokens": cumulative["output_tokens"],
                    **({"output_token_details": cumulative["output_token_details"]}
                        if "output_token_details" in cumulative else {})}
            chunk.usage_metadata = subtract_usage(cumulative, state.emitted)
            state.emitted = cumulative
            if event.delta.stop_reason:
                logger.debug("anthropic_framework_stream_usage_completed", extra={
                    "input_tokens": cumulative["input_tokens"],
                    "output_tokens": cumulative["output_tokens"],
                    "total_tokens": cumulative["total_tokens"],
                })
        return chunk, block
