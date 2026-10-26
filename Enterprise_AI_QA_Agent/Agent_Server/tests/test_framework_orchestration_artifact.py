"""A pre-graph continuation must use the admitted application implementation."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.runtime import framework_message_stages as stages
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected, run, stage


def test_orchestration_sources_include_the_actual_application_artifact():
    from src.runtime.framework_orchestration_artifact import orchestration_artifact

    artifact = orchestration_artifact()
    assert artifact.schema_version == 1
    assert {"application/orchestration/input_orchestrator_service.py",
        "application/intent/semantic_intent_service.py", "application/intent/safety_intent_service.py",
        "application/intent/mode_selection_policy.py", "application/testing/mode_intent_service.py",
        "application/security/authorization.py", "schemas/session.py"} <= artifact.sources.keys()
    assert artifact.python_version and artifact.dependencies["pydantic"]
    assert len(artifact.dependency_lock_sha256) == 64
    assert artifact == orchestration_artifact()


def test_application_and_schema_dependencies_are_in_the_source_contract():
    from src.runtime.framework_orchestration_artifact import ORCHESTRATION_SOURCES

    root = Path(__file__).resolve().parents[1] / "src"
    sources = set(ORCHESTRATION_SOURCES)
    prefixes = ("src.application.intent.", "src.application.testing.", "src.application.security.", "src.schemas.")
    for name in sources:
        for node in ast.walk(ast.parse((root / name).read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(prefixes):
                assert node.module[4:].replace(".", "/") + ".py" in sources, (name, node.module)


@pytest.mark.parametrize("changed", ["source", "dependency", "lock", "python", "missing", "schema"])
def test_changed_or_missing_orchestration_artifact_cannot_be_silently_accepted(changed):
    from src.runtime.framework_orchestration_artifact import orchestration_artifact, require_orchestration_artifact

    original = orchestration_artifact().model_dump(mode="json")
    if changed == "source":
        original["sources"]["application/intent/semantic_intent_service.py"] = "0" * 64
    elif changed == "dependency":
        original["dependencies"]["pydantic"] = "different-version"
    elif changed == "lock":
        original["dependency_lock_sha256"] = "0" * 64
    elif changed == "python":
        original["python_version"] = "different-version"
    elif changed == "schema":
        original["schema_version"] = 2
    else:
        original = None
    with pytest.raises(ValueError):
        require_orchestration_artifact(original)


@pytest.mark.asyncio
async def test_artifact_is_persisted_before_semantic_preparation(admission, monkeypatch):
    from src.runtime.framework_orchestration_artifact import orchestration_artifact

    h = admission
    task = await selected(h)

    async def inspect(session, payload, **kwargs):
        saved = await stage(h, task)
        assert saved.source_manifest["orchestration_artifact"] == orchestration_artifact().model_dump(mode="json")
        assert "semantic_input" not in saved.source_manifest
        assert (await h.calls.snapshot(h.binding)).calls == []
        raise ConnectionError("inspected artifact before semantic input")

    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", inspect)
    with pytest.raises(ConnectionError, match="before semantic input"):
        await run(h, task)


@pytest.mark.asyncio
async def test_artifact_cannot_change_between_begin_and_execution_freeze(admission, monkeypatch):
    from src.runtime import framework_orchestration_artifact as artifacts

    h = admission
    task = await selected(h)
    frozen = stages.FrameworkMessageStages.freeze_execution

    async def change_before_freeze(self, owner, request):
        original = artifacts.orchestration_artifact()
        changed = original.model_copy(update={"python_version": "changed-after-admission"})
        monkeypatch.setattr(artifacts, "orchestration_artifact", lambda: changed)
        return await frozen(self, owner, request)

    monkeypatch.setattr(stages.FrameworkMessageStages, "freeze_execution", change_before_freeze)
    with pytest.raises(ValueError, match="artifact"):
        await run(h, task)
    assert h.runtime.calls == [] and (await stage(h, task)).phase == "orchestrating"
