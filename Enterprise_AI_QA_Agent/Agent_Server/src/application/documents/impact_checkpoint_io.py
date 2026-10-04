"""Shared immutable JSON objects and unconfirmed-write handling for impact."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from uuid import uuid4

from src.application.documents.impact_sources import canonical_impact_json
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_impact_calls import DocumentImpactCallObject

logger = logging.getLogger(__name__)


class ImpactCallCheckpointUnavailable(RuntimeError):
    """The last PG/object write is not confirmed; never infer permission to resend."""


async def store_impact_json(objects, claim, value, filename):
    encoded = await asyncio.to_thread(canonical_impact_json, value)
    saved = await objects.store_uploaded_bytes(content=encoded, filename=filename, content_type="application/json",
        object_prefix=f"document-impacts/{claim.task.project_id}/{claim.task.id}/{claim.attempt_id}/calls/{uuid4()}")
    return DocumentImpactCallObject(storage_uri=saved["uri"], sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded))


async def confirmed_impact_write(operation, *, task_id, stage):
    try:
        return await operation
    except ContinuationLeaseLostError:
        raise
    except (ValueError, KeyError, TimeoutError):
        raise
    except Exception as exc:
        logger.error("document_impact_call_checkpoint_unconfirmed", extra={"task_id": task_id,
            "stage": stage, "error_type": type(exc).__name__})
        raise ImpactCallCheckpointUnavailable("影响调用检查点提交尚不能确认，请只读回查") from exc
