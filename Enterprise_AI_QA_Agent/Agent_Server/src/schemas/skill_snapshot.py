from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillPromptSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    prompt_block: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content(self):
        if hashlib.sha256(self.prompt_block.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("Skill快照正文与哈希不一致")
        return self
