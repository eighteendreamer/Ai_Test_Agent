from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[2] / "agent_web" / "src"


def test_frontend_regression_api_contract_has_typed_keyset_endpoints():
    types_source = (WEB_ROOT / "types.ts").read_text(encoding="utf-8")
    api_source = (WEB_ROOT / "services" / "api.ts").read_text(encoding="utf-8")

    assert "export interface RegressionFailurePage" in types_source
    assert "export interface RegressionContext" in types_source
    assert "export interface RegressionBatchPage" in types_source
    assert "listRegressionFailures(" in api_source
    assert "/regression-failures" in api_source
    assert "getRegressionContext(" in api_source
    assert "/regression-context" in api_source
    assert "listRegressionBatches(" in api_source
    assert "/regression-batches" in api_source


def test_projects_view_lazy_loads_filtered_regression_center():
    source = (WEB_ROOT / "views" / "ProjectsView.vue").read_text(encoding="utf-8")

    assert 'type ResourceTab = "cases" | "suites" | "runs" | "legacy-smoke" | "regressions"' in source
    assert "resourceTab === 'regressions'" in source
    assert "loadRegressionFailures" in source
    assert "regressionStatus" in source
    assert "regressionMode" in source
    assert "regressionNextCursor" in source
    assert "回归中心" in source
    assert "旧冒烟历史" in source
    assert "loadLegacySmokeRuns" in source


def test_projects_view_lazily_opens_public_evidence_and_batch_timeline():
    source = (WEB_ROOT / "views" / "ProjectsView.vue").read_text(encoding="utf-8")

    assert "openRegressionEvidence" in source
    assert "api.getRegressionContext" in source
    assert "api.listRegressionBatches" in source
    assert "regressionDrawerOpen" in source
    assert "证据与回归时间线" in source
    assert "artifact.content_url" in source
