from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.application.deep_agents import (
    DeepAgentRuntimeAdapter,
    DeepAgentRuntimeRequest,
)
from src.application.deep_agents.runtime_adapter import _project_root_from_context
from src.application.deep_agents.read_only_backend import build_read_only_filesystem_backend
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.modes.code_review_mode.models import ProjectSource
from src.registry.skills import SkillRegistry


class _FakeAgent:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    async def ainvoke(self, payload, *, config):
        self._calls.append({"payload": payload, "config": config})
        return {
            "messages": [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "content": "DA_E1_OK"},
            ]
        }


@pytest.mark.asyncio
async def test_da_e1_maps_messages_and_keeps_business_tools_empty():
    calls: list[dict] = []
    factory_calls: list[dict] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return _FakeAgent(calls)

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=factory,
    )
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            model_key="qwen-test",
            system_prompt="pilot",
            messages=[{"role": "user", "content": "inspect"}],
        )
    )

    assert result.output_text == "DA_E1_OK"
    assert result.metadata["stage"] == "DA-E1"
    assert factory_calls[0]["tools"] == []
    assert calls[0]["config"] == {"configurable": {"thread_id": "turn-1"}}
    assert calls[0]["payload"]["messages"] == [{"role": "user", "content": "inspect"}]


@pytest.mark.asyncio
async def test_da_e1_rejects_missing_assistant_output():
    class EmptyAgent:
        async def ainvoke(self, payload, *, config):
            return {"messages": [{"role": "user", "content": "only input"}]}

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=lambda **_kwargs: EmptyAgent(),
    )

    with pytest.raises(RuntimeError, match="no assistant message"):
        await adapter.execute(
            DeepAgentRuntimeRequest(
                session_id="session-1",
                turn_id="turn-1",
                trace_id="trace-1",
                model_key="qwen-test",
                system_prompt="pilot",
                messages=[],
            )
        )


def test_da_e2_resolves_only_explicit_local_project_root():
    assert _project_root_from_context({"project_root": "C:/workspace"}) == "C:/workspace"
    assert _project_root_from_context(
        {"project_source": {"source_type": "local", "root_path": "C:/repo"}}
    ) == "C:/repo"
    assert _project_root_from_context(
        {"project_source": {"source_type": "ssh", "root_path": "/srv/repo"}}
    ) == ""


async def _resolved_model():
    return object()


def test_da_e2_read_matches_legacy_project_file_reader(tmp_path: Path):
    """The pilot must preserve the existing code-review reader's text contract."""
    pytest.importorskip("deepagents")
    target = tmp_path / "sample.py"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    source = ProjectSource(source_type="local", root_path=str(tmp_path), project_name="fixture")

    legacy = ToolRuntimeService._read_local_project_file(
        object(), source, file_path="sample.py", start_line=2, end_line=3, max_chars=16000
    )
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    current = backend.read("/sample.py", offset=1, limit=2)

    assert current.error is None
    assert current.file_data is not None
    # The legacy reader uses ``splitlines()`` and drops the slice's final
    # newline; the official backend preserves it.  Compare the actual text
    # lines while making this presentation-only difference explicit.
    assert current.file_data["content"].rstrip("\n") == legacy["content"]
    assert legacy["file"]["path"] == "sample.py"
    assert legacy["file"]["start_line"] == 2
    assert legacy["file"]["end_line"] == 3


@pytest.mark.asyncio
async def test_da_e2_read_and_grep_are_safe_under_concurrency(tmp_path: Path):
    """Concurrent read-only calls must not share mutable request or Skill state."""
    pytest.importorskip("deepagents")
    target = tmp_path / "parallel.txt"
    target.write_text("needle\n" * 200, encoding="utf-8")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)

    async def read_once():
        return await asyncio.to_thread(backend.read, "/parallel.txt", 0, 5)

    async def grep_once():
        return await asyncio.to_thread(backend.grep, "needle", "/parallel.txt", max_count=3)

    reads, greps = await asyncio.gather(
        asyncio.gather(*(read_once() for _ in range(12))),
        asyncio.gather(*(grep_once() for _ in range(12))),
    )
    assert all(item.error is None for item in reads)
    assert all(item.file_data and "needle" in item.file_data["content"] for item in reads)
    assert all(item.error is None and len(item.matches) == 3 for item in greps)


@pytest.mark.asyncio
async def test_da_e2_concurrent_skill_staging_is_request_scoped(tmp_path: Path):
    pytest.importorskip("deepagents")

    async def configure(key: str):
        skills_root = tmp_path / key
        skill_dir = skills_root / key
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {key}\ndescription: {key} only\n---\n\n# {key}\n",
            encoding="utf-8",
        )
        adapter = DeepAgentRuntimeAdapter(
            model_resolver=lambda _key: _resolved_model(),
            skill_registry=SkillRegistry(skills_root=skills_root),
        )
        kwargs, cleanup = await asyncio.to_thread(
            adapter._configure_harness,
            object(),
            read_only_filesystem_enabled=True,
            read_only_max_file_size_mb=1,
            read_only_max_output_chars=120000,
            context={"project_root": str(tmp_path), "skill_keys": [key]},
        )
        return key, kwargs["backend"], cleanup

    configured = await asyncio.gather(configure("alpha"), configure("beta"))
    try:
        for key, backend, _cleanup in configured:
            own = backend.read(f"/skills/{key}/SKILL.md")
            other = "beta" if key == "alpha" else "alpha"
            foreign = backend.read(f"/skills/{other}/SKILL.md")
            assert own.error is None and own.file_data and f"# {key}" in own.file_data["content"]
            assert foreign.error is not None
    finally:
        for _key, _backend, cleanup in configured:
            assert cleanup is not None
            cleanup()


@pytest.mark.parametrize("path", ["../outside.txt", "/../outside.txt"])
def test_da_e2_rejects_virtual_path_traversal(tmp_path: Path, path: str):
    pytest.importorskip("deepagents")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    result = backend.read(path)
    assert result.error and "path traversal" in result.error.lower()


def test_da_e2_does_not_expand_home_shorthand(tmp_path: Path):
    pytest.importorskip("deepagents")
    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    result = backend.read("~/.env")
    assert result.error == "File '~/.env' not found"


def test_da_e2_bounds_single_line_output(tmp_path: Path):
    pytest.importorskip("deepagents")
    (tmp_path / "long-line.txt").write_text("x" * 1201, encoding="utf-8")
    backend = build_read_only_filesystem_backend(
        tmp_path,
        max_file_size_mb=1,
        max_output_chars=1000,
    )
    result = backend.read("/long-line.txt", offset=0, limit=1)
    assert result.error and "output limit" in result.error.lower()


def test_da_e2_rejects_escape_symlink_and_accepts_in_root_symlink(tmp_path: Path):
    pytest.importorskip("deepagents")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    inside = tmp_path / "inside.txt"
    inside.write_text("inside", encoding="utf-8")
    link_out = tmp_path / "outside-link.txt"
    link_in = tmp_path / "inside-link.txt"
    try:
        link_out.symlink_to(outside)
        link_in.symlink_to(inside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic-link privilege is unavailable: {exc}")

    backend = build_read_only_filesystem_backend(tmp_path, max_file_size_mb=1)
    assert backend.read("/inside-link.txt").error is None
    outside_result = backend.read("/outside-link.txt")
    assert outside_result.error and "outside root" in outside_result.error.lower()
