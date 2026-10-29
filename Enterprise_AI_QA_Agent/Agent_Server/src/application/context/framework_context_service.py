"""One cross-Turn assembly for native input, its display projection and coverage."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Any

from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.deep_agents.framework_transcript import deserialize_messages, require_closed_transcript
from src.application.deep_agents.result_projection import _normalize_messages
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_transcript import (
    CONTEXT_RUNTIME_VERSION, IDENTITY_CONTEXT_RUNTIME_VERSIONS, TURN_CONTEXT_RUNTIME_VERSION,
    FrameworkTranscriptAssetV2, FrameworkTranscriptPreparation,
    TRANSCRIPT_PREPARATION_KEY, parse_framework_transcript_asset,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkContext:
    framework_messages: list[dict[str, Any]]
    runtime_messages: list[dict[str, Any]]
    preparation: dict[str, Any]


class FrameworkContextService:
    @staticmethod
    def compatible_asset(session, binding):
        raw = (session.metadata or {}).get("framework_transcript_asset")
        if raw is None:
            return None
        asset = parse_framework_transcript_asset(raw)
        if asset.session_id != session.id or asset.source_turn_id == binding.turn_id:
            raise ValueError("Framework transcript source does not match this new Turn")
        if binding.runtime_version in IDENTITY_CONTEXT_RUNTIME_VERSIONS:
            if not isinstance(asset, FrameworkTranscriptAssetV2):
                raise ValueError("Schema-1 transcript requires an explicit coverage migration")
            if asset.model_config_fingerprint != binding.model_config_fingerprint:
                raise ValueError("Framework transcript model configuration is incompatible")
            if binding.runtime_version == CONTEXT_RUNTIME_VERSION and asset.runtime_version != CONTEXT_RUNTIME_VERSION:
                raise ValueError("Legacy context runtime cannot consume a newer transcript version")
            if (binding.runtime_version == TURN_CONTEXT_RUNTIME_VERSION
                and asset.runtime_version == TURN_CONTEXT_RUNTIME_VERSION
                and (asset.model_provider != binding.model_provider
                     or asset.model_transport != binding.model_transport)):
                raise ValueError("Framework transcript provider/transport protocol is incompatible")
        elif isinstance(asset, FrameworkTranscriptAssetV2):
            raise ValueError("Legacy runtime cannot consume identity-covered transcripts")
        require_closed_transcript(asset.framework_messages, session_id=session.id,
            source_turn_id=asset.source_turn_id, operation="import")
        return asset

    @staticmethod
    def _history(session, request):
        hygiene = TranscriptHygieneService()
        messages = session.messages
        if len({item.id for item in messages}) != len(messages):
            raise ValueError("Framework context contains duplicate business message identities")
        position = next((i for i, item in enumerate(messages) if item.id == request.user_message_id), None)
        if position is None:
            raise ValueError("Framework context requires its admitted user message identity")
        current = messages[position]
        if (current.role.value != "user" or current.metadata.get("turn_id") != request.turn_id
            or not hygiene.is_context_eligible(current)
            or current.content.strip() != request.user_message.strip()):
            raise ValueError("Admitted user message does not match the execution request")
        user_turns = {item.metadata.get("turn_id") for item in messages if item.role.value == "user"}
        predecessors = {}
        for index, item in enumerate(messages[:position]):
            turn_id = item.metadata.get("turn_id")
            if (item.role.value == "user" and turn_id and turn_id != request.turn_id
                and item.metadata.get("delivery_status") != "queued" and hygiene.is_context_eligible(item)):
                predecessors.setdefault(turn_id, index)
        ordered = []
        for index, item in enumerate(messages):
            if item.id == current.id:
                continue
            turn_id = item.metadata.get("turn_id")
            if (not hygiene.is_context_eligible(item) or item.role.value not in {"user", "assistant", "system"}
                or item.metadata.get("delivery_status") == "queued" or turn_id == request.turn_id):
                continue
            if turn_id in predecessors:
                # A completed predecessor's answer may be persisted after the
                # current queued user row. Order by its admitted Turn, not row time.
                rank = (predecessors[turn_id], 0 if item.role.value == "user" else 1, index)
            elif index < position and (not turn_id or turn_id not in user_turns):
                rank = (index, 0, index)
            else:
                continue
            ordered.append((rank, item))
        return [item for _, item in sorted(ordered, key=lambda pair: pair[0])] + [current]

    def prepare(self, session, request) -> FrameworkContext:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_to_dict

        binding = request.runtime_binding
        try:
            asset = self.compatible_asset(session, binding)
            if (session.metadata.get("context_summary") or {}).get("summary"):
                raise ValueError("Legacy context summary requires an identity-aware coverage migration")
            history = self._history(session, request)
            covered_ids = [item.id for item in history]
            prefix = deepcopy(asset.framework_messages) if asset is not None else []
            mapping = deepcopy(asset.business_message_map) if asset is not None else {}
            consumed = len(asset.covered_message_ids) if asset is not None else 0
            if asset is not None:
                if covered_ids[:consumed] != asset.covered_message_ids:
                    raise ValueError("Framework transcript coverage does not match admitted business history")
                by_id = {item.id: item for item in history}
                source = by_id[asset.source_input_message_id]
                if source.role.value != "user" or source.metadata.get("turn_id") != asset.source_turn_id:
                    raise ValueError("Framework transcript source input has no matching business Turn")
                if any(by_id[identity].role.value != "assistant"
                    or by_id[identity].metadata.get("turn_id") != asset.source_turn_id
                    for identity in asset.source_output_message_ids):
                    raise ValueError("Framework transcript source outputs have no matching business Turn")
            classes = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
            for item in history[consumed:]:
                prefix.extend(messages_to_dict([classes[item.role.value](id=item.id, content=item.content)]))
                mapping[item.id] = [item.id]
            preparation = FrameworkTranscriptPreparation(session_id=session.id, turn_id=request.turn_id,
                source_input_message_id=request.user_message_id, covered_message_ids=covered_ids,
                business_message_map=mapping, model_config_fingerprint=binding.model_config_fingerprint,
                model_provider=(binding.model_provider if binding.runtime_version == TURN_CONTEXT_RUNTIME_VERSION else None),
                model_transport=(binding.model_transport if binding.runtime_version == TURN_CONTEXT_RUNTIME_VERSION else None),
                input_message_count=len(prefix), input_messages_sha256=approval_value_hash(prefix))
            # Native serialization stays untouched; display is a one-way projection.
            result = FrameworkContext(prefix, _normalize_messages(deserialize_messages(prefix)),
                preparation.model_dump(mode="json"))
        except ValueError as exc:
            logger.warning("framework_context_rejected", extra={"session_id": session.id,
                "turn_id": request.turn_id, "error_type": type(exc).__name__})
            raise
        logger.info("framework_context_prepared", extra={"session_id": session.id, "turn_id": request.turn_id,
            "covered_message_count": len(covered_ids), "native_message_count": len(prefix),
            "imported_message_count": len(asset.framework_messages) if asset is not None else 0})
        return result

    @staticmethod
    def publish(session_id, state, *, assistant_message_id):
        context = state["context_bundle"]
        binding = context["runtime_binding"]
        preparation = FrameworkTranscriptPreparation.model_validate(context.get(TRANSCRIPT_PREPARATION_KEY))
        if (preparation.session_id != session_id or preparation.turn_id != state["turn_id"]
            or binding["session_id"] != session_id or binding["turn_id"] != state["turn_id"]
            or preparation.model_config_fingerprint != binding["model_config_fingerprint"]):
            raise ValueError("Transcript preparation does not match its original binding")
        if binding["runtime_version"] == TURN_CONTEXT_RUNTIME_VERSION:
            protocol = dict(context.get("framework_model_protocol") or {})
            if (preparation.model_provider != binding.get("model_provider")
                or preparation.model_transport != binding.get("model_transport")
                or protocol != {"provider": binding.get("model_provider"),
                                "transport": binding.get("model_transport")}):
                raise ValueError("Transcript model protocol does not match its original binding")
        messages = state["framework_messages"]
        prefix = messages[:preparation.input_message_count]
        if approval_value_hash(prefix) != preparation.input_messages_sha256:
            raise ValueError("Framework transcript prefix changed; coverage needs explicit summary reconciliation")
        outputs = [assistant_message_id] if assistant_message_id else []
        mapping = deepcopy(preparation.business_message_map)
        if outputs:
            native_outputs = [item["data"].get("id") for item in messages[preparation.input_message_count:]
                if item.get("type") == "ai"]
            if not native_outputs:
                raise ValueError("Business output has no original native output range")
            mapping[assistant_message_id] = native_outputs
        payload = dict(schema_version=2, runtime_kind="deepagents", runtime_version=binding["runtime_version"],
            session_id=session_id, source_turn_id=preparation.turn_id, framework_messages=messages,
            source_input_message_id=preparation.source_input_message_id, source_output_message_ids=outputs,
            covered_message_ids=[*preparation.covered_message_ids, *outputs], business_message_map=mapping,
            model_config_fingerprint=preparation.model_config_fingerprint,
            serializer="langchain.messages.v1")
        if preparation.model_provider is not None:
            payload["model_provider"] = preparation.model_provider
        if preparation.model_transport is not None:
            payload["model_transport"] = preparation.model_transport
        return FrameworkTranscriptAssetV2(**payload, content_sha256=approval_value_hash(payload)).model_dump(
            mode="json", exclude_none=True)
