import type {
  AgentDescriptor,
  ConversationResponse,
  EmailConfigPublic,
  EmailConfigUpdateRequest,
  ExecutionEvent,
  HealthResponse,
  ModelConfigActionResponse,
  ModelConfigConnectionTestResponse,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
  SessionDetail,
  SessionReplayResponse,
  ToolArtifactRecord,
  ToolApprovalRequest,
  ToolDescriptor,
  ToolJobDetail,
  ToolJobRecord,
  SessionVerificationResponse,
} from "../types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  getHealth(): Promise<HealthResponse> {
    return request("/api/v1/health");
  },
  listAgents(): Promise<AgentDescriptor[]> {
    return request("/api/v1/registry/agents");
  },
  listTools(): Promise<ToolDescriptor[]> {
    return request("/api/v1/registry/tools");
  },
  listModelConfigs(): Promise<ModelConfigPublic[]> {
    return request("/api/v1/settings/models");
  },
  updateModelConfig(payload: ModelConfigUpdateRequest): Promise<ModelConfigPublic> {
    return request("/api/v1/settings/models", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  editModelConfig(modelName: string, payload: ModelConfigUpdateRequest): Promise<ModelConfigPublic> {
    return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  activateModelConfig(modelName: string): Promise<ModelConfigActionResponse> {
    return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}/activate`, {
      method: "POST",
    });
  },
  testModelConfigConnection(modelName: string): Promise<ModelConfigConnectionTestResponse> {
    return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}/test-connection`, {
      method: "POST",
    });
  },
  deleteModelConfig(modelName: string): Promise<ModelConfigActionResponse> {
    return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}`, {
      method: "DELETE",
    });
  },
  listEmailConfigs(): Promise<EmailConfigPublic[]> {
    return request("/api/v1/settings/email");
  },
  updateEmailConfig(payload: EmailConfigUpdateRequest): Promise<EmailConfigPublic> {
    return request("/api/v1/settings/email", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  createSession(
    title = "Enterprise Intelligent QA Session",
    selectedAgent?: string,
  ): Promise<SessionDetail> {
    return request("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({
        title,
        selected_agent: selectedAgent ?? null,
      }),
    });
  },
  getSession(sessionId: string): Promise<SessionDetail> {
    return request(`/api/v1/sessions/${sessionId}`);
  },
  listApprovals(sessionId: string): Promise<ToolApprovalRequest[]> {
    return request(`/api/v1/sessions/${sessionId}/approvals`);
  },
  resolveApproval(
    sessionId: string,
    approvalId: string,
    decision: "approved" | "denied",
    reason?: string,
  ): Promise<ToolApprovalRequest> {
    return request(`/api/v1/sessions/${sessionId}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        reason: reason || null,
      }),
    });
  },
  sendMessage(
    sessionId: string,
    content: string,
    agentKey?: string,
  ): Promise<ConversationResponse> {
    return request(`/api/v1/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        content,
        agent_key: agentKey || null,
      }),
    });
  },
  interruptSession(sessionId: string, reason?: string): Promise<SessionDetail> {
    return request(`/api/v1/sessions/${sessionId}/interrupt`, {
      method: "POST",
      body: JSON.stringify({
        reason: reason || null,
        source: "web_console",
      }),
    });
  },
  resumeSession(sessionId: string, reason?: string): Promise<ConversationResponse> {
    return request(`/api/v1/sessions/${sessionId}/resume`, {
      method: "POST",
      body: JSON.stringify({
        reason: reason || null,
        source: "web_console",
      }),
    });
  },
  replaySession(sessionId: string): Promise<SessionReplayResponse> {
    return request(`/api/v1/sessions/${sessionId}/replay`);
  },
  listToolJobs(sessionId: string): Promise<ToolJobRecord[]> {
    return request(`/api/v1/sessions/${sessionId}/tool-jobs`);
  },
  getToolJobDetail(sessionId: string, jobId: string): Promise<ToolJobDetail> {
    return request(`/api/v1/sessions/${sessionId}/tool-jobs/${jobId}`);
  },
  listArtifacts(sessionId: string): Promise<ToolArtifactRecord[]> {
    return request(`/api/v1/sessions/${sessionId}/artifacts`);
  },
  listVerifications(sessionId: string): Promise<SessionVerificationResponse> {
    return request(`/api/v1/sessions/${sessionId}/verifications`);
  },
  connectEvents(sessionId: string, onEvent: (event: ExecutionEvent) => void): EventSource {
    const source = new EventSource(`/api/v1/sessions/${sessionId}/events`);
    source.onmessage = (message) => {
      const payload = JSON.parse(message.data) as ExecutionEvent;
      onEvent(payload);
    };
    return source;
  },
};
