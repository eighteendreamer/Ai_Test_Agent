from __future__ import annotations

from typing import Protocol

from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult


class ModelPort(Protocol):
    """Provider-neutral model boundary used by the runtime service.

    Implementations receive the already resolved database model configuration
    and bearer token.  They return the existing business DTO, so LangChain
    message types do not cross the application boundary.
    """

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult: ...
