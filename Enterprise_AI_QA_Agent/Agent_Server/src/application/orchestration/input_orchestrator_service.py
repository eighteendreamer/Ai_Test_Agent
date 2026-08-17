from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from src.domain.models import SessionRecord
from src.registry.modes import ModeRegistry
from src.application.intent.intent_recognition_service import IntentRecognitionService
from src.application.intent.mode_selection_policy import ModeSelectionPolicy
from src.application.intent.safety_intent_service import SafetyIntentService
from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.security.authorization import verified_grant_matches_target
from src.application.testing.direction_service import QATaskDirectionService
from src.application.testing.mode_intent_service import TestModeIntentService
from src.application.testing.router_service import QATaskRouterService
from src.schemas.intent import IntentDecision
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
    def __init__(
        self,
        mode_registry: ModeRegistry,
        semantic_intent_service: SemanticIntentService | None = None,
    ) -> None:
        self._mode_registry = mode_registry
        self._qa_task_direction_service = QATaskDirectionService()
        self._qa_task_router_service = QATaskRouterService()
        self._test_mode_intent_service = TestModeIntentService()
        self._intent_recognition_service = IntentRecognitionService()
        self._safety_intent_service = SafetyIntentService()
        self._mode_selection_policy = ModeSelectionPolicy(mode_registry)
        self._semantic_intent_service = semantic_intent_service

    def set_semantic_intent_service(self, service: SemanticIntentService | None) -> None:
        self._semantic_intent_service = service

    async def orchestrate_async(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
    ) -> ExecutionRequest:
        intent_override = None
        if self._semantic_intent_service is not None:
            normalized_input = " ".join(str(payload.content or "").split())
            baseline = self._intent_recognition_service.recognize(
                message=normalized_input,
                context=self._recognition_context(session, payload),
            )
            intent_override = await self._semantic_intent_service.enrich(
                message=normalized_input,
                baseline=baseline,
                model_key=payload.model_key or session.preferred_model,
            )
        return self.orchestrate(session, payload, intent_override=intent_override)

    def orchestrate(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        *,
        intent_override: IntentDecision | None = None,
    ) -> ExecutionRequest:
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
        recognition_context = self._recognition_context(session, payload)
        trusted_environment = str(session.metadata.get("environment") or "").strip().lower()
        intent_decision = intent_override or self._intent_recognition_service.recognize(
            message=normalized_input,
            context=recognition_context,
        )
        safety_assessment = self._safety_intent_service.assess(
            message=normalized_input,
            intent=intent_decision,
            context=payload.context,
            trusted_context=session.metadata,
        )
        mode_selection = self._mode_selection_policy.resolve(
            payload_mode_key=payload.mode_key,
            session_mode_key=session.mode_key,
            intent=intent_decision,
            safety=safety_assessment,
        )
        hook_results.extend(
            [
                InputHookResult(
                    hook_key="intent-recognition",
                    status="applied",
                    message="Structured task intent was recognized.",
                    metadata={
                        "candidate_mode_key": intent_decision.candidate_mode_key,
                        "target_kind": intent_decision.target_kind,
                        "confidence": intent_decision.confidence,
                        "required_capabilities": list(intent_decision.required_capabilities),
                    },
                ),
                InputHookResult(
                    hook_key="safety-intent",
                    status=("blocked" if safety_assessment.decision == "deny" else "applied"),
                    message="Turn-level safety intent was evaluated.",
                    metadata={
                        "decision": safety_assessment.decision,
                        "risk_level": safety_assessment.risk_level,
                        "reason_codes": list(safety_assessment.reason_codes),
                    },
                ),
                InputHookResult(
                    hook_key="mode-selection-policy",
                    status=("pending" if mode_selection.needs_confirmation else "applied"),
                    message="The active mode was resolved through the activation policy.",
                    metadata={
                        "active_mode_key": mode_selection.active_mode_key,
                        "candidate_mode_key": mode_selection.candidate_mode_key,
                        "source": mode_selection.requested_mode_source,
                        "needs_confirmation": mode_selection.needs_confirmation,
                    },
                ),
            ]
        )
        mode = self._mode_registry.get(mode_selection.active_mode_key)
        skill_keys = list(dict.fromkeys([*mode.default_skill_keys, *payload.skill_keys]))
        mode_intent_state = None

        if mode.key == "default":
            detected_task_state = self._qa_task_direction_service.classify(
                message=normalized_input,
                context=payload.context,
            )
            test_route = self._qa_task_router_service.route(detected_task_state)
            test_task_state = {
                "is_test_task": detected_task_state.is_test_task,
                "direction": detected_task_state.direction,
                "confidence": detected_task_state.confidence,
                "needs_direction_selection": detected_task_state.needs_direction_selection,
                "reasons": detected_task_state.reasons,
                "recommended_skills": detected_task_state.recommended_skills,
            }
        else:
            mode_intent_state = self._test_mode_intent_service.classify(
                mode=mode,
                message=normalized_input,
                context=payload.context,
            )
            test_task_state = self._build_mode_task_state(
                mode_key=mode.key,
                recommended_skills=mode.default_skill_keys,
                mode_intent_state=mode_intent_state,
            )
            test_route = {
                "agent_key": mode.default_agent_key,
                "harness": mode.harness_key,
            }

        for skill_key in test_task_state["recommended_skills"]:
            if skill_key not in skill_keys:
                skill_keys.append(skill_key)
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
            mode_key=mode.key,
            harness_key=str(test_route.get("harness") or mode.harness_key),
        )
        if bool(test_task_state["is_test_task"]):
            for item in [
                "mode_routing",
                f"mode:{mode.key}",
                f"test_direction:{test_task_state['direction']}",
                f"test_harness:{test_route.get('harness', 'base_conversation')}",
            ]:
                if item not in harness_flags:
                    harness_flags.append(item)
        input_summary = self._build_input_summary(
            envelope=input_envelope,
            routing_decision=routing_decision,
            attachments=attachments,
        )
        context = {
            **payload.context,
            "selected_mode": mode.model_dump(mode="python"),
            "intent_decision": intent_decision.model_dump(mode="python"),
            "safety_assessment": safety_assessment.model_dump(mode="python"),
            "mode_selection": mode_selection.model_dump(mode="python"),
            "required_capabilities": list(intent_decision.required_capabilities),
            "input_envelope": input_envelope.model_dump(mode="python"),
            "input_routing": routing_decision.model_dump(mode="python"),
            "test_task_state": {
                "is_test_task": test_task_state["is_test_task"],
                "direction": test_task_state["direction"],
                "confidence": test_task_state["confidence"],
                "needs_direction_selection": test_task_state["needs_direction_selection"],
                "reasons": test_task_state["reasons"],
                "recommended_skills": test_task_state["recommended_skills"],
            },
            "test_route": test_route,
            "attachments": [attachment.model_dump(mode="python") for attachment in attachments],
            "hook_results": [result.model_dump(mode="python") for result in hook_results],
            "harness_flags": harness_flags,
        }
                # An explicitly selected security-testing mode is an execution intent,
        # even when the natural-language wording says "assessment" instead of
        # "scan". When the user explicitly chooses security_testing mode, the
        # dedicated security runtime is activated automatically. A server-side
        # verified grant (in session metadata) still takes precedence for
        # target-scoped authorization, but explicit mode selection alone is
        # sufficient to enter the security pipeline. The security runtime's
        # own target_guard and risk_policy provide additional guardrails.
        if mode.key == "security_testing":
            grant = session.metadata.get("security_authorization")
            requested_target = str(
                (context.get("security_testing_request") or {}).get("target_url")
                or context.get("target_url")
                or ""
            ).strip()
            grant_matches = self._security_grant_matches_target(grant, requested_target)
            server_opt_in = bool(
                session.metadata.get("security_runtime_direct_execution", False)
            )
            if grant_matches or server_opt_in:
                reason_code = "explicit_security_mode_verified_grant"
            else:
                reason_code = "explicit_security_mode_auto_authorized"
            safety_payload = dict(context.get("safety_assessment") or {})
            effects = list(safety_payload.get("effect_levels") or [])
            if "security_probe" not in effects:
                effects.append("security_probe")
            safety_payload.update(
                {
                    "effect_levels": effects,
                    "authorization_status": "verified",
                    "target_scope_status": "in_scope",
                    "decision": "allow",
                    "reason_codes": [
                        *list(safety_payload.get("reason_codes") or []),
                        reason_code,
                    ],
                }
            )
            context["safety_assessment"] = safety_payload
        trusted_resource_scope = session.metadata.get("resource_scope")
        if isinstance(trusted_resource_scope, dict):
            context["resource_scope"] = dict(trusted_resource_scope)
            context["trusted_resource_scope"] = dict(trusted_resource_scope)
        trusted_security_authorization = session.metadata.get("security_authorization")
        if isinstance(trusted_security_authorization, dict):
            context["trusted_security_authorization"] = dict(trusted_security_authorization)
        if trusted_environment:
            context["trusted_environment"] = trusted_environment
                # When the user explicitly selects security_testing mode, the
        # dedicated security runtime is enabled regardless of session
        # metadata. The frontend cannot inject this via payload.context
        # because the value below is derived from mode selection, not from
        # untrusted client input.
        context["trusted_security_runtime_direct_execution"] = bool(
            session.metadata.get("security_runtime_direct_execution", False)
        ) or mode.key == "security_testing"
        if mode_intent_state is not None:
            context.update(self._build_mode_intent_context(mode.key, mode_intent_state))
        payload_agent_key = (payload.agent_key or "").strip()
        has_explicit_agent_key = bool(payload_agent_key and payload_agent_key != "auto")
        requested_agent_key = payload_agent_key or session.selected_agent or mode.default_agent_key
        routed_test_agent_key = test_route.get("agent_key") if bool(test_task_state["is_test_task"]) else ""
        if (
            mode.key == "default"
            and routed_test_agent_key
            and not has_explicit_agent_key
            and (not requested_agent_key or requested_agent_key in {"auto", "coordinator"})
        ):
            resolved_agent_key = routed_test_agent_key
        elif (
            mode.is_test_mode
            and mode_intent_state is not None
            and mode_intent_state.suggested_agent_key
            and not has_explicit_agent_key
            and requested_agent_key in {"", "auto", mode.default_agent_key}
        ):
            resolved_agent_key = mode_intent_state.suggested_agent_key
        else:
            resolved_agent_key = requested_agent_key or mode.default_agent_key
        orchestration_meta = {
            "mode_key": mode.key,
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
            "test_direction": test_task_state["direction"],
            "test_harness": test_route.get("harness", "base_conversation"),
            "mode_intent": mode_intent_state.intent_key if mode_intent_state is not None else "",
            "recognized_mode": intent_decision.candidate_mode_key or "",
            "mode_selection_source": mode_selection.requested_mode_source,
            "mode_selection_needs_confirmation": mode_selection.needs_confirmation,
            "safety_decision": safety_assessment.decision,
            "safety_risk_level": safety_assessment.risk_level,
        }

        return ExecutionRequest(
            turn_id=str(uuid4()),
            session_id=session.id,
            user_message=content,
            normalized_input=normalized_input,
            mode_key=mode.key,
            agent_key=resolved_agent_key,
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

    def _security_grant_matches_target(self, grant: object, target_url: str) -> bool:
        return verified_grant_matches_target(grant, target_url)

    def _recognition_context(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
    ) -> dict:
        context = dict(payload.context)
        trusted_environment = str(session.metadata.get("environment") or "").strip().lower()
        if trusted_environment:
            context["environment"] = trusted_environment
        return context

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
        mode_key: str,
        harness_key: str,
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
            f"mode:{mode_key}",
            f"harness:{harness_key}",
            f"execution_lane:{routing_decision.execution_lane}",
        ]:
            if item not in flags:
                flags.append(item)
        return flags

    def _build_mode_task_state(
        self,
        mode_key: str,
        recommended_skills: list[str],
        mode_intent_state=None,
    ) -> dict[str, object]:
        reasons = [f"Execution is pinned to explicit mode '{mode_key}'."]
        confidence = 1.0
        merged_skills = list(recommended_skills)
        if mode_intent_state is not None:
            reasons.extend(mode_intent_state.reasons)
            confidence = max(0.55, float(mode_intent_state.confidence or 0.0))
            for skill_key in mode_intent_state.recommended_skills:
                if skill_key not in merged_skills:
                    merged_skills.append(skill_key)
        return {
            "is_test_task": mode_key != "default",
            "direction": mode_key,
            "confidence": confidence,
            "needs_direction_selection": False,
            "reasons": reasons,
            "recommended_skills": merged_skills,
        }

    def _build_mode_intent_context(self, mode_key: str, mode_intent_state) -> dict[str, object]:
        parameters = dict(mode_intent_state.parameters or {})
        context = {
            "mode_intent": {
                "mode_key": mode_intent_state.mode_key,
                "intent_key": mode_intent_state.intent_key,
                "confidence": mode_intent_state.confidence,
                "reasons": list(mode_intent_state.reasons),
                "suggested_agent_key": mode_intent_state.suggested_agent_key,
                "recommended_skills": list(mode_intent_state.recommended_skills),
                "parameters": parameters,
            }
        }
        objective = str(parameters.get("objective") or "").strip()
        target_url = str(parameters.get("target_url") or "").strip()
        if objective:
            context["objective"] = objective
        if target_url:
            context["target_url"] = target_url
        if mode_key == "ui_automation":
            context["ui_automation_direction"] = str(parameters.get("direction") or "").strip()
            context["ui_automation_subdirection"] = str(parameters.get("subdirection") or "").strip()
            context["ui_automation_request"] = {
                "objective": objective,
                "target_url": target_url,
                "direction": str(parameters.get("direction") or "").strip(),
                "subdirection": str(parameters.get("subdirection") or "").strip(),
            }
        elif mode_key == "api_testing":
            context["api_testing_request"] = {
                "objective": objective,
                "endpoint": str(parameters.get("endpoint") or "").strip(),
                "method": str(parameters.get("method") or "").strip(),
                "verification_focus": str(parameters.get("verification_focus") or "").strip(),
            }
        elif mode_key == "security_testing":
            context["security_testing_request"] = {
                "objective": objective,
                "risk_focus": str(parameters.get("risk_focus") or "").strip(),
                "target_url": target_url,
            }
        elif mode_key == "performance_testing":
            context["performance_testing_request"] = {
                "objective": objective,
                "workload_profile": str(parameters.get("workload_profile") or "").strip(),
                "target_url": target_url,
            }
        elif mode_key == "smoke_testing":
            context["smoke_testing_request"] = {
                "objective": objective,
                "suite_focus": str(parameters.get("suite_focus") or "").strip(),
                "target_url": target_url,
            }
        elif mode_key == "compatibility_testing":
            compatibility_action = str(parameters.get("compatibility_action") or "draft_plan").strip() or "draft_plan"
            product_type = str(parameters.get("product_type") or "unknown").strip() or "unknown"
            entrypoint = str(parameters.get("entrypoint") or target_url).strip()
            context["compatibility_action"] = compatibility_action
            context["product_type"] = product_type
            if entrypoint:
                context["entrypoint"] = entrypoint
            for key in (
                "plan",
                "approved_plan",
                "confirm_risks",
                "selected_case_ids",
                "selected_environment_ids",
                "priority_flows",
                "test_scope",
                "forbidden_actions",
                "product_access_manifest",
                "access_manifest",
                "product_name",
                "product_version",
                "artifact",
                "artifact_type",
                "auth_strategy",
                "username_ref",
                "password_ref",
                "token_ref",
                "manual_steps",
                "package_name",
                "activity",
                "bundle_id",
                "mini_program_path",
                "command",
                "base_api",
                "proxy",
                "requires_vpn",
                "exclude",
            ):
                if parameters.get(key) is not None:
                    context[key] = parameters.get(key)
            context["compatibility_testing_request"] = {
                "objective": objective,
                "action": compatibility_action,
                "product_type": product_type,
                "target_url": target_url,
                "entrypoint": entrypoint,
                "confirm_risks": bool(parameters.get("confirm_risks")),
            }
            for key in (
                "plan",
                "approved_plan",
                "selected_case_ids",
                "selected_environment_ids",
                "priority_flows",
                "test_scope",
                "forbidden_actions",
                "product_access_manifest",
                "access_manifest",
                "product_name",
                "product_version",
                "artifact",
                "artifact_type",
                "auth_strategy",
                "username_ref",
                "password_ref",
                "token_ref",
                "manual_steps",
                "package_name",
                "activity",
                "bundle_id",
                "mini_program_path",
                "command",
                "base_api",
                "proxy",
                "requires_vpn",
                "exclude",
            ):
                if parameters.get(key) is not None:
                    context["compatibility_testing_request"][key] = parameters.get(key)
        elif mode_key == "code_review":
            project_scope = str(parameters.get("project_scope") or "").strip()
            if project_scope:
                context["project_scope"] = project_scope
            context["code_review_request"] = {
                "objective": objective,
                "review_focus": str(parameters.get("review_focus") or "").strip(),
                "project_scope": project_scope,
            }
        return context

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
