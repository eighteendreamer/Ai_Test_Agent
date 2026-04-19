export type SessionStatus =
  | "idle"
  | "running"
  | "waiting_approval"
  | "interrupted"
  | "completed"
  | "failed";

export type SessionMode =
  | "normal"
  | "coordinator"
  | "resumed"
  | "direct_connect"
  | "remote"
  | "assistant_viewer"
  | "background_task";

export type RuntimeMode = "interactive" | "headless" | "background";

export type MessageRole = "system" | "user" | "assistant" | "tool" | "event";

export interface HealthResponse {
  status: string;
  name: string;
  environment: string;
  memory_backend?: string;
  knowledge_enabled?: boolean;
  knowledge_target?: string;
}

export type ServiceCheckStatus = "online" | "degraded" | "offline";

export interface ServiceCheckItem {
  key: string;
  label: string;
  status: ServiceCheckStatus;
  detail: string;
  meta?: string;
}

export interface SystemStatusSummary {
  label: string;
  tone: ServiceCheckStatus;
  checks: ServiceCheckItem[];
  onlineCount: number;
  totalCount: number;
}

export interface ToolDescriptor {
  key: string;
  name: string;
  description: string;
  category: string;
  permission_level?: "safe" | "ask" | "restricted";
  supports_streaming?: boolean;
  enabled_by_default?: boolean;
  tags?: string[];
}

export interface AgentDescriptor {
  key: string;
  name: string;
  role: string;
  summary: string;
  description: string;
  supported_tools: string[];
  supported_skills: string[];
  supported_models: string[];
  default_model?: string | null;
  tags: string[];
}

export interface ModelDescriptor {
  key: string;
  name: string;
  provider: string;
  summary: string;
  supports_tools: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  tags: string[];
}

export interface ModelConfigPublic {
  key: string;
  name: string;
  provider: string;
  transport: string;
  api_base_url: string;
  description: string;
  supports_tools: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  is_active: boolean;
  is_default: boolean;
  temperature?: number | null;
  max_tokens: number;
  has_secret: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ModelConfigUpdateRequest {
  model_name: string;
  provider: string;
  base_url: string;
  api_key?: string | null;
  is_active: boolean;
}

export interface ModelConfigActionResponse {
  ok: boolean;
  message: string;
  item?: ModelConfigPublic | null;
}

export interface ModelConfigConnectionTestResponse {
  ok: boolean;
  message: string;
  item: ModelConfigPublic;
  provider: string;
  api_base_url: string;
  latency_ms?: number | null;
  preview?: string | null;
}

export interface EmailConfigPublic {
  provider: "aliyun" | "cybermail";
  enabled: boolean;
  is_default: boolean;
  from_email: string;
  from_name: string;
  reply_to: string;
  access_key_id?: string | null;
  account_name?: string | null;
  region?: string | null;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_username?: string | null;
  use_tls: boolean;
  has_access_key_secret: boolean;
  has_smtp_password: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface EmailConfigUpdateRequest {
  provider: "aliyun" | "cybermail";
  enabled: boolean;
  is_default: boolean;
  from_email: string;
  from_name: string;
  reply_to: string;
  access_key_id?: string | null;
  access_key_secret?: string | null;
  account_name?: string | null;
  region?: string | null;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_username?: string | null;
  smtp_password?: string | null;
  use_tls: boolean;
}

export interface ToolApprovalRequest {
  id: string;
  session_id: string;
  tool_key: string;
  tool_name: string;
  reason: string;
  status: "pending" | "approved" | "denied";
  created_at: string;
  resolved_at?: string | null;
  decision_note?: string | null;
  metadata: Record<string, unknown>;
}

export interface WorkerDispatchRecord {
  task_id: string;
  child_session_id?: string;
  agent_key: string;
  model_key?: string;
  description: string;
  status: string;
  completed_at?: string;
}

export interface WorkerFailureReason {
  agent_key?: string;
  description?: string;
  reason?: string;
  timestamp?: string;
}

export interface WorkerFailureGuard {
  turn_id?: string;
  count?: number;
  last_error?: string;
  recent_errors?: WorkerFailureReason[];
  blocked?: boolean;
}

export interface SessionSnapshot {
  id: string;
  session_id: string;
  version: number;
  stage: string;
  created_at: string;
  graph_state: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SessionDetail {
  id: string;
  title: string;
  status: SessionStatus;
  session_mode: SessionMode;
  runtime_mode: RuntimeMode;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  event_count: number;
  snapshot_count: number;
  preferred_model?: string | null;
  selected_agent?: string | null;
  pending_approvals: ToolApprovalRequest[];
  last_snapshot?: SessionSnapshot | null;
  control_state: string;
  is_resumable: boolean;
  is_interrupted: boolean;
  replay_available: boolean;
  verification_results: VerificationResult[];
  metadata: Record<string, unknown>;
}

export interface PendingInputPayload {
  content?: string;
  submit_mode?: string;
  command_name?: string | null;
  message_kind?: string;
}

export interface PendingInputQueueEntry {
  id: string;
  created_at: string;
  busy_status: string;
  queue_behavior: string;
  interrupt_policy: string;
  reason: string;
  payload: PendingInputPayload;
  metadata: Record<string, unknown>;
}

export type SessionWatcherPhase =
  | "idle"
  | "running"
  | "waiting_approval"
  | "interrupted"
  | "failed"
  | "completed";

export interface ExecutionEvent {
  type: string;
  session_id: string;
  timestamp: string;
  payload: Record<string, string | number | boolean | null | undefined>;
}

export interface ConversationResponse {
  session: SessionDetail;
  output: ChatMessage;
  events: ExecutionEvent[];
}

export interface SessionReplayResponse {
  session_id: string;
  control_state: string;
  latest_snapshot?: SessionSnapshot | null;
  events: ExecutionEvent[];
  metadata: Record<string, unknown>;
}

export interface ToolExecutionSummary {
  call_id: string;
  job_id?: string | null;
  tool_key: string;
  tool_name: string;
  status: string;
  summary: string;
  trace_id?: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  approval_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export type ToolJobStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "resume_requested"
  | "retry_requested"
  | "completed"
  | "partial"
  | "failed"
  | "denied"
  | "cancelled";

export interface ToolArtifactRecord {
  id: string;
  tool_job_id: string;
  session_id: string;
  turn_id: string;
  trace_id: string;
  tool_key: string;
  artifact_type: string;
  label?: string | null;
  path: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ToolJobRecord {
  id: string;
  session_id: string;
  turn_id: string;
  trace_id: string;
  call_id: string;
  tool_key: string;
  tool_name: string;
  status: ToolJobStatus;
  attempt: number;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  summary: string;
  error_message?: string | null;
  artifact_count: number;
  created_at: string;
  updated_at: string;
  heartbeat_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  metadata: Record<string, unknown>;
}

export interface ToolJobDetail extends ToolJobRecord {
  artifacts: ToolArtifactRecord[];
}

export type VerificationStatus = "passed" | "failed" | "partial" | "not_run";

export interface VerificationEvidence {
  source_type: string;
  source_id: string;
  label: string;
  detail: string;
  metadata: Record<string, unknown>;
}

export interface VerificationResult {
  id: string;
  session_id: string;
  turn_id: string;
  trace_id: string;
  verifier: string;
  status: VerificationStatus;
  summary: string;
  assertion_count: number;
  passed_count: number;
  failed_count: number;
  evidence: VerificationEvidence[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface SessionVerificationResponse {
  session_id: string;
  verification_results: VerificationResult[];
  metadata: Record<string, unknown>;
}
