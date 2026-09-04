from __future__ import annotations

import secrets
import urllib.parse
from typing import Any

import httpx

from src.application.model_providers.models import AuthStartResult, CompletedAuthFlow, PendingAuthFlow
from src.application.model_providers.provider_base import ModelProviderAdapter
from src.application.models.oauth_provider_profiles import get_profile
from src.schemas.oauth import OAuthStartRequest, OAuthStatusResponse


class CodeBuddyPollingProvider(ModelProviderAdapter):
    provider_key = "codebuddy"

    async def start_auth(self, request: OAuthStartRequest) -> AuthStartResult:
        profile = self._get_profile()
        client_id, client_secret = self._get_provider_credentials()
        if not client_id or not client_secret:
            raise ValueError(
                "CodeBuddy OAuth credentials are not configured in .env. "
                "Please set OAUTH_CODEBUDDY_CLIENT_ID and OAUTH_CODEBUDDY_CLIENT_SECRET first."
            )

        authorization_url = profile.authorization_url_template.strip()
        if not authorization_url:
            raise ValueError("CodeBuddy authorization endpoint is not configured.")

        state = secrets.token_urlsafe(32)
        params: dict[str, str] = {
            "client_id": client_id,
            "state": state,
            "scope": profile.default_scope,
            **dict(profile.extra_auth_params),
        }
        if request.redirect_uri:
            params["redirect_uri"] = request.redirect_uri

        authorization_url = f"{authorization_url}?{urllib.parse.urlencode(params)}"

        self._flow_store.set_pending(
            state,
            PendingAuthFlow(
                provider=self.provider_key,
                client_id=client_id,
                client_secret=client_secret,
                token_url=self._get_poll_url(),
                redirect_uri=request.redirect_uri,
                code_verifier="",
                scope=profile.default_scope,
            ),
        )

        return AuthStartResult(
            state=state,
            authorization_url=authorization_url,
            redirect_uri=request.redirect_uri,
        )

    async def poll_auth(self, state: str) -> None:
        pending = self._flow_store.get_pending(state)
        if pending is None:
            return

        poll_url = pending.token_url.strip()
        if not poll_url:
            self._flow_store.mark_failed(
                state,
                "CodeBuddy polling endpoint is not configured. "
                "Please set OAUTH_CODEBUDDY_POLL_URL before using this provider.",
            )
            self._flow_store.pop_pending(state)
            return

        payload = await self._request_poll_status(
            poll_url=poll_url,
            state=state,
            client_id=pending.client_id,
            client_secret=pending.client_secret,
        )

        status = self._extract_status(payload)
        if status == "pending":
            return

        self._flow_store.pop_pending(state)
        if status == "completed":
            access_token = self._extract_token(payload, "access_token")
            refresh_token = self._extract_token(payload, "refresh_token")
            if not access_token:
                self._flow_store.mark_failed(
                    state,
                    "CodeBuddy polling returned success but no access_token was found in the response.",
                )
                return
            self._flow_store.set_completed(
                state,
                CompletedAuthFlow(
                    provider=self.provider_key,
                    status="completed",
                    access_token=access_token,
                    refresh_token=refresh_token,
                ),
            )
            return

        self._flow_store.mark_failed(state, self._extract_error(payload))

    async def handle_callback(self, *, code: str, state: str, error: str | None = None) -> None:
        self._flow_store.mark_failed(
            state,
            error
            or "CodeBuddy is configured as a polling provider. "
            "This provider does not complete authorization through the shared callback route.",
        )

    def get_flow_status(self, state: str) -> OAuthStatusResponse:
        if self._flow_store.get_pending(state) is not None:
            return OAuthStatusResponse(state=state, status="pending")

        completed = self._flow_store.get_completed(state)
        if completed is None:
            return OAuthStatusResponse(state=state, status="failed", error="OAuth state not found or already consumed.")

        preview = (completed.access_token[:20] + "...") if completed.access_token else None
        return OAuthStatusResponse(
            state=state,
            status=completed.status,
            refresh_token=completed.refresh_token or None,
            access_token_preview=preview,
            error=completed.error or None,
        )

    async def list_models(self, *, state: str | None = None, base_url: str | None = None) -> list[dict]:
        completed = self._require_completed_flow(state)
        models_url = (base_url or self._settings.model.oauth_codebuddy_models_endpoint).strip()
        if not models_url:
            raise ValueError(
                "CodeBuddy model listing endpoint is not configured. "
                "Please set OAUTH_CODEBUDDY_MODELS_ENDPOINT or provide a Base URL first."
            )

        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.get(
                models_url,
                headers={
                    "Authorization": f"Bearer {completed.access_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()

        raw_items = payload.get("data", payload.get("models", payload))
        if not isinstance(raw_items, list):
            return []

        results: list[dict] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
            if not model_id:
                continue
            display_name = str(item.get("name") or item.get("display_name") or model_id).strip()
            results.append({"id": model_id, "raw_id": model_id, "name": display_name})
        return results

    async def get_runtime_token(self, config) -> str:
        refresh_token = str(getattr(config, "oauth_refresh_token", "") or "").strip()
        if not refresh_token:
            raise ValueError(
                "CodeBuddy runtime token is unavailable because the model configuration does not contain an OAuth refresh token."
            )
        token_url = self._get_profile().token_url_template.strip()
        if not token_url:
            raise ValueError("CodeBuddy token endpoint is not configured, so runtime refresh is unavailable.")
        access_token, _, _ = await self._refresh_access_token(
            token_url=token_url,
            refresh_token=refresh_token,
        )
        return access_token

    async def _request_poll_status(
        self,
        *,
        poll_url: str,
        state: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        payload = {
            "state": state,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.post(
                poll_url,
                json=payload,
                headers={"Accept": "application/json"},
            )
            if response.status_code in (404, 405, 415):
                response = await client.get(
                    poll_url,
                    params={"state": state, "client_id": client_id},
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("CodeBuddy polling endpoint returned a non-object JSON payload.")
            return data

    def _extract_status(self, payload: dict[str, Any]) -> str:
        status = self._pick_string(
            payload,
            ("status",),
            ("data", "status"),
            ("result", "status"),
        ).lower()
        if status in {"completed", "success", "authorized", "done"}:
            return "completed"
        if status in {"failed", "error", "denied", "cancelled", "canceled"}:
            return "failed"
        if self._extract_token(payload, "access_token"):
            return "completed"
        return "pending"

    def _extract_error(self, payload: dict[str, Any]) -> str:
        return self._pick_string(
            payload,
            ("error_description",),
            ("error",),
            ("message",),
            ("data", "error_description"),
            ("data", "error"),
            ("data", "message"),
            ("result", "error_description"),
            ("result", "error"),
            ("result", "message"),
        ) or "CodeBuddy polling reported a failed authorization."

    def _extract_token(self, payload: dict[str, Any], field: str) -> str:
        return self._pick_string(
            payload,
            (field,),
            ("data", field),
            ("result", field),
            ("tokens", field),
            ("data", "tokens", field),
        )

    def _pick_string(self, payload: dict[str, Any], *paths: tuple[str, ...]) -> str:
        for path in paths:
            current: Any = payload
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            if isinstance(current, str) and current.strip():
                return current.strip()
        return ""

    def _require_completed_flow(self, state: str | None) -> CompletedAuthFlow:
        if not state:
            raise ValueError("CodeBuddy model listing requires a completed OAuth state.")
        completed = self._flow_store.get_completed(state)
        if completed is None or completed.status != "completed" or not completed.access_token:
            raise ValueError("CodeBuddy authorization is not completed yet. Please finish browser login first.")
        return completed

    def _get_profile(self):
        profile = get_profile(self.provider_key)
        if not profile:
            raise ValueError("Unknown OAuth provider 'codebuddy'.")
        return profile

    def _get_provider_credentials(self) -> tuple[str, str]:
        return (
            self._settings.model.oauth_codebuddy_client_id.strip(),
            self._settings.model.oauth_codebuddy_client_secret.strip(),
        )

    def _get_poll_url(self) -> str:
        return self._settings.model.oauth_codebuddy_poll_url.strip()

    async def _refresh_access_token(
        self,
        *,
        token_url: str,
        refresh_token: str,
    ) -> tuple[str, str, dict[str, Any]]:
        client_id, client_secret = self._get_provider_credentials()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
            if response.status_code in (404, 405, 415):
                response = await client.post(
                    token_url,
                    json=data,
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("CodeBuddy token endpoint returned a non-object JSON payload.")

        access_token = self._extract_token(payload, "access_token")
        next_refresh_token = self._extract_token(payload, "refresh_token")
        if not access_token:
            raise ValueError("CodeBuddy token endpoint returned no access_token for the refresh-token grant.")
        return access_token, next_refresh_token, payload
