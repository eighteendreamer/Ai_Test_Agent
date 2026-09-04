from __future__ import annotations

from src.application.model_providers.models import CompletedAuthFlow
from src.application.model_providers.adapters.standard_oauth import StandardOAuthProviderAdapter


class GitHubOAuthProvider(StandardOAuthProviderAdapter):
    provider_key = "github"

    def _get_provider_credentials(self) -> tuple[str, str, str | None]:
        return (
            self._settings.model.oauth_github_client_id,
            self._settings.model.oauth_github_client_secret,
            None,
        )

    def _build_model_list_headers(self) -> dict[str, str]:
        return {"Copilot-Integration-Id": "vscode-chat"}

    def get_persisted_token(self, completed: CompletedAuthFlow) -> str | None:
        return completed.refresh_token or completed.access_token or None

    async def get_runtime_token(self, config) -> str:
        token = str(getattr(config, "oauth_refresh_token", "") or "").strip()
        if not token:
            raise ValueError(
                "GitHub OAuth login did not return a usable token. Please authorize again and save the model configuration once the login succeeds."
            )
        return token
