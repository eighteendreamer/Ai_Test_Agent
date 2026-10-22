import type {
  SessionDetail,
  SessionSummary,
  SessionSummaryPage,
  SessionSnapshot,
  SessionFlowResponse,
  SessionReplayResponse,
  AgentTaskAccepted,
  ExecutionEvent,
  InputAttachment,
  ToolApprovalRequest,
  ToolJobRecord,
  ToolJobDetail,
  ToolArtifactRecord,
  ReportListPage,
  TaskPoolPage,
  TaskPoolEntriesPage,
  TaskPoolFilters,
  SessionVerificationResponse,
} from "../types";
import { request, requestText } from "./http";

function sessionPath(sessionId: string): string {
  return `/api/v1/sessions/${encodeURIComponent(sessionId)}`;
}

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

export function listTaskPoolEntries(limit = 24, offset = 0, filters: TaskPoolFilters = {}): Promise<TaskPoolEntriesPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return request(`/api/v1/task-pool/entries?${params.toString()}`);
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return request(sessionPath(sessionId));
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
  return request(sessionPath(sessionId), {
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
  return request(`${sessionPath(sessionId)}/snapshots?${query.toString()}`);
}

export function listSessionEvents(
  sessionId: string,
  params: { limit?: number; afterEventId?: string } = {},
): Promise<ExecutionEvent[]> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 500) });
  if (params.afterEventId) query.set("after_event_id", params.afterEventId);
  return request(`${sessionPath(sessionId)}/events/history?${query.toString()}`);
}

export function getSessionFlow(
  sessionId: string,
  params: { turnId?: string } = {},
): Promise<SessionFlowResponse> {
  const query = new URLSearchParams();
  const turnId = String(params.turnId || "").trim();
  if (turnId) query.set("turn_id", turnId);
  const suffix = query.toString();
  return request(`${sessionPath(sessionId)}/flow${suffix ? `?${suffix}` : ""}`);
}

export function listApprovals(sessionId: string): Promise<ToolApprovalRequest[]> {
  return request(`${sessionPath(sessionId)}/approvals`);
}

export function resolveApproval(
  sessionId: string,
  approvalId: string,
  decision: "approved" | "denied",
  reason?: string,
  clientRequestId?: string,
): Promise<ToolApprovalRequest | AgentTaskAccepted> {
  return request(`${sessionPath(sessionId)}/approvals/${encodeURIComponent(approvalId)}`, {
    method: "POST",
    body: JSON.stringify({
      decision,
      reason: reason || null,
      client_request_id: clientRequestId || null,
    }),
  });
}

export function sendMessage(
  sessionId: string,
  content: string,
  modeKey?: string,
  attachments: InputAttachment[] = [],
  context?: Record<string, unknown>,
  metadata?: Record<string, unknown>,
): Promise<AgentTaskAccepted> {
  return request(`${sessionPath(sessionId)}/messages`, {
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
  return request(`${sessionPath(sessionId)}/interrupt`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null, source: "web_console" }),
  });
}

export function resumeSession(
  sessionId: string,
  reason?: string,
  clientRequestId?: string,
): Promise<AgentTaskAccepted> {
  return request(`${sessionPath(sessionId)}/resume`, {
    method: "POST",
    body: JSON.stringify({
      reason: reason || null,
      source: "web_console",
      client_request_id: clientRequestId || null,
    }),
  });
}

export function replaySession(sessionId: string): Promise<SessionReplayResponse> {
  return request(`${sessionPath(sessionId)}/replay`);
}

export function listToolJobs(sessionId: string): Promise<ToolJobRecord[]> {
  return request(`${sessionPath(sessionId)}/tool-jobs`);
}

export function getToolJobDetail(sessionId: string, jobId: string): Promise<ToolJobDetail> {
  return request(`${sessionPath(sessionId)}/tool-jobs/${encodeURIComponent(jobId)}`);
}

export function listArtifacts(sessionId: string): Promise<ToolArtifactRecord[]> {
  return request(`${sessionPath(sessionId)}/artifacts`);
}

export function getArtifactContent(sessionId: string, artifactId: string): Promise<string> {
  return requestText(`${sessionPath(sessionId)}/artifacts/${encodeURIComponent(artifactId)}/content`);
}

export function listVerifications(sessionId: string): Promise<SessionVerificationResponse> {
  return request(`${sessionPath(sessionId)}/verifications`);
}

export function connectEvents(
  sessionId: string,
  onEvent: (event: ExecutionEvent) => void,
  lastEventId = "",
  onMalformed?: (error: string) => void,
): EventSource {
  const query = new URLSearchParams();
  if (lastEventId) query.set("last_event_id", lastEventId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const source = new EventSource(`${sessionPath(sessionId)}/events${suffix}`);
  source.onmessage = (message) => {
    let payload: unknown;
    try {
      payload = JSON.parse(message.data);
    } catch {
      console.warn("[SSE] invalid JSON event frame", { sessionId, eventId: message.lastEventId });
      onMalformed?.("事件流收到无法解析的消息，已跳过该帧。");
      return;
    }
    const event = payload as Partial<ExecutionEvent> | null;
    if (
      !event ||
      typeof event !== "object" ||
      Array.isArray(event) ||
      typeof event.type !== "string" ||
      !event.type ||
      event.session_id !== sessionId ||
      typeof event.timestamp !== "string" ||
      !event.payload ||
      typeof event.payload !== "object" ||
      Array.isArray(event.payload) ||
      (event.id != null && typeof event.id !== "string")
    ) {
      console.warn("[SSE] invalid event schema or session", { sessionId, eventId: message.lastEventId });
      onMalformed?.("事件流收到无效消息，已跳过该帧。");
      return;
    }
    if (!event.id && message.lastEventId) {
      event.id = message.lastEventId;
    }
    onEvent(event as ExecutionEvent);
  };
  return source;
}
