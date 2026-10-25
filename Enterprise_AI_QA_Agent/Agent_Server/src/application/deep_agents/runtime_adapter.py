from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import nullcontext
from pathlib import Path
import shutil
import tempfile
from typing import Any, Awaitable, Callable

from src.application.deep_agents.read_only_backend import (
    build_read_only_filesystem_backend,
)
from src.application.deep_agents.framework_transcript import (
    deserialize_messages,
    serialize_messages,
)
from src.schemas.framework_approval import NativeToolInterrupt


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
    framework_messages: list[dict[str, Any]] | None = None
    checkpoint_namespace: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    read_only_filesystem_enabled: bool = False
    read_only_max_file_size_mb: int = 10
    read_only_max_output_chars: int = 120000
    cognitive_planning_enabled: bool = False
    cognitive_subagents_enabled: bool = False
    max_subagent_calls_per_turn: int = 1
    subagent_model_call_limit: int = 6
    subagent_tool_call_limit: int = 12
    on_subagent_event: Callable[[dict[str, Any]], None] | None = None
    business_tools: list[Any] = field(default_factory=list)
    interrupt_on: dict[str, Any] = field(default_factory=dict)
    resume: dict[str, Any] | None = None
    continue_from_checkpoint: bool = False


@dataclass(frozen=True)
class DeepAgentRuntimeResult:
    output_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    framework_messages: list[dict[str, Any]] = field(default_factory=list)
    todos: list[dict[str, str]] = field(default_factory=list)
    subagent_events: list[dict[str, Any]] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    native_interrupts: list[NativeToolInterrupt] = field(default_factory=list)


ModelResolver = Callable[[str], Awaitable[Any]]
AgentFactory = Callable[..., Any]
DEEP_AGENT_RUN_NAME = "enterprise_ai_qa_agent.deep_agent_turn"


