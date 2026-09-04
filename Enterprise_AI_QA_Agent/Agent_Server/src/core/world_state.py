"""D4 World State: incremental context updates replacing full rebuild.

Each context section is independently snapshotted. Only changed sections
emit diffs, reducing prompt reconstruction cost from O(all) to O(changed).

Reference: Codex WorldStateSection pattern.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionSnapshot:
    """Immutable snapshot of a context section."""

    key: str
    content: str
    content_hash: str = ""

    def __post_init(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self, "content_hash",
                hashlib.sha256(self.content.encode()).hexdigest()[:16],
            )


@dataclass(frozen=True)
class ContextFragment:
    """A piece of rendered context: either full or delta."""

    kind: str  # "full" | "delta"
    section_key: str
    content: str
    priority: int = 0


class WorldStateSection(Protocol):
    """Protocol for a context section that supports incremental updates."""

    @property
    def key(self) -> str:
        ...

    @property
    def priority(self) -> int:
        ...

    def snapshot(self) -> SectionSnapshot:
        """Capture current state as an immutable snapshot."""
        ...

    def render_diff(self, prev: SectionSnapshot | None) -> str | None:
        """Render only the changed portion, or None if unchanged."""
        ...

    def render_full(self) -> str:
        """Render the full section content."""
        ...


@dataclass
class StaticSection:
    """A section with static content that rarely changes (identity, contract, mode)."""

    _key: str
    _content: str
    _priority: int = 0

    @property
    def key(self) -> str:
        return self._key

    @property
    def priority(self) -> int:
        return self._priority

    def snapshot(self) -> SectionSnapshot:
        return SectionSnapshot(key=self._key, content=self._content)

    def render_diff(self, prev: SectionSnapshot | None) -> str | None:
        if prev is not None and prev.content_hash == self.snapshot().content_hash:
            return None
        return None

    def render_full(self) -> str:
        return self._content


@dataclass
class DynamicSection:
    """A section with dynamic content that changes each turn (tools, permissions, memory)."""

    _key: str
    _priority: int = 10
    _content_provider: Any = None

    @property
    def key(self) -> str:
        return self._key

    @property
    def priority(self) -> int:
        return self._priority

    def snapshot(self) -> SectionSnapshot:
        content = self.render_full()
        return SectionSnapshot(key=self._key, content=content)

    def render_diff(self, prev: SectionSnapshot | None) -> str | None:
        current = self.snapshot()
        if prev is not None and prev.content_hash == current.content_hash:
            return None
        if prev is None:
            return current.content
        return f"[{self._key} updated]\n{current.content}"

    def render_full(self) -> str:
        if self._content_provider is not None:
            return str(self._content_provider())
        return ""


class WorldState:
    """Incremental context state manager.

    Maintains a list of sections and renders only changed sections as diffs.
    Unchanged sections are skipped, reducing prompt reconstruction cost.
    """

    def __init__(self) -> None:
        self._sections: dict[str, WorldStateSection] = {}
        self._prev_snapshots: dict[str, SectionSnapshot] = {}

    def register_section(self, section: WorldStateSection) -> None:
        """Register a context section."""
        self._sections[section.key] = section

    def unregister_section(self, key: str) -> None:
        """Remove a context section."""
        self._sections.pop(key, None)
        self._prev_snapshots.pop(key, None)

    def render(self) -> list[ContextFragment]:
        """Render all sections, emitting diffs for changed ones.

        On first call (no previous state), emits full content for all sections.
        On subsequent calls, only emits fragments for sections that changed.
        """
        fragments: list[ContextFragment] = []

        for key, section in sorted(self._sections.items(), key=lambda x: x[1].priority):
            current = section.snapshot()
            prev = self._prev_snapshots.get(key)

            if prev is not None and prev.content_hash == current.content_hash:
                continue

            if prev is None:
                fragments.append(ContextFragment(
                    kind="full",
                    section_key=key,
                    content=section.render_full(),
                    priority=section.priority,
                ))
            else:
                diff = section.render_diff(prev)
                if diff is not None:
                    fragments.append(ContextFragment(
                        kind="delta",
                        section_key=key,
                        content=diff,
                        priority=section.priority,
                    ))

            self._prev_snapshots[key] = current

        return fragments

    def get_snapshot(self, key: str) -> SectionSnapshot | None:
        """Get the last rendered snapshot for a section."""
        return self._prev_snapshots.get(key)

    def reset(self) -> None:
        """Clear all previous snapshots (force full re-render)."""
        self._prev_snapshots.clear()
