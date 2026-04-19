from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from src.domain.models import SessionRecord
from src.schemas.session import (
    ExecutionRequest,
    InputAttachment,
    InputEnvelope,
    InputHookResult,
    InputRoutingDecision,
    MessageKind,
    SendMessageRequest,
)


class InputOrchestratorService:
    def orchestrate(self, session: SessionRecord, payload: SendMessageRequest) -> ExecutionRequest:
        raw_content = payload.content or ""
        content = raw_content.strip()
        attachments = list(payload.attachments)
        message_kind = payload.message_kind
        command_name = (payload.command_name or "").strip() or None
        command_args = ""
        hook_results: list[InputHookResult] = []

        detected_command_name, detected_command_args = self._parse_slash_command(content)
        if detected_command_name and message_kind == MessageKind.user_input:
            message_kind = MessageKind.slash_command
            command_name = command_name or detected_command_name
            command_args = detected_command_args
            hook_results.append(
                InputHookResult(
                    hook_key="slash-command-detector",
                    status="applied",
                    message=f"Detected slash command '{command_name}'.",
                    metadata={
                        "command_name": command_name,
                        "command_args_preview": self._preview_text(command_args, 80),
                    },
                )
            )
        elif command_name and detected_command_name == command_name:
            command_args = detected_command_args

        if not content and not attachments and not command_name:
            raise ValueError("Message content, attachments, or command metadata must be provided.")

        if attachments:
            hook_results.append(
                InputHookResult(
                    hook_key="attachment-normalizer",
                    status="applied",
                    message=f"Normalized {len(attachments)} attachment(s) for this input.",
                    metadata={
                        "attachment_count": len(attachments),
                        "attachment_names": [item.name for item in attachments[:5]],
                    },
                )
            )

        normalized_input = " ".join(content.split())
        skill_keys = list(dict.fromkeys(payload.skill_keys))
        input_envelope = InputEnvelope(
            raw_content=raw_content,
            normalized_content=normalized_input,
            message_kind=message_kind,
            submit_mode=payload.submit_mode,
            command_name=command_name,
            command_args=command_args,
            attachment_count=len(attachments),
            attachment_names=[item.name for item in attachments[:5]],
            has_text=bool(content),
            has_attachments=bool(attachments),
            source=payload.source,
        )
        routing_decision = self._build_routing_decision(
            session=session,
            payload=payload,
            message_kind=message_kind,
            command_name=command_name,
            command_args=command_args,
            attachment_count=len(attachments),
        )
        harness_flags = self._build_harness_flags(
            existing_flags=payload.context.get("harness_flags", []),
            session=session,
            routing_decision=routing_decision,
        )
        input_summary = self._build_input_summary(
            envelope=input_envelope,
            routing_decision=routing_decision,
            attachments=attachments,
        )
        context = {
            **payload.context,
            "input_envelope": input_envelope.model_dump(mode="python"),
            "input_routing": routing_decision.model_dump(mode="python"),
            "attachments": [attachment.model_dump(mode="python") for attachment in attachments],
            "hook_results": [result.model_dump(mode="python") for result in hook_results],
            "harness_flags": harness_flags,
        }
        orchestration_meta = {
            "message_kind": message_kind.value,
            "submit_mode": payload.submit_mode,
            "command_name": command_name,
            "command_args": command_args,
            "attachment_count": len(attachments),
            "interrupt_if_busy": payload.interrupt_if_busy,
            "detected_slash_command": bool(detected_command_name),
            "execution_lane": routing_decision.execution_lane,
            "queue_behavior": routing_decision.queue_behavior,
            "interrupt_policy": routing_decision.interrupt_policy,
            "source": payload.source,
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
            input_envelope=input_envelope,
            routing_decision=routing_decision,
            orchestration_meta=orchestration_meta,
            context=context,
        )

    def _build_routing_decision(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        message_kind: MessageKind,
        command_name: str | None,
        command_args: str,
        attachment_count: int,
    ) -> InputRoutingDecision:
        execution_lane = {
            MessageKind.user_input: "conversation_turn",
            MessageKind.slash_command: "slash_command_turn",
            MessageKind.system_command: "system_command_turn",
            MessageKind.task_notification: "task_notification_turn",
            MessageKind.coordinator_assignment: "coordinator_assignment_turn",
        }[message_kind]
        if payload.interrupt_if_busy:
            queue_behavior = "interrupt_then_retry"
            interrupt_policy = "interrupt_active_turn"
        elif payload.submit_mode in {"queued", "enqueue", "background"}:
            queue_behavior = "enqueue_if_busy"
            interrupt_policy = "wait_for_active_turn"
        else:
            queue_behavior = "reject_when_busy"
            interrupt_policy = "wait_for_active_turn"
        should_stream_response = session.runtime_mode.value != "background"
        return InputRoutingDecision(
            execution_lane=execution_lane,
            queue_behavior=queue_behavior,
            interrupt_policy=interrupt_policy,
            should_persist_user_message=True,
            should_stream_response=should_stream_response,
            expects_model_turn=True,
            metadata={
                "session_mode": session.session_mode.value,
                "runtime_mode": session.runtime_mode.value,
                "command_name": command_name,
                "command_args_preview": self._preview_text(command_args, 80),
                "attachment_count": attachment_count,
            },
        )

    def _build_harness_flags(
        self,
        existing_flags: object,
        session: SessionRecord,
        routing_decision: InputRoutingDecision,
    ) -> list[str]:
        flags: list[str] = []
        for item in existing_flags if isinstance(existing_flags, list) else []:
            text = str(item).strip()
            if text and text not in flags:
                flags.append(text)
        for item in [
            "input_orchestrator",
            "permission_gate",
            "event_sourcing",
            "snapshot_resume",
            "verification",
            f"session_mode:{session.session_mode.value}",
            f"runtime_mode:{session.runtime_mode.value}",
            f"execution_lane:{routing_decision.execution_lane}",
        ]:
            if item not in flags:
                flags.append(item)
        return flags

    def _parse_slash_command(self, content: str) -> tuple[str | None, str]:
        if not content.startswith("/"):
            return None, ""
        first_token, _, remainder = content.partition(" ")
        command = first_token[1:].strip().lower()
        return (command or None, remainder.strip())

    def _build_input_summary(
        self,
        envelope: InputEnvelope,
        routing_decision: InputRoutingDecision,
        attachments: Iterable[InputAttachment],
    ) -> str:
        parts: list[str] = [f"kind={envelope.message_kind.value}"]
        parts.append(f"lane={routing_decision.execution_lane}")
        parts.append(f"queue={routing_decision.queue_behavior}")
        if envelope.command_name:
            parts.append(f"command={envelope.command_name}")
        if envelope.command_args:
            parts.append(f"args={self._preview_text(envelope.command_args, 80)}")
        attachment_list = list(attachments)
        if attachment_list:
            names = ", ".join(item.name for item in attachment_list[:3])
            if len(attachment_list) > 3:
                names += ", ..."
            parts.append(f"attachments={len(attachment_list)}[{names}]")
        if envelope.normalized_content:
            parts.append(f"text={self._preview_text(envelope.normalized_content, 120)}")
        return " | ".join(parts)

    def _preview_text(self, value: str, limit: int) -> str:
        preview = " ".join((value or "").split())
        if len(preview) > limit:
            return preview[: limit - 3] + "..."
        return preview
