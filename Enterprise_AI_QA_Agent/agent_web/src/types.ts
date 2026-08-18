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
export type ModeKey =
  | "default"
  | "code_review"
  | "ui_automation"
  | "api_testing"
  | "security_testing"
  | "compatibility_testing"
  | "performance_testing"
  | "smoke_testing";

export type MessageRole = "system" | "user" | "assistant" | "tool" | "event";

export interface HealthResponse {
  status: string;
  name: string;
  environment: string;
  memory_backend?: string;
  session_backend?: string;
  tool_job_backend?: string;
  ui_graph_backend?: string;
  knowledge_enabled?: boolean;
  memory_target?: string;
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
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  supports_streaming?: boolean;
  enabled_by_default?: boolean;
  tags?: string[];
}

export interface ToolSearchResponse {
  query: string;
  total_tools: number;
  matches: ToolDescriptor[];
}

export interface SkillDescriptor {
  key: string;
  name: string;
  summary: string;
  description: string;
  recommended_agents: string[];
  tags: string[];
  tool_keys: string[];
  installed?: boolean;
  managed_root?: string;
  path?: string;
  source?: string;
  references?: string[];
  content?: string;
}

export interface SkillInstallRequest {
  source_path?: string | null;
  url?: string | null;
  key?: string | null;
  overwrite?: boolean;
}

export interface SkillUploadRequest {
  filename: string;
  content_base64: string;
  key?: string | null;
  overwrite?: boolean;
}

export type SkillMarketplaceSource = "anthropic" | "skillsmp";

export interface SkillMarketplaceItem {
  source: SkillMarketplaceSource | string;
  id: string;
  key?: string;
  name: string;
  description?: string;
  tags?: string[];
  url?: string;
  content?: string;
  metadata?: Record<string, unknown>;
}

export interface SkillMarketplaceSearchResponse {
  source: SkillMarketplaceSource | string;
  query: string;
  items: SkillMarketplaceItem[];
  count: number;
}

export interface SkillMarketplaceInstallRequest {
  source: SkillMarketplaceSource | string;
  skill_id: string;
  url?: string | null;
  key?: string | null;
  overwrite?: boolean;
}

export interface SkillBulkInstallResponse {
  ok: boolean;
  status: string;
  summary: string;
  installed_count: number;
  failed_count: number;
  items: SkillDescriptor[];
  failed: Array<{ key: string; source: string; error: string }>;
}

