from __future__ import annotations

from src.schemas.model_config import ModelConfigRecord
from src.application.model_providers.adapters.standard_oauth import StandardOAuthProviderAdapter


class GoogleOAuthProvider(StandardOAuthProviderAdapter):
    provider_key = "google"

    def _get_provider_credentials(self) -> tuple[str, str, str | None]:
        return (
            self._settings.model.oauth_google_client_id,
            self._settings.model.oauth_google_client_secret,
            None,
        )

    def _build_model_list_headers(self) -> dict[str, str]:
        if self._settings.model.oauth_google_project_id.strip():
            return {"x-goog-user-project": self._settings.model.oauth_google_project_id.strip()}
        return {}

    def build_runtime_headers(self, config: ModelConfigRecord, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
            **config.extra_headers,
        }
        if self._settings.model.oauth_google_project_id.strip():
            headers["x-goog-user-project"] = self._settings.model.oauth_google_project_id.strip()
        return headers
