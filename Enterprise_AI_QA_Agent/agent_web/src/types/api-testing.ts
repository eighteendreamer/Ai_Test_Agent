/**
 * API Testing Mode frontend type definitions.
 *
 * These types describe the structured output from the api-test-runner tool
 * that the frontend can use to render specialized UI components.
 */

// ---------------------------------------------------------------------------
// Pending Selection (returned when user input is needed)
// ---------------------------------------------------------------------------

export interface ApiTestingPendingSelection {
  kind: "project" | "endpoint_scope" | "endpoints" | "credential";
  prompt: string;
  options: ApiTestingSelectionOption[];
  recommended_option_id: string;
  allow_free_text: boolean;
  notes: string[];
}

export interface ApiTestingSelectionOption {
  id: string;
  label: string;
  value: string;
  description?: string;
  recommended?: boolean;
  // Project-specific fields.
  project_name?: string;
  project_url?: string;
  doc_count?: number;
  endpoint_count?: number;
  score?: number;
  // Endpoint-specific fields.
  method?: string;
  path?: string;
  summary?: string;
  capability?: string;
  is_core?: boolean;
}

// ---------------------------------------------------------------------------
// Campaign Report (returned after execution)
// ---------------------------------------------------------------------------

export interface ApiTestingReport {
  campaign_id: string;
  project_name: string;
  summary: string;
  total_tasks: number;
  completed: number;
  failed: number;
  skipped: number;
  passed_checks: number;
  failed_checks: number;
  duration_ms: number;
  tasks: ApiTestingTaskSummary[];
  findings: string[];
  artifacts: ApiTestingArtifact[];
  verification_result: ApiTestingVerificationResult | Record<string, unknown>;
  evaluation_result: ApiTestingEvaluationResult | Record<string, unknown>;
  generated_at: string;
}

export interface ApiTestingTaskSummary {
  task_id: string;
  name: string;
  method: string;
  path: string;
  full_url: string;
  capability: string;
  status: "completed" | "failed" | "skipped" | "running" | "pending";
  response_status: number | null;
  duration_ms: number;
  check_count: number;
  checks_passed: number;
  checks_failed: number;
  error: string | null;
  checks: ApiTestingCheckResult[];
  evidence?: string;
}

export interface ApiTestingCheckResult {
  name: string;
  kind: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  description: string;
}

// ---------------------------------------------------------------------------
// Tool Output (full api-test-runner response)
// ---------------------------------------------------------------------------

export interface ApiTestRunnerOutput {
  status: "completed" | "partial" | "failed";
  phase: string;
  summary: string;
  trace_id?: string;
  selected_agent?: string;
  selected_tools?: string[];
  context_refs?: Array<Record<string, unknown>>;
  pending_selection?: ApiTestingPendingSelection;
  selected_project?: {
    project_id?: string;
    project_name: string;
    project_url: string;
    doc_count: number;
    endpoint_count: number;
  };
  project_candidates?: Array<{
    project_id?: string;
    project_name: string;
    project_url: string;
    doc_count: number;
    endpoint_count: number;
    score: number;
  }>;
  endpoint_count?: number;
  selected_endpoint_count?: number;
  campaign_id?: string;
  task_count?: number;
  report?: ApiTestingReport;
  report_markdown?: string;
  verification_result?: ApiTestingVerificationResult | Record<string, unknown>;
  evaluation_result?: ApiTestingEvaluationResult | Record<string, unknown>;
  artifacts?: ApiTestingArtifact[];
  errors?: ApiTestingErrorRecord[];
  execution_checkpoint?: ApiTestingExecutionCheckpoint;
  task_events?: ApiTestingTaskEvent[];
  notes?: string[];
}

export interface ApiTestingArtifact {
  type: string;
  filename: string;
  content_type: string;
  label: string;
  task_id?: string;
}

export interface ApiTestingVerificationResult {
  passed: boolean;
  verdict: string;
  summary: string;
  pass_rate: number;
  critical_passed: boolean;
  sla_passed: boolean;
  total_rules: number;
  failed_rules: number;
  rules: Array<Record<string, unknown>>;
}

export interface ApiTestingEvaluationResult {
  campaign_id: string;
  quality_score: Record<string, unknown>;
  coverage: Record<string, unknown>;
  failure_classifications: Array<Record<string, unknown>>;
  verification_verdict?: ApiTestingVerificationResult | null;
  recommendations: string[];
  summary: string;
}

export interface ApiTestingErrorRecord {
  task_id?: string;
  category?: string;
  severity?: string;
  message?: string;
  response_status?: number | null;
  is_transient?: boolean;
}

export interface ApiTestingExecutionCheckpoint {
  phase: string;
  campaign_id: string;
  last_event_type: string;
  active_task_id: string;
  active_task_status: string;
  task_summary: Record<string, number>;
  event_count: number;
  updated_at: string;
  trace_id: string;
}

export interface ApiTestingTaskEvent {
  event_id: string;
  event_type: string;
  task_id: string;
  task_name: string;
  method: string;
  path: string;
  status: string;
  phase: string;
  attempts: number;
  response_status: number | null;
  duration_ms: number;
  worker_session_id: string;
  summary: string;
  error: string;
  at: string;
}

// ---------------------------------------------------------------------------
// Helper: detect if a tool output is from api-test-runner
// ---------------------------------------------------------------------------

export function isApiTestRunnerOutput(output: Record<string, unknown>): output is ApiTestRunnerOutput & Record<string, unknown> {
  return (
    typeof output.phase === "string" &&
    typeof output.summary === "string" &&
    "api_testing_state" in output
  );
}

export function hasApiTestingReport(output: ApiTestRunnerOutput): boolean {
  return output.status === "completed" && output.report != null;
}

export function hasApiTestingPendingSelection(output: ApiTestRunnerOutput): boolean {
  return output.status === "partial" && output.pending_selection != null;
}
