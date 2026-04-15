from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


EmailProvider = Literal["aliyun", "cybermail"]


class EmailConfigRecord(BaseModel):
    provider: EmailProvider
    enabled: bool = False
    is_default: bool = False
    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    access_key_id: str | None = None
    access_key_secret: str | None = None
    account_name: str | None = None
    region: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    use_tls: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmailConfigPublic(BaseModel):
    provider: EmailProvider
    enabled: bool = False
    is_default: bool = False
    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    access_key_id: str | None = None
    account_name: str | None = None
    region: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    use_tls: bool = True
    has_access_key_secret: bool = False
    has_smtp_password: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmailConfigUpdateRequest(BaseModel):
    provider: EmailProvider
    enabled: bool = False
    is_default: bool = False
    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    access_key_id: str | None = None
    access_key_secret: str | None = None
    account_name: str | None = None
    region: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    use_tls: bool = True


class SettingsBundle(BaseModel):
    model_configs: list[dict] = Field(default_factory=list)
    email_configs: list[EmailConfigPublic] = Field(default_factory=list)
