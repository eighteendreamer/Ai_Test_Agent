import type {
  AgentDescriptor,
  ApiDocRecord,
  ApiDocImportIntegrationRequest,
  ApiDocImportUrlRequest,
  ApiDocUploadRequest,
  ApiDocUpdateRequest,
  ChannelAdvancedSettings,
  ChannelGatewayDecision,
  ChannelGatewaySessionReleaseRequest,
  ChannelGatewaySessionReleaseResponse,
  ChannelInboundMessage,
  ChannelConfigActionResponse,
  ChannelConfigCreateRequest,
  ChannelConfigPublic,
  ChannelConfigUpdateRequest,
  ChannelDomain,
  ChannelPairingApproveRequest,
  ChannelPairingRequestPublic,
  ChannelPairingSessionPublic,
  ChannelPairingStartRequest,
  ConversationResponse,
  DockerContainerActionRequest,
  DockerContainerActionResponse,
  DockerContainerCreateRequest,
  DockerContainerCreateResponse,
  DockerContainerLogsResponse,
  DockerContainerRemoveRequest,
  DockerImagePullRequest,
  DockerImagePullResponse,
  DockerImageRemoveRequest,
  DockerOverviewResponse,
  DockerTemplateCreateRequest,
  EmailConfigPublic,
  EmailConfigCreateRequest,
  EmailConfigActionResponse,
  EmailConfigUpdateRequest,
  ExecutionEvent,
  HealthResponse,
  ModeDescriptor,
  MailboxProviderInfo,
  MailboxSendConfirmRequest,
  ModelConfigActionResponse,
  ModelConfigConnectionTestResponse,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
  ReportListPage,
  SessionDetail,
  InputAttachment,
  IntegrationCreateRequest,
  IntegrationImportSourcesResponse,
  IntegrationRecord,
  IntegrationTestResponse,
  ManagedMCPToolCallRequest,
  ManagedMCPToolCallResponse,
  ManagedMCPPromptsResponse,
  ManagedMCPResourcesResponse,
  ManagedMCPTestResponse,
  ManagedMCPToolsResponse,
  ManagedMCPServerDescriptor,
  MCPServerCreateRequest,
  MCPServerImportRequest,
  MCPServerImportResponse,
  MCPServerUpdateRequest,
  MCPProviderDescriptor,
  IntegrationUpdateRequest,
  MCPServerDescriptor,
  UploadedAttachmentRecord,
  SessionSummary,
  SessionSummaryPage,
  SessionReplayResponse,
  TaskPoolPage,
  ToolArtifactRecord,
  ToolApprovalRequest,
  ToolDescriptor,
  ToolSearchResponse,
  ToolJobDetail,
  ToolJobRecord,
  KnowledgeGraphResponse,
  KnowledgeProjectDeleteResponse,
  KnowledgeProjectSummary,
  SkillDescriptor,
  SkillBulkInstallResponse,
  SkillInstallRequest,
  SkillMarketplaceInstallRequest,
  SkillMarketplaceSearchResponse,
  SkillUploadRequest,
  SkillUpsertRequest,
  SecurityProfilesResponse,
  SessionVerificationResponse,
  ProjectRecord,
  ProjectPage,
  ProjectOverview,
  TestCaseGenerationResponse,
  TestCaseLifecycleStatus,
  TestCasePage,
  TestCasePriority,
  TestCaseRecord,
  TestCaseVersionRecord,
  TestSuiteBundle,
  TestSuitePage,
  TestRunDetail,
  TestRunPage,
  TestRunStatus,
} from "../types";
import type {
  CompatibilityArtifactRecord,
  CompatibilityExecutionReport,
  CompatibilityQueuedTask,
  CompatibilityRunnerCleanupResponse,
  CompatibilityRunnerRecord,
  CompatibilityRunnerTaskSummary,
  CompatibilityTestRunnerOutput,
  CompatibilityTaskRequeueResponse,
} from "../types/compatibility-testing";

