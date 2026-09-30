from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def persisted_turn_budget(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the model budget together at every checkpoint boundary."""
    return {
        "turn_token_usage": dict(state.get("turn_token_usage") or {}),
        "model_context_window": int(state.get("model_context_window") or 0),
    }
