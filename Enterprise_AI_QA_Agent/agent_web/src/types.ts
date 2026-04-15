export type SessionStatus =
  | "idle"
  | "running"
  | "waiting_approval"
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
  model_id: string;
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
  metadata: Record<string, unknown>;
}

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