class DeepAgentRuntimeAdapter:
    """Small boundary around the official ``create_deep_agent`` harness.

    Framework-native filesystem and cognitive tools are configured here, while
    business tools arrive only as application-owned LangChain wrappers. The
    adapter never receives a Registry handler directly; ToolRuntime governance
    remains the execution owner.
    """

    def __init__(
        self,
        *,
        model_resolver: ModelResolver,
        agent_factory: AgentFactory | None = None,
        skill_registry: Any | None = None,
        checkpointer_factory: Callable | None = None,
    ) -> None:
        self._model_resolver = model_resolver
        self._agent_factory = agent_factory
        self._skill_registry = skill_registry
        self._checkpointer_factory = checkpointer_factory

    async def execute(self, request: DeepAgentRuntimeRequest) -> DeepAgentRuntimeResult:
        if (
            request.interrupt_on or request.resume is not None or request.continue_from_checkpoint
        ) and self._checkpointer_factory is None:
            raise DeepAgentRuntimeError("Governed Deep Agents HITL requires a checkpointer.")
        if request.resume is not None and request.continue_from_checkpoint:
            raise DeepAgentRuntimeError("A checkpoint continuation cannot also submit a new decision.")
        checkpoint_context = (
            self._checkpointer_factory()
            if self._checkpointer_factory is not None
            else nullcontext(None)
        )
        async with checkpoint_context as checkpointer:
            return await self._execute(request, checkpointer=checkpointer)

    async def _execute(
        self, request: DeepAgentRuntimeRequest, *, checkpointer: Any
    ) -> DeepAgentRuntimeResult:
        if not request.session_id or not request.turn_id or not request.model_key:
            raise DeepAgentRuntimeError(
                "Deep Agents runtime requires session_id, turn_id and model_key."
            )

        execution_owner = getattr(checkpointer, "runtime_owner", None)
        if execution_owner is not None:
            binding = execution_owner.binding
            if (binding.session_id != request.session_id or binding.turn_id != request.turn_id
                or binding.checkpoint_namespace != request.checkpoint_namespace):
                raise DeepAgentRuntimeError("Checkpoint owner does not match the runtime request binding.")

        stage = _pilot_stage(request)
        try:
            model = await self._model_resolver(request.model_key)
            cleanup = None
            if self._agent_factory is None:
                harness_kwargs, cleanup = self._configure_harness(
                    model,
                    read_only_filesystem_enabled=request.read_only_filesystem_enabled,
                    read_only_max_file_size_mb=request.read_only_max_file_size_mb,
                    read_only_max_output_chars=request.read_only_max_output_chars,
                    cognitive_planning_enabled=request.cognitive_planning_enabled,
                    cognitive_subagents_enabled=request.cognitive_subagents_enabled,
                    max_subagent_calls_per_turn=request.max_subagent_calls_per_turn,
                    subagent_model_call_limit=request.subagent_model_call_limit,
                    subagent_tool_call_limit=request.subagent_tool_call_limit,
                    context=request.context,
                )
            else:
                harness_kwargs = {}
            if request.interrupt_on:
                try:
                    from langchain.agents.middleware import HumanInTheLoopMiddleware
                except ImportError as exc:
                    raise DeepAgentRuntimeError(
                        "Deep Agents governed HITL requires LangChain HumanInTheLoopMiddleware."
                    ) from exc
                # create_deep_agent installs its own batched middleware when
                # interrupt_on is present. Passing the same middleware name
                # replaces that default with the required per-call policy.
                harness_kwargs = {
                    **harness_kwargs,
                    "middleware": [
                        *list(harness_kwargs.get("middleware") or []),
                        HumanInTheLoopMiddleware(
                            interrupt_on=request.interrupt_on,
                            interrupt_mode="per_call",
                        ),
                    ],
                }
            factory = self._resolve_factory()
            # Business tools are already wrapped by LangChainToolAdapter and
            # therefore enter the application's governance boundary.
            try:
                agent = factory(
                    model=model,
                    tools=list(request.business_tools),
                    system_prompt=request.system_prompt,
                    checkpointer=checkpointer,
                    interrupt_on=request.interrupt_on or None,
                    **harness_kwargs,
                )
                invoke_input: Any
                if request.continue_from_checkpoint:
                    invoke_input = None
                elif request.resume is not None:
                    from langgraph.types import Command

                    invoke_input = Command(resume=request.resume)
                else:
                    input_messages: list[Any] = list(request.messages)
                    if request.framework_messages:
                        input_messages = deserialize_messages(request.framework_messages)
                    invoke_input = {"messages": input_messages}
                configurable = {"thread_id": request.turn_id}
                if request.checkpoint_namespace and execution_owner is None:
                    configurable["checkpoint_ns"] = request.checkpoint_namespace
                invoke_config = {
                    "configurable": configurable,
                    "run_name": DEEP_AGENT_RUN_NAME,
                    "metadata": {
                        "session_id": request.session_id,
                        "turn_id": request.turn_id,
                        "trace_id": request.trace_id,
                        "harness": "deepagents",
                    },
                }
                # LangGraph 1.2.12 cannot use sync durability without a saver.
                # Non-persistent legacy cognitive pilots have no checkpoint;
                # every persisted initial/resume/continue path must use sync.
                persistence_options = {"durability": "sync"} if checkpointer is not None else {}
                if request.cognitive_subagents_enabled:
                    result, subagent_events = await _invoke_with_subagent_events(
                        agent,
                        invoke_input,
                        invoke_config,
                        on_event=request.on_subagent_event,
                        persistence_options=persistence_options,
                    )
                else:
                    result = await agent.ainvoke(invoke_input, config=invoke_config, **persistence_options)
                    subagent_events = []
                native_interrupts = []
                if execution_owner is not None:
                    from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts

                    native_interrupts = await collect_pending_tool_interrupts(agent, checkpointer, invoke_config)
                    reported_ids = {item.id for item in result.get("__interrupt__", [])}
                    if {item.interrupt_id for item in native_interrupts} != reported_ids:
                        raise DeepAgentRuntimeError("Returned interrupts disagree with pending native tasks.")
            finally:
                if cleanup is not None:
                    cleanup()
        except DeepAgentRuntimeError:
            raise
        except Exception as exc:
            raise DeepAgentRuntimeError(
                f"Deep Agents execution failed at the {stage} boundary: "
                f"{exc.__class__.__name__}: {str(exc)[:240]}"
            ) from exc

        raw_messages = result.get("messages") if isinstance(result, dict) else []
        messages = _normalize_messages(raw_messages)
        framework_messages = serialize_messages(raw_messages)
        interrupts = [
            {"id": item.id, "value": item.value}
            for item in result.get("__interrupt__", [])
        ]
        output_text = "" if interrupts else _last_assistant_text(messages)
        if not output_text and not interrupts:
            raise DeepAgentRuntimeError(
                "Deep Agents execution returned no assistant message."
            )
        return DeepAgentRuntimeResult(
            output_text=output_text,
            metadata={
                "harness": "deepagents",
                "stage": stage,
                "message_count": len(messages),
            },
            messages=messages,
            framework_messages=framework_messages,
            todos=_normalize_todos(result.get("todos") if isinstance(result, dict) else []),
            subagent_events=subagent_events,
            interrupts=interrupts,
            native_interrupts=native_interrupts,
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
        cognitive_planning_enabled: bool = False,
        cognitive_subagents_enabled: bool = False,
        max_subagent_calls_per_turn: int = 1,
        subagent_model_call_limit: int = 6,
        subagent_tool_call_limit: int = 12,
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
        }
        if not cognitive_subagents_enabled:
            excluded_tools.add("task")
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

        planning_middleware = []
        if cognitive_planning_enabled:
            try:
                from langchain.agents.middleware import TodoListMiddleware
            except ImportError as exc:
                raise DeepAgentRuntimeError(
                    "Deep Agents DA-E3 cognitive planning requires TodoListMiddleware."
                ) from exc
            planning_middleware = [TodoListMiddleware()]

        subagent_kwargs: dict[str, Any] = {}
        if cognitive_subagents_enabled:
            if not read_only_filesystem_enabled:
                raise DeepAgentRuntimeError(
                    "DA-E3 cognitive subagents require the governed read-only filesystem."
                )
            try:
                from langchain.agents.middleware import ToolCallLimitMiddleware
            except ImportError as exc:
                raise DeepAgentRuntimeError(
                    "Deep Agents DA-E3 subagents require ToolCallLimitMiddleware."
                ) from exc
            planning_middleware.append(
                ToolCallLimitMiddleware(
                    tool_name="task",
                    run_limit=max(1, int(max_subagent_calls_per_turn)),
                    exit_behavior="continue",
                )
            )

        if not read_only_filesystem_enabled:
            return ({"middleware": planning_middleware} if planning_middleware else {}), None

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
            from langchain.agents.middleware import (
                ModelCallLimitMiddleware,
                ToolCallLimitMiddleware,
            )
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
            *planning_middleware,
            FilesystemMiddleware(
                backend=backend,
                tools=["read_file", "ls", "glob", "grep"],
                _permissions=permissions,
            )
        ]
        if cognitive_subagents_enabled:
            subagent_kwargs = {
                "subagents": [
                    {
                        "name": "code-review-researcher",
                        "description": (
                            "Inspect multiple project files for one bounded code-review "
                            "question and return concise evidence with file paths."
                        ),
                        "system_prompt": (
                            "You are a bounded code-review evidence researcher. Use only "
                            "the provided read-only filesystem tools. Do not delegate, "
                            "execute commands, modify files, or call business tools. Return "
                            "only concise findings with concrete file paths."
                        ),
                        "model": model,
                        "tools": [],
                        "middleware": [
                            FilesystemMiddleware(
                                backend=backend,
                                tools=["read_file", "ls", "glob", "grep"],
                                _permissions=permissions,
                            ),
                            ModelCallLimitMiddleware(
                                run_limit=max(1, int(subagent_model_call_limit)),
                                exit_behavior="end",
                            ),
                            ToolCallLimitMiddleware(
                                run_limit=max(1, int(subagent_tool_call_limit)),
                                exit_behavior="end",
                            ),
                        ],
                    }
                ]
            }
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
                *planning_middleware,
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
            **subagent_kwargs,
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


