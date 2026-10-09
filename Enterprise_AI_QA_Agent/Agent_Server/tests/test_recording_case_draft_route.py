"""录制完成后生成用例草稿的 HTTP 契约测试。

路由只负责项目范围和错误映射；实际事件转换及 PostgreSQL 草稿写入由
RecordingCaseDraftService/TestCaseService 的生产测试覆盖，不在这里复制业务逻辑。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.recordings import router
from src.schemas.case_management import (
    TestCaseBundle as _TestCaseBundle,
    TestCaseRecord as _TestCaseRecord,
    TestCaseSourceRef as _TestCaseSourceRef,
    TestCaseVersionRecord as _TestCaseVersionRecord,
)


def _bundle() -> _TestCaseBundle:
    case = _TestCaseRecord(
        id="case-recording-1",
        project_id="project-1",
        case_key="ui_rec_abc",
        title="录制草稿",
        mode_key="ui_automation",
        case_type="ui_recorded",
        created_at="2026-10-10T00:00:00Z",
        updated_at="2026-10-10T00:00:00Z",
    )
    version = _TestCaseVersionRecord(
        id="version-recording-1",
        case_id=case.id,
        version=1,
        content_hash="a" * 64,
        content_format="legacy-v1",
        source_refs=[_TestCaseSourceRef(source_type="ui_recording", source_id="recording-1")],
        model_key="rule-based-recorder-conversion",
        prompt_version="p2-2.v2",
        skill_versions={"recorder": "p2-2.v2"},
        preconditions=["入口地址 https://example.test"],
        steps=[{"order": 1, "action": "点击「提交」", "kind": "ui_action"}],
        assertions=[{"kind": "manual_review", "target": "page", "operator": "manual_review"}],
        test_data={},
        cleanup=[],
        execution_notes="评审后执行",
        created_at="2026-10-10T00:00:00Z",
    )
    return _TestCaseBundle(case=case, version=version)


class FakeDraftService:
    def __init__(self, result: dict | None = None, error: ValueError | None = None) -> None:
        self.result = result or {
            "recording_id": "recording-1",
            "step_count": 1,
            **_bundle().model_dump(mode="json"),
        }
        self.error = error
        self.calls: list[tuple[str, str | None]] = []

    async def create_draft_from_recording(self, recording_id: str, *, project_id: str | None = None,
                                          created_by: str | None = None) -> dict:
        self.calls.append((recording_id, project_id))
        if self.error is not None:
            raise self.error
        return self.result

    async def get_created_draft(self, recording_id: str, *, project_id: str | None = None):
        self.calls.append((recording_id, project_id))
        if self.error is not None:
            raise self.error
        return self.result


def _app(service: FakeDraftService | None = None, *, installed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    if installed:
        app.state.recording_case_draft_service = service or FakeDraftService()
    return app


def test_recording_case_draft_route_returns_existing_case_bundle() -> None:
    service = FakeDraftService()
    client = TestClient(_app(service))

    response = client.post(
        "/api/v1/recordings/recording-1/case-draft",
        params={"project_id": "project-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["recording_id"] == "recording-1"
    assert body["step_count"] == 1
    assert body["case"]["project_id"] == "project-1"
    assert body["version"]["source_refs"][0]["source_type"] == "ui_recording"
    assert service.calls == [("recording-1", "project-1")]


def test_recording_case_draft_route_maps_not_found_and_conflict() -> None:
    missing = TestClient(_app(FakeDraftService(error=ValueError("recording not found: recording-1"))))
    response = missing.post("/api/v1/recordings/recording-1/case-draft")
    assert response.status_code == 404

    conflict = TestClient(_app(FakeDraftService(error=ValueError("recording does not belong to the given project"))))
    response = conflict.post(
        "/api/v1/recordings/recording-1/case-draft",
        params={"project_id": "project-2"},
    )
    assert response.status_code == 409


def test_recording_case_draft_route_requires_service() -> None:
    client = TestClient(_app(installed=False))

    response = client.post("/api/v1/recordings/recording-1/case-draft")

    assert response.status_code == 503
    assert response.json()["detail"] == "recording case draft service not initialized"


def test_recording_case_draft_read_route_never_creates():
    service = FakeDraftService()
    response = TestClient(_app(service)).get("/api/v1/recordings/recording-1/case-draft?project_id=project-1")
    assert response.status_code == 200
    assert response.json()["case"]["id"] == "case-recording-1"
    assert service.calls == [("recording-1", "project-1")]
