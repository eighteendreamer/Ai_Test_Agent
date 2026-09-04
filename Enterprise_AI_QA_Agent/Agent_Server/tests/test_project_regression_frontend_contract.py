from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[2] / "agent_web" / "src"
SERVICES_DIR = WEB_ROOT / "services"


def _all_services_source() -> str:
    """Read all .ts files in the services directory (api.ts was split into sub-modules)."""
    parts: list[str] = []
    for ts_file in sorted(SERVICES_DIR.glob("*.ts")):
        parts.append(ts_file.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_frontend_regression_api_contract_has_typed_keyset_endpoints():
    types_source = (WEB_ROOT / "types.ts").read_text(encoding="utf-8")
    api_source = _all_services_source()

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


def test_security_profile_frontend_contract_consumes_capability_matrix():
    types_source = (WEB_ROOT / "types.ts").read_text(encoding="utf-8")
    api_source = _all_services_source()
    view_source = (
        WEB_ROOT / "features" / "tools" / "plugins" / "ScannersPlugin.vue"
    ).read_text(encoding="utf-8")

    assert "verification_capabilities" in types_source
    assert "listSecurityProfiles()" in api_source
    assert "/api/v1/registry/security-profiles" in api_source
    assert "Verification Contract" in view_source
    assert "selectedProfile.verification_capabilities.assertions" in view_source
    assert "selectedProfile.verification_capabilities.parsed_fields" in view_source


def test_frontend_run_item_approval_contract_uses_dedicated_endpoint():
    types_source = (WEB_ROOT / "types.ts").read_text(encoding="utf-8")
    api_source = _all_services_source()
    store_source = (WEB_ROOT / "stores" / "session.ts").read_text(encoding="utf-8")
    run_contract = types_source.split("export type RunItemStatus", 1)[1].split(
        "export interface TestRunDetail",
        1,
    )[0]

    assert '"waiting_approval"' in run_contract
    assert "waiting_approval: number;" in run_contract
    assert "approval_id?: string | null;" in run_contract
    assert "tool_job_id?: string | null;" in run_contract
    assert "resolveTestRunItemApproval(" in api_source
    assert "/run-items/${encodeURIComponent(itemId)}/approval" in api_source
    assert "approval_id: approvalId" in api_source
    assert 'approval?.metadata?.source === "test_run_case_execution"' in store_source


def test_projects_view_exposes_terminal_approval_and_cleanup_operations():
    types_source = (WEB_ROOT / "types.ts").read_text(encoding="utf-8")
    api_source = _all_services_source()
    view_source = (WEB_ROOT / "views" / "ProjectsView.vue").read_text(encoding="utf-8")

    assert "resource_cleanup_completed_at?: string | null;" in types_source
    assert "getTestRun(" in api_source
    assert "reconcileCancelledRunResources(" in api_source
    assert "openRunDetail" in view_source
    assert "waiting_approval" in view_source
    assert "decideRunItemApproval" in view_source
    assert "重试资源补偿" in view_source
    assert "失败项回归" in view_source
