from __future__ import annotations

from src.infrastructure.sponsor_config_store import (
    DEFAULT_SPONSORS,
    MySQLSponsorConfigStore,
)
from src.schemas.sponsor import SponsorRecord


def test_to_public_prefixes_local_logo_file() -> None:
    record = SponsorRecord(
        id=1,
        name="E-API",
        logo_file="e-api.png",
        website_url="https://api.ewo.so/",
        sponsor_type="中转站",
        sort_order=0,
        enabled=True,
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "/sponsors/e-api.png"
    assert public.name == "E-API"
    assert public.website_url == "https://api.ewo.so/"
    assert public.sponsor_type == "中转站"


def test_to_public_keeps_remote_logo_url() -> None:
    record = SponsorRecord(
        id=2,
        name="Remote",
        logo_file="https://cdn.example.com/logo.png",
        website_url="https://example.com/",
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "https://cdn.example.com/logo.png"


def test_to_public_normalizes_leading_slash() -> None:
    record = SponsorRecord(
        id=3,
        name="Slash",
        logo_file="/e-api.png",
        website_url="https://example.com/",
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "/sponsors/e-api.png"


def test_default_sponsors_seed_is_complete() -> None:
    assert len(DEFAULT_SPONSORS) >= 1
    for sponsor in DEFAULT_SPONSORS:
        assert sponsor.name.strip()
        assert sponsor.logo_file.strip()
        assert sponsor.website_url.startswith("https://")
        assert sponsor.sponsor_type.strip()
        assert sponsor.enabled
