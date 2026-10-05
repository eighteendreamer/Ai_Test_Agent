export interface DocumentImpactPolicy {
  max_case_versions: number;
  max_context_chars: number;
  timeout_seconds: number;
}

export interface DocumentImpactAnalysisPolicy {
  model_key: string | null;
  analysis_guidance: string;
  max_input_bytes: number;
}

export interface DocumentImpactBatchPolicy {
  max_segment_chars: number;
  max_segments: number;
  boundary_context_chars: number;
  max_retrieval_bytes: number;
}

export interface DocumentImpactInvocationPolicy {
  max_logical_calls: number;
  max_reserved_estimated_tokens: number;
  call_timeout_seconds: number;
  max_response_bytes: number;
  token_method: "utf8_bytes_estimate" | "tiktoken_content";
  encoding_name: "cl100k_base" | "o200k_base" | null;
  framing_allowance_tokens: number;
}

export interface DocumentImpactReasoningPolicy {
  max_group_units: number;
  max_group_read_bytes: number;
}

export interface DocumentImpactSettings<T> { scope_key: string; revision: number; payload: T }

export interface DocumentImpactTaskRequest {
  creation_key: string;
  difference_snapshot_id: string;
  case_version_ids?: string[];
  model_key?: string | null;
  environment_key?: string | null;
  target_release?: string | null;
}

export interface DocumentImpactObject {
  storage_uri: string;
  sha256: string;
  size_bytes: number;
}

export interface DocumentImpactTaskRecord {
  id: string;
  project_id: string;
  document_id: string;
  version_id: string;
  difference_snapshot_id: string;
  creation_key: string;
  status: "queued" | "running" | "completed" | "failed";
  phase: "queued" | "preparing" | "input_saved" | "analyzing" | "result_saved" | "finished";
  message: string;
  attempt_no: number;
  input_checkpoint: (DocumentImpactObject & { task_id: string; input_sha256: string; created_at: string }) | null;
  snapshot_id: string | null;
  error_code: string | null;
  failure_artifact: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  deadline_at: string | null;
  completed_at: string | null;
}

export interface DocumentImpactPage<T> { items: T[]; has_more: boolean; limit: number; offset: number }
export interface DocumentImpactUnit { kind: "segment" | "case" | "difference"; id: string }
export interface DocumentImpactTextCitation {
  source_id: string; text_sha256: string; start_char: number; end_char: number; quote: string;
}
export interface DocumentImpactCaseCitation {
  version_id: string;
  version_record_sha256: string;
  root: "content_json" | "execution_notes";
  path: (string | number)[];
  value: unknown;
}
export interface DocumentImpactCaseCandidate {
  version_id: string;
  level: "none" | "minor" | "major" | "uncertain";
  reason: string;
  verification_points: string[];
  citations: DocumentImpactCaseCitation[];
}
export interface DocumentImpactRuleCandidate {
  key: string;
  kind: "added" | "modified" | "removed" | "uncertain";
  summary: string;
  reason: string;
  document_citations: DocumentImpactTextCitation[];
  case_impacts: DocumentImpactCaseCandidate[];
  applicability_suggestion: "unknown" | "current" | "future";
  uncertainties: string[];
}
export interface DocumentImpactResponseCheckpoint {
  task_id: string;
  call_id: string;
  validator_version: "impact-response-validator-v1" | "impact-response-validator-v2";
  request_sha256: string;
  response_sha256: string;
  manifest_sha256: string;
  result: DocumentImpactObject;
  created_at: string;
}
export interface DocumentImpactUnitResult {
  unit: DocumentImpactUnit;
  status: "analyzed" | "evidence_insufficient" | "rejected" | "ineligible" | "unknown" | "unprocessed" | "incomplete";
  calls: string[];
  final_check: DocumentImpactResponseCheckpoint | null;
  reason_codes: string[];
  unresolved: string[];
  candidates: DocumentImpactRuleCandidate[];
  reason?: string;
}
export interface DocumentImpactWorkflowProtocol {
  version: "impact-coordinator-v1" | "impact-coordinator-v2";
  discovery_instruction: string;
  case_instruction: string;
  sha256: string;
}
export interface DocumentImpactWorkflowResult {
  implementation_version: "impact-coordinator-v1" | "impact-coordinator-v2";
  task_id: string;
  input_sha256: string;
  manifest_sha256: string;
  protocol: DocumentImpactWorkflowProtocol;
  units: DocumentImpactUnitResult[];
  rules: { id: string; candidate: DocumentImpactRuleCandidate; origins: { checkpoint: DocumentImpactResponseCheckpoint; local_key: string }[] }[];
  overlaps: { rule_ids: string[]; status: "possible_overlap_or_conflict" }[];
  basis_limitations: string[];
  status: "complete" | "partial";
  semantic_status: "pending_review";
  deployment_status: "unknown";
  reasoning_checkpoint?: DocumentImpactObject & { task_id: string; input_sha256: string; manifest_sha256: string; config_sha256: string; created_at: string };
  discovery_tasks?: (DocumentImpactUnitResult & { task_id: string; evidence_outcomes: { unit: DocumentImpactUnit; outcome: "analyzed" | "evidence_insufficient" | "unprocessed"; reason: string }[] })[];
}
export interface DocumentImpactSnapshot extends DocumentImpactObject {
  id: string;
  task_id: string;
  // Complete audit metadata stays intact for JSON inspection; it is not an editable request.
  seed: Readonly<Record<string, unknown>>;
  input_sha256: string;
  manifest_sha256: string;
  status: "complete" | "partial";
  unit_count: number;
  analyzed_count: number;
  rule_count: number;
  created_at: string;
}
export interface DocumentImpactSnapshotView { snapshot: DocumentImpactSnapshot; result: DocumentImpactWorkflowResult }
