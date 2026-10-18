"""Resolve private API material using server-persisted dispatch relationships."""
from __future__ import annotations

import logging
from types import SimpleNamespace

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

    async def save_worker_result(self, *, dispatch_ref, context, result):
        _, _, parent = await self.resolve_dispatch(dispatch_ref, context)
        job_id = str(getattr(context, "tool_job_id", "") or "")
        if not job_id:
            raise ApiCredentialReferenceError("api_worker_result_job_identity_missing")
        ref = await self._store.save_worker_result({"dispatch_ref": dispatch_ref,
            "child_session_id": context.session_id, "tool_job_id": job_id, "task_result": result},
            owner_session_id=parent.id, project_id=parent.project_id)
        logger.info("api_worker_original_result_saved", extra={"parent_session_id": parent.id,
            "child_session_id": context.session_id, "tool_job_id": job_id, "private_result_ref": ref})
        return ref

    async def load_worker_result(self, ref, *, dispatch_ref, job, session, parent_context):
        frozen, _, parent = await self.resolve_dispatch(dispatch_ref, SimpleNamespace(session_id=session.id))
        if (parent.id != parent_context.get("session_id") or session.project_id != parent.project_id
            or frozen["parent_turn_id"] != parent_context.get("turn_id") or job.session_id != session.id):
            raise ApiCredentialReferenceError("api_worker_result_scope_invalid")
        payload = await self._store.load_worker_result(ref, owner_session_id=parent.id, project_id=parent.project_id)
        if (payload.get("dispatch_ref") != dispatch_ref or payload.get("child_session_id") != session.id
            or payload.get("tool_job_id") != job.id):
            raise ApiCredentialReferenceError("api_worker_result_binding_invalid")
        logger.info("api_worker_original_result_resolved", extra={"parent_session_id": parent.id,
            "child_session_id": session.id, "tool_job_id": job.id, "private_result_ref": ref})
        return payload["task_result"]
