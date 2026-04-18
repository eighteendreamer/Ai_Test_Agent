from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from src.domain.models import SessionRecord
from src.schemas.session import (
    ExecutionRequest,
    InputAttachment,
    InputHookResult,
    MessageKind,
    SendMessageRequest,
)


class PromptSubmissionService:
    def prepare(self, session: SessionRecord, payload: SendMessageRequest) -> ExecutionRequest:
        content = payload.content.strip()
        attachments = list(payload.attachments)
        message_kind = payload.message_kind
        command_name = (payload.command_name or "").strip() or None
        hook_results: list[InputHookResult] = []

        detected_command_name = self._detect_slash_command(content)
        if detected_command_name and message_kind == MessageKind.user_input:
            message_kind = MessageKind.slash_command
            command_name = command_name or detected_command_name
            hook_results.append(
                InputHookResult(
                    hook_key="slash-command-detector",
                    status="applied",
                    message=f"Detected slash command '{command_name}'.",
                    metadata={"command_name": command_name},
                )
            )

        if not content and not attachments and not command_name:
            raise ValueError("Message content, attachments, or command metadata must be provided.")

        if attachments:
            hook_results.append(
                InputHookResult(
                    hook_key="attachment-normalizer",
                    status="applied",
                    message=f"Normalized {len(attachments)} attachment(s) for this input.",
                    metadata={"attachment_count": len(attachments)},
                )
            )

        normalized_input = " ".join(content.split())
        skill_keys = list(dict.fromkeys(payload.skill_keys))
        input_summary = self._build_input_summary(
            content=content,
            message_kind=message_kind,
            command_name=command_name,
            attachments=attachments,
        )
        context = {
            **payload.context,
            "input_envelope": {
                "message_kind": message_kind.value,
                "submit_mode": payload.submit_mode,
                "command_name": command_name,
                "attachment_count": len(attachments),
                "has_text": bool(content),
            },
            "attachments": [attachment.model_dump(mode="python") for attachment in attachments],
            "hook_results": [result.model_dump(mode="python") for result in hook_results],
        }
        orchestration_meta = {
            "message_kind": message_kind.value,
            "submit_mode": payload.submit_mode,
            "command_name": command_name,
            "attachment_count": len(attachments),
            "interrupt_if_busy": payload.interrupt_if_busy,
            "detected_slash_command": bool(detected_command_name),
        }

        return ExecutionRequest(
            turn_id=str(uuid4()),
            session_id=session.id,
            user_message=content,
            normalized_input=normalized_input,
            agent_key=payload.agent_key or session.selected_agent,
            model_key=payload.model_key or session.preferred_model,
            skill_keys=skill_keys,
            attachments=attachments,
            message_kind=message_kind,
            submit_mode=payload.submit_mode,
            command_name=command_name,
            input_summary=input_summary,
            hook_results=hook_results,
            orchestration_meta=orchestration_meta,
            context=context,
        )

    def _detect_slash_command(self, content: str) -> str | None:
        if not content.startswith("/"):
            return None
        command = content.split(maxsplit=1)[0][1:].strip().lower()
        return command or None

    def _build_input_summary(
        self,
        content: str,
        message_kind: MessageKind,
        command_name: str | None,
        attachments: Iterable[InputAttachment],
    ) -> str:
        parts: list[str] = [f"kind={message_kind.value}"]
        if command_name:
            parts.append(f"command={command_name}")
        attachment_list = list(attachments)
        if attachment_list:
            names = ", ".join(item.name for item in attachment_list[:3])
            if len(attachment_list) > 3:
                names += ", ..."
            parts.append(f"attachments={len(attachment_list)}[{names}]")
        if content:
            preview = " ".join(content.split())
            if len(preview) > 120:
                preview = preview[:117] + "..."
            parts.append(f"text={preview}")
        return " | ".join(parts)
