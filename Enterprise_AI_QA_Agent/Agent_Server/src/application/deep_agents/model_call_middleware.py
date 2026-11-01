"""Explicit policy wiring for main, named subagent and summary model calls."""
from __future__ import annotations

import logging

from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import get_buffer_string
from langgraph.runtime import get_runtime
from deepagents.backends import StateBackend
from src.application.deep_agents.result_projection import DeepAgentRuntimeError
# Keep the existing import path for installed readers and callers.
from src.application.models.framework_model_middleware import FrameworkModelCallMiddleware
from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT, SummarizationMiddleware, compute_summarization_defaults,
)


logger = logging.getLogger(__name__)


def governed_summarization(*, policy, owner, model, model_key, backend, branch_name=None):
    """Replace the official same-name slot using its custom summarizer API.

    LangChain 1.4.6 defaults to model.with_retry inside summarization. A custom
    callable receives the officially trimmed history and owns each actual
    request explicitly, avoiding that implicit retry loop.
    """
    async def summarize(messages):
        prompt = DEEPAGENTS_DEFAULT_SUMMARY_PROMPT.format(messages=get_buffer_string(messages, format="xml"))
        request_messages = [HumanMessage(content=prompt)]
        result = await policy.invoke(owner=owner, model=model, model_key=model_key,
            execution_info=get_runtime().execution_info, branch_name=branch_name, purpose="summary",
            messages=request_messages, tools=[], options={},
            send=lambda: model.model_copy(update={"cache": False}).ainvoke(request_messages,
                config={"metadata": {"lc_source": "summarization", **internal_call_metadata()}}))
        summary = result.text.strip()
        if not summary:
            # The upstream custom-summarizer contract accepts empty strings.
            # Do not let one replace a real history range with an empty body.
            logger.warning("framework_summary_empty", extra={"binding_id": owner.binding.binding_id,
                "model_key": model_key, "branch_name": branch_name, "source_message_count": len(messages)})
            raise DeepAgentRuntimeError("Deep Agents summarization returned an empty summary.")
        return summary

    defaults = compute_summarization_defaults(model)
    return SummarizationMiddleware(model=model, backend=backend, trigger=defaults["trigger"],
        keep=defaults["keep"], truncate_args_settings=defaults["truncate_args_settings"], summarizer=summarize)


def configure_model_policy(kwargs, *, policy, owner, model, model_key):
    """Explicitly wire every exposed model branch; do not assume inheritance."""
    backend = kwargs.get("backend") or StateBackend()

    def middleware(existing, branch_name=None, branch_model=model):
        return [*existing, governed_summarization(policy=policy, owner=owner, model=branch_model,
            model_key=model_key, backend=backend, branch_name=branch_name),
            FrameworkModelCallMiddleware(policy=policy, owner=owner, model_key=model_key, branch_name=branch_name)]

    result = {**kwargs, "middleware": middleware(list(kwargs.get("middleware") or []))}
    if kwargs.get("subagents"):
        result["subagents"] = [{**agent, "middleware": middleware(list(agent.get("middleware") or []),
            branch_name=agent["name"], branch_model=agent.get("model", model))} for agent in kwargs["subagents"]]
    return result
