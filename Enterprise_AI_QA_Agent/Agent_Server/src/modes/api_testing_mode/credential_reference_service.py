"""Resolve private API material using server-persisted dispatch relationships."""
from __future__ import annotations

import logging

from src.modes.api_testing_mode.campaign_state import CredentialSession
from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError

logger = logging.getLogger("uvicorn.error.api_testing_mode.credentials")


class ApiCredentialReferenceService:
    # No upstream counterpart (checked: API runtime/coordinator receipts, channel
    # credential codec, PG business stores): redacted public output cannot carry
    # executable credentials; these references bind the existing owned receipts.
    def __init__(self, *, store, session_store):
        self._store = store
        self._sessions = session_store

    async def initialize(self):
        await self._store.initialize()

    async def _parent(self, session_id):
        parent = await self._sessions.get_session(session_id)
        if parent is None:
            raise ApiCredentialReferenceError("api_credential_owner_unavailable")
        return parent

    async def freeze_dispatch(self, *, parent_context, dispatch_task_id, task, token_field, credential):
        parent = await self._parent(parent_context["session_id"])
        turn = str(parent_context.get("turn_id") or "")
        if not turn:
            raise ApiCredentialReferenceError("api_credential_parent_turn_missing")
        if credential is not None:
            await self._store.save_credential(credential.model_dump(mode="json"),
                owner_session_id=parent.id, project_id=parent.project_id)
        # Also verify an inherited reference after the parent service was recreated.
        if task.get("auth_ref"):
            await self._store.load_credential(task["auth_ref"], owner_session_id=parent.id, project_id=parent.project_id)
        ref = await self._store.save_dispatch({"parent_turn_id": turn, "dispatch_task_id": dispatch_task_id,
            "task": task, "auth_token_field": token_field}, owner_session_id=parent.id, project_id=parent.project_id)
        logger.info("api_worker_input_frozen", extra={"parent_session_id": parent.id,
            "parent_turn_id": turn, "dispatch_task_id": dispatch_task_id, "dispatch_ref": ref})
        return ref

    async def resolve_dispatch(self, ref, context):
        child = await self._sessions.get_session(str(getattr(context, "session_id", "") or ""))
        if child is None or child.metadata.get("dispatch_role") != "api_execution_worker":
            raise ApiCredentialReferenceError("api_worker_dispatch_scope_invalid")
        meta = child.metadata
        parent = await self._parent(str(meta.get("parent_session_id") or ""))
        if child.project_id != parent.project_id:
            raise ApiCredentialReferenceError("api_worker_project_scope_invalid")
        turn, task_id = str(meta.get("parent_turn_id") or ""), str(meta.get("task_id") or "")
        # Existing coordinator registers receipts before starting the child.
        if not any(isinstance(r, dict) and r.get("child_session_id") == child.id
                   and r.get("parent_turn_id") == turn and r.get("task_id") == task_id
                   for r in parent.metadata.get("worker_dispatches", [])):
            raise ApiCredentialReferenceError("api_worker_dispatch_receipt_missing")
        payload = await self._store.load_dispatch(ref, owner_session_id=parent.id, project_id=parent.project_id)
        if payload["parent_turn_id"] != turn or payload["dispatch_task_id"] != task_id:
            raise ApiCredentialReferenceError("api_worker_dispatch_identity_invalid")
        credential = None
        if auth_ref := payload["task"].get("auth_ref"):
            credential = CredentialSession.model_validate(await self._store.load_credential(auth_ref,
                owner_session_id=parent.id, project_id=parent.project_id))
        logger.info("api_worker_input_resolved", extra={"child_session_id": child.id,
            "parent_session_id": parent.id, "dispatch_task_id": task_id, "dispatch_ref": ref})
        return payload, credential, parent

    async def save_worker_credential(self, credential, parent):
        await self._store.save_credential(credential.model_dump(mode="json"),
            owner_session_id=parent.id, project_id=parent.project_id)
        logger.info("api_worker_credential_saved", extra={"parent_session_id": parent.id,
            "credential_ref": credential.credential_session_id})

    async def save_parent_credential(self, credential, parent_context):
        parent = await self._parent(parent_context["session_id"])
        await self.save_worker_credential(credential, parent)

    async def load_parent_credential(self, ref, parent_context):
        parent = await self._parent(parent_context["session_id"])
        return CredentialSession.model_validate(await self._store.load_credential(ref,
            owner_session_id=parent.id, project_id=parent.project_id))
