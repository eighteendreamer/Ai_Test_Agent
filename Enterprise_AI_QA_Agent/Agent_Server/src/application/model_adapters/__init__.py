"""LangChain model boundary adapters.

The package contains only the boundary contract and lossless message
conversion in this batch.  The legacy provider clients remain the runtime
default until the adapter contract is wired behind its feature flag.
"""

from src.application.model_adapters.base import ModelPort
from src.application.model_adapters.langchain_model_adapter import LangChainModelAdapter
from src.application.model_adapters.legacy_model_adapter import LegacyProviderAdapter
from src.application.model_adapters.message_adapter import (
    from_langchain_message,
    to_langchain_tools,
    to_langchain_messages,
)

__all__ = [
    "LangChainModelAdapter",
    "LegacyProviderAdapter",
    "ModelPort",
    "from_langchain_message",
    "to_langchain_messages",
    "to_langchain_tools",
]
