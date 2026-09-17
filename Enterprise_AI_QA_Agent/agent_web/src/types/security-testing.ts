/**
 * Security Testing Mode frontend type definitions.
 *
 * These types describe the structured output from the security-scan-runner tool
 * so the frontend can render campaign progress, reports, evidence checks, and
 * delivery state without reverse-engineering tool text.
 */

// ---------------------------------------------------------------------------
// Campaign Report
// ---------------------------------------------------------------------------

export interface SecurityTestingReport {
  campaign_id: string;
  title: string;
  target_summary: string;
  scope_description: string;
  executive_summary: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  skipped_tasks: number;
  total_findings: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  findings: SecurityFinding[];
  activities: Array<Record<string, unknown>>;
  assets_discovered: number;
  services_discovered: number;
  evidence_count: number;
  execution_record_count: number;
  duration_seconds: number;
  tested_at: string;
  generated_at: string;
  recommendations: string[];
  limitations: string[];
  artifacts: SecurityTestingArtifact[];
  verification_result: SecurityVerificationResult | Record<string, unknown>;
  evaluation_result: SecurityEvaluationResult | Record<string, unknown>;
}

export interface SecurityFinding {
  finding_id: string;
  title: string;
  category: string;
  surface_type: string;
  severity: "critical" | "high" | "medium" | "low" | "info" | string;
  confidence: string;
  cvss_score?: number | null;
  cve_id: string;
  affected_target: string;
  affected_port?: number | null;
  affected_service: string;
  description: string;
  evidence_summary: string;
  reproduction_steps: string[];
  recommendation: string;
  references: string[];
  source_task_ids: string[];
  verified: boolean;
  false_positive: boolean;
}

// ---------------------------------------------------------------------------
// Tool Output (full security-scan-runner response)
// ---------------------------------------------------------------------------

export interface SecurityScanRunnerOutput {
  status: "completed" | "partial" | "failed";
  phase: string;
  summary: string;
  trace_id?: string;
  selected_agent?: string;
  selected_tools?: string[];
  context_refs?: Array<Record<string, unknown>>;
  targets?: Array<Record<string, unknown>>;
  campaign_id?: string;
  task_count?: number;
  task_summary?: Record<string, number>;
  report?: SecurityTestingReport;
  report_markdown?: string;
  report_html?: string;
  artifacts?: SecurityTestingArtifact[];
  delivery?: SecurityReportDelivery;
  verification_result?: SecurityVerificationResult | Record<string, unknown>;
  evaluation_result?: SecurityEvaluationResult | Record<string, unknown>;
  errors?: SecurityTestingErrorRecord[];
  execution_checkpoint?: SecurityExecutionCheckpoint;
  task_events?: SecurityTaskEvent[];
  notes?: string[];
  security_testing_state?: Record<string, unknown>;
}

export interface SecurityTestingArtifact {
  type: string;
  filename: string;
  content_type: string;
  label: string;
  task_id?: string;
}

export interface SecurityReportDelivery {
  channel: string;
  status: "not_requested" | "sent" | "failed" | "skipped" | string;
  recipients: string[];
  subject: string;
  summary: string;
  sent: boolean;
  provider: string;
  from_email: string;
  recipient_count: number;
  artifact_paths: string[];
  error: string;
  delivered_at: string;
}

export interface SecurityVerificationResult {
  passed: boolean;
  verdict: "approved" | "warning" | "rejected" | string;
  summary: string;
  evidence_count: number;
  execution_record_count: number;
  total_rules: number;
  failed_rules: number;
  rules: Array<Record<string, unknown>>;
}

export interface SecurityEvaluationResult {
  campaign_id: string;
  quality_score: Record<string, unknown>;
  coverage: Record<string, unknown>;
  failure_classifications: Array<Record<string, unknown>>;
  risk_summary: Record<string, number>;
  verification_verdict?: SecurityVerificationResult | null;
  recommendations: string[];
  summary: string;
}

export interface SecurityTestingErrorRecord {
  task_id?: string;
  category?: string;
  severity?: string;
  message?: string;
  command_profile?: string;
  target?: string;
  is_transient?: boolean;
}

export interface SecurityExecutionCheckpoint {
  post_recon_stage?: number;
  waiting_reason?: string;
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

export interface SecurityTaskEvent {
  event_id: string;
  event_type: string;
  task_id: string;
  task_name: string;
  command_profile: string;
  tool_family: string;
  target: string;
  status: string;
  phase: string;
  attempts: number;
  worker_session_id: string;
  runner_key: string;
  summary: string;
  error: string;
  at: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function isSecurityScanRunnerOutput(output: Record<string, unknown>): output is SecurityScanRunnerOutput {
  return (
    typeof output.phase === "string" &&
    typeof output.summary === "string" &&
    "security_testing_state" in output
  );
}

export function hasSecurityTestingReport(output: SecurityScanRunnerOutput): boolean {
  return output.status === "completed" && output.report != null;
}

export function hasSecurityTestingDelivery(output: SecurityScanRunnerOutput): boolean {
  return output.delivery?.status === "sent";
}
