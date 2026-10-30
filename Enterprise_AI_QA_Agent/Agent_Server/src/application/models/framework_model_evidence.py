"""Immutable application model evidence, using the existing object service.

These are application envelopes and SDK outputs, not captured HTTP bodies.
Object uploads and PostgreSQL references are separate commits: an unconfirmed
write must be reconciled, never interpreted as permission to resend a model.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from importlib.metadata import version
from typing import Any
from uuid import uuid4

from langchain_core.messages import messages_to_dict
from pydantic import TypeAdapter

from src.application.models.framework_model_call_policy import canonical_model_input
from src.schemas.framework_model_call import FrameworkModelObject


logger = logging.getLogger(__name__)


class FrameworkModelEvidence:
    def __init__(self, *, objects):
        self._objects = objects

    async def store_request(self, owner, intent, envelope: bytes) -> FrameworkModelObject:
        if hashlib.sha256(envelope).hexdigest() != intent.request_sha256:
            raise ValueError("Model request envelope does not match its intent digest")
        return await self._store(owner, intent, envelope, "request")

    async def store_response(self, owner, intent, result, *, response_kind: str) -> FrameworkModelObject:
        if response_kind == "langchain_messages":
            messages = result.result if hasattr(result, "result") else [result]
            data = {"messages": messages_to_dict(messages),
                "structured_response": getattr(result, "structured_response", None)}
            serializer = {"name": "langchain.messages_to_dict", "version": version("langchain-core")}
        elif response_kind == "model_invocation_result":
            # The input has its own immutable asset. Do not copy credentials or
            # transport clients; only the existing application DTO is serialized.
            data = result.model_dump(mode="json", exclude={"request_payload"})
            serializer = {"name": "ModelInvocationResult", "version": "1"}
        else:
            raise ValueError("Unsupported application model response kind")
        payload = {"schema_version": 1, "source": "sdk_application", "kind": response_kind,
            "binding_id": owner.binding.binding_id, "binding_fingerprint": owner.binding.fingerprint,
            "attempt_id": intent.attempt_id, "logical_call_id": intent.logical_call_id,
            "request_sha256": intent.request_sha256, "serializer": serializer,
            "data": TypeAdapter(Any).dump_python(data, mode="json")}
        return await self._store(owner, intent, canonical_model_input(payload), "response")

    async def store_wire(self, owner, intent, observation):
        if observation is None or observation.request_body is None and not observation.response_body:
            return None
        request = (await self._store_bytes(owner, intent, observation.request_body, "wire-request")
            if observation.request_body is not None else None)
        response = (await self._store_bytes(owner, intent, observation.response_body, "wire-response")
            if observation.response_body else None)
        return {
            "request": request, "response": response,
            "request_headers": observation.request_headers,
            "response_headers": observation.response_headers,
            "response_status_code": observation.response_status_code,
            "request_body_truncated": observation.request_body_truncated,
            "response_body_truncated": observation.response_body_truncated,
            "response_complete": observation.response_complete,
            "attempt_sequence": observation.request_attempt_count,
        }

    async def _store(self, owner, intent, content, stage, *, filename=None, content_type="application/json"):
        # UUID write identities avoid overwriting an earlier/uncertain upload.
        # As in impact_checkpoint_io, retain unconfirmed objects. These logs
        # identify candidates for readback, not a durable reconciliation queue.
        prefix = "framework-models/" + hashlib.sha256(owner.binding.binding_id.encode()).hexdigest()
        prefix += "/" + hashlib.sha256(intent.attempt_id.encode()).hexdigest() + "/" + uuid4().hex
        digest = hashlib.sha256(content).hexdigest()
        context = {"binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
            "evidence_stage": stage, "object_prefix": prefix, "content_sha256": digest, "size_bytes": len(content)}
        logger.info("framework_model_evidence_storage_started", extra=context)
        try:
            saved = await self._objects.store_uploaded_bytes(content=content, filename=filename or stage + ".json",
                object_prefix=prefix, content_type=content_type)
        except (Exception, asyncio.CancelledError) as exc:
            logger.log(logging.WARNING if isinstance(exc, asyncio.CancelledError) else logging.ERROR,
                "framework_model_evidence_storage_unconfirmed", extra={**context, "error_type": type(exc).__name__})
            raise
        reference = FrameworkModelObject(storage_uri=saved["uri"], sha256=digest, size_bytes=len(content))
        logger.info("framework_model_evidence_stored", extra={**context, "storage_uri": reference.storage_uri})
        return reference

    async def _store_bytes(self, owner, intent, content: bytes, stage: str):
        return await self._store(owner, intent, content, stage,
            filename=stage + ".bin", content_type="application/octet-stream")

    async def read(self, reference: FrameworkModelObject):
        reference = FrameworkModelObject.model_validate(reference.model_dump())
        loaded = await self._objects.read_object_uri(reference.storage_uri)
        content = loaded["content"]
        if len(content) != reference.size_bytes or hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ValueError("Model evidence object digest or size does not match its reference")
        return json.loads(content)

    async def read_response(self, binding, intent, reference: FrameworkModelObject):
        payload = await self.read(reference)
        expected = {"schema_version": 1, "source": "sdk_application", "binding_id": binding.binding_id,
            "binding_fingerprint": binding.fingerprint, "attempt_id": intent.attempt_id,
            "logical_call_id": intent.logical_call_id, "request_sha256": intent.request_sha256}
        if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("Model response evidence identity does not match its original request")
        return payload
