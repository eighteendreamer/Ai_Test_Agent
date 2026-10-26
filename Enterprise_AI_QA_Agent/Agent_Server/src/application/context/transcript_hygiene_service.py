from __future__ import annotations

from datetime import datetime
from typing import Any

from src.application.prompting.untrusted_content import wrap_untrusted_content
from src.schemas.model_config import is_model_failure_summary
from src.schemas.session import ChatMessage, MessageRole


class TranscriptHygieneService:
    def finalization_assistant_message(self, *, message_id: str, content: str,
        created_at: datetime, state: dict[str, Any]) -> ChatMessage:
        """One deterministic projection for normal finalization and row checks."""
        return ChatMessage(id=message_id, role=MessageRole.assistant, content=content, created_at=created_at,
            metadata=self.assistant_message_metadata(
                response_mode=str((state.get("model_response_summary") or {}).get("mode") or "ok"),
                metadata={"turn_id": state["turn_id"], "agent_key": state["selected_agent_key"],
                    "agent_name": state["selected_agent_name"], "model_key": state["selected_model_key"],
                    "model_name": state["selected_model_name"]}))

    def build_runtime_messages(
        self,
        messages: list[ChatMessage],
        limit: int = 24,
        summary: str | None = None,
        summary_covers_until: int = 0,
    ) -> list[dict[str, Any]]:
        runtime_messages: list[dict[str, Any]] = []
        if summary and summary.strip():
            runtime_messages.append(
                {
                    "role": "user",
                    "content": (
                        "[Earlier conversation summary]\n"
                        + wrap_untrusted_content("conversation_summary", summary.strip())
                    ),
                }
            )
        tail = messages[summary_covers_until:] if summary_covers_until > 0 else messages
        for item in tail[-limit:]:
            if not self.is_context_eligible(item):
                continue
            role = item.role.value
            if role not in {"user", "assistant", "system"}:
                continue
            content = str(item.content or "").strip()
            if not content:
                continue
            runtime_messages.append({"role": role, "content": content})
        return runtime_messages

    def is_context_eligible(self, message: ChatMessage) -> bool:
        metadata = message.metadata or {}
        if metadata.get("context_eligible") is False:
            return False
        content = str(message.content or "").strip()
        if not content:
            return False
        if message.role.value == "assistant":
            response_mode = str(metadata.get("response_mode") or "").strip()
            if is_model_failure_summary({"mode": response_mode}):
                return False
            if content.startswith("Model invocation failed for '"):
                return False
        return True

    def classify_message(self, message: ChatMessage) -> dict[str, Any]:
        metadata = message.metadata or {}
        role = message.role.value
        content = str(message.content or "").strip()
        response_mode = str(metadata.get("response_mode") or "").strip() or "ok"
        transcript_bucket = str(metadata.get("transcript_bucket") or "").strip()
        if not transcript_bucket:
            if role == "tool":
                transcript_bucket = "tool"
            elif role == "assistant" and response_mode != "ok":
                transcript_bucket = "error"
            else:
                transcript_bucket = "conversation"
        return {
            "role": role,
            "content": content,
            "response_mode": response_mode,
            "transcript_bucket": transcript_bucket,
            "context_eligible": self.is_context_eligible(message),
        }

    def summarize_messages(self, messages: list[ChatMessage]) -> dict[str, int]:
        summary = {
            "conversation_count": 0,
            "tool_count": 0,
            "error_count": 0,
            "hidden_count": 0,
            "context_eligible_count": 0,
        }
        for item in messages:
            classification = self.classify_message(item)
            bucket = classification["transcript_bucket"]
            if bucket == "tool":
                summary["tool_count"] += 1
            elif bucket == "error":
                summary["error_count"] += 1
            elif classification["content"]:
                summary["conversation_count"] += 1
            else:
                summary["hidden_count"] += 1
            if classification["context_eligible"]:
                summary["context_eligible_count"] += 1
        return summary

    def build_display_transcript(
        self,
        messages: list[ChatMessage],
        *,
        limit: int,
        include_assistant: bool = True,
        include_tools: bool = False,
        include_errors: bool = True,
    ) -> list[dict[str, Any]]:
        transcript_excerpt: list[dict[str, Any]] = []
        for item in messages[-limit:]:
            classification = self.classify_message(item)
            if not classification["content"]:
                continue
            if classification["transcript_bucket"] == "tool" and not include_tools:
                continue
            if classification["transcript_bucket"] == "error" and not include_errors:
                continue
            if classification["role"] == "assistant" and not include_assistant:
                continue
            transcript_excerpt.append(
                {
                    "role": classification["role"],
                    "content": classification["content"],
                    "created_at": item.created_at.isoformat(),
                    "transcript_bucket": classification["transcript_bucket"],
                    "context_eligible": classification["context_eligible"],
                    "response_mode": classification["response_mode"],
                }
            )
        return transcript_excerpt

    def user_message_metadata(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **(metadata or {}),
            "context_eligible": True,
            "transcript_bucket": "conversation",
        }

    def assistant_message_metadata(
        self,
        response_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned_mode = str(response_mode or "ok").strip() or "ok"
        return {
            **(metadata or {}),
            "response_mode": cleaned_mode,
            "context_eligible": cleaned_mode == "ok",
            "transcript_bucket": "conversation" if cleaned_mode == "ok" else "error",
        }

    def tool_message_metadata(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **(metadata or {}),
            "context_eligible": False,
            "transcript_bucket": "tool",
        }
