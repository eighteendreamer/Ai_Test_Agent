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
    parsed_target = urlparse(target)
    target_host = (parsed_target.hostname or "").lower()
    target_port = parsed_target.port
    if not target_host:
        return False
    allowed_targets = [
        str(item).strip()
        for item in (grant.get("targets") or [])
        if str(item).strip()
    ]
    for allowed in allowed_targets:
        parsed_allowed = urlparse(allowed)
        allowed_host = (parsed_allowed.hostname or allowed.split(":", 1)[0]).strip("[]").lower()
        allowed_port = parsed_allowed.port
        if target_host == allowed_host and (allowed_port is None or allowed_port == target_port):
            return True
    return False


__all__ = ["verified_grant_matches_target"]
