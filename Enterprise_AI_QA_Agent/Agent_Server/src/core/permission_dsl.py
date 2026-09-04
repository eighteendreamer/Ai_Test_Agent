from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class PermissionRule:
    tool_pattern: str
    arg_pattern: str | None = None
    decision: Literal["allow", "deny", "ask"] = "allow"
    source: Literal["user", "project", "enterprise"] = "user"
    priority: int = 0

    def matches(self, tool_key: str, arguments: dict[str, Any] | None = None) -> bool:
        if not fnmatch.fnmatch(tool_key, self.tool_pattern):
            return False
        if self.arg_pattern and arguments:
            args_str = str(sorted(arguments.items()))
            return fnmatch.fnmatch(args_str, self.arg_pattern)
        return True


class PermissionRuleEngine:
    SOURCE_PRIORITY = {"enterprise": 200, "project": 100, "user": 0}

    def __init__(self) -> None:
        self._rules: list[PermissionRule] = []

    def add_rule(self, rule: PermissionRule) -> None:
        effective_priority = self.SOURCE_PRIORITY.get(rule.source, 0) + rule.priority
        self._rules.append(
            PermissionRule(
                tool_pattern=rule.tool_pattern,
                arg_pattern=rule.arg_pattern,
                decision=rule.decision,
                source=rule.source,
                priority=effective_priority,
            )
        )
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, tool_pattern: str, source: Literal["user", "project", "enterprise"] | None = None) -> int:
        before = len(self._rules)
        self._rules = [
            r for r in self._rules
            if not (r.tool_pattern == tool_pattern and (source is None or r.source == source))
        ]
        return before - len(self._rules)

    def evaluate(self, tool_key: str, arguments: dict[str, Any] | None = None) -> Literal["allow", "deny", "ask"] | None:
        for rule in self._rules:
            if rule.matches(tool_key, arguments):
                return rule.decision
        return None

    def list_rules(
        self,
        source: Literal["user", "project", "enterprise"] | None = None,
    ) -> list[PermissionRule]:
        if source is None:
            return list(self._rules)
        return [r for r in self._rules if r.source == source]

    def load_rules(self, rules: list[dict[str, Any]]) -> None:
        for r in rules:
            self.add_rule(PermissionRule(
                tool_pattern=r["tool_pattern"],
                arg_pattern=r.get("arg_pattern"),
                decision=r.get("decision", "allow"),
                source=r.get("source", "user"),
                priority=r.get("priority", 0),
            ))

    def clear(self, source: Literal["user", "project", "enterprise"] | None = None) -> None:
        if source is None:
            self._rules.clear()
        else:
            self._rules = [r for r in self._rules if r.source != source]
