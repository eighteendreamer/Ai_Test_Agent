"""Replay saved production runs to verify environment design prerequisites.

Read-only application research: no database writes, HTTP, model calls or new
deployment assertions. The only write is this stage's explicit audit report.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
RESEARCH_BASELINE = "d47ab66d7cd9380ac9b4fd7d46b3952e07206edb"
sys.path.insert(0, str(ROOT / "Agent_Server"))

from src.application.test_runs.plan_mapping import build_plan_run, json_digest
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.document_impact_analysis import DocumentImpactEnvironment
from src.schemas.project import ProjectRecord
from src.schemas.requirements import RequirementMatrix, RequirementReviewSource
from src.schemas.run_management import TestRunDetail


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def research() -> dict:
    baseline = RESEARCH_BASELINE
    report = json.loads((DOCS / "p4-execution-product-validation.json").read_bytes())
    raw_files = report["raw_files"]
    for item in raw_files:
        content = (ROOT / item["path"]).read_bytes()
        assert len(content) == item["size_bytes"] and digest(content) == item["sha256"], item["path"]
    fixture_path = DOCS / "p4-execution-product-App-verified/requirement-execution-product.json"
    fixture = json.loads(fixture_path.read_bytes())
    runs = []
    for raw in fixture["run_details"]:
        detail = TestRunDetail.model_validate(raw)
        run = detail.run
        request = CasePlanRunRequest(creation_key=run.creation_key,
            mode_key=run.mode_key, session_id=run.session_id)
        _, reproduced, _ = build_plan_run(run.plan_snapshot.plan, request,
            now=run.created_at, created_by=run.created_by, regression=run.plan_snapshot.regression)
        assert reproduced.id == run.id and reproduced.creation_hash == run.creation_hash
        assert reproduced.plan_snapshot == run.plan_snapshot
        assert run.status == "completed"
        assert run.plan_snapshot.plan.request.runtime_context == {}
        assert all(result.status == "failed" for result in detail.results)
        steps = [len(e.payload["content"]["steps"]) for e in run.plan_snapshot.plan.evidence
            if e.kind == "case"]
        assert steps == [12]
        assert "environment_id" not in raw["run"] and "deployment_id" not in raw["run"]
        plan_data = run.plan_snapshot.plan.model_dump(mode="json")
        if run.plan_snapshot.plan.regression is None:
            plan_data.pop("regression", None)
        hash_input = {"plan": plan_data, "request": request.model_dump(mode="json")}
        if run.plan_snapshot.regression:
            hash_input["regression"] = run.plan_snapshot.regression.model_dump(mode="json")
        assert json_digest(hash_input) == run.creation_hash
        # Design experiment only; do not persist or alter an old typed contract.
        changed_input = {**hash_input, "request": {**hash_input["request"], "environment_id": None}}
        assert json_digest(changed_input) != run.creation_hash
        runs.append({"run_id": run.id, "run_kind": run.run_kind,
            "creation_hash": run.creation_hash, "mapping_reproduced": True,
            "full_case_steps": steps, "result_statuses": [r.status for r in detail.results],
            "runtime_context_empty": True, "formal_target_binding_absent": True,
            "adding_optional_null_changes_old_creation_hash": True})
    assert {r["run_kind"] for r in runs} == {"normal", "regression"}
    assert fixture["matrix"]["deployment_status"] == "unknown"
    assert fixture["autonomous_agent_model_execution"] is False
    assert len(fixture["business_http"]) == 6
    assert "environment_id" not in CasePlanningRequest.model_fields
    assert "environment_id" not in CasePlanRunRequest.model_fields
    assert "environment_id" not in ProjectRecord.model_fields
    assert RequirementReviewSource.model_fields["deployment_status"].default == "unknown"
    assert RequirementMatrix.model_fields["deployment_status"].default == "unknown"
    declaration = DocumentImpactEnvironment(environment_key="declared-example", target_release="declared-v2")
    assert declaration.provenance == "user_declared" and declaration.deployment_status == "unknown"
    arbitrary = CasePlanningRequest(objective="research only",
        runtime_context={"environment": {"unverified_label": "example"}})
    assert arbitrary.runtime_context["environment"]["unverified_label"] == "example"
    assert "revision" not in inspect.signature(MySQLApplicationConfigStore.get).parameters
    assert "expected_revision" not in inspect.signature(MySQLApplicationConfigStore.save).parameters
    sources = [
        "Agent_Server/src/schemas/project.py", "Agent_Server/src/schemas/case_planning.py",
        "Agent_Server/src/schemas/run_management.py", "Agent_Server/src/schemas/requirements.py",
        "Agent_Server/src/schemas/resource.py", "Agent_Server/src/infrastructure/application_config_store.py",
        "Agent_Server/src/application/test_cases/planning_service.py",
        "Agent_Server/src/application/test_runs/plan_run_service.py",
        "Agent_Server/src/application/test_runs/plan_mapping.py",
        "Agent_Server/src/application/test_runs/agent_case_context.py",
        "Agent_Server/src/application/test_runs/agent_regression_service.py",
        "Agent_Server/src/application/test_runs/run_store.py",
        "Agent_Server/src/application/documents/impact_analysis_context.py",
        "Agent_Server/src/schemas/document_impact_analysis.py",
        "agent_web/src/features/tools/plugins/DocumentImpactPanel.vue",
        "Agent_Server/src/application/security/execution_environment_service.py",
        "Agent_Server/src/modes/compatibility_testing_mode/environment_matrix.py",
    ]
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix() + "/"
    source_hashes = []
    for name in sources:
        saved = subprocess.check_output(["git", "show", f"{baseline}:{prefix}{name}"], cwd=repo)
        local = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        assert local == saved.replace(b"\r\n", b"\n"), name
        source_hashes.append({"path": name, "git_blob_sha256": digest(saved)})
    gitignore = (ROOT / ".gitignore").read_bytes()
    assert digest(gitignore) == "d604cb2eb1e5da2b92ec113f2e14d05889963ae0fe9492760670f3a1c5c8b241"
    return {"research_baseline": baseline, "existing_raw_files_verified": len(raw_files),
        "fixture_path": fixture_path.relative_to(ROOT).as_posix(),
        "fixture_sha256": digest(fixture_path.read_bytes()), "runs": runs,
        "source_blobs": source_hashes, "matrix_deployment_status": "unknown",
        "config_store_has_historical_revision_get": False,
        "config_store_has_expected_revision_save": False,
        "arbitrary_runtime_environment_is_accepted_data": True,
        "impact_environment_declaration_exists": True,
        "impact_environment_declaration_is_deployment_proof": False,
        "production_code_modified": False, "new_environment_feature_implemented": False,
        "database_or_http_used_this_research": False, "autonomous_agent_execution_tested": False,
        "gitignore_unchanged": True, "full_P0_P5_complete": False}


if __name__ == "__main__":
    result = research()
    output = DOCS / "p4-environment-research-facts-verified.json"
    if "--record" in sys.argv:
        if output.exists():
            raise RuntimeError("Preserve the prior research report; use a new stage filename")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        assert json.loads(output.read_bytes()) == result
    print(json.dumps({"baseline": result["research_baseline"],
        "existing_raw_files_verified": result["existing_raw_files_verified"],
        "saved_runs_replayed": len(result["runs"]), "deployment_status": "unknown",
        "new_environment_feature_implemented": False}, ensure_ascii=False))
