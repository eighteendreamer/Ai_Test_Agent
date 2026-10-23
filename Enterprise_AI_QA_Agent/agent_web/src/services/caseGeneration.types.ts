import type { RequirementReview, RequirementVersion } from "./requirements.types";
import type { DocumentParseSnapshot } from "../types";

export interface GenerationTaskPolicy {
  timeout_seconds: number;
  call_timeout_seconds: number;
  max_input_bytes: number;
  max_response_bytes: number;
  framing_allowance_tokens: number;
  transport_policy: "provider_defaults" | "single_attempt";
}
export interface GenerationTaskSettings { scope_key: string; revision: number; payload: GenerationTaskPolicy }
export interface GenerationTaskRequest {
  creation_key: string;
  requirement_id: string;
  requirement_version_id: string;
  target_case_version_ids?: string[];
  include_new_cases?: boolean;
  new_case_mode_key?: string | null;
  max_new_cases?: number;
  model_key?: string | null;
}
export interface GenerationObject { storage_uri: string; sha256: string; size_bytes: number }
export interface GenerationCoverageStepReference {
  case_key: string; step_order: number; expected_field: string; expected_path: (string | number)[] | null;
}
export interface GenerationCoverageReviewItem {
  candidate_id: string; source_block_id: string; start_char: number; end_char: number;
  source: { kind: "line" | "docx" | "page"; start_line?: number | null; end_line?: number | null;
    page?: number | null; path: number[]; part?: string | null };
  scope: "local" | "inherited" | "unscoped";
  model_disposition: "in_scope" | "out_of_scope" | "uncertain" | "not_reported";
  rationale: string | null; step_refs: GenerationCoverageStepReference[]; validation_issues: string[];
}
export interface GenerationCoverageReview {
  schema_version: "document-generation-coverage-v2"; review_state: "required";
  mapping_state: "complete" | "incomplete" | "empty_inventory"; parse_snapshot_id: string; module_id: string;
  candidate_inventory_complete: boolean; items: GenerationCoverageReviewItem[];
  unknown_candidate_ids: string[]; warnings: string[]; sha256: string;
}
export interface GenerationGroup {
  id: string; project_id: string; document_id: string; version_id: string;
  creation_key: string; request_hash: string; scope: GenerationObject; scope_sha256: string;
  module_ids: string[]; config: GenerationTaskSettings; created_at: string; sha256: string;
}
export type GenerationModuleKind = "update" | "create" | "retirement_candidate";
export interface GenerationReceipt {
  module_id: string; kind: GenerationModuleKind; scope_sha256: string;
  response: GenerationObject | null;
  versions: { case_id: string; version_id: string; version: number; content_hash: string }[];
  retirement_target: Record<string, unknown> | null;
  generation_review?: GenerationCoverageReview | null;
}
export interface GenerationTask {
  id: string; project_id: string; document_id: string; version_id: string; group_id: string; module_id: string;
  creation_key: string; status: "queued" | "running" | "completed" | "failed";
  phase: "queued" | "generating" | "invoking" | "response_saved" | "result_saved" | "finished";
  message: string; attempt_no: number; error_code: string | null;
  created_at: string; updated_at: string; deadline_at: string | null; completed_at: string | null;
  checkpoint: {
    phase: "invoking" | "response_saved" | "result_saved";
    invocation_attempt_id: string | null; budget: Record<string, unknown>;
    response: GenerationObject | null; receipt: GenerationReceipt | null;
  } | null;
}
export interface GenerationGroupView { group: GenerationGroup; modules: GenerationTask[] }
export interface GenerationGroupPage { items: GenerationGroup[]; has_more: boolean; limit: number; offset: number }
export interface IncrementalGenerationScope {
  implementation_version: "incremental-generation-scope-v1";
  project_id: string; document_id: string;
  request: Omit<GenerationTaskRequest, "creation_key">;
  requirement: RequirementVersion; review: RequirementReview;
  input_checkpoint: Record<string, unknown>;
  template: Record<string, unknown> | null; authoring: Record<string, unknown> | null;
  modules: {
    id: string; kind: GenerationModuleKind; target: Record<string, unknown> | null;
    expected_latest_version: number | null; generation: Record<string, unknown> | null; sha256: string;
  }[];
  basis_limitations: string[]; sha256: string;
}
export interface DocumentGenerationTaskRequest {
  creation_key: string; parse_snapshot_id: string; objective: string; mode_key: string;
  max_cases_per_module: number; model_key: string | null;
}
export interface DocumentGenerationScope {
  implementation_version: "document-generation-scope-v1";
  project_id: string; document_id: string; request: Omit<DocumentGenerationTaskRequest, "creation_key">;
  parse_snapshot: DocumentParseSnapshot; text: string;
  segments: { module_id: string; index: number; title: string; section_id: string | null;
    start_char: number; end_char: number; block_ids: string[] }[];
  template: Record<string, unknown>; authoring: Record<string, unknown>;
  modules: IncrementalGenerationScope["modules"]; basis_limitations: string[]; sha256: string;
}
export type GenerationScope = IncrementalGenerationScope | DocumentGenerationScope;
export interface GenerationResponse {
  text: string; tool_calls: Record<string, unknown>[];
  request_payload: Record<string, unknown>; response_summary: Record<string, unknown>;
  raw_response: Record<string, unknown>;
}
export interface GenerationRetryRequest { creation_key: string }
export interface GenerationRetry {
  implementation_version: "generation-retry-v1";
  id: string; task_id: string; parent_task_id: string; parent_task_sha256: string;
  project_id: string; document_id: string; group_id: string; group_sha256: string;
  scope_sha256: string; module_id: string; module_sha256: string;
  creation_key: string; config: GenerationTaskSettings;
  strategy: "frozen_request" | "saved_response"; response: GenerationObject | null;
  created_at: string; sha256: string;
}
export interface GenerationRetryView { retry: GenerationRetry; task: GenerationTask }
export interface GenerationRetryPage { items: GenerationRetryView[]; has_more: boolean; limit: number; offset: number }
