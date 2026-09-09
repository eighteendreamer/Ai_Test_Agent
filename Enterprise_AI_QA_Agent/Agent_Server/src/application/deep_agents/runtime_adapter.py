from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile
from typing import Any, Awaitable, Callable

from src.application.deep_agents.read_only_backend import (
    build_read_only_filesystem_backend,
)


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
    read_only_filesystem_enabled: bool = False
    read_only_max_file_size_mb: int = 10
    read_only_max_output_chars: int = 120000


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
    DA-E1 proves the model/message/state boundary.  DA-E2 adds an explicitly
    scoped, read-only project filesystem and SkillRegistry-backed skills;
    business tools, checkpoints and subagent dispatch remain future batches.
    """

    def __init__(
        self,
        *,
        model_resolver: ModelResolver,
        agent_factory: AgentFactory | None = None,
        skill_registry: Any | None = None,
    ) -> None:
        self._model_resolver = model_resolver
        self._agent_factory = agent_factory
        self._skill_registry = skill_registry

    async def execute(self, request: DeepAgentRuntimeRequest) -> DeepAgentRuntimeResult:
        if not request.session_id or not request.turn_id or not request.model_key:
            raise DeepAgentRuntimeError(
                "Deep Agents runtime requires session_id, turn_id and model_key."
            )

        try:
            model = await self._model_resolver(request.model_key)
            cleanup = None
            if self._agent_factory is None:
                harness_kwargs, cleanup = self._configure_harness(
                    model,
                    read_only_filesystem_enabled=request.read_only_filesystem_enabled,
                    read_only_max_file_size_mb=request.read_only_max_file_size_mb,
                    read_only_max_output_chars=request.read_only_max_output_chars,
                    context=request.context,
                )
            else:
                harness_kwargs = {}
            factory = self._resolve_factory()
            # ``tools=[]`` is intentional for DA-E1.  Business tools must not
            # bypass ToolRuntimeService before the DA-E4 governance adapter.
            try:
                agent = factory(
                    model=model,
                    tools=[],
                    system_prompt=request.system_prompt,
                    **harness_kwargs,
                )
                result = await agent.ainvoke(
                    {"messages": list(request.messages)},
                    config={"configurable": {"thread_id": request.turn_id}},
                )
            finally:
                if cleanup is not None:
                    cleanup()
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
                "stage": "DA-E2" if request.read_only_filesystem_enabled else "DA-E1",
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

    def _configure_harness(
        self,
        model: Any,
        *,
        read_only_filesystem_enabled: bool,
        read_only_max_file_size_mb: int,
        context: dict[str, Any],
        read_only_max_output_chars: int = 120000,
    ) -> tuple[dict[str, Any], Any | None]:
        """Configure the official harness without bypassing project governance.

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

        excluded_tools = {
            "write_file",
            "edit_file",
            "delete",
            "execute",
            "task",
        }
        if not read_only_filesystem_enabled:
            excluded_tools.update({"ls", "read_file", "glob", "grep"})
        profile = HarnessProfile(
            excluded_tools=frozenset(excluded_tools),
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

        if not read_only_filesystem_enabled:
            return {}, None

        project_root = _project_root_from_context(context)
        if not project_root:
            raise DeepAgentRuntimeError(
                "DA-E2 read-only filesystem requires an explicit local project_root."
            )
        try:
            root = Path(project_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DeepAgentRuntimeError(
                f"DA-E2 project_root is not a readable local directory: {project_root}"
            ) from exc
        if not root.is_dir():
            raise DeepAgentRuntimeError(
                f"DA-E2 project_root is not a directory: {root}"
            )

        try:
            from deepagents.backends import CompositeBackend, FilesystemBackend
            from deepagents import FilesystemPermission
            from deepagents.middleware import FilesystemMiddleware, SkillsMiddleware
        except ImportError as exc:
            raise DeepAgentRuntimeError(
                "DA-E2 read-only filesystem requires Deep Agents filesystem APIs."
            ) from exc

        project_backend = build_read_only_filesystem_backend(
            root,
            max_file_size_mb=read_only_max_file_size_mb,
            max_output_chars=read_only_max_output_chars,
        )
        permissions = [
            FilesystemPermission(
                operations=["read", "write"],
                paths=[
                    "/.env",
                    "/.env.*",
                    "/**/.env",
                    "/**/.env.*",
                    "/**/*credentials*",
                    "/**/*secret*",
                    "/**/*.pem",
                    "/**/*.key",
                ],
                mode="deny",
            )
        ]
        backend = project_backend
        middleware = [
            FilesystemMiddleware(
                backend=backend,
                tools=["read_file", "ls", "glob", "grep"],
                _permissions=permissions,
            )
        ]
        cleanup = None
        skill_keys = context.get("skill_keys")
        if skill_keys:
            if self._skill_registry is None:
                raise DeepAgentRuntimeError(
                    "DA-E2 skill_keys were supplied but no SkillRegistry is configured."
                )
            selected_keys = [str(item).strip() for item in skill_keys if str(item).strip()]
            selected = self._skill_registry.get_many(selected_keys)
            if len(selected) != len(set(selected_keys)):
                raise DeepAgentRuntimeError(
                    "DA-E2 skill_keys contain an unknown or disabled SkillRegistry key."
                )
            staging = tempfile.TemporaryDirectory(prefix="deepagents-skills-")
            staging_root = Path(staging.name)
            for descriptor in selected:
                source = (self._skill_registry.skills_root / descriptor.key).resolve()
                if not source.is_dir() or not (source / "SKILL.md").is_file():
                    staging.cleanup()
                    raise DeepAgentRuntimeError(
                        f"DA-E2 SkillRegistry entry has no valid SKILL.md: {descriptor.key}"
                    )
                shutil.copytree(source, staging_root / descriptor.key)
            skill_backend = FilesystemBackend(
                root_dir=staging_root,
                virtual_mode=True,
                max_file_size_mb=read_only_max_file_size_mb,
            )
            backend = CompositeBackend(
                default=project_backend,
                routes={"/skills/": skill_backend},
            )
            middleware = [
                FilesystemMiddleware(
                    backend=backend,
                    tools=["read_file", "ls", "glob", "grep"],
                    _permissions=permissions,
                ),
                SkillsMiddleware(backend=backend, sources=["/skills/"]),
            ]
            cleanup = staging.cleanup
        return {
            "backend": backend,
            "middleware": middleware,
        }, cleanup


def _project_root_from_context(context: dict[str, Any]) -> str:
    direct = str(context.get("project_root") or context.get("root_path") or "").strip()
    if direct:
        return direct
    source = context.get("project_source")
    if isinstance(source, dict):
        source_type = str(source.get("source_type") or "local").strip().lower()
        if source_type == "local":
            return str(source.get("root_path") or "").strip()
    return ""


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
