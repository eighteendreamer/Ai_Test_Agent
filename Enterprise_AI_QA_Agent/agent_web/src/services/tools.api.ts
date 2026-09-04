import type {
  AgentDescriptor,
  ToolDescriptor,
  ToolSearchResponse,
  ModeDescriptor,
  SecurityProfilesResponse,
  SkillDescriptor,
  SkillBulkInstallResponse,
  SkillInstallRequest,
  SkillMarketplaceInstallRequest,
  SkillMarketplaceSearchResponse,
  SkillUploadRequest,
  SkillUpsertRequest,
  ApiDocRecord,
  ApiDocImportIntegrationRequest,
  ApiDocImportUrlRequest,
  ApiDocUploadRequest,
  ApiDocUpdateRequest,
  UploadedAttachmentRecord,
  IntegrationRecord,
  IntegrationCreateRequest,
  IntegrationImportSourcesResponse,
  IntegrationTestResponse,
  IntegrationUpdateRequest,
} from "../types";
import { request } from "./http";

export function listAgents(): Promise<AgentDescriptor[]> {
  return request("/api/v1/registry/agents");
}

export function listTools(): Promise<ToolDescriptor[]> {
  return request("/api/v1/registry/tools/summary");
}

export function getTool(toolKey: string): Promise<ToolDescriptor> {
  return request(`/api/v1/registry/tools/${encodeURIComponent(toolKey)}`);
}

export function searchTools(q: string, limit = 10, includeSchema = false): Promise<ToolSearchResponse> {
  const params = new URLSearchParams({
    q,
    limit: String(limit),
    include_schema: includeSchema ? "true" : "false",
  });
  return request(`/api/v1/registry/tools/search?${params.toString()}`);
}

export function listModes(): Promise<ModeDescriptor[]> {
  return request("/api/v1/registry/modes");
}

export function listSecurityProfiles(): Promise<SecurityProfilesResponse> {
  return request("/api/v1/registry/security-profiles");
}

export function listSkills(): Promise<SkillDescriptor[]> {
  return request("/api/v1/registry/skills");
}

export function getSkill(skillKey: string): Promise<SkillDescriptor> {
  return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`);
}

export function upsertSkill(skillKey: string, payload: SkillUpsertRequest): Promise<SkillDescriptor> {
  return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function installSkill(payload: SkillInstallRequest): Promise<SkillDescriptor> {
  return request("/api/v1/registry/skills/install", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function uploadSkill(payload: SkillUploadRequest): Promise<SkillDescriptor | SkillBulkInstallResponse> {
  return request("/api/v1/registry/skills/upload", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function searchSkillMarketplace(
  source: string,
  q: string,
  limit = 20,
): Promise<SkillMarketplaceSearchResponse> {
  const params = new URLSearchParams({ source, q, limit: String(limit) });
  return request(`/api/v1/registry/skills/marketplaces/search?${params.toString()}`);
}

export function installMarketplaceSkill(
  payload: SkillMarketplaceInstallRequest,
): Promise<SkillDescriptor | SkillBulkInstallResponse> {
  return request("/api/v1/registry/skills/marketplaces/install", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteSkill(skillKey: string): Promise<{ ok: boolean; message: string }> {
  return request(`/api/v1/registry/skills/${encodeURIComponent(skillKey)}`, { method: "DELETE" });
}

export function listApiDocs(
  filters: { projectId?: string; unbound?: boolean } = {},
): Promise<ApiDocRecord[]> {
  const params = new URLSearchParams();
  if (filters.projectId) params.set("project_id", filters.projectId);
  if (filters.unbound) params.set("unbound", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request(`/api/v1/registry/api-docs${suffix}`);
}

export function getApiDoc(docId: string): Promise<ApiDocRecord> {
  return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`);
}

export function uploadApiDoc(payload: ApiDocUploadRequest): Promise<ApiDocRecord> {
  return request("/api/v1/registry/api-docs/upload", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function importApiDocFromUrl(payload: ApiDocImportUrlRequest): Promise<ApiDocRecord> {
  return request("/api/v1/registry/api-docs/import-url", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function importApiDocFromIntegration(payload: ApiDocImportIntegrationRequest): Promise<ApiDocRecord> {
  return request("/api/v1/registry/api-docs/import-integration", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApiDoc(docId: string, payload: ApiDocUpdateRequest): Promise<ApiDocRecord> {
  return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function uploadAttachment(payload: {
  filename: string;
  content_base64: string;
  source?: string;
}): Promise<UploadedAttachmentRecord> {
  return request("/api/v1/registry/attachments/upload", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteApiDoc(docId: string): Promise<{ ok: boolean; deleted_id: string }> {
  return request(`/api/v1/registry/api-docs/${encodeURIComponent(docId)}`, { method: "DELETE" });
}

export function listIntegrations(): Promise<IntegrationRecord[]> {
  return request("/api/v1/registry/integrations");
}

export function listIntegrationImportSources(
  integrationId: string,
  workspaceId?: string | null,
): Promise<IntegrationImportSourcesResponse> {
  const params = new URLSearchParams();
  if (workspaceId) params.set("workspace_id", workspaceId);
  const query = params.toString();
  const path = `/api/v1/registry/integrations/${encodeURIComponent(integrationId)}/import-sources`;
  return request(query ? `${path}?${query}` : path);
}

export function getIntegration(integrationId: string): Promise<IntegrationRecord> {
  return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`);
}

export function createIntegration(payload: IntegrationCreateRequest): Promise<IntegrationRecord> {
  return request("/api/v1/registry/integrations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateIntegration(
  integrationId: string,
  payload: IntegrationUpdateRequest,
): Promise<IntegrationRecord> {
  return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteIntegration(integrationId: string): Promise<{ ok: boolean; deleted_id: string }> {
  return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}`, { method: "DELETE" });
}

export function testIntegration(integrationId: string): Promise<IntegrationTestResponse> {
  return request(`/api/v1/registry/integrations/${encodeURIComponent(integrationId)}/test`, { method: "POST" });
}
