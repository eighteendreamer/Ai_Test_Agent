from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class DeepAgentRuntimeError(RuntimeError):
    """A boundary error raised by the optional Deep Agents harness."""


@dataclass(frozen=True)
class DeepAgentRuntimeRequest:
    session_id: str
    turn_id: str
    trace_id: str
    model_key: str
    system_prompt: str
    messages: list[dict[str, Any]]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeepAgentRuntimeResult:
    output_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)


ModelResolver = Callable[[str], Awaitable[Any]]
AgentFactory = Callable[..., Any]


class DeepAgentRuntimeAdapter:
    """Small boundary around the official ``create_deep_agent`` harness.

    This adapter deliberately does not expose business tools or execute them.
    DA-E1 only proves the model/message/state boundary.  Tool governance,
    filesystem permissions, checkpoints and subagent dispatch are added in
    later migration batches after their existing business contracts are mapped.
    """

    def __init__(
        self,
        *,
        model_resolver: ModelResolver,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self._model_resolver = model_resolver
        self._agent_factory = agent_factory

    async def execute(self, request: DeepAgentRuntimeRequest) -> DeepAgentRuntimeResult:
        if not request.session_id or not request.turn_id or not request.model_key:
            raise DeepAgentRuntimeError(
                "Deep Agents runtime requires session_id, turn_id and model_key."
            )

        try:
            model = await self._model_resolver(request.model_key)
            if self._agent_factory is None:
                self._configure_da_e1_harness(model)
            factory = self._resolve_factory()
            # ``tools=[]`` is intentional for DA-E1.  Business tools must not
            # bypass ToolRuntimeService before the DA-E4 governance adapter.
            agent = factory(model=model, tools=[], system_prompt=request.system_prompt)
            result = await agent.ainvoke(
                {"messages": list(request.messages)},
                config={"configurable": {"thread_id": request.turn_id}},
            )
        except DeepAgentRuntimeError:
            raise
        except Exception as exc:
            raise DeepAgentRuntimeError(
                "Deep Agents execution failed at the DA-E1 boundary: "
                f"{exc.__class__.__name__}: {str(exc)[:240]}"
            ) from exc

        messages = _normalize_messages(result.get("messages") if isinstance(result, dict) else [])
        output_text = _last_assistant_text(messages)
        if not output_text:
            raise DeepAgentRuntimeError(
                "Deep Agents execution returned no assistant message."
            )
        return DeepAgentRuntimeResult(
            output_text=output_text,
            metadata={
                "harness": "deepagents",
                "stage": "DA-E1",
                "message_count": len(messages),
            },
            messages=messages,
        )

    def _resolve_factory(self) -> AgentFactory:
        if self._agent_factory is not None:
            return self._agent_factory
        try:
            from deepagents import create_deep_agent
        except ImportError as exc:
            raise DeepAgentRuntimeError(
                "Deep Agents pilot is enabled but the optional 'deepagents' "
                "package is not installed in this environment."
            ) from exc
        return create_deep_agent

    @staticmethod
    def _configure_da_e1_harness(model: Any) -> None:
        """Hide Deep Agents' default tools for the isolated DA-E1 pilot.

        ``create_deep_agent(..., tools=[])`` is additive.  Without an explicit
        HarnessProfile it still exposes filesystem and ``task`` tools, which
        would bypass this application's ToolRuntime governance.
        """
        try:
            from deepagents import (
                GeneralPurposeSubagentProfile,
                HarnessProfile,
                register_harness_profile,
            )
        except ImportError as exc:
            raise DeepAgentRuntimeError(
                "Deep Agents DA-E1 requires HarnessProfile support to hide "
                "its built-in filesystem and subagent tools."
            ) from exc

        excluded_tools = frozenset(
            {
                "ls",
                "read_file",
                "write_file",
                "edit_file",
                "delete",
                "glob",
                "grep",
                "execute",
                "task",
            }
        )
        profile = HarnessProfile(
            excluded_tools=excluded_tools,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        )
        providers = {"openai"}
        get_ls_params = getattr(model, "_get_ls_params", None)
        if callable(get_ls_params):
            try:
                provider = get_ls_params().get("ls_provider")
            except Exception:
                provider = None
            if provider:
                providers.add(str(provider))
        for provider in providers:
            register_harness_profile(provider, profile)


def _normalize_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for message in value:
        if isinstance(message, dict):
            normalized.append(message)
            continue
        role = str(getattr(message, "type", "") or "")
        content = getattr(message, "content", "")
        normalized.append({"role": role, "content": content})
    return normalized


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(message.get("role") or message.get("type") or "").lower()
        if role not in {"assistant", "ai"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text") or item.get("content") or "").strip()
                for item in content
                if isinstance(item, dict) and (item.get("text") or item.get("content"))
            ).strip()
            if text:
                return text
    return ""
