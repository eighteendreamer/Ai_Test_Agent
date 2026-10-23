"""Contract checks for project-bound, fixed-version scene knowledge reads."""
import pytest
from pydantic import ValidationError

from src.application.recorder.scene_knowledge import RecordingSceneKnowledge, SceneKnowledgeQuery, SCENE_KNOWLEDGE_PROTOCOL
from src.application.runtime.tool_input_validation import validate_tool_input
from src.registry.tools import ToolRegistry


def test_scene_read_requires_exact_identity_and_search_is_catalog_only():
    with pytest.raises(ValidationError):
        SceneKnowledgeQuery(operation="scene_read", query="scene")
    with pytest.raises(ValidationError):
        SceneKnowledgeQuery(operation="scene_search", scene_id="scene")
    query = SceneKnowledgeQuery(operation="scene_read", scene_id="s", version_id="v", content_hash="a" * 64)
    assert query.operation == "scene_read"


def test_scene_operations_do_not_require_text_query():
    schema = ToolRegistry().get("knowledge-rag").input_schema
    assert validate_tool_input(schema, {
        "operation": "scene_read",
        "scene_id": "scene",
        "version_id": "version",
        "content_hash": "a" * 64,
    }) == []
    assert validate_tool_input(schema, {"operation": "scene_search"}) == []


@pytest.mark.asyncio
async def test_knowledge_reader_uses_persisted_session_project_and_preserves_not_executed():
    class Sessions:
        async def get_session(self, session_id): return type("Session", (), {"id": session_id, "project_id": "p1"})()
    class Projects:
        async def get(self, project_id): assert project_id == "p1"
    class Scenes:
        async def search(self, project_id, query, limit, offset):
            assert (project_id, query, limit, offset) == ("p1", "x", 2, 3)
            return {"items": [{"scene_id": "s", "version_id": "v", "content_hash": "a" * 64}], "has_more": False}
        async def get(self, project_id, scene_id, version_id):
            assert (project_id, scene_id, version_id) == ("p1", "s", "v")
            content = type("Content", (), {"model_dump": lambda self, mode=None: {"name": "S", "status": "organized", "steps": [], "aliases": []}})()
            source = {"recording_id": "r", "original_draft": {"steps": []}}
            version = type("Version", (), {"scene_id": "s", "id": "v", "version": 1, "content_hash": "a" * 64,
                "content": content, "source": source})()
            return type("Bundle", (), {"version": version})()
    reader = RecordingSceneKnowledge(scenes=Scenes(), projects=Projects(), sessions=Sessions())
    context = type("Context", (), {"session_id": "session", "context_bundle": {"project_id": "p1"}})()
    catalog = await reader.query(SceneKnowledgeQuery(operation="scene_search", query="x", limit=2, offset=3), context)
    assert catalog["protocol"] == SCENE_KNOWLEDGE_PROTOCOL and catalog["verification_status"] == "not_executed"
    with pytest.raises(ValueError, match="hash"):
        await reader.query(SceneKnowledgeQuery(operation="scene_read", scene_id="s", version_id="v", content_hash="b" * 64), context)
