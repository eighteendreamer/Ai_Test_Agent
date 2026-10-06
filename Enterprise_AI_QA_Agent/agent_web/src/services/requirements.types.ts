import type { DocumentImpactResponseCheckpoint, DocumentImpactRuleCandidate, DocumentImpactWorkflowResult } from "./documentImpacts.types";
import type { TestCaseVersionReview, TestCaseLifecycleStatus } from "../types";
import type { GenerationReceipt } from "./caseGeneration.types";

export interface RequirementPointInput { key: string; description: string }
export interface RequirementPoint extends RequirementPointInput { id: string }
export interface RequirementCaseLinkInput { case_version_id: string; point_keys: string[] }
export interface RequirementCaseLink extends RequirementCaseLinkInput { case_id: string; version_record_sha256: string }
export interface RequirementReviewRequest {
  request_id: string;
  rule_id: string;
  decision: "accept" | "reject" | "defer";
  note: string;
  expected_review_id?: string | null;
  requirement_id?: string | null;
  expected_version_id?: string | null;
  change?: "added" | "modified" | "removed" | null;
  title?: string | null;
  statement?: string | null;
  points?: RequirementPointInput[];
  case_links?: RequirementCaseLinkInput[];
}
export interface RequirementVersion {
  id: string; requirement_id: string; version: number; parent_version_id: string | null;
  review_id: string; change: "added" | "modified" | "removed";
  title: string; statement: string; points: RequirementPoint[]; case_links: RequirementCaseLink[];
  content_sha256: string; created_at: string;
}
export interface RequirementRecord {
  id: string; project_id: string; document_id: string; current_version_id: string;
  latest_version: number; created_at: string; updated_at: string;
}
export interface RequirementBundle { requirement: RequirementRecord; version: RequirementVersion }
export interface RequirementReview {
  id: string; project_id: string; document_id: string; request: RequirementReviewRequest; request_sha256: string;
  source: {
    snapshot_id: string; task_id: string; snapshot_sha256: string; input_sha256: string; manifest_sha256: string;
    rule: DocumentImpactWorkflowResult["rules"][number]; case_assessments: DocumentImpactRuleCandidate[];
    case_checkpoints: DocumentImpactResponseCheckpoint[];
    basis_limitations: string[]; analysis_status: "complete" | "partial"; deployment_status: "unknown";
  };
  previous_review_id: string | null; review_number: number; requirement_version: RequirementVersion | null; created_at: string;
}
export interface RequirementPage<T> { items: T[]; has_more: boolean; limit: number; offset: number }
export interface RequirementCoverageLinkInput extends RequirementCaseLinkInput {
  content_hash: string; generation_task_id?: string | null;
}
export interface RequirementCoverageRequest {
  request_id: string; expected_coverage_id?: string | null; note: string; case_links: RequirementCoverageLinkInput[];
}
export interface RequirementCoverageLink extends RequirementCaseLink {
  content_hash: string; review_status: "draft" | "pending_review" | "approved" | "unavailable";
  review: TestCaseVersionReview | null; lifecycle_status: TestCaseLifecycleStatus; active_version_id: string | null;
  generation: {
    task_id: string; source_document_id: string; group_id: string; group_sha256: string; module_sha256: string;
    receipt: GenerationReceipt; retry: { id: string; sha256: string } | null; provenance: Record<string, unknown>;
  } | null;
}
export interface RequirementCoverageReview {
  implementation_version: "requirement-coverage-v1"; id: string; project_id: string; document_id: string;
  requirement_id: string; requirement_version_id: string; requirement_version_sha256: string;
  request: RequirementCoverageRequest; request_sha256: string; previous_coverage_id: string | null;
  revision: number; case_links: RequirementCoverageLink[]; created_at: string; sha256: string;
}
export interface RequirementMatrix extends RequirementBundle {
  points: {
    point: RequirementPoint; case_links: RequirementCaseLink[]; previous_case_links: RequirementCaseLink[];
    design_status: "uncovered" | "design_covered" | "needs_review"; execution_status: "passed" | "failed" | "partial" | "not_assessed";
    execution_scope?: "selected_checks"; execution_checks?: RequirementExecutionCheck[];
  }[];
  removed: boolean; deployment_status: "unknown";
  coverage_review?: RequirementCoverageReview | null;
  execution_review?: RequirementExecutionReview | null;
}

export interface RequirementExecutionCheckInput {
  result_id: string; verification_id: string; check_index: number; point_keys: string[];
}
export interface RequirementExecutionRequest {
  request_id: string; coverage_id: string; expected_execution_id?: string | null; note: string;
  checks: RequirementExecutionCheckInput[];
}
export interface RequirementExecutionCheck extends RequirementExecutionCheckInput {
  run_id: string; run_item_id: string; attempt_id: string; attempt_no: number; case_id: string; case_version_id: string;
  content_hash: string; version_record_sha256: string; result_payload_hash: string; result_record_sha256: string;
  result_status: "passed" | "failed" | "error" | "blocked" | "skipped";
  run_kind: "normal" | "regression"; run_origin: "native" | "legacy_smoke_import";
  execution_strategy: "legacy" | "agent"; mode_key: string; regression_source_result_id: string | null;
  plan_sha256: string | null; check: Record<string, unknown>; check_sha256: string;
  tool_sources: Record<string, unknown>[];
}
export interface RequirementExecutionReview {
  implementation_version: "requirement-execution-v1"; id: string; project_id: string; document_id: string;
  requirement_id: string; requirement_version_id: string; requirement_version_sha256: string; coverage_sha256: string;
  request: RequirementExecutionRequest; request_sha256: string; previous_execution_id: string | null;
  revision: number; checks: RequirementExecutionCheck[]; created_at: string; sha256: string;
}
export interface RequirementExecutionCandidate {
  result_id: string; run_id: string; case_id: string; case_version_id: string; status: string;
  summary: string; created_at: string; checks: Record<string, unknown>[]; warnings: string[];
}
