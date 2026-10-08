from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def persisted_turn_budget(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the model budget together at every checkpoint boundary."""
    budget = {
        "turn_token_usage": dict(state.get("turn_token_usage") or {}),
        "model_context_window": int(state.get("model_context_window") or 0),
    }
    # Absence preserves the original semantics of historical checkpoints.
    if state.get("model_call_count") is not None:
        budget.update(model_call_count=int(state["model_call_count"]),
            max_iterations=int(state["max_iterations"]))
    return budget
