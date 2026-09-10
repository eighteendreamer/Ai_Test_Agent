from __future__ import annotations

import re
from typing import Any


def validate_tool_input(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Shared Registry validation, extracted from the legacy tool executor."""
    errors: list[str] = []
    properties = dict(schema.get("properties") or {})
    for key in schema.get("required") or []:
        value = arguments.get(key)
        if key not in arguments or value is None or value == "":
            errors.append(f"Missing required field: {key}")
    for key, value in arguments.items():
        rule = properties.get(key)
        if not isinstance(rule, dict) or value is None:
            continue
        expected = rule.get("type")
        if expected == "array" and not isinstance(value, list):
            errors.append(f"Field '{key}' must be an array.")
            continue
        if expected == "string" and not isinstance(value, str):
            errors.append(f"Field '{key}' must be a string.")
            continue
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"Field '{key}' must be an integer.")
            continue
        if expected == "boolean" and not isinstance(value, bool):
            errors.append(f"Field '{key}' must be a boolean.")
            continue
        if expected == "array" and len(value) < int(rule.get("minItems") or 0):
            errors.append(f"Field '{key}' must contain at least {rule.get('minItems')} item(s).")
        item_rule = rule.get("items") if expected == "array" else None
        if isinstance(item_rule, dict) and item_rule.get("format") == "email":
            invalid = [item for item in value if not re.fullmatch(
                r"[^\s@]+@[^\s@]+\.[^\s@]+", str(item or "").strip()
            )]
            if invalid:
                errors.append(f"Field '{key}' contains invalid email address values.")
    return errors
