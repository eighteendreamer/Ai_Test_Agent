from __future__ import annotations

from src.application.model_providers.adapters.standard_oauth import StandardOAuthProviderAdapter


class CodexOAuthProvider(StandardOAuthProviderAdapter):
    """Experimental OpenAI/Codex OAuth skeleton.

    This adapter intentionally reuses the standard browser OAuth flow so the
    existing UI can exercise the provider contract end-to-end. It remains
    disabled in provider presets until the endpoint contract is confirmed
    against official OpenAI documentation.
    """

    provider_key = "codex"

    def _get_provider_credentials(self) -> tuple[str, str, str | None]:
        return (
            self._settings.model.oauth_codex_client_id,
            self._settings.model.oauth_codex_client_secret,
            None,
        )
