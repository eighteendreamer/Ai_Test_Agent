import type { DocumentImpactResponseCheckpoint, DocumentImpactRuleCandidate, DocumentImpactWorkflowResult } from "./documentImpacts.types";

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
export interface RequirementMatrix extends RequirementBundle {
  points: {
    point: RequirementPoint; case_links: RequirementCaseLink[]; previous_case_links: RequirementCaseLink[];
    design_status: "uncovered" | "design_covered" | "needs_review"; execution_status: "not_assessed";
  }[];
  removed: boolean; deployment_status: "unknown";
}
