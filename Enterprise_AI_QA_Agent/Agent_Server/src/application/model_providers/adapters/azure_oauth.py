from __future__ import annotations

from src.application.model_providers.adapters.standard_oauth import StandardOAuthProviderAdapter


class AzureOAuthProvider(StandardOAuthProviderAdapter):
    provider_key = "azure_ad"

    def _get_provider_credentials(self) -> tuple[str, str, str | None]:
        return (
            self._settings.model.oauth_azure_ad_client_id,
            self._settings.model.oauth_azure_ad_client_secret,
            self._settings.model.oauth_azure_ad_tenant_id,
        )