async function readErrorMessage(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json() as Record<string, unknown>;
      const detail = payload.detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail.trim();
      }
      const message = payload.message;
      if (typeof message === "string" && message.trim()) {
        return message.trim();
      }
      return JSON.stringify(payload);
    } catch {
      return `Request failed: ${response.status}`;
    }
  }

  try {
    const detail = await response.text();
    return detail || `Request failed: ${response.status}`;
  } catch {
    return `Request failed: ${response.status}`;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const detail = await readErrorMessage(response);
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  listProjects(params: { status?: string; query?: string; limit?: number; offset?: number } = {}): Promise<ProjectPage> {
    const query = new URLSearchParams({
      limit: String(params.limit ?? 100),
      offset: String(params.offset ?? 0),
    });
    if (params.status) query.set("status", params.status);
    if (params.query) query.set("query", params.query);
    return request(`/api/v1/projects?${query.toString()}`);
  },
  createProject(payload: {
    project_key: string;
    name: string;
    description?: string | null;
    base_url?: string | null;
    graph_scope_key?: string | null;
  }): Promise<ProjectRecord> {
    return request("/api/v1/projects", { method: "POST", body: JSON.stringify(payload) });
  },
  updateProject(projectId: string, payload: Partial<Pick<ProjectRecord, "name" | "description" | "base_url" | "graph_scope_key">>): Promise<ProjectRecord> {
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  archiveProject(projectId: string): Promise<ProjectRecord> {
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/archive`, { method: "POST" });
  },
  getProjectOverview(projectId: string): Promise<ProjectOverview> {
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/overview`);
  },
  listTestCases(projectId: string, params: {
    status?: TestCaseLifecycleStatus;
    mode_key?: string;
    priority?: TestCasePriority;
    query?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<TestCasePage> {
    const query = new URLSearchParams({
      limit: String(params.limit ?? 50),
      offset: String(params.offset ?? 0),
    });
    if (params.status) query.set("status", params.status);
    if (params.mode_key) query.set("mode_key", params.mode_key);
    if (params.priority) query.set("priority", params.priority);
    if (params.query) query.set("query", params.query);
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-cases?${query.toString()}`);
  },
  generateTestCases(projectId: string, payload: {
    objective: string;
    mode_key: string;
    model_key?: string | null;
    api_doc_ids?: string[];
    include_knowledge_graph?: boolean;
    include_history?: boolean;
    max_cases?: number;
  }): Promise<TestCaseGenerationResponse> {
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-cases/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  submitTestCaseReview(caseId: string): Promise<TestCaseRecord> {
    return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/submit-review`, { method: "POST" });
  },
  activateTestCase(caseId: string, versionId?: string | null): Promise<TestCaseRecord> {
    return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/activate`, {
      method: "POST",
      body: JSON.stringify({ version_id: versionId ?? null }),
    });
  },
  listTestCaseVersions(caseId: string): Promise<TestCaseVersionRecord[]> {
    return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/versions`);
  },
  listTestSuites(projectId: string, params: { limit?: number; offset?: number } = {}): Promise<TestSuitePage> {
    const query = new URLSearchParams({
      limit: String(params.limit ?? 50),
      offset: String(params.offset ?? 0),
    });
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/suites?${query.toString()}`);
  },
  createTestSuite(projectId: string, payload: {
    name: string;
    description?: string | null;
    items: Array<{ case_id: string; case_version_id: string }>;
  }): Promise<TestSuiteBundle> {
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/suites`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createTestRun(suiteId: string, payload: {
    mode_key: string;
    session_id?: string | null;
  }): Promise<TestRunDetail> {
    return request(`/api/v1/suites/${encodeURIComponent(suiteId)}/runs`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listTestRuns(projectId: string, params: {
    status?: TestRunStatus;
    limit?: number;
    offset?: number;
  } = {}): Promise<TestRunPage> {
    const query = new URLSearchParams({
      limit: String(params.limit ?? 50),
      offset: String(params.offset ?? 0),
    });
    if (params.status) query.set("status", params.status);
    return request(`/api/v1/projects/${encodeURIComponent(projectId)}/runs?${query.toString()}`);
  },
  cancelTestRun(runId: string, reason = "Cancelled by operator"): Promise<TestRunDetail> {
    return request(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  },
  getHealth(): Promise<HealthResponse> {
    return request("/api/v1/health");
  },
  listKnowledgeProjects(): Promise<KnowledgeProjectSummary[]> {
    return request("/api/v1/knowledge/projects");
  },
  getKnowledgeGraph(projectScope: string): Promise<KnowledgeGraphResponse> {
    const params = new URLSearchParams({ project_scope: projectScope });
    return request(`/api/v1/knowledge/graph?${params.toString()}`);
  },
  deleteKnowledgeProject(projectScope: string): Promise<KnowledgeProjectDeleteResponse> {
    const params = new URLSearchParams({ project_scope: projectScope });
    return request(`/api/v1/knowledge/project?${params.toString()}`, {
      method: "DELETE",
    });
  },
  listAgents(): Promise<AgentDescriptor[]> {
    return request("/api/v1/registry/agents");
  },
  listTools(): Promise<ToolDescriptor[]> {
    return request("/api/v1/registry/tools/summary");
  },
  getTool(toolKey: string): Promise<ToolDescriptor> {
    return request(`/api/v1/registry/tools/${encodeURIComponent(toolKey)}`);
  },
  searchTools(q: string, limit = 10, includeSchema = false): Promise<ToolSearchResponse> {
    const params = new URLSearchParams({
      q,
      limit: String(limit),
      include_schema: includeSchema ? "true" : "false",
    });
    return request(`/api/v1/registry/tools/search?${params.toString()}`);
  },
  listModes(): Promise<ModeDescriptor[]> {
    return request("/api/v1/registry/modes");
  },
  listSecurityProfiles(): Promise<SecurityProfilesResponse> {
    return request("/api/v1/registry/security-profiles");
  },
  listSkills(): Promise<SkillDescriptor[]> {
    return request("/api/v1/registry/skills");
  },
  getSkill(skillKey: string): Promise<SkillDescriptor> {
    return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`);
  },
  upsertSkill(skillKey: string, payload: SkillUpsertRequest): Promise<SkillDescriptor> {
    return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  installSkill(payload: SkillInstallRequest): Promise<SkillDescriptor> {
    return request("/api/v1/registry/skills/install", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  uploadSkill(payload: SkillUploadRequest): Promise<SkillDescriptor | SkillBulkInstallResponse> {
    return request("/api/v1/registry/skills/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  searchSkillMarketplace(source: string, q: string, limit = 20): Promise<SkillMarketplaceSearchResponse> {
    const params = new URLSearchParams({ source, q, limit: String(limit) });
    return request(`/api/v1/registry/skills/marketplaces/search?${params.toString()}`);
  },
  installMarketplaceSkill(payload: SkillMarketplaceInstallRequest): Promise<SkillDescriptor | SkillBulkInstallResponse> {
    return request("/api/v1/registry/skills/marketplaces/install", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  deleteSkill(skillKey: string): Promise<{ ok: boolean; message: string }> {
    return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`, {
      method: "DELETE",
    });
  },
  listModelConfigs(signal?: AbortSignal): Promise<ModelConfigPublic[]> {
    return request("/api/v1/settings/models", { signal });
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
  listOAuthProviders(signal?: AbortSignal): Promise<{ providers: import("../types").OAuthProviderProfile[] }> {
    return request("/api/v1/oauth/providers", { signal });
  },
  startOAuthFlow(payload: import("../types").OAuthStartRequest): Promise<import("../types").OAuthStartResponse> {
    return request("/api/v1/oauth/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getOAuthStatus(state: string): Promise<import("../types").OAuthStatusResponse> {
    return request(`/api/v1/oauth/status/${encodeURIComponent(state)}`);
  },
  listOAuthModels(
    provider: string,
    state?: string | null,
    base_url?: string | null,
  ): Promise<import("../types").OAuthModelsResponse> {
    const params = new URLSearchParams({ provider });
    if (state) params.set("state", state);
    if (base_url) params.set("base_url", base_url);
    return request(`/api/v1/oauth/models?${params.toString()}`);
  },
  listEmailConfigs(signal?: AbortSignal): Promise<EmailConfigPublic[]> {
    return request("/api/v1/settings/email", { signal });
  },
  createEmailConfig(payload: EmailConfigCreateRequest): Promise<EmailConfigPublic> {
    return request("/api/v1/settings/email", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateEmailConfig(configId: number, payload: EmailConfigUpdateRequest): Promise<EmailConfigPublic> {
    return request(`/api/v1/settings/email/${configId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  activateEmailConfig(configId: number): Promise<EmailConfigActionResponse> {
    return request(`/api/v1/settings/email/${configId}/activate`, {
      method: "POST",
    });
  },
  deleteEmailConfig(configId: number): Promise<EmailConfigActionResponse> {
    return request(`/api/v1/settings/email/${configId}`, {
      method: "DELETE",
    });
  },
  listChannelConfigs(): Promise<ChannelConfigPublic[]> {
    return request("/api/v1/settings/channels");
  },
  getChannelAdvancedSettings(): Promise<ChannelAdvancedSettings> {
    return request("/api/v1/settings/channels/advanced");
  },
  updateChannelAdvancedSettings(payload: ChannelAdvancedSettings): Promise<ChannelAdvancedSettings> {
    return request("/api/v1/settings/channels/advanced", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  evaluateChannelInbound(payload: ChannelInboundMessage): Promise<ChannelGatewayDecision> {
    return request("/api/v1/settings/channels/gateway/evaluate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listChannelPairingRequests(): Promise<ChannelPairingRequestPublic[]> {
    return request("/api/v1/settings/channels/gateway/pairing");
  },
  approveChannelPairing(payload: ChannelPairingApproveRequest): Promise<ChannelPairingRequestPublic> {
    return request("/api/v1/settings/channels/gateway/pairing/approve", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  releaseChannelGatewaySession(payload: ChannelGatewaySessionReleaseRequest): Promise<ChannelGatewaySessionReleaseResponse> {
    return request("/api/v1/settings/channels/gateway/session/release", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createChannelConfig(payload: ChannelConfigCreateRequest): Promise<ChannelConfigPublic> {
    return request("/api/v1/settings/channels", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateChannelConfig(configId: number, payload: ChannelConfigUpdateRequest): Promise<ChannelConfigPublic> {
    return request(`/api/v1/settings/channels/${configId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteChannelConfig(configId: number): Promise<ChannelConfigActionResponse> {
    return request(`/api/v1/settings/channels/${configId}`, {
      method: "DELETE",
    });
  },
  startChannelPairing(domain: ChannelDomain, payload: ChannelPairingStartRequest): Promise<ChannelPairingSessionPublic> {
    return request(`/api/v1/settings/channels/${encodeURIComponent(domain)}/pairing/start`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getChannelPairing(sessionId: string): Promise<ChannelPairingSessionPublic> {
    return request(`/api/v1/settings/channels/pairing/${encodeURIComponent(sessionId)}`);
  },
  getActiveChannelPairing(domain: ChannelDomain): Promise<ChannelPairingSessionPublic | null> {
    return request(`/api/v1/settings/channels/${encodeURIComponent(domain)}/pairing/active`);
  },

  // --- Agent Mailbox API ---------------------------------------------------

  listMailProviders(signal?: AbortSignal): Promise<{ providers: MailboxProviderInfo[] }> {
    return request("/api/v1/mail/providers", { signal });
  },
  mailProviderStatus(provider: string): Promise<Record<string, unknown>> {
    return request(`/api/v1/mail/providers/${provider}/status`, {
      method: "POST",
    });
  },
  mailProviderSetupAction(
    provider: string,
    action: string,
    payload: Record<string, unknown> = {},
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return request(`/api/v1/mail/providers/${provider}/setup-action`, {
      method: "POST",
      body: JSON.stringify({ action, payload }),
      signal,
    });
  },
  mailTestSendPrepare(payload: { recipients: string[]; subject: string; content?: string; content_html?: string; config_id?: number | null }): Promise<Record<string, unknown>> {
    return request("/api/v1/mail/test-send/prepare", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  mailTestSendConfirm(payload: MailboxSendConfirmRequest): Promise<Record<string, unknown>> {
    return request("/api/v1/mail/test-send/confirm", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createSession(
    title = "Enterprise Intelligent QA Session",
    modeKey = "default",
  ): Promise<SessionDetail> {
    return request("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({
        title,
        mode_key: modeKey,
      }),
    });
  },
  listSessions(): Promise<SessionSummary[]> {
    return request("/api/v1/sessions");
  },
  listSessionsPage(limit = 10, offset = 0, modeKey?: string): Promise<SessionSummaryPage> {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (modeKey) {
      params.set("mode_key", modeKey);
    }
    return request(`/api/v1/sessions?${params.toString()}`);
  },
  listReportsPage(limit = 10, offset = 0): Promise<ReportListPage> {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    return request(`/api/v1/reports?${params.toString()}`);
  },
  listTaskPoolPage(limit = 24, offset = 0): Promise<TaskPoolPage> {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    return request(`/api/v1/task-pool?${params.toString()}`);
  },
  getSession(sessionId: string): Promise<SessionDetail> {
    return request(`/api/v1/sessions/${sessionId}`);
  },
  updateSession(
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
  },
  listApiDocs(filters: { projectId?: string; unbound?: boolean } = {}): Promise<ApiDocRecord[]> {
    const params = new URLSearchParams();
    if (filters.projectId) params.set("project_id", filters.projectId);
    if (filters.unbound) params.set("unbound", "true");
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request(`/api/v1/registry/api-docs${suffix}`);
  },
  getApiDoc(docId: string): Promise<ApiDocRecord> {
    return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`);
  },
  uploadApiDoc(payload: ApiDocUploadRequest): Promise<ApiDocRecord> {
    return request("/api/v1/registry/api-docs/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  importApiDocFromUrl(payload: ApiDocImportUrlRequest): Promise<ApiDocRecord> {
    return request("/api/v1/registry/api-docs/import-url", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  importApiDocFromIntegration(payload: ApiDocImportIntegrationRequest): Promise<ApiDocRecord> {
    return request("/api/v1/registry/api-docs/import-integration", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateApiDoc(docId: string, payload: ApiDocUpdateRequest): Promise<ApiDocRecord> {
    return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  uploadAttachment(payload: {
    filename: string;
    content_base64: string;
    source?: string;
  }): Promise<UploadedAttachmentRecord> {
    return request("/api/v1/registry/attachments/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  deleteApiDoc(docId: string): Promise<{ ok: boolean; deleted_id: string }> {
    return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`, {
      method: "DELETE",
    });
  },
  listIntegrations(): Promise<IntegrationRecord[]> {
    return request("/api/v1/registry/integrations");
  },
  listIntegrationImportSources(
    integrationId: string,
    workspaceId?: string | null,
  ): Promise<IntegrationImportSourcesResponse> {
    const params = new URLSearchParams();
    if (workspaceId) {
      params.set("workspace_id", workspaceId);
    }
    const query = params.toString();
    const path = `/api/v1/registry/integrations/${encodeURIComponent(integrationId)}/import-sources`;
    return request(query ? `${path}?${query}` : path);
  },
  getIntegration(integrationId: string): Promise<IntegrationRecord> {
    return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`);
  },
  createIntegration(payload: IntegrationCreateRequest): Promise<IntegrationRecord> {
    return request("/api/v1/registry/integrations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateIntegration(integrationId: string, payload: IntegrationUpdateRequest): Promise<IntegrationRecord> {
    return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteIntegration(integrationId: string): Promise<{ ok: boolean; deleted_id: string }> {
    return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`, {
      method: "DELETE",
    });
  },
  testIntegration(integrationId: string): Promise<IntegrationTestResponse> {
    return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}/test`, {
      method: "POST",
    });
  },
  listMcpServers(): Promise<MCPServerDescriptor[]> {
    return request("/api/v1/registry/mcp");
  },
  listManagedMcpServers(): Promise<ManagedMCPServerDescriptor[]> {
    return request("/api/v1/registry/mcp/managed");
  },
  createManagedMcpServer(payload: MCPServerCreateRequest): Promise<ManagedMCPServerDescriptor> {
    return request("/api/v1/registry/mcp/managed", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  importManagedMcpServers(payload: MCPServerImportRequest): Promise<MCPServerImportResponse> {
    return request("/api/v1/registry/mcp/managed/import", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateManagedMcpServer(serverKey: string, payload: MCPServerUpdateRequest): Promise<ManagedMCPServerDescriptor> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteManagedMcpServer(serverKey: string): Promise<{ ok: boolean; deleted_id: string }> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}`, {
      method: "DELETE",
    });
  },
  confirmManagedMcpStdio(serverKey: string): Promise<ManagedMCPServerDescriptor> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/confirm-stdio`, {
      method: "POST",
    });
  },
  reconnectManagedMcpServer(serverKey: string): Promise<ManagedMCPServerDescriptor> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/reconnect`, {
      method: "POST",
    });
  },
  listMcpProviders(): Promise<MCPProviderDescriptor[]> {
    return request("/api/v1/registry/mcp/providers");
  },
  listManagedMcpTools(serverKey: string): Promise<ManagedMCPToolsResponse> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/tools`);
  },
  listManagedMcpResources(serverKey: string): Promise<ManagedMCPResourcesResponse> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/resources`);
  },
  listManagedMcpPrompts(serverKey: string): Promise<ManagedMCPPromptsResponse> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/prompts`);
  },
  testManagedMcpServer(serverKey: string): Promise<ManagedMCPTestResponse> {
    return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/test`, {
      method: "POST",
    });
  },
  callManagedMcpTool(serverKey: string, toolName: string, payload: ManagedMCPToolCallRequest): Promise<ManagedMCPToolCallResponse> {
    return request(
      `/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/tools/${encodeURIComponent(toolName)}/call`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
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
  listCompatibilityRunners(): Promise<CompatibilityRunnerRecord[]> {
    return request("/api/v1/compatibility/runners");
  },
  cleanupCompatibilityRunners(payload: {
    older_than_seconds?: number;
    runner_ids?: string[];
  } = {}): Promise<CompatibilityRunnerCleanupResponse> {
    return request("/api/v1/compatibility/runners/cleanup", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  draftCompatibilityPlan(payload: Record<string, unknown>): Promise<CompatibilityTestRunnerOutput> {
    return request("/api/v1/compatibility/plans/draft", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dispatchCompatibilityPlan(payload: Record<string, unknown>): Promise<CompatibilityTestRunnerOutput> {
    return request("/api/v1/compatibility/plans/dispatch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  registerCompatibilityRunner(payload: {
    runner_id: string;
    name?: string;
    os?: string;
    capabilities?: string[];
    devices?: string[];
    max_parallel?: number;
    metadata?: Record<string, unknown>;
  }): Promise<CompatibilityRunnerRecord> {
    return request("/api/v1/compatibility/runners/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  heartbeatCompatibilityRunner(
    runnerId: string,
    payload: {
      status?: string;
      active_task_ids?: string[];
      metadata?: Record<string, unknown>;
    },
  ): Promise<CompatibilityRunnerRecord> {
    return request(`/api/v1/compatibility/runners/${runnerId}/heartbeat`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  pollCompatibilityRunnerTasks(runnerId: string, limit = 1): Promise<{ runner_id: string; tasks: CompatibilityQueuedTask[] }> {
    return request(`/api/v1/compatibility/runners/${runnerId}/tasks/poll?limit=${encodeURIComponent(String(limit))}`, {
      method: "POST",
    });
  },
  reportCompatibilityRunnerTask(
    runnerId: string,
    taskId: string,
    payload: {
      status: string;
      result?: Record<string, unknown>;
      artifacts?: Record<string, unknown>[];
      error?: string | null;
      metadata?: Record<string, unknown>;
    },
  ): Promise<CompatibilityQueuedTask> {
    return request(`/api/v1/compatibility/runners/${runnerId}/tasks/${taskId}/report`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  uploadCompatibilityArtifact(
    runnerId: string,
    taskId: string,
    payload: {
      filename: string;
      content_base64: string;
      type?: string;
      label?: string;
      mime_type?: string | null;
      metadata?: Record<string, unknown>;
    },
  ): Promise<CompatibilityArtifactRecord> {
    return request(`/api/v1/compatibility/runners/${runnerId}/tasks/${taskId}/artifacts/upload`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listCompatibilityRunnerTasks(params: { dispatch_id?: string; runner_id?: string } = {}): Promise<CompatibilityQueuedTask[]> {
    const query = new URLSearchParams();
    if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
    if (params.runner_id) query.set("runner_id", params.runner_id);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request(`/api/v1/compatibility/tasks${suffix}`);
  },
  requeueCompatibilityTasks(payload: {
    task_ids?: string[];
    dispatch_id?: string | null;
    runner_id?: string | null;
    statuses?: string[];
    reason?: string;
  }): Promise<CompatibilityTaskRequeueResponse> {
    return request("/api/v1/compatibility/tasks/requeue", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listCompatibilityArtifacts(params: { task_id?: string; dispatch_id?: string; runner_id?: string; artifact_type?: string } = {}): Promise<CompatibilityArtifactRecord[]> {
    const query = new URLSearchParams();
    if (params.task_id) query.set("task_id", params.task_id);
    if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
    if (params.runner_id) query.set("runner_id", params.runner_id);
    if (params.artifact_type) query.set("artifact_type", params.artifact_type);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request(`/api/v1/compatibility/artifacts${suffix}`);
  },
  getCompatibilitySummary(params: { dispatch_id?: string; runner_id?: string } = {}): Promise<CompatibilityRunnerTaskSummary> {
    const query = new URLSearchParams();
    if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
    if (params.runner_id) query.set("runner_id", params.runner_id);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request(`/api/v1/compatibility/summary${suffix}`);
  },
  getCompatibilityReport(params: { dispatch_id?: string; runner_id?: string } = {}): Promise<CompatibilityExecutionReport> {
    const query = new URLSearchParams();
    if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
    if (params.runner_id) query.set("runner_id", params.runner_id);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request(`/api/v1/compatibility/report${suffix}`);
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
  connectEvents(sessionId: string, onEvent: (event: ExecutionEvent) => void, lastEventId = ""): EventSource {
    const query = new URLSearchParams();
    if (lastEventId) {
      query.set("last_event_id", lastEventId);
    }
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
  },

  // General Settings
  getGeneralSettings(): Promise<Record<string, unknown>> {
    return request("/api/v1/settings/general");
  },
  saveGeneralSettings(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return request("/api/v1/settings/general", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  // Data Management
  exportPreview(): Promise<{ ok: boolean; session_count: number }> {
    return request("/api/v1/settings/data/export/preview", { method: "POST" });
  },
  exportStart(): Promise<{ ok: boolean; task_id: string; total: number }> {
    return request("/api/v1/settings/data/export/start", { method: "POST" });
  },
  exportProgress(taskId: string): Promise<{ progress: number; total: number; status: string; error?: string }> {
    return request(`/api/v1/settings/data/export/progress/${taskId}`);
  },
  async exportDownload(taskId: string): Promise<void> {
    const a = document.createElement("a");
    a.href = `/api/v1/settings/data/export/download/${taskId}`;
    a.download = `qa-agent-backup-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  async importData(file: File): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/api/v1/settings/data/import", { method: "POST", body: form });
    if (!resp.ok) throw new Error(await readErrorMessage(resp));
    return resp.json();
  },
  cleanupData(payload: {
    action: string;
    dry_run: boolean;
    time_range_days?: number | null;
    confirm?: boolean;
  }): Promise<Record<string, unknown>> {
    return request("/api/v1/settings/data/cleanup", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  // --- Docker Management API ------------------------------------------------

  dockerOverview(): Promise<DockerOverviewResponse> {
    return request("/api/v1/docker/overview");
  },
  dockerPullImage(payload: DockerImagePullRequest): Promise<DockerImagePullResponse> {
    return request("/api/v1/docker/images/pull", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerRemoveImage(payload: DockerImageRemoveRequest): Promise<{ ok: boolean; action: string; image: string }> {
    return request("/api/v1/docker/images/remove", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerCreateContainer(payload: DockerContainerCreateRequest): Promise<DockerContainerCreateResponse> {
    return request("/api/v1/docker/containers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerCreateTemplateContainer(
    templateKey: string,
    payload: DockerTemplateCreateRequest,
  ): Promise<DockerContainerCreateResponse> {
    return request(`/api/v1/docker/templates/${encodeURIComponent(templateKey)}/create`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerContainerAction(
    containerId: string,
    payload: DockerContainerActionRequest,
  ): Promise<DockerContainerActionResponse> {
    return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/action`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerRemoveContainer(
    containerId: string,
    payload: DockerContainerRemoveRequest,
  ): Promise<{ ok: boolean; action: string; container_id: string }> {
    return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/remove`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  dockerContainerLogs(containerId: string, tail = 200): Promise<DockerContainerLogsResponse> {
    const params = new URLSearchParams({ tail: String(tail) });
    return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/logs?${params.toString()}`);
  },
};
