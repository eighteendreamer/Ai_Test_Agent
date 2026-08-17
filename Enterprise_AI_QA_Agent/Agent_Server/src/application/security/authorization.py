from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse


def verified_grant_matches_target(grant: object, target_url: str) -> bool:
    """Return whether a server-trusted grant covers one concrete target.

    The grant is intentionally treated as untrusted input until its status,
    expiry, and host/port scope all match. Paths are not part of the existing
    authorization contract; the host and optional port are the stable scope
    boundary used by the security runtime.
    """
    if not isinstance(grant, dict) or str(grant.get("status") or "").lower() != "verified":
        return False
    target = str(target_url or "").strip()
    if not target:
        return False
    expires_at = str(grant.get("expires_at") or "").strip()
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                return False
        except ValueError:
            return False
    target_host, target_port = _target_coordinates(target)
    if not target_host:
        return False
    allowed_targets = [
        str(item).strip()
        for item in (grant.get("targets") or [])
        if str(item).strip()
    ]
    for allowed in allowed_targets:
        allowed_host, allowed_port = _target_coordinates(allowed)
        if target_host == allowed_host and (allowed_port is None or allowed_port == target_port):
            return True
    return False


def _target_coordinates(value: str) -> tuple[str, int | None]:
    text = str(value or "").strip()
    if not text:
        return "", None
    candidate = text if "://" in text else f"//{text}"
    try:
        parsed = urlparse(candidate)
        return (parsed.hostname or "").strip("[]").lower(), parsed.port
    except ValueError:
        return "", None


__all__ = ["verified_grant_matches_target"]
