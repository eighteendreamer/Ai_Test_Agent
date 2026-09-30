"""Central Bearer authentication for the HTTP API."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any

from starlette.responses import JSONResponse

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
LOGGER = logging.getLogger(__name__)


def _is_local_client(scope: Scope) -> bool:
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return False

    host = str(client[0]).strip()
    if host.casefold() == "testclient":
        return True

    try:
        address = ip_address(host.split("%", 1)[0])
    except ValueError:
        return False

    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return isinstance(address, (IPv4Address, IPv6Address)) and address.is_loopback


class APIAuthenticationMiddleware:
    """Require a configured Bearer token, with local-only development fallback.

    OAuth authorization-code callbacks and the minimal liveness endpoint are
    intentionally public. OAuth start/status and mail webhooks remain protected.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        api_token: str | None,
        api_v1_prefix: str,
        app_env: str,
    ) -> None:
        self.app = app
        normalized_token = (api_token or "").strip()
        self.api_token = normalized_token or None
        self.api_v1_prefix = "/" + api_v1_prefix.strip("/")
        self.app_env = app_env

        if self.api_token:
            LOGGER.info("api_authentication_enabled", extra={"app_env": app_env})
        else:
            LOGGER.warning(
                "api_authentication_disabled",
                extra={"app_env": app_env, "access_scope": "loopback_only"},
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "/")
        method = str(scope.get("method") or "GET").upper()
        if self._is_public_route(path, method):
            await self.app(scope, receive, send)
            return

        if self.api_token is None:
            if _is_local_client(scope):
                await self.app(scope, receive, send)
                return
            LOGGER.warning(
                "api_authentication_unconfigured_remote_rejected",
                extra={
                    "http_path": path,
                    "http_method": method,
                    "client_host": self._client_host(scope),
                },
            )
            await JSONResponse(
                status_code=503,
                content={
                    "detail": "API_AUTH_TOKEN is required for non-loopback API access."
                },
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        authorization_values = [
            value
            for key, value in scope.get("headers", [])
            if key.lower() == b"authorization"
        ]
        if len(authorization_values) != 1 or not self._is_valid_bearer(
            authorization_values[0]
        ):
            LOGGER.warning(
                "api_authentication_rejected",
                extra={
                    "http_path": path,
                    "http_method": method,
                    "client_host": self._client_host(scope),
                },
            )
            await JSONResponse(
                status_code=401,
                content={"detail": "A valid Bearer token is required."},
                headers={
                    "Cache-Control": "no-store",
                    "WWW-Authenticate": "Bearer",
                },
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _is_valid_bearer(self, authorization: bytes) -> bool:
        scheme, separator, candidate = authorization.partition(b" ")
        candidate = candidate.strip()
        return bool(
            separator
            and scheme.lower() == b"bearer"
            and candidate
            and self.api_token
            and hmac.compare_digest(candidate, self.api_token.encode("utf-8"))
        )

    def _is_public_route(self, path: str, method: str) -> bool:
        if method == "OPTIONS":
            return True

        if method == "GET" and path == f"{self.api_v1_prefix}/health/live":
            return True

        if method != "GET":
            return False

        oauth_prefix = f"{self.api_v1_prefix}/oauth"
        if path == f"{oauth_prefix}/callback":
            return True

        callback_prefix = f"{oauth_prefix}/"
        if not path.startswith(callback_prefix) or not path.endswith("/callback"):
            return False
        return len(path[len(callback_prefix) :].split("/")) == 2

    @staticmethod
    def _client_host(scope: Scope) -> str | None:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])
        return None
