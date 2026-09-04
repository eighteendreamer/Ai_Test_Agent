import type {
  SessionDetail,
  SessionSummary,
  SessionSummaryPage,
  SessionSnapshot,
  SessionFlowResponse,
  SessionReplayResponse,
  ConversationResponse,
  ExecutionEvent,
  InputAttachment,
  ToolApprovalRequest,
  ToolJobRecord,
  ToolJobDetail,
  ToolArtifactRecord,
  ReportListPage,
  TaskPoolPage,
  SessionVerificationResponse,
} from "../types";
import { request } from "./http";

export function createSession(
  title = "Enterprise Intelligent QA Session",
  modeKey = "default",
): Promise<SessionDetail> {
  return request("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ title, mode_key: modeKey }),
  });
}

export function listSessions(): Promise<SessionSummary[]> {
  return request("/api/v1/sessions");
}

export function listSessionsPage(limit = 10, offset = 0, modeKey?: string): Promise<SessionSummaryPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (modeKey) params.set("mode_key", modeKey);
  return request(`/api/v1/sessions?${params.toString()}`);
}

export function listReportsPage(limit = 10, offset = 0): Promise<ReportListPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request(`/api/v1/reports?${params.toString()}`);
}

export function listTaskPoolPage(limit = 24, offset = 0): Promise<TaskPoolPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request(`/api/v1/task-pool?${params.toString()}`);
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return request(`/api/v1/sessions/${sessionId}`);
}

export function updateSession(
  sessionId: string,
  payload: {
    mode_key?: string | null;
    project_id?: string | null;
    preferred_model?: string | null;
    selected_agent?: string | null;
    metadata?: Record<string, unknown> | null;
  },
): Promise<SessionDetail> {
  return request(`/api/v1/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function listSessionSnapshots(
  sessionId: string,
  params: { limit?: number; includeGraphState?: boolean } = {},
): Promise<SessionSnapshot[]> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 20),
    include_graph_state: params.includeGraphState === false ? "false" : "true",
  });
  return request(`/api/v1/sessions/${sessionId}/snapshots?${query.toString()}`);
}

export function listSessionEvents(
  sessionId: string,
  params: { limit?: number; afterEventId?: string } = {},
): Promise<ExecutionEvent[]> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 500) });
  if (params.afterEventId) query.set("after_event_id", params.afterEventId);
  return request(`/api/v1/sessions/${sessionId}/events/history?${query.toString()}`);
}

export function getSessionFlow(
  sessionId: string,
  params: { turnId?: string } = {},
): Promise<SessionFlowResponse> {
  const query = new URLSearchParams();
  const turnId = String(params.turnId || "").trim();
  if (turnId) query.set("turn_id", turnId);
  const suffix = query.toString();
  return request(`/api/v1/sessions/${sessionId}/flow${suffix ? `?${suffix}` : ""}`);
}

export function listApprovals(sessionId: string): Promise<ToolApprovalRequest[]> {
  return request(`/api/v1/sessions/${sessionId}/approvals`);
}

export function resolveApproval(
  sessionId: string,
  approvalId: string,
  decision: "approved" | "denied",
  reason?: string,
): Promise<ToolApprovalRequest> {
  return request(`/api/v1/sessions/${sessionId}/approvals/${approvalId}`, {
    method: "POST",
    body: JSON.stringify({ decision, reason: reason || null }),
  });
}

export function sendMessage(
  sessionId: string,
  content: string,
  modeKey?: string,
  attachments: InputAttachment[] = [],
  context?: Record<string, unknown>,
  metadata?: Record<string, unknown>,
): Promise<ConversationResponse> {
  return request(`/api/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content,
      mode_key: modeKey || null,
      attachments,
      context: context || {},
      metadata: metadata || {},
    }),
  });
}

export function interruptSession(sessionId: string, reason?: string): Promise<SessionDetail> {
  return request(`/api/v1/sessions/${sessionId}/interrupt`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null, source: "web_console" }),
  });
}

export function resumeSession(sessionId: string, reason?: string): Promise<ConversationResponse> {
  return request(`/api/v1/sessions/${sessionId}/resume`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null, source: "web_console" }),
  });
}

export function replaySession(sessionId: string): Promise<SessionReplayResponse> {
  return request(`/api/v1/sessions/${sessionId}/replay`);
}

export function listToolJobs(sessionId: string): Promise<ToolJobRecord[]> {
  return request(`/api/v1/sessions/${sessionId}/tool-jobs`);
}

export function getToolJobDetail(sessionId: string, jobId: string): Promise<ToolJobDetail> {
  return request(`/api/v1/sessions/${sessionId}/tool-jobs/${jobId}`);
}

export function listArtifacts(sessionId: string): Promise<ToolArtifactRecord[]> {
  return request(`/api/v1/sessions/${sessionId}/artifacts`);
}

export function listVerifications(sessionId: string): Promise<SessionVerificationResponse> {
  return request(`/api/v1/sessions/${sessionId}/verifications`);
}

export function connectEvents(
  sessionId: string,
  onEvent: (event: ExecutionEvent) => void,
  lastEventId = "",
): EventSource {
  const query = new URLSearchParams();
  if (lastEventId) query.set("last_event_id", lastEventId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const source = new EventSource(`/api/v1/sessions/${sessionId}/events${suffix}`);
  source.onmessage = (message) => {
    const payload = JSON.parse(message.data) as ExecutionEvent;
    if (!payload.id && message.lastEventId) {
      payload.id = message.lastEventId;
    }
    onEvent(payload);
  };
  return source;
}
