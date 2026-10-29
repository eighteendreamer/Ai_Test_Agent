"""One prepared context for the platform prompt and native framework input."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.application.permissions.permission_service import PermissionService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.agent import AgentDescriptor, ModelDescriptor, ToolDescriptor
from src.schemas.framework_approval import approval_value_hash
from src.schemas.settings import GeneralSettingsPayload
from tests.test_framework_context_transcript import context_case, covered_asset


VERSION = "v2.2.0"


def assembly_components(tmp_path, *, tools=None):
    """Real assembly nodes; only external retrieval/configuration ports are controlled."""
    root = tmp_path / "skills"
    skill = root / "tdd-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# TDD Review\nFROZEN_SKILL_BODY", encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references" / "evidence.md").write_text("reference evidence", encoding="utf-8")
    skills = SkillRegistry(skills_root=root)
    registry = tools or ToolRegistry()
    registry.register_dynamic(ToolDescriptor(key="inspect_value", name="Inspect value", category="execution",
        description="Inspect a test value", permission_level="ask", input_schema={"type": "object",
            "properties": {"value": {"type": "string"}}, "required": ["value"]}), "inspect_value")
    registry.register_dynamic(ToolDescriptor(key="foreign_internal", name="Foreign internal", category="execution",
        description="Not available to this mode", exposure="internal", owner_mode_key="foreign"), "foreign_internal")
    agent = AgentDescriptor(key="context-reviewer", name="Context Reviewer", role="worker", summary="Review",
        description="Review supplied evidence", supported_tools=["inspect_value", "foreign_internal", "skill"],
        supported_skills=["tdd-review"], supported_models=["fake"], default_model="fake")
    agents = SimpleNamespace(resolve_for_message=lambda *args, **kwargs: agent, list=lambda: [agent])
    model = ModelDescriptor(key="fake", name="Frozen model", provider="openai", summary="Controlled model")
    models = SimpleNamespace(resolve_for_agent=lambda **kwargs: model)
    reads = []

    async def observations(**kwargs):
        reads.append(("observations", kwargs))
        item = {"summary": "OBSERVATION_CONTEXT", "created_at": "2026-10-29T00:00:00+00:00", "metadata": {}}
        return SimpleNamespace(hits=[SimpleNamespace(model_dump=lambda **kwargs: item)],
            total_docs=1, total_session_docs=1)

    async def memories(**kwargs):
        reads.append(("memory", kwargs))
        return SimpleNamespace(hits=[SimpleNamespace(model_dump=lambda **kwargs: {"id": "memory-original"})],
            prompt_blocks=["MEMORY_CONTEXT"])

    async def cases(**kwargs):
        reads.append(("cases", kwargs))
        return SimpleNamespace(hits=[], prompt_blocks=["CASE_CONTEXT"])

    def active_servers():
        reads.append(("mcp", {}))
        return [{"key": "controlled-mcp"}]

    def settings():
        reads.append(("settings", {}))
        return GeneralSettingsPayload(modelOutputLanguage="fr-FR")

    return SimpleNamespace(agent_registry=agents, tool_registry=registry, model_registry=models,
        skill_registry=skills, skill_runtime_service=SkillRuntimeService(skills),
        memory_runtime_service=SimpleNamespace(retrieve_observation_context=observations, retrieve_for_turn=memories),
        mcp_runtime_service=SimpleNamespace(list_active_servers=active_servers,
            build_prompt_blocks=lambda servers: ["MCP_CONTEXT"]),
        test_case_embedding_service=SimpleNamespace(retrieve=cases), permission_service=PermissionService(),
        prompt_assembly_service=PromptAssemblyService(settings), reads=reads, skill_file=skill / "SKILL.md")


def build_assembly(parts):
    from src.graph.builder import build_turn_context_assembly

    return build_turn_context_assembly(**{key: getattr(parts, key) for key in (
        "agent_registry", "tool_registry", "model_registry", "skill_registry", "skill_runtime_service",
        "memory_runtime_service", "mcp_runtime_service", "test_case_embedding_service", "permission_service",
        "prompt_assembly_service")})


def prepared_case():
    session, request, runtime = context_case()
    session.project_id = "official-project"
    binding = request.runtime_binding.model_copy(update={"runtime_version": VERSION})
    request = request.model_copy(update={"runtime_binding": binding, "agent_key": "context-reviewer",
        "skill_keys": ["tdd-review"], "context": {"requested_tool_keys": ["inspect_value", "foreign_internal", "skill"],
            "attachments": [{"name": "context.txt", "text_excerpt": "ATTACHMENT_CONTEXT"}]}})
    session.metadata["framework_transcript_asset"] = covered_asset(session, binding)
    return session, request, runtime


def test_full_prompt_keeps_native_input_separate_and_has_no_unavailable_skill_dispatcher():
    result = PromptAssemblyService().build_for_turn(state={
        "selected_agent_key": "reviewer", "runtime_messages": [{"role": "user", "content": "CURRENT_INPUT"}],
        "user_message": "CURRENT_INPUT", "model_visible_tool_keys": ["inspect_value"],
        "approval_required_tool_keys": ["inspect_value"],
        "context_bundle": {"runtime_binding": {"runtime_kind": "deepagents", "runtime_version": VERSION},
            "attachments": [{"name": "context.txt", "text_excerpt": "ATTACHMENT_CONTEXT"}]}},
        available_agent_keys=["reviewer"])
    assert "ATTACHMENT_CONTEXT" in result.system_prompt
    assert "Approval-gated tools: inspect_value" in result.system_prompt
    assert "registered `skill` tool" not in result.system_prompt
    assert "CURRENT_INPUT" not in result.system_prompt
    assert result.runtime_messages == [{"role": "user", "content": "CURRENT_INPUT"}]


@pytest.mark.asyncio
async def test_shared_assembly_freezes_all_sources_without_rebuilding_native_history(tmp_path):
    parts = assembly_components(tmp_path)
    session, request, runtime = prepared_case()
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    native = deepcopy(state["framework_messages"])
    display = deepcopy(state["runtime_messages"])
    result = await build_assembly(parts).prepare(state)
    assert native and result["framework_messages"] == native
    assert result["runtime_messages"] == display
    assert native[:4] == session.metadata["framework_transcript_asset"]["framework_messages"]
    prompt = result["system_prompt"]
    for marker in ("FROZEN_SKILL_BODY", "OBSERVATION_CONTEXT", "MEMORY_CONTEXT", "CASE_CONTEXT", "MCP_CONTEXT",
        "ATTACHMENT_CONTEXT", "# Output Language Preference"):
        assert prompt.count(marker) == 1, marker
    assert "French (fr-FR)" in prompt and "code review pilot" not in prompt
    assert "registered `skill`" not in prompt and "Use `skill`" not in prompt
    assert result["model_visible_tool_keys"] == ["inspect_value"]
    assert result["context_bundle"]["framework_tool_descriptors"] == [
        parts.tool_registry.get("inspect_value").model_dump(mode="json")]
    assert result["approval_required_tool_keys"] == ["inspect_value"]
    assert "skill" not in result["available_tool_keys"] and result["deferred_tool_keys"] == []
    assert result["context_bundle"]["skill_prompt_snapshots"][0]["prompt_block"] == result["skill_prompt_blocks"][0]
    assert [kind for kind, _ in parts.reads] == ["observations", "memory", "cases", "mcp", "settings"]
    assert next(args for kind, args in parts.reads if kind == "cases")["project_id"] == session.project_id
    event = next(event for event in result["event_log"] if event["type"] == "graph.prompt_assembled")
    assert "system_prompt_preview" not in event["payload"]


@pytest.mark.asyncio
async def test_full_context_has_no_metadata_fallback_for_a_missing_selected_skill(tmp_path):
    parts = assembly_components(tmp_path)
    parts.skill_file.unlink()
    session, request, runtime = prepared_case()
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    with pytest.raises((FileNotFoundError, KeyError)):
        await build_assembly(parts).prepare(state)


def test_new_context_reader_is_explicit_and_old_capabilities_do_not_claim_it():
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    _, request, _ = prepared_case()
    current = FrameworkWorkerCapabilities("a" * 64, True)
    current.require(request.runtime_binding, operation="message")
    for reader in (FrameworkWorkerCapabilities("a" * 64, True, context_transcript_enabled=False),
        FrameworkWorkerCapabilities("a" * 64, True, turn_context_assembly_enabled=False)):
        with pytest.raises(TaskDeferred, match="version_unavailable"):
            reader.require(request.runtime_binding, operation="message")


@pytest.mark.asyncio
async def test_full_assembly_binding_requires_the_composed_service_before_adapter_use():
    session, request, runtime = prepared_case()
    runtime._deep_agent_enabled = True
    runtime._deep_agent_bound_runtime_adapter = object()
    with pytest.raises(RuntimeError, match="context assembly.*unavailable"):
        await runtime.execute_turn(session, request)


@pytest.mark.asyncio
async def test_assembly_cannot_silently_replace_the_bound_model(tmp_path):
    parts = assembly_components(tmp_path)
    parts.model_registry.resolve_for_agent = lambda **kwargs: ModelDescriptor(
        key="different", name="Different model", provider="openai", summary="Unbound")
    session, request, runtime = prepared_case()
    with pytest.raises(ValueError, match="bound model selection"):
        await build_assembly(parts).prepare(runtime._build_initial_state(session, request,
            include_framework_transcript=True))


def test_full_context_harness_never_reopens_mutable_skills_even_for_native_schema_reads(tmp_path):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter

    async def model_resolver(key):
        raise AssertionError("No model resolution during harness configuration")

    def forbidden(*args, **kwargs):
        raise AssertionError("Platform-owned Skill must not be loaded a second time")

    adapter = DeepAgentRuntimeAdapter(model_resolver=model_resolver,
        skill_registry=SimpleNamespace(get_many=forbidden))
    _, request, _ = prepared_case()
    for schema_only in (False, True):
        kwargs, cleanup = adapter._configure_harness(model=object(), read_only_filesystem_enabled=True,
            read_only_max_file_size_mb=10, context={"project_root": str(tmp_path), "skill_keys": ["tdd-review"],
                "runtime_binding": request.runtime_binding.model_dump(mode="json")}, _schema_only=schema_only)
        assert cleanup is None
        assert [type(item).__name__ for item in kwargs["middleware"]] == [
            "_ToolExclusionMiddleware", "FilesystemMiddleware"]


def test_older_context_binding_does_not_import_a_newer_assembly_transcript():
    from src.application.context.framework_context_service import FrameworkContextService
    from src.schemas.framework_approval import approval_value_hash

    session, request, _ = context_case()
    asset = covered_asset(session, request.runtime_binding)
    asset.update(runtime_version=VERSION, model_provider="openai", model_transport="openai_chat_completions")
    asset["content_sha256"] = approval_value_hash({key: value for key, value in asset.items() if key != "content_sha256"})
    session.metadata["framework_transcript_asset"] = asset
    with pytest.raises(ValueError, match="newer transcript"):
        FrameworkContextService.compatible_asset(session, request.runtime_binding)


def test_v22_transcript_requires_an_explicit_provider_and_transport_source():
    from src.schemas.framework_transcript import FrameworkTranscriptAssetV2

    session, request, _ = context_case()
    asset = covered_asset(session, request.runtime_binding)
    asset["runtime_version"] = VERSION
    asset["content_sha256"] = approval_value_hash({key: value for key, value in asset.items()
        if key != "content_sha256"})
    with pytest.raises(ValueError, match="provider|transport"):
        FrameworkTranscriptAssetV2.model_validate(asset)


def test_v22_transcript_rejects_a_source_protocol_mismatch_even_with_the_same_fingerprint():
    from src.application.context.framework_context_service import FrameworkContextService

    session, request, _ = context_case()
    binding = request.runtime_binding.model_copy(update={"runtime_version": VERSION,
        "model_provider": "openai", "model_transport": "openai_chat_completions"})
    asset = covered_asset(session, binding)
    asset.update(runtime_version=VERSION, model_provider="anthropic", model_transport="anthropic_messages")
    asset["content_sha256"] = approval_value_hash({key: value for key, value in asset.items()
        if key != "content_sha256"})
    session.metadata["framework_transcript_asset"] = asset
    with pytest.raises(ValueError, match="provider|transport|protocol"):
        FrameworkContextService.compatible_asset(session, binding)


def test_v22_initial_state_records_the_actual_model_protocol():
    from src.application.models.model_runtime_service import ModelRuntimeService
    from tests.test_framework_chat_models import _config

    session, request, runtime = context_case()
    binding = request.runtime_binding.model_copy(update={"runtime_version": VERSION,
        "model_provider": "openai", "model_transport": "openai_chat_completions"})
    request = request.model_copy(update={"runtime_binding": binding, "model_key": "fixture-model"})
    config = _config("openai_chat_completions", "http://127.0.0.1:1", key="fixture-model")
    runtime._model_runtime_service = SimpleNamespace(get_model_config=lambda key: config)
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    assert state["selected_model_provider"] == config.provider
    assert state["selected_model_transport"] == config.transport
    assert state["context_bundle"]["framework_model_protocol"] == {
        "provider": config.provider, "transport": config.transport,
    }


def test_v22_initial_state_rejects_provider_transport_drift_from_the_bound_model():
    from tests.test_framework_chat_models import _config

    session, request, runtime = context_case()
    binding = request.runtime_binding.model_copy(update={"runtime_version": VERSION,
        "model_provider": "anthropic", "model_transport": "anthropic_messages"})
    request = request.model_copy(update={"runtime_binding": binding, "model_key": "fixture-model"})
    config = _config("openai_chat_completions", "http://127.0.0.1:1", key="fixture-model")
    runtime._model_runtime_service = SimpleNamespace(get_model_config=lambda key: config)
    with pytest.raises(ValueError, match="provider/transport"):
        runtime._build_initial_state(session, request, include_framework_transcript=True)


def test_unbound_client_context_cannot_select_full_prompt_semantics():
    from src.schemas.framework_transcript import uses_turn_context_assembly

    session, request, runtime = context_case()
    request = request.model_copy(update={"runtime_binding": None, "context": {
        "runtime_binding": {"runtime_kind": "deepagents", "runtime_version": VERSION}}})
    state = runtime._build_initial_state(session, request)
    assert not uses_turn_context_assembly(state["context_bundle"])


def test_full_prompt_refuses_to_fabricate_an_admitted_input_when_history_is_empty():
    with pytest.raises(ValueError, match="admitted message projection"):
        PromptAssemblyService().build_for_turn(state={"user_message": "not an admitted projection",
            "context_bundle": {"runtime_binding": {"runtime_kind": "deepagents", "runtime_version": VERSION}}},
            available_agent_keys=[])


@pytest.mark.asyncio
async def test_legacy_graph_executes_the_same_composed_nodes_without_a_framework_loop(tmp_path):
    from src.graph.builder import build_agent_graph
    from src.runtime.control import RuntimeControlRegistry
    from src.schemas.model_config import ModelInvocationResult

    parts = assembly_components(tmp_path)
    service = build_assembly(parts)
    calls = []

    async def invoke(model_key, request):
        calls.append(request)
        return ModelInvocationResult(text="LEGACY_REPLY", response_summary={})

    dependencies = {key: getattr(parts, key) for key in (
        "agent_registry", "tool_registry", "model_registry", "skill_registry", "skill_runtime_service",
        "memory_runtime_service", "mcp_runtime_service", "test_case_embedding_service", "permission_service",
        "prompt_assembly_service")}
    graph = build_agent_graph(**dependencies, model_runtime_service=SimpleNamespace(invoke=invoke),
        tool_runtime_service=object(), runtime_control=RuntimeControlRegistry(), turn_context_assembly=service)
    session, request, runtime = prepared_case()
    request = request.model_copy(update={"runtime_binding": None})
    result = await graph.ainvoke(runtime._build_initial_state(session, request))
    assert result["final_response"] == "LEGACY_REPLY" and len(calls) == 1
    assert "registered `skill` tool" in calls[0].system_prompt
    assert [event["type"] for event in result["event_log"] if event["type"] in {
        "graph.context_built", "graph.route_selected", "graph.permission_evaluated", "graph.prompt_assembled"}] == [
            "graph.context_built", "graph.route_selected", "graph.permission_evaluated", "graph.prompt_assembled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("descriptors", [None, [], [{"key": "foreign_internal", "name": "Foreign", "category": "test",
    "description": "Not the frozen visible tool"}]])
async def test_full_context_does_not_fallback_to_live_registry_for_missing_or_foreign_tool_schema(descriptors):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    session, request, runtime = prepared_case()
    state = runtime._build_initial_state(session, request, include_framework_transcript=True)
    state["model_visible_tool_keys"] = ["inspect_value"]
    state["context_bundle"]["framework_tool_descriptors"] = descriptors
    runtime._deep_agent_governed_tools_enabled = True
    runtime._tool_governance_service = object()
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="tool descriptors"):
        await runtime._build_deep_agent_business_tools(state=state, tool_records=[])
