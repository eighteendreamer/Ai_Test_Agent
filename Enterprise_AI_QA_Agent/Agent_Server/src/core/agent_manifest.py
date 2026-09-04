"""D3 Declarative Agent Manifest.

AgentManifest extends AgentDescriptor with routing-relevant metadata:
- capability_tags: what this agent can do (matched against intent required_tags)
- allowed_mode_keys: which testing modes this agent can operate in
- max_concurrent_instances: concurrency limit for this agent

This enables capability-based agent selection instead of keyword matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.schemas.agent import AgentDescriptor


@dataclass(frozen=True)
class AgentManifest:
    """Declarative agent registration record.

    Extends AgentDescriptor with routing-relevant metadata for the UnifiedRouter.
    """

    descriptor: AgentDescriptor
    capability_tags: list[str] = field(default_factory=list)
    allowed_mode_keys: list[str] | None = None
    max_concurrent_instances: int = 10

    @property
    def key(self) -> str:
        return self.descriptor.key

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def role(self) -> str:
        return self.descriptor.role

    def matches_mode(self, mode_key: str) -> bool:
        """Check if this agent can operate in the given mode."""
        if self.allowed_mode_keys is None:
            return True
        return mode_key in self.allowed_mode_keys

    def capability_score(self, required_tags: list[str], preferred_tags: list[str]) -> float:
        """Score this agent's capability match against intent tags.

        Returns a float in [0, 1] where:
        - 1.0 = all required tags matched + all preferred tags matched
        - 0.0 = no required tags matched
        """
        if not required_tags and not preferred_tags:
            return 0.5

        capability_set = set(self.capability_tags)
        required_matched = sum(1 for t in required_tags if t in capability_set)
        preferred_matched = sum(1 for t in preferred_tags if t in capability_set)

        required_total = len(required_tags) or 1
        preferred_total = len(preferred_tags) or 1

        required_score = required_matched / required_total
        preferred_score = preferred_matched / preferred_total

        return 0.7 * required_score + 0.3 * preferred_score