def _pilot_stage(request: DeepAgentRuntimeRequest) -> str:
    if request.business_tools or request.interrupt_on or request.resume:
        return "DA-E4"
    if request.cognitive_planning_enabled or request.cognitive_subagents_enabled:
        return "DA-E3"
    if request.read_only_filesystem_enabled:
        return "DA-E2"
    return "DA-E1"


async def _invoke_with_subagent_events(
    agent: Any,
    invoke_input: Any,
    invoke_config: dict[str, Any],
    *,
    on_event: Callable[[dict[str, Any]], None] | None,
    persistence_options: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project LangGraph v2 events into a stable, framework-neutral subset."""
    result: dict[str, Any] | None = None
    projected: list[dict[str, Any]] = []
    active_runs: dict[str, str] = {}
    interrupts: dict[str, Any] = {}
    async for event in agent.astream_events(
        invoke_input,
        config=invoke_config,
        version="v2",
        stream_mode=["updates", "values"],
        **persistence_options,
    ):
        event_type = str(event.get("event") or "")
        event_name = str(event.get("name") or "").strip()
        run_id = str(event.get("run_id") or "").strip()
        data = event.get("data") or {}
        tool_input = data.get("input") or {}
        if event_name == "task" and event_type == "on_tool_start" and run_id:
            agent_name = (
                str(tool_input.get("subagent_type") or "").strip()
                if isinstance(tool_input, dict)
                else ""
            )
            active_runs[run_id] = agent_name
            item = {
                "type": "started",
                "name": agent_name,
                "run_id": run_id,
                "parent_run_id": str((event.get("parent_ids") or [""])[-1]),
            }
            projected.append(item)
            if on_event is not None:
                on_event(item)
        elif event_name == "task" and event_type == "on_tool_end" and run_id in active_runs:
            item = {
                "type": "completed",
                "name": active_runs.pop(run_id),
                "run_id": run_id,
                "parent_run_id": str((event.get("parent_ids") or [""])[-1]),
            }
            projected.append(item)
            if on_event is not None:
                on_event(item)
        if event_type == "on_chain_stream" and event_name == DEEP_AGENT_RUN_NAME and not event.get("parent_ids"):
            chunk = data.get("chunk")
            if isinstance(chunk, tuple) and len(chunk) == 2:
                mode, payload = chunk
                if mode == "values" and isinstance(payload, dict):
                    result = payload
                elif mode == "updates" and isinstance(payload, dict):
                    for interruption in payload.get("__interrupt__", []):
                        interrupts[interruption.id] = interruption
        if event_type == "on_chain_end" and event_name == DEEP_AGENT_RUN_NAME and not event.get("parent_ids"):
            output = (event.get("data") or {}).get("output")
            if isinstance(output, dict):
                result = output
    if result is None:
        raise DeepAgentRuntimeError(
            "Deep Agents subagent event stream ended without a final graph state."
        )
    if interrupts:
        result = {**result, "__interrupt__": list(interrupts.values())}
    return result, projected


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
        item = {"role": {"ai": "assistant", "human": "user"}.get(role, role), "content": content}
        if getattr(message, "tool_calls", None):
            item["tool_calls"] = list(message.tool_calls)
        if getattr(message, "tool_call_id", None):
            item["tool_call_id"] = message.tool_call_id
            item["name"] = getattr(message, "name", None)
        normalized.append(item)
    return normalized


def _normalize_todos(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        status = str(item.get("status") or "").strip()
        if content and status in {"pending", "in_progress", "completed"}:
            normalized.append({"content": content, "status": status})
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
