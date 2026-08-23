from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SponsorRecord(BaseModel):
    id: int | None = None
    name: str
    logo_file: str
    website_url: str
    sponsor_type: str = ""
    sort_order: int = 0
    enabled: bool = True
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SponsorPublic(BaseModel):
    id: int
    name: str
    logo_url: str
    website_url: str
    sponsor_type: str = ""
    description: str | None = None
