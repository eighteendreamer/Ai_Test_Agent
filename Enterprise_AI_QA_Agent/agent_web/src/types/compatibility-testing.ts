export type CompatibilityPriority = "P0" | "P1" | "P2";
export type CompatibilityAvailability = "available" | "missing_provider" | "missing_runner" | "planned_only";

export interface CompatibilityProductArtifact {
  kind: string;
  uri: string;
  metadata?: Record<string, unknown>;
}

export interface CompatibilityProductAccessManifest {
  product_type: string;
  name: string;
  version?: string | null;
  artifact?: CompatibilityProductArtifact | null;
  entrypoint: {
    url?: string | null;
    package_name?: string | null;
    activity?: string | null;
    bundle_id?: string | null;
    mini_program_path?: string | null;
    command?: string | null;
  };
  auth: {
    strategy: string;
    username_ref?: string | null;
    password_ref?: string | null;
    token_ref?: string | null;
    manual_steps?: string[];
  };
  network: {
    requires_vpn: boolean;
    base_api?: string | null;
    proxy?: string | null;
  };
  test_scope: {
    modules: string[];
    priority_flows: string[];
    exclude: string[];
    forbidden_actions: string[];
    data_policy: string;
  };
  metadata?: Record<string, unknown>;
}

export interface CompatibilityProductProfile {
  product_profile_id: string;
  name: string;
  product_type: string;
  entrypoint?: string;
  access_manifest?: CompatibilityProductAccessManifest | null;
  auth?: Record<string, unknown>;
  test_scope?: string[];
  priority_flows?: string[];
  forbidden_actions?: string[];
  data_policy?: string;
  metadata?: Record<string, unknown>;
}

export interface CompatibilityProbeSummary {
  product_type: string;
  automation_capabilities: string[];
  required_providers: string[];
  manual_intervention_points: string[];
  blocking_requirements: string[];
  confidence: string;
  notes: string[];
}

export interface CompatibilityEnvironmentSpec {
  environment_id: string;
  name: string;
  priority: CompatibilityPriority;
  provider: string;
  os?: string | null;
  os_version?: string | null;
  browser?: string | null;
  browser_version?: string | null;
  device?: string | null;
  viewport?: string | null;
  automation_driver?: string | null;
  availability: CompatibilityAvailability;
  unavailable_reason?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CompatibilityCase {
  case_id: string;
  name: string;
  priority: CompatibilityPriority;
  risk_level: "low" | "medium" | "high";
  requires_manual_approval: boolean;
  assertions?: string[];
  notes?: string[];
}

export interface CompatibilityRiskItem {
  risk_id: string;
  case_id?: string | null;
  action: string;
  level: "low" | "medium" | "high";
  reason: string;
  suggested_control: string;
}

export interface CompatibilityModeCall {
  tool_key: string;
  reason: string;
  arguments: Record<string, unknown>;
}

export interface CompatibilityRunnerTask {
  task_id: string;
  environment_id: string;
  case_ids: string[];
  runner_selector: Record<string, unknown>;
  mode_calls: CompatibilityModeCall[];
  status: string;
  skipped_reason?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CompatibilityDispatchPlan {
  dispatch_id: string;
  plan_id: string;
  plan_version: number;
  status: string;
  tasks: CompatibilityRunnerTask[];
  skipped_environments: CompatibilityEnvironmentSpec[];
  total_case_runs: number;
  notes?: string[];
}

export interface CompatibilityPlan {
  plan_id: string;
  version: number;
  status: string;
  product: CompatibilityProductProfile;
  probe: CompatibilityProbeSummary;
  environments: CompatibilityEnvironmentSpec[];
  cases: CompatibilityCase[];
  risks: CompatibilityRiskItem[];
  estimated_task_count: number;
  estimated_duration_minutes: number;
  notes?: string[];
}

export interface CompatibilityTestRunnerOutput {
  status: string;
  ok: boolean;
  phase: string;
  summary: string;
  action?: string;
  compatibility_run?: Record<string, unknown>;
  plan?: CompatibilityPlan;
  dispatch_plan?: CompatibilityDispatchPlan;
  runner_queue?: {
    queued_task_count: number;
    tasks: Record<string, unknown>[];
    backend: string;
  };
  runner_summary?: CompatibilityRunnerTaskSummary | null;
  risks?: CompatibilityRiskItem[];
  report_markdown?: string;
  next_steps?: string[];
  missing_components?: string[];
}

export interface CompatibilityRunnerRecord {
  runner_id: string;
  name: string;
  os: string;
  capabilities: string[];
  devices: string[];
  max_parallel: number;
  status: string;
  active_task_ids: string[];
  registered_at: string;
  heartbeat_at: string;
  metadata: Record<string, unknown>;
}

export interface CompatibilityRunnerCleanupResponse {
  deleted_count: number;
  runner_ids: string[];
  skipped_runner_ids?: string[];
  skipped_reasons?: Record<string, string>;
}

export interface CompatibilityQueuedTask {
  task_id: string;
  dispatch_id: string;
  plan_id: string;
  environment_id: string;
  case_ids: string[];
  runner_selector: Record<string, unknown>;
  mode_calls: Record<string, unknown>[];
  status: string;
  assigned_runner_id?: string | null;
  result: Record<string, unknown>;
  error?: string | null;
  artifacts?: CompatibilityArtifactRecord[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface CompatibilityArtifactRecord {
  artifact_id: string;
  task_id: string;
  dispatch_id: string;
  plan_id: string;
  runner_id?: string | null;
  environment_id: string;
  type: string;
  uri: string;
  mime_type?: string | null;
  label: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface CompatibilityRunnerTaskSummary {
  total: number;
  queued: number;
  assigned: number;
  completed: number;
  failed: number;
  cancelled: number;
  other: number;
  artifact_count: number;
  by_environment: Record<string, Record<string, number>>;
  failure_summaries: Record<string, unknown>[];
}

export interface CompatibilityTaskRequeueResponse {
  requeued_count: number;
  task_ids: string[];
  skipped_task_ids?: string[];
  skipped_reasons?: Record<string, string>;
}

export interface CompatibilityEnvironmentReport {
  environment_id: string;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  pending: number;
  artifact_count: number;
  status: string;
}

export interface CompatibilityFailureReport {
  task_id: string;
  environment_id: string;
  runner_id?: string | null;
  summary: string;
  error?: string | null;
  artifact_ids: string[];
}

export interface CompatibilityRecoverableTaskReport {
  task_id: string;
  environment_id: string;
  runner_id?: string | null;
  status: string;
  summary: string;
  error?: string | null;
}

export interface CompatibilityExecutionReport {
  report_id: string;
  dispatch_id?: string | null;
  runner_id?: string | null;
  generated_at: string;
  status: string;
  summary: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  pending_tasks: number;
  pass_rate: number;
  artifact_count: number;
  environments: CompatibilityEnvironmentReport[];
  failures: CompatibilityFailureReport[];
  recoverable_tasks: CompatibilityRecoverableTaskReport[];
  artifacts: CompatibilityArtifactRecord[];
  markdown: string;
}

export function isCompatibilityTestRunnerOutput(output: Record<string, unknown>): output is CompatibilityTestRunnerOutput & Record<string, unknown> {
  return (
    typeof output.phase === "string" &&
    typeof output.summary === "string" &&
    ("plan" in output || "compatibility_run" in output || "missing_components" in output)
  );
}
