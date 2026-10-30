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
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any
from uuid import uuid4

from langchain_core.messages import messages_to_dict
from pydantic import TypeAdapter

from src.application.models.framework_model_call_policy import canonical_model_input
from src.schemas.framework_model_call import FrameworkModelObject, FrameworkModelEvidenceUpload, model_evidence_prefix


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameworkModelUploadReadback:
    """Verified bytes at an original upload address, never execution authority."""

    upload: FrameworkModelEvidenceUpload
    content: bytes = field(repr=False)
    authoritative: bool = field(default=False, init=False)


class FrameworkModelEvidence:
    def __init__(self, *, objects, writes=None):
        self._objects = objects
        self._writes = writes

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
        # Commit the original target before PUT; uncertainty never causes a
        # replacement UUID, replay, cleanup, or an authoritative ledger receipt.
        upload_id = uuid4().hex
        prefix = model_evidence_prefix(owner.binding.binding_id, intent.attempt_id, upload_id)
        digest = hashlib.sha256(content).hexdigest()
        context = {"binding_id": owner.binding.binding_id, "model_attempt_id": intent.attempt_id,
            "evidence_stage": stage, "upload_id": upload_id, "object_prefix": prefix,
            "content_sha256": digest, "size_bytes": len(content)}
        logger.info("framework_model_evidence_storage_started", extra=context)
        try:
            arguments = dict(content=content, filename=filename or stage + ".json",
                object_prefix=prefix, content_type=content_type)
            target = None
            if self._writes is not None:
                description = self._objects.describe_uploaded_bytes(**arguments)
                target = FrameworkModelObject(storage_uri=description["uri"], sha256=digest, size_bytes=len(content))
                await self._writes.prepare_evidence_upload(owner, intent,
                    upload_id=upload_id, stage=stage, target=target)
                arguments.update(bucket_name=description["bucket"], object_name=description["object_name"])
            saved = await self._objects.store_uploaded_bytes(**arguments)
            reference = FrameworkModelObject(storage_uri=saved["uri"], sha256=digest, size_bytes=len(content))
            if target is not None and reference != target:
                raise ValueError("Model evidence storage returned a different upload identity")
        except (Exception, asyncio.CancelledError) as exc:
            logger.log(logging.WARNING if isinstance(exc, asyncio.CancelledError) else logging.ERROR,
                "framework_model_evidence_storage_unconfirmed", extra={**context, "error_type": type(exc).__name__})
            raise
        logger.info("framework_model_evidence_stored", extra={**context, "storage_uri": reference.storage_uri})
        return reference

    async def _store_bytes(self, owner, intent, content: bytes, stage: str):
        return await self._store(owner, intent, content, stage,
            filename=stage + ".bin", content_type="application/octet-stream")

    async def read(self, reference: FrameworkModelObject):
        return json.loads(await self._read_bytes(reference))

    async def _read_bytes(self, reference: FrameworkModelObject):
        reference = FrameworkModelObject.model_validate(reference.model_dump())
        loaded = await self._objects.read_object_uri(reference.storage_uri)
        content = loaded["content"]
        if len(content) != reference.size_bytes or hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ValueError("Model evidence object digest or size does not match its reference")
        return content

    async def read_upload(self, binding, upload_id: str) -> FrameworkModelUploadReadback:
        """Read only the durable address; an unreadable object proves no rollback."""
        if self._writes is None:
            raise ValueError("Model upload readback requires its durable journal")
        context = {"binding_id": binding.binding_id, "upload_id": upload_id, "authoritative": False}
        try:
            saved = await self._writes.get_evidence_upload(binding, upload_id)
            content = await self._read_bytes(saved.target)
            if saved.stage == "response":
                self._validate_response(binding, saved.intent, json.loads(content))
            if await self._writes.get_evidence_upload(binding, upload_id) != saved:
                raise ValueError("Model upload identity changed during readback")
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("framework_model_upload_readback_unconfirmed",
                extra={**context, "error_type": type(exc).__name__})
            raise
        logger.info("framework_model_upload_readback_verified", extra={**context, "evidence_stage": saved.stage})
        return FrameworkModelUploadReadback(upload=saved, content=content)

    async def read_response(self, binding, intent, reference: FrameworkModelObject):
        payload = await self.read(reference)
        return self._validate_response(binding, intent, payload)

    @staticmethod
    def _validate_response(binding, intent, payload):
        expected = {"schema_version": 1, "source": "sdk_application", "binding_id": binding.binding_id,
            "binding_fingerprint": binding.fingerprint, "attempt_id": intent.attempt_id,
            "logical_call_id": intent.logical_call_id, "request_sha256": intent.request_sha256}
        if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("Model response evidence identity does not match its original request")
        return payload
