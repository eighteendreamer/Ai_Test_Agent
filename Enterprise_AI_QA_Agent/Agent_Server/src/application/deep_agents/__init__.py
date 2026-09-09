"""Deep Agents integration boundaries.

The package is intentionally dependency-light at import time.  The optional
``deepagents`` package is loaded only when the pilot flag is enabled and a
turn is actually routed through the adapter.
"""

from .runtime_adapter import (
    DeepAgentRuntimeAdapter,
    DeepAgentRuntimeError,
    DeepAgentRuntimeRequest,
    DeepAgentRuntimeResult,
)

__all__ = [
    "DeepAgentRuntimeAdapter",
    "DeepAgentRuntimeError",
    "DeepAgentRuntimeRequest",
    "DeepAgentRuntimeResult",
]
