"""Shared request lifecycle for model callers with a captured framework owner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable
from uuid import uuid4

from langchain_core.messages import AIMessage, messages_to_dict

from src.application.model_clients.http_observation import (
    current_provider_call_observation, observe_provider_call,
)
from src.schemas.framework_model_call import FrameworkModelCallIntent, FrameworkModelUsage
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult, model_invocation_parameters
from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired


logger = logging.getLogger(__name__)


def canonical_model_input(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def model_config_fingerprint(config: ModelConfigRecord) -> str:
    return hashlib.sha256(canonical_model_input(model_invocation_parameters(config))).hexdigest()


def dto_call_contract(binding, fingerprint, request, options):
    """The same frozen input/position for initial sends and recovery admission."""
    for field, identity in (("session_id", binding.session_id), ("turn_id", binding.turn_id)):
        if request.trace_context.get(field, identity) != identity:
            raise ValueError("Governed DTO model identity does not match the captured owner")
    position = request.trace_context.get("logical_call_id")
    if not position:
        raise ValueError("Governed DTO model invocation requires a durable call position")
    envelope = canonical_model_input({"request": request.model_dump(mode="json", exclude={"trace_context"}),
        "options": options, "configuration_fingerprint": fingerprint})
    logical = hashlib.sha256(canonical_model_input(["dto-position-v1", binding.binding_id, position])).hexdigest()
    return envelope, logical


def model_usage(value) -> FrameworkModelUsage | None:
    # ModelResponse may include a structured-output ToolMessage. Only the
    # provider's AI messages carry usage; never infer usage from display text.
    messages = value.result if hasattr(value, "result") else [value]
    ai = [message for message in messages if isinstance(message, AIMessage)]
    if not ai or any(message.usage_metadata is None for message in ai):
        return None
    return FrameworkModelUsage(**{name: sum(message.usage_metadata[name] for message in ai)
        for name in ("input_tokens", "output_tokens", "total_tokens")})


def dto_model_usage(result, *, transport: str) -> FrameworkModelUsage | None:
    """Read complete native counters; missing facts retain the reservation."""
    usage = result.response_summary.get("usage")
    if not isinstance(usage, dict):
        return None
    if transport == "google_gemini_generate_content":
        keys = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")
    elif "prompt_tokens" in usage:
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    else:
        keys = ("input_tokens", "output_tokens", "total_tokens")
    # Anthropic omits total and reports cache input separately. The native
    # client preserves these fields; LangChain's normalized usage has total.
    native_anthropic = transport == "anthropic_messages" and "total_tokens" not in usage
    required = keys[:2] if native_anthropic else keys
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in required):
        return None
    input_tokens, output_tokens = (usage[key] for key in keys[:2])
    if native_anthropic:
        cached = [usage[key] if usage.get(key) is not None else 0
            for key in ("cache_read_input_tokens", "cache_creation_input_tokens")]
        if any(type(value) is not int or value < 0 for value in cached):
            return None
        input_tokens += sum(cached)
    total_tokens = input_tokens + output_tokens if native_anthropic else usage[keys[2]]
    if total_tokens < input_tokens + output_tokens:
        return None
    return FrameworkModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


class FrameworkModelCallPolicy:
    def __init__(self, *, calls, model_config_resolver: Callable[[str], ModelConfigRecord | None], evidence=None):
        self._calls = calls
        self._model_config_resolver = model_config_resolver
        self._evidence = evidence

    async def invoke(self, *, owner, model, model_key: str, execution_info, branch_name: str | None,
                     purpose: str, messages: list, tools: list, options: dict,
                     send: Callable[[], Awaitable[Any]]) -> Any:
        """Reserve, commit intent, send once, then settle or retain uncertainty.

        No upstream counterpart (checked: LangChain model wrap hooks,
        ModelCallLimitMiddleware 1.4.6, PostgresDocumentImpactCalls): graph
        middleware does not own this application's root budget or lease.
        """
        config = self._model_config_resolver(model_key)
        if config is None:
            raise ValueError("Governed model configuration is unavailable")
        fingerprint = model_config_fingerprint(config)
        built = (getattr(model, "metadata", None) or {}).get("qa_model_config_fingerprint")
        if fingerprint != built:
            logger.warning("framework_model_configuration_changed", extra={"binding_id": owner.binding.binding_id,
                "model_key": model_key})
            raise ValueError("Model configuration changed after the official model was resolved")
        if (execution_info is None or not execution_info.task_id
            or execution_info.thread_id != owner.binding.thread_id):
            raise ValueError("Governed model request requires its persisted graph task identity")
        envelope = canonical_model_input({"messages": messages_to_dict(messages), "tools": tools,
            "options": options, "configuration_fingerprint": fingerprint})
        request_sha256 = hashlib.sha256(envelope).hexdigest()
        branch_id = "root"
        if branch_name is not None:
            # The final namespace segment belongs to this model node. Its
            # parent identifies the cognitive subgraph across model iterations.
            parent_ns = execution_info.checkpoint_ns.rsplit("|", 1)[0]
            branch_id = "subagent-" + hashlib.sha256(canonical_model_input([branch_name, parent_ns])).hexdigest()
        logical = hashlib.sha256(canonical_model_input(["graph-position-v1", owner.binding.binding_id, branch_id,
            execution_info.checkpoint_ns, execution_info.task_id, purpose])).hexdigest()
        output_tokens = max([config.max_output_tokens] + [value for name, value in options.items()
            if name in {"max_tokens", "max_completion_tokens", "max_output_tokens"}
            and isinstance(value, int) and not isinstance(value, bool)])
        intent = FrameworkModelCallIntent(position_version=1, attempt_id=uuid4().hex, logical_call_id=logical,
            branch_id=branch_id, parent_branch_id="root" if branch_name is not None else None,
            purpose=purpose, model_key=model_key, model_config_fingerprint=fingerprint,
            transport=config.transport, request_sha256=request_sha256,
            # An explicit estimate, not a provider tokenizer or a hard cost cap.
            # UTF-8 bytes include native blocks/tool schemas plus framing allowance.
            reserved_input_tokens=len(envelope) + 1024, reserved_output_tokens=output_tokens)
        return await self._invoke_intent(owner=owner, intent=intent, send=send, usage_reader=model_usage,
            envelope=envelope, response_kind="langchain_messages")

    async def invoke_dto(self, *, owner, config: ModelConfigRecord, request: ModelInvocationRequest,
                         options: dict, send: Callable[[], Awaitable[Any]]) -> Any:
        """Use the same lifecycle for application calls outside graph nodes."""
        intent, envelope = self._dto_intent(owner=owner, config=config, request=request, options=options)
        return await self._invoke_intent(owner=owner, intent=intent, send=send,
            usage_reader=lambda result: dto_model_usage(result, transport=config.transport),
            envelope=envelope, response_kind="model_invocation_result")

    def _dto_intent(self, *, owner, config, request, options):
        current = self._model_config_resolver(config.key)
        fingerprint = model_config_fingerprint(config)
        if current is None or model_config_fingerprint(current) != fingerprint:
            raise ValueError("Model configuration changed before the DTO request was sent")
        envelope, logical = dto_call_contract(owner.binding, fingerprint, request, options)
        request_sha256 = hashlib.sha256(envelope).hexdigest()
        intent = FrameworkModelCallIntent(position_version=1, attempt_id=uuid4().hex, logical_call_id=logical,
            branch_id="root", purpose="agent", model_key=config.key, model_config_fingerprint=fingerprint,
            transport=config.transport, request_sha256=request_sha256,
            reserved_input_tokens=len(envelope) + 1024, reserved_output_tokens=config.max_output_tokens)
        return intent, envelope

    async def restore_completed_dto(self, *, owner, expected, config: ModelConfigRecord,
                                    request: ModelInvocationRequest, options: dict) -> ModelInvocationResult:
        """Decode a specifically settled DTO response; never call the provider.

        This is an explicit application recovery port. It neither creates a
        successor nor writes a checkpoint, and cannot promote late observations.
        """
        try:
            return await self._restore_completed_dto(owner=owner, expected=expected, config=config,
                request=request, options=options)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("framework_model_completed_dto_rejected", extra={"binding_id": owner.binding.binding_id,
                "model_attempt_id": expected.intent.attempt_id, "error_type": type(exc).__name__})
            raise

    async def _restore_completed_dto(self, *, owner, expected, config, request, options):
        if self._evidence is None:
            raise ValueError("Completed DTO import requires its original evidence store")
        expected = await self._calls.verify_completed(owner, expected=expected)
        intent, envelope = self._dto_intent(owner=owner, config=config, request=request, options=options)
        if (intent.model_dump(exclude={"attempt_id"}) != expected.intent.model_dump(exclude={"attempt_id"})
            or expected.request is None or expected.request.sha256 != intent.request_sha256):
            raise ValueError("Completed DTO import differs from its frozen request")
        original_request = await self._evidence.read(expected.request)
        if canonical_model_input(original_request) != envelope:
            raise ValueError("Completed DTO request asset differs from its frozen input")
        selected = next(item for item in expected.response_observations
            if item.observation_id == expected.settled_response_id)
        if selected.response_kind != "model_invocation_result":
            raise ValueError("Completed response is not an application DTO")
        payload = await self._evidence.read_response(owner.binding, expected.intent, selected.response)
        if (payload.get("kind") != "model_invocation_result"
            or payload.get("serializer") != {"name": "ModelInvocationResult", "version": "1"}):
            raise ValueError("Completed DTO response serializer is unsupported")
        result = ModelInvocationResult.model_validate(payload["data"])
        if result.model_dump(mode="json", exclude={"request_payload"}) != payload["data"]:
            raise ValueError("Completed DTO response contains unsupported data")
        if dto_model_usage(result, transport=config.transport) != expected.usage:
            raise ValueError("Completed DTO response usage differs from its settled ledger")
        # Object reads cross an external/async boundary. Verify the owner and
        # exact proof again before exposing a result to a recovery caller.
        await self._calls.verify_completed(owner, expected=expected)
        logger.info("framework_model_completed_dto_read", extra={"binding_id": owner.binding.binding_id,
            "model_attempt_id": expected.intent.attempt_id, "settled_response_id": expected.settled_response_id})
        return result

    async def _invoke_intent(self, *, owner, intent, send, usage_reader, envelope, response_kind):
        observation = None
        saved = await self._calls.prepare_initial(owner, intent)
        intent = saved.intent
        if (saved.owner_epoch != owner.owner_epoch or saved.owner_task_id != owner.task_id
            or saved.owner_attempt_id != owner.attempt_id):
            raise FrameworkModelRecoveryRequired("position_owned_by_prior_attempt", saved)
        if self._evidence is not None and saved.request is None and saved.phase == "ready":
            request = await self._evidence.store_request(owner, intent, envelope)
            await self._calls.attach_request(owner, intent.attempt_id, request=request)
        current, permitted = await self._calls.start(owner, intent.attempt_id)
        if not permitted:
            logger.warning("framework_model_reentry_requires_reconciliation", extra={
                "binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id, "phase": current.phase})
            raise FrameworkModelRecoveryRequired("position_already_started", current)
        logger.info("framework_model_request_started", extra={"binding_id": owner.binding.binding_id,
            "model_attempt_id": intent.attempt_id, "logical_call_id": intent.logical_call_id,
            "branch_id": intent.branch_id, "purpose": intent.purpose, "transport": intent.transport})
        try:
            active_observation = current_provider_call_observation()
            if active_observation is None:
                with observe_provider_call(intent.logical_call_id, intent.transport) as created_observation:
                    result = await send()
                active_observation = created_observation
            else:
                result = await send()
        except (Exception, asyncio.CancelledError) as exc:
            code = "request_cancelled" if isinstance(exc, asyncio.CancelledError) else "provider_request_failed"
            try:
                await self._calls.mark_unknown(owner, intent.attempt_id, code=code)
            except Exception as accounting_error:
                # Keep the original send failure visible. A failed settlement
                # leaves invoking + its reservation for owner-bound reconciliation.
                logger.error("framework_model_unknown_settlement_failed", extra={
                    "binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
                    "error_type": type(accounting_error).__name__, "request_error_type": type(exc).__name__})
                exc.add_note("Model attempt remains reserved and requires durable reconciliation")
            raise
        if self._evidence is not None:
            try:
                response = await self._evidence.store_response(owner, intent, result, response_kind=response_kind)
                observation = await self._calls.append_response_observation(owner, intent.attempt_id,
                    response=response, response_kind=response_kind)
                wire = await self._evidence.store_wire(owner, intent, active_observation)
                logger.info("framework_model_wire_candidate", extra={
                    "binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
                    "has_observation": active_observation is not None,
                    "request_bytes": len(active_observation.request_body) if active_observation and active_observation.request_body is not None else 0,
                    "response_bytes": len(active_observation.response_body) if active_observation else 0,
                })
                if wire is not None:
                    await self._calls.append_wire_observation(owner, intent.attempt_id, observation=wire)
            except (Exception, asyncio.CancelledError) as exc:
                # The response has arrived. Preserve uncertainty and any saved
                # object even if its ledger reference could not be confirmed.
                logger.error("framework_model_response_evidence_unconfirmed", extra={
                    "binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
                    "response_sha256": response.sha256 if "response" in locals() else None,
                    "response_uri": response.storage_uri if "response" in locals() else None,
                    "error_type": type(exc).__name__})
                exc.add_note("Provider response arrived; reconcile evidence without resending the request")
                raise
        try:
            await self._calls.complete(owner, intent.attempt_id, usage=usage_reader(result),
                **({"response_id": observation.observation_id} if observation is not None else {}))
        except (Exception, asyncio.CancelledError) as exc:
            # An acknowledgement failure may occur before or after commit.
            # Preserve the durable phase; only reconciliation can decide which
            # outcome was saved. Never reissue the provider request here.
            logger.error("framework_model_response_settlement_failed", extra={
                "binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
                "error_type": type(exc).__name__})
            exc.add_note("Provider response arrived; verify the durable model attempt before continuation")
            raise
        return result