export interface SkillUpsertRequest {
  content: string;
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

export interface ModeDescriptor {
  key: ModeKey | string;
  name: string;
  summary: string;
  description: string;
  category: string;
  is_test_mode: boolean;
  case_driven_policy: "required" | "optional" | "exempt";
  default_agent_key: string;
  allowed_agent_keys: string[];
  default_skill_keys: string[];
  registered_tool_keys: string[];
  harness_key: string;
  placeholder: boolean;
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

export type OAuthProviderKey =
  | "azure_ad"
  | "google"
  | "github"
  | "codebuddy"
  | "trae"
  | "codex"
  | "generic";

export interface OAuthProviderProfile {
  key: OAuthProviderKey;
  display_name: string;
  authorization_url_template: string;
  token_url_template: string;
  default_scope: string;
  extra_auth_params: Record<string, string>;
  notes: string;
  /** LLM API base URL (empty for providers that need a custom resource URL) */
  api_base_url: string;
  /** Default transport protocol for this provider */
  default_transport: string;
  /** Whether this provider supports server-side model listing */
  has_model_listing: boolean;
  /** Whether the user must supply a resource-specific Base URL (e.g. Azure AD) */
  requires_base_url: boolean;
  /** Provider login flow kind: standard_oauth / custom_callback / polling_auth */
  auth_mode: string;
  /** Whether the user may manually enter a model name when listing is unavailable */
  supports_manual_model_name: boolean;
  /** Whether this provider is implemented and available in the UI */
  is_enabled: boolean;
}

export interface OAuthStartRequest {
  provider: OAuthProviderKey | string;
  redirect_uri: string;
  model_name?: string | null;
}

export interface OAuthModelItem {
  id: string;
  raw_id: string;
  name: string;
}

export interface OAuthModelsResponse {
  provider: string;
  models: OAuthModelItem[];
}

export interface OAuthStartResponse {
  state: string;
  authorization_url: string;
  redirect_uri: string;
}

export interface OAuthStatusResponse {
  state: string;
  status: "pending" | "completed" | "failed";
  refresh_token?: string | null;
  access_token_preview?: string | null;
  error?: string | null;
}

export interface ModelConfigPublic {
  id?: number | null;
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
  context_window: number;
  max_output_tokens: number;
  has_secret: boolean;
  capabilities: ModelCapabilities;
  capability_overrides: ModelCapabilitiesOverride;
  created_at?: string | null;
  updated_at?: string | null;
  auth_type: "api_key" | "oauth2";
  oauth_provider?: OAuthProviderKey | string | null;
  has_oauth_refresh_token: boolean;
  applications: ModelApplication[];
}

export type ModelApplication = "task_execution" | "embedding_retrieval";

export interface ModelCapabilities {
  text_input: boolean;
  text_output: boolean;
  tool_calling: boolean;
  vision: boolean;
  multi_image: boolean;
  file_input: boolean;
  pdf_input: boolean;
  reasoning: boolean;
  json_mode: boolean;
  streaming: boolean;
  parallel_tool_calls: boolean;
  image_url_input: boolean;
  image_base64_input: boolean;
}

export interface ModelCapabilitiesOverride {
  text_input?: boolean | null;
  text_output?: boolean | null;
  tool_calling?: boolean | null;
  vision?: boolean | null;
  multi_image?: boolean | null;
  file_input?: boolean | null;
  pdf_input?: boolean | null;
  reasoning?: boolean | null;
  json_mode?: boolean | null;
  streaming?: boolean | null;
  parallel_tool_calls?: boolean | null;
  image_url_input?: boolean | null;
  image_base64_input?: boolean | null;
}

export interface ModelConfigUpdateRequest {
  model_name: string;
  provider: string;
  transport?: string | null;
  base_url: string;
  api_key?: string | null;
  is_active: boolean;
  use_provider_defaults?: boolean | null;
  capability_overrides: ModelCapabilitiesOverride;
  auth_type: "api_key" | "oauth2";
  oauth_provider?: OAuthProviderKey | string | null;
  oauth_refresh_token?: string | null;
  context_window?: number | null;
  max_output_tokens?: number | null;
  applications: ModelApplication[];
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
  id: number;
  config_name: string;
  provider: string;
  enabled: boolean;
  is_default: boolean;
  sender_email: string;
  has_api_key?: boolean;
  has_secret_key?: boolean;
  description?: string | null;
  extra_config?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface EmailConfigCreateRequest {
  config_name: string;
  provider: string;
  enabled: boolean;
  is_default: boolean;
  sender_email: string;
  api_key?: string | null;
  secret_key?: string | null;
  description?: string | null;
  extra_config?: Record<string, unknown>;
}

export interface EmailConfigUpdateRequest {
  config_name: string;
  provider: string;
  enabled: boolean;
  is_default: boolean;
  sender_email: string;
  api_key?: string | null;
  secret_key?: string | null;
  description?: string | null;
  extra_config?: Record<string, unknown>;
}

export interface EmailConfigActionResponse {
  ok: boolean;
  message: string;
  item?: EmailConfigPublic | null;
}

export type ChannelProvider = "qq" | "feishu" | "weixin";
export type ChannelDomain = "qq" | "feishu" | "lark" | "weixin";
export type ChannelStatus = "unconfigured" | "configured" | "disabled";
export type ChannelPairingStatus = "pending" | "confirmed" | "expired";

export interface ChannelConfigPublic {
  id: number;
  config_name: string;
  provider: ChannelProvider;
  domain: ChannelDomain;
  enabled: boolean;
  status: ChannelStatus;
  public_config: Record<string, unknown>;
  credential_fields: Record<string, boolean>;
  has_credentials: boolean;
  description?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ChannelConfigCreateRequest {
  config_name: string;
  provider: ChannelProvider;
  domain: ChannelDomain;
  enabled: boolean;
  public_config: Record<string, unknown>;
  credentials?: Record<string, string> | null;
  description?: string | null;
}

export interface ChannelConfigUpdateRequest extends ChannelConfigCreateRequest {
  clear_credentials?: boolean;
}

export interface ChannelConfigActionResponse {
  ok: boolean;
  message: string;
  item?: ChannelConfigPublic | null;
}

export interface ChannelPairingStartRequest {
  config_name?: string | null;
  enabled?: boolean;
  device_hint?: string | null;
}

export interface ChannelPairingSessionPublic {
  session_id: string;
  provider: ChannelProvider;
  domain: ChannelDomain;
  status: ChannelPairingStatus;
  pairing_url: string;
  qr_payload: string;
  expires_at: string;
  destroy_at: string;
  confirmed_at?: string | null;
  interval?: number | null;
  message?: string | null;
  item?: ChannelConfigPublic | null;
}

export interface ChannelAdvancedAllowlist {
  enabled: boolean;
  allow_all: boolean;
  qq_users: string[];
  feishu_users: string[];
  weixin_users: string[];
  qq_groups: string[];
  feishu_groups: string[];
  weixin_groups: string[];
  qq_approvers: string[];
  feishu_approvers: string[];
  weixin_approvers: string[];
  qq_admins: string[];
  feishu_admins: string[];
  weixin_admins: string[];
}

export interface ChannelAdvancedSelfUserIds {
  qq: string[];
  feishu: string[];
  weixin: string[];
}

export interface ChannelAdvancedPairing {
  enabled: boolean;
  request_ttl_minutes: number;
  max_pending_per_platform: number;
}

export interface ChannelAdvancedRoute {
  connection_id: string;
  platform: "" | ChannelDomain | string;
  chat_type: "" | "dm" | "group" | "guild" | "direct" | "thread" | string;
  chat_id: string;
  user_id: string;
  thread_id: string;
  model: string;
  tool_approval_mode: "" | "ask" | "auto" | "yolo" | "inherit" | string;
  workspace_root: string;
}

export interface ChannelAdvancedSettings {
  allowlist: ChannelAdvancedAllowlist;
  max_steps: number;
  debounce_ms: number;
  queue_mode: "steer" | "followup" | "collect" | "interrupt" | string;
  queue_cap: number;
  queue_drop: "summarize" | "old" | "new" | string;
  ignore_self_messages: boolean;
  self_user_ids: ChannelAdvancedSelfUserIds;
  pairing: ChannelAdvancedPairing;
  routes: ChannelAdvancedRoute[];
}

export interface ChannelInboundMessage {
  connection_id?: string;
  platform: ChannelDomain | string;
  domain?: ChannelDomain | string;
  chat_type: "dm" | "group" | "guild" | "direct" | "thread" | string;
  chat_id: string;
  user_id: string;
  user_name?: string;
  operator_id?: string;
  thread_id?: string;
  message_id?: string;
  text?: string;
  is_from_self?: boolean;
  claim_session?: boolean;
}

export interface ChannelGatewayRouteDecision {
  connection_id: string;
  platform: string;
  chat_type: string;
  chat_id: string;
  user_id: string;
  thread_id: string;
  workspace_root: string;
  model: string;
  tool_approval_mode: string;
}

export interface ChannelGatewayQueueDecision {
  session_key: string;
  mode: string;
  cap: number;
  drop: string;
  active: boolean;
  queued: boolean;
  rejected: boolean;
  dropped: boolean;
  pending: number;
}

export interface ChannelPairingRequestPublic {
  code: string;
  platform: string;
  connection_id: string;
  domain: string;
  chat_type: string;
  chat_id: string;
  user_id: string;
  user_name?: string;
  created_at: string;
  expires_at: string;
}

export interface ChannelGatewayDecision {
  allowed: boolean;
  action: "dispatch" | "ignore" | "reject" | "pairing_required" | "steer" | "queued" | "interrupt" | string;
  reason: string;
  message: string;
  is_admin: boolean;
  is_approver: boolean;
  max_steps: number;
  debounce_ms: number;
  route: ChannelGatewayRouteDecision;
  queue?: ChannelGatewayQueueDecision | null;
  pairing?: ChannelPairingRequestPublic | null;
}

export interface ChannelPairingApproveRequest {
  code: string;
  approve: boolean;
}

export interface ChannelGatewaySessionReleaseRequest {
  session_key: string;
}

export interface ChannelGatewaySessionReleaseResponse {
  session_key: string;
  active: boolean;
  next_message?: ChannelInboundMessage | null;
  pending: number;
  dropped_summaries: string[];
}

// --- Agent Mailbox types ---------------------------------------------------

export type MailboxProviderKey = string;

export interface MailboxProviderInfo {
  provider: string;
  display_name: string;
  auth_type: string;
  capabilities: string[];
  configuration_fields?: string[];
  default_base_url?: string;
}

export interface MailboxProviderStatus {
  ok: boolean;
  provider: string;
  capabilities: string[];
  error?: string;
  auth_status?: string;
  email?: string;
  aliases?: string[];
}

export interface MailboxSendPrepareRequest {
  recipients: string[];
  subject: string;
  content?: string;
  content_html?: string;
  config_id?: number | null;
}

export interface MailboxSendConfirmRequest {
  confirmation_token: string;
  config_id?: number | null;
}

export interface MailboxProvisionInboxRequest {
  options?: Record<string, unknown>;
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
  is_completion_worker?: boolean;
  debate_stage?: string;
  debate_round_index?: number;
  debate_total_round_count?: number;
  dispatch_role?: string;
  source_stage?: string;
  source_round_index?: number;
}

export interface CodeReviewReportMeta {
  task_id?: string;
  agent_key?: string;
  description?: string;
  status?: string;
  report_session_id?: string;
  summary?: string;
  updated_at?: string;
  completed_at?: string;
}

export interface PendingCompletionWorkerMeta {
  task_id?: string;
  description?: string;
  prompt?: string;
  agent_key?: string;
  model_key?: string | null;
  skill_keys?: string[];
  context?: Record<string, unknown>;
  parent_turn_id?: string;
  parent_trace_id?: string;
}

export interface CodeReviewDebateProgressMeta {
  stage?: string;
  status?: string;
  stage_label?: string;
  updated_at?: string;
  peer_review_count?: number;
  current_round_index?: number;
  total_round_count?: number;
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

export interface SessionSummary {
  id: string;
  title: string;
  status: SessionStatus;
  session_mode: SessionMode;
  runtime_mode: RuntimeMode;
  mode_key: ModeKey | string;
  project_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionSummaryPage {
  items: SessionSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ReportListEntry {
  session: SessionSummary;
  artifacts: ToolArtifactRecord[];
  verifications: VerificationResult[];
  worker_dispatches: WorkerDispatchRecord[];
  report_meta: Record<string, unknown>;
  report_session_id?: string | null;
  report_artifacts: ToolArtifactRecord[];
}

export interface ReportListPage {
  items: ReportListEntry[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface TaskPoolSessionSummary extends SessionSummary {
  selected_agent?: string | null;
  worker_dispatches: WorkerDispatchRecord[];
  parent_session_id: string;
}

export interface TaskPoolPage {
  items: TaskPoolSessionSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface SessionDetail {
  id: string;
  title: string;
  status: SessionStatus;
  session_mode: SessionMode;
  runtime_mode: RuntimeMode;
  mode_key: ModeKey | string;
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
  id?: string;
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

export interface KnowledgeProjectSummary {
  project_scope: string;
  page_count: number;
  element_count: number;
  entity_count: number;
  edge_count: number;
  latest_updated_at?: string | null;
}

export interface KnowledgeGraphSummary {
  project_scope: string;
  page_count: number;
  element_count: number;
  entity_count: number;
  edge_count: number;
  relation_counts: Record<string, number>;
  latest_updated_at?: string | null;
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  kind: string;
  summary: string;
  metadata: Record<string, unknown>;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  metadata: Record<string, unknown>;
}

export interface KnowledgeGraphResponse {
  summary: KnowledgeGraphSummary;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

export interface KnowledgeProjectDeleteResponse {
  ok: boolean;
  project_scope: string;
  deleted_counts: Record<string, number>;
  message: string;
}

export interface InputAttachment {
  kind: string;
  name: string;
  uri?: string | null;
  content_type?: string | null;
  text_excerpt?: string | null;
  metadata: Record<string, unknown>;
}

export interface ApiDocRecord {
  id: string;
  title: string;
  filename: string;
  project_id?: string | null;
  legacy_project_name?: string | null;
  project_name?: string | null;
  project_url?: string | null;
  source: string;
  format_label: string;
  content_type: string;
  size_bytes: number;
  storage_uri: string;
  bucket?: string | null;
  object_name?: string | null;
  endpoint_count?: number | null;
  preview_available: boolean;
  preview_truncated: boolean;
  preview_text?: string | null;
  preview_error?: string | null;
  uploaded_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ApiDocUploadRequest {
  filename: string;
  content_base64: string;
  source?: string;
  title?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  project_url?: string | null;
}

export interface ApiDocUpdateRequest {
  title?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  project_url?: string | null;
}

export interface ApiDocImportUrlRequest {
  url: string;
  title?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  project_url?: string | null;
  source?: string;
}

export interface ApiDocImportIntegrationRequest {
  integration_id: string;
  title?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  project_url?: string | null;
  document_url?: string | null;
  workspace_id?: string | null;
  import_source_id?: string | null;
  source?: string;
}

export type IntegrationKind = "mcp" | "api";

export type ProjectStatus = "active" | "archived";

export interface ProjectRecord {
  id: string;
  project_key: string;
  name: string;
  description?: string | null;
  base_url?: string | null;
  graph_scope_key?: string | null;
  status: ProjectStatus;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface ProjectPage {
  items: ProjectRecord[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectOverview {
  project: ProjectRecord;
  api_doc_count: number;
  session_count: number;
  test_case_count: number;
  test_suite_count: number;
  test_run_count: number;
  graph: {
    available: boolean;
    project_scope?: string | null;
    page_count: number;
    element_count: number;
    entity_count: number;
    edge_count: number;
    error?: string | null;
  };
}

export type LegacySmokeMappedStatus = "passed" | "failed" | "blocked" | "skipped";

export interface LegacySmokeScopeBinding {
  source_system: "legacy_smoke_catalog";
  project_id: string;
  project_scope: string;
  created_at: string;
}

export interface LegacySmokeCaseSummary {
  source_system: "legacy_smoke_catalog";
  read_only: true;
  legacy_case_id: string;
  title: string;
  case_type: string;
  legacy_status: string;
  mapped_status: LegacySmokeMappedStatus;
  summary: string;
  assertion_count: number;
  passed_count: number;
  failed_count: number;
  duration_ms: number;
  evidence_count: number;
}

export interface LegacySmokeRunSummary {
  source_system: "legacy_smoke_catalog";
  read_only: true;
  legacy_run_id: string;
  legacy_plan_id: string;
  legacy_plan_version: number;
  project_scope: string;
  legacy_status: string;
  legacy_verdict: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  blocked_cases: number;
  started_at: string;
  completed_at?: string | null;
  summary: string;
  case_results: LegacySmokeCaseSummary[];
}

export interface LegacySmokeRunPage {
  project_id: string;
  bindings: LegacySmokeScopeBinding[];
  items: LegacySmokeRunSummary[];
  limit: number;
  next_cursor?: string | null;
  has_more: boolean;
}

export type TestCaseLifecycleStatus = "draft" | "pending_review" | "active" | "disabled" | "archived";
export type TestCasePriority = "P0" | "P1" | "P2" | "P3";

export interface TestCaseSourceRef {
  source_type: string;
  source_id: string;
  version?: string | null;
  label: string;
  uri?: string | null;
  metadata: Record<string, unknown>;
}

export interface TestCaseStep {
  order: number;
  action: string;
  expected?: string | null;
  kind: string;
  data: Record<string, unknown>;
}

export interface TestCaseAssertion {
  kind: string;
  target: string;
  operator: string;
  expected: unknown;
  description: string;
}

export interface TestCaseRecord {
  id: string;
  project_id: string;
  case_key: string;
  title: string;
  mode_key: string;
  case_type: string;
  priority: TestCasePriority;
  lifecycle_status: TestCaseLifecycleStatus;
  active_version_id?: string | null;
  latest_version: number;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface TestCaseVersionRecord {
  id: string;
  case_id: string;
  version: number;
  preconditions: string[];
  steps: TestCaseStep[];
  assertions: TestCaseAssertion[];
  test_data: Record<string, unknown>;
  cleanup: string[];
  content_hash: string;
  source_refs: TestCaseSourceRef[];
  model_key: string;
  prompt_version: string;
  skill_versions: Record<string, string>;
  created_at: string;
}

export interface TestCaseBundle {
  case: TestCaseRecord;
  version: TestCaseVersionRecord;
}

export interface TestCasePage {
  items: TestCaseRecord[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface TestCaseGenerationResponse {
  items: TestCaseBundle[];
  warnings: string[];
}

export type TestSuiteStatus = "active" | "archived";

export interface TestSuiteRecord {
  id: string;
  project_id: string;
  name: string;
  description?: string | null;
  status: TestSuiteStatus;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface TestSuiteItemRecord {
  id: string;
  suite_id: string;
  case_id: string;
  case_version_id: string;
  position: number;
}

export interface TestSuiteBundle {
  suite: TestSuiteRecord;
  items: TestSuiteItemRecord[];
}

export interface TestSuitePage {
  items: TestSuiteBundle[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export type TestRunStatus = "queued" | "running" | "completed" | "cancelled";
export type RunItemStatus = "queued" | "claimed" | "running" | "waiting_approval" | "passed" | "failed" | "error" | "blocked" | "skipped" | "cancelled";

export interface TestRunStats {
  total: number;
  queued: number;
  claimed: number;
  running: number;
  waiting_approval: number;
  passed: number;
  failed: number;
  error: number;
  blocked: number;
  skipped: number;
  cancelled: number;
}

export interface TestRunRecord {
  id: string;
  project_id: string;
  suite_id: string;
  run_kind: "normal" | "regression";
  mode_key: string;
  session_id?: string | null;
  parent_run_id?: string | null;
  status: TestRunStatus;
  stats: TestRunStats;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  cancel_reason?: string | null;
}

export interface TestRunItemRecord {
  id: string;
  run_id: string;
  case_id: string;
  case_version_id: string;
  position: number;
  status: RunItemStatus;
  attempt_no: number;
  lease_owner?: string | null;
  lease_token?: string | null;
  lease_expires_at?: string | null;
  heartbeat_at?: string | null;
  approval_id?: string | null;
  tool_job_id?: string | null;
  result_id?: string | null;
  regression_source_result_id?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface TestCaseResultRecord {
  id: string;
  run_id: string;
  run_item_id: string;
  case_id: string;
  case_version_id: string;
  regression_source_result_id?: string | null;
  attempt_id: string;
  attempt_no: number;
  status: "passed" | "failed" | "error" | "blocked" | "skipped";
  summary: string;
  actual: Record<string, unknown>;
  evidence_refs: Array<Record<string, unknown>>;
  artifact_ids: string[];
  verification_ids: string[];
  tool_job_id?: string | null;
  metrics: Record<string, unknown>;
  error_message?: string | null;
  payload_hash: string;
  created_at: string;
}

export interface TestRunDetail {
  run: TestRunRecord;
  items: TestRunItemRecord[];
  attempts: Array<Record<string, unknown>>;
  results: Array<Record<string, unknown>>;
}

export interface TestRunPage {
  items: TestRunRecord[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export type RegressionFailureStatus = "failed" | "error" | "blocked";

export interface LatestRegressionRecord {
  run_id: string;
  run_status: TestRunStatus;
  run_item_id: string;
  item_status: RunItemStatus;
  result_id?: string | null;
  result_status?: RunItemStatus | null;
  case_version_id: string;
  created_at: string;
  updated_at: string;
}

export interface RegressionFailureSummary {
  source_result_id: string;
  source_run_id: string;
  source_run_status: TestRunStatus;
  source_run_created_at: string;
  case_id: string;
  case_version_id: string;
  case_key: string;
  case_title: string;
  mode_key: string;
  failure_status: RegressionFailureStatus;
  summary: string;
  error_message?: string | null;
  failed_at: string;
  evidence_count: number;
  artifact_count: number;
  verification_count: number;
  has_actual: boolean;
  regression_batch_count: number;
  latest_regression?: LatestRegressionRecord | null;
}

export interface RegressionFailurePage {
  items: RegressionFailureSummary[];
  limit: number;
  next_cursor?: string | null;
  has_more: boolean;
}

export interface RegressionEvidenceSummary {
  evidence_type: string;
  evidence_id: string;
  label: string;
}

export interface RegressionArtifactLink {
  artifact_id: string;
  content_url?: string | null;
}

export interface RegressionVerificationSummary {
  id: string;
  verifier: string;
  status: string;
  summary: string;
  assertion_count: number;
  passed_count: number;
  failed_count: number;
  created_at?: string | null;
}

export interface RegressionContext {
  source_result_id: string;
  source_run_id: string;
  case_id: string;
  case_version_id: string;
  mode_key: string;
  failure_status: RegressionFailureStatus;
  summary: string;
  error_message?: string | null;
  metrics: Record<string, unknown>;
  evidence: RegressionEvidenceSummary[];
  artifacts: RegressionArtifactLink[];
  verifications: RegressionVerificationSummary[];
  failed_at: string;
}

export interface RegressionBatchRecord {
  run_id: string;
  run_kind: "normal" | "regression";
  run_status: TestRunStatus;
  parent_run_id?: string | null;
  run_item_id: string;
  item_status: RunItemStatus;
  result_id?: string | null;
  result_status?: RunItemStatus | null;
  case_version_id: string;
  created_at: string;
  updated_at: string;
}

export interface RegressionBatchPage {
  source_result_id: string;
  items: RegressionBatchRecord[];
  limit: number;
  next_cursor?: string | null;
  has_more: boolean;
}
export type IntegrationTransport = "stdio" | "http" | "websocket";
export type IntegrationAuthType = "none" | "bearer" | "api_key" | "basic";

export interface IntegrationRecord {
  id: string;
  name: string;
  kind: IntegrationKind;
  enabled: boolean;
  description?: string | null;
  project_name?: string | null;
  document_url?: string | null;
  transport?: IntegrationTransport | null;
  endpoint_url?: string | null;
  command?: string | null;
  capabilities: string[];
  headers: Record<string, string>;
  env: Record<string, string>;
  base_url?: string | null;
  auth_type: IntegrationAuthType;
  auth_config: Record<string, string>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface IntegrationCreateRequest {
  name: string;
  kind: IntegrationKind;
  enabled?: boolean;
  description?: string | null;
  project_name?: string | null;
  document_url?: string | null;
  transport?: IntegrationTransport | null;
  endpoint_url?: string | null;
  command?: string | null;
  capabilities?: string[];
  headers?: Record<string, string>;
  env?: Record<string, string>;
  base_url?: string | null;
  auth_type?: IntegrationAuthType;
  auth_config?: Record<string, string>;
  metadata?: Record<string, unknown>;
}

export interface IntegrationUpdateRequest {
  name?: string | null;
  enabled?: boolean | null;
  description?: string | null;
  project_name?: string | null;
  document_url?: string | null;
  transport?: IntegrationTransport | null;
  endpoint_url?: string | null;
  command?: string | null;
  capabilities?: string[] | null;
  headers?: Record<string, string> | null;
  env?: Record<string, string> | null;
  base_url?: string | null;
  auth_type?: IntegrationAuthType | null;
  auth_config?: Record<string, string> | null;
  metadata?: Record<string, unknown> | null;
}

export interface IntegrationTestResponse {
  ok: boolean;
  message: string;
  target_url?: string | null;
  integration_id?: string | null;
  status_code?: number | null;
  latency_ms?: number | null;
  preview?: string | null;
}

export interface IntegrationWorkspaceDescriptor {
  id: string;
  name: string;
  description?: string | null;
  project_name?: string | null;
  document_count: number;
}

export interface IntegrationImportSourceDescriptor {
  id: string;
  label: string;
  document_url: string;
  kind: string;
  summary?: string | null;
  project_name?: string | null;
  workspace_id?: string | null;
  workspace_name?: string | null;
}

export interface IntegrationImportSourcesResponse {
  integration_id: string;
  kind: IntegrationKind;
  supports_workspace_selection: boolean;
  workspaces: IntegrationWorkspaceDescriptor[];
  sources: IntegrationImportSourceDescriptor[];
}

export interface MCPServerDescriptor {
  key: string;
  name: string;
  summary: string;
  transport: string;
  status: string;
  capabilities: string[];
  enabled: boolean;
}

export type ManagedMCPSourceKind = "builtin" | "external";
export type MCPServerTransport = "stdio" | "streamable_http" | "sse";

export interface MCPProviderDescriptor {
  key: string;
  name: string;
  summary: string;
  supports_workspace_selection: boolean;
  supports_document_import: boolean;
}

export interface MCPServerCreateRequest {
  name: string;
  enabled?: boolean;
  transport?: MCPServerTransport | null;
  purpose?: string | null;
  config?: Record<string, unknown>;
  supported_protocols?: MCPServerTransport[];
  description?: string | null;
  project_name?: string | null;
  document_url?: string | null;
  endpoint_url?: string | null;
  command?: string | null;
  args?: string[];
  headers?: Record<string, string>;
  env?: Record<string, string>;
  cwd?: string | null;
  capabilities?: string[];
  provider_key?: string | null;
  confirmed_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface MCPServerUpdateRequest {
  name?: string | null;
  enabled?: boolean | null;
  transport?: MCPServerTransport | null;
  purpose?: string | null;
  config?: Record<string, unknown> | null;
  supported_protocols?: MCPServerTransport[] | null;
  description?: string | null;
  project_name?: string | null;
  document_url?: string | null;
  endpoint_url?: string | null;
  command?: string | null;
  args?: string[] | null;
  headers?: Record<string, string> | null;
  env?: Record<string, string> | null;
  cwd?: string | null;
  capabilities?: string[] | null;
  provider_key?: string | null;
  confirmed_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface MCPServerImportRequest {
  payload: Record<string, unknown>;
}

export interface MCPServerImportResponse {
  servers: Array<{
    id: string;
    name: string;
    enabled: boolean;
    transport: MCPServerTransport;
    purpose?: string | null;
    config: Record<string, unknown>;
    supported_protocols: MCPServerTransport[];
    created_at: string;
    updated_at: string;
  }>;
}

export interface ManagedMCPServerDescriptor {
  key: string;
  name: string;
  summary: string;
  transport: string;
  status: string;
  capabilities: string[];
  enabled: boolean;
  purpose?: string | null;
  config: Record<string, unknown>;
  supported_protocols: string[];
  source_kind: ManagedMCPSourceKind;
  provider_key?: string | null;
  provider_name?: string | null;
  integration_id?: string | null;
  project_name?: string | null;
  endpoint_url?: string | null;
  document_url?: string | null;
  supports_workspace_selection: boolean;
  supports_document_import: boolean;
  metadata: Record<string, unknown>;
}

export type ManagedMCPToolSourceKind = "builtin_capability" | "external_tool" | "external_capability";

export interface ManagedMCPToolDescriptor {
  key: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  source_kind: ManagedMCPToolSourceKind;
  managed_server_key: string;
  server_name: string;
  provider_key?: string | null;
  tags: string[];
}

export interface ManagedMCPToolsResponse {
  server_key: string;
  server_name: string;
  source_kind: ManagedMCPSourceKind;
  tools: ManagedMCPToolDescriptor[];
}

export interface ManagedMCPResourceDescriptor {
  uri: string;
  name: string;
  description: string;
  mime_type?: string | null;
  managed_server_key: string;
  server_name: string;
  provider_key?: string | null;
  tags: string[];
}

export interface ManagedMCPPromptDescriptor {
  name: string;
  description: string;
  arguments: Array<Record<string, unknown>>;
  managed_server_key: string;
  server_name: string;
  provider_key?: string | null;
  tags: string[];
}

export interface ManagedMCPResourcesResponse {
  server_key: string;
  server_name: string;
  source_kind: ManagedMCPSourceKind;
  resources: ManagedMCPResourceDescriptor[];
}

export interface ManagedMCPPromptsResponse {
  server_key: string;
  server_name: string;
  source_kind: ManagedMCPSourceKind;
  prompts: ManagedMCPPromptDescriptor[];
}

export interface ManagedMCPTestResponse {
  ok: boolean;
  message: string;
  server_key: string;
  server_name: string;
  source_kind: ManagedMCPSourceKind;
  status_code?: number | null;
  latency_ms?: number | null;
  tool_count?: number | null;
}

export interface ManagedMCPToolCallRequest {
  arguments?: Record<string, unknown>;
}

export interface ManagedMCPToolCallResponse {
  ok: boolean;
  server_key: string;
  server_name: string;
  source_kind: ManagedMCPSourceKind;
  tool_name: string;
  result: Record<string, unknown>;
  error?: string | null;
}

export interface UploadedAttachmentRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  storage_uri: string;
  preview_text?: string | null;
  preview_truncated: boolean;
  preview_error?: string | null;
  uploaded_at: string;
  metadata: Record<string, unknown>;
}

export interface SecurityProfileDescriptor {
  profile_key: string;
  tool_name: string;
  description: string;
  tool_family: string;
  surface_types: string[];
  allowed_arguments: string[];
  risk_level: string;
  requires_approval: boolean;
  timeout_seconds: number;
  parser_key: string;
  artifact_policy: string;
  notes: string;
  verification_capabilities: {
    structured_output: boolean;
    parsed_fields: string[];
    assertions: Record<string, string[]>;
  };
}

export interface SecurityFamilyGroup {
  family: string;
  runner_key: string;
  profiles: SecurityProfileDescriptor[];
}

export interface SecurityProfilesResponse {
  families: SecurityFamilyGroup[];
  surface_family_map: Record<string, string[]>;
  family_runner_map: Record<string, string>;
  total_count: number;
}

// --- Docker management types ----------------------------------------------

export interface DockerEnvironment {
  cli_available: boolean;
  daemon_available: boolean;
  docker_path: string;
  client_version: string;
  server_version: string;
  operating_system?: string;
  os_type?: string;
  architecture?: string;
  name?: string;
  cpus?: number;
  memory_bytes?: number;
  error?: string;
}

export interface DockerPortBinding {
  host_port: number;
  container_port: number;
  protocol: "tcp" | "udp";
}

export interface DockerVolumeBinding {
  source: string;
  target: string;
  read_only: boolean;
}

export interface DockerImage {
  id: string;
  repository: string;
  tag: string;
  reference: string;
  digest: string;
  size: string;
  created_at: string;
}

export interface DockerContainer {
  id: string;
  name: string;
  image: string;
  image_id: string;
  command: string;
  state: string;
  status: string;
  ports: string;
  created_at: string;
  size: string;
  labels: string;
  managed: boolean;
}

export interface DockerRequiredImage {
  key: string;
  image: string;
  category: string;
  purpose: string;
  template_key: string;
  installed: boolean;
  image_id?: string;
  size?: string;
  container_count: number;
}

export interface DockerTemplate {
  key: string;
  image: string;
  category: string;
  purpose: string;
  default_name: string;
  ports: DockerPortBinding[];
  volumes: DockerVolumeBinding[];
  environment_keys: string[];
}

export interface DockerSummary {
  required_total: number;
  required_installed: number;
  image_count: number;
  container_count: number;
  running_count: number;
}

export interface DockerOverviewResponse {
  ok: boolean;
  environment: DockerEnvironment;
  required_images: DockerRequiredImage[];
  templates: DockerTemplate[];
  images: DockerImage[];
  containers: DockerContainer[];
  summary: DockerSummary;
}

export interface DockerImagePullRequest {
  image: string;
}

export interface DockerImagePullResponse {
  ok: boolean;
  action: string;
  image: string;
  message: string;
  output?: string;
}

export interface DockerImageRemoveRequest {
  image: string;
  force?: boolean;
}

export interface DockerContainerCreateRequest {
  name: string;
  image: string;
  command?: string[];
  entrypoint?: string | null;
  ports?: DockerPortBinding[];
  volumes?: DockerVolumeBinding[];
  environment?: Record<string, string>;
  restart_policy?: "no" | "always" | "unless-stopped" | "on-failure";
  start?: boolean;
}

export interface DockerContainerCreateResponse {
  ok: boolean;
  created: boolean;
  container_id: string;
  name?: string;
  image?: string;
  state?: string;
  message?: string;
}

export interface DockerTemplateCreateRequest {
  name?: string;
  pull_if_missing?: boolean;
}

export interface DockerContainerActionRequest {
  action: "start" | "stop" | "restart" | "pause" | "unpause";
}

export interface DockerContainerActionResponse {
  ok: boolean;
  action: string;
  container_id: string;
}

export interface DockerContainerRemoveRequest {
  force?: boolean;
}

export interface DockerContainerLogsResponse {
  ok: boolean;
  container_id: string;
  tail: number;
  logs: string;
}
