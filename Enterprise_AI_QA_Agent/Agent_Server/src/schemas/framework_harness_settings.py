"""Versioned SDK Harness settings, not frozen authorization or root budgets.

No upstream counterpart (checked: prepared graph inputs, RuntimeService,
Deep Agents 0.7.13 builder): checkpoints retain graph channels, not the
application configuration used to build that graph on the next Worker.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


HARNESS_SETTINGS_KEY = "framework_harness_settings"


class FrameworkHarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    pilot_stage: Literal["DA-E1", "DA-E2", "DA-E3", "DA-E4"]
    read_only_filesystem_enabled: bool
    read_only_max_file_size_mb: int = Field(ge=1)
    read_only_max_output_chars: int = Field(ge=1)
    cognitive_planning_enabled: bool
    cognitive_subagents_enabled: bool
    max_subagent_calls_per_turn: int = Field(ge=1)
    subagent_model_call_limit: int = Field(ge=1)
    subagent_tool_call_limit: int = Field(ge=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Harness settings require an integer schema version")
        return value

    @classmethod
    def capture(cls, request, *, pilot_stage):
        return cls(pilot_stage=pilot_stage, **{name: getattr(request, name)
            for name in cls.model_fields if name not in {"schema_version", "pilot_stage"}})

    def adapter_options(self):
        return self.model_dump(exclude={"schema_version", "pilot_stage"})
