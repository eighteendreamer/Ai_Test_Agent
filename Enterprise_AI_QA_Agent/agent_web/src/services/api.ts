import type {
  AgentDescriptor,
  ConversationResponse,
  EmailConfigPublic,
  EmailConfigUpdateRequest,
  ExecutionEvent,
  HealthResponse,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
  SessionDetail,
  ToolDescriptor,
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
  connectEvents(sessionId: string, onEvent: (event: ExecutionEvent) => void): EventSource {
    const source = new EventSource(`/api/v1/sessions/${sessionId}/events`);
    source.onmessage = (message) => {
      const payload = JSON.parse(message.data) as ExecutionEvent;
      onEvent(payload);
    };
    return source;
  },
};
