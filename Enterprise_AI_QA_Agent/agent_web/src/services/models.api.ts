import type {
  ModelConfigPublic,
  ModelConfigUpdateRequest,
  ModelConfigActionResponse,
  ModelConfigConnectionTestResponse,
} from "../types";
import type { OAuthProviderProfile, OAuthStartRequest, OAuthStartResponse, OAuthStatusResponse, OAuthModelsResponse } from "../types";
import { request } from "./http";

export function listModelConfigs(signal?: AbortSignal): Promise<ModelConfigPublic[]> {
  return request("/api/v1/settings/models", { signal });
}

export function updateModelConfig(payload: ModelConfigUpdateRequest): Promise<ModelConfigPublic> {
  return request("/api/v1/settings/models", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function editModelConfig(modelName: string, payload: ModelConfigUpdateRequest): Promise<ModelConfigPublic> {
  return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function activateModelConfig(modelName: string): Promise<ModelConfigActionResponse> {
  return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}/activate`, { method: "POST" });
}

export function testModelConfigConnection(modelName: string): Promise<ModelConfigConnectionTestResponse> {
  return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}/test-connection`, { method: "POST" });
}

export function deleteModelConfig(modelName: string): Promise<ModelConfigActionResponse> {
  return request(`/api/v1/settings/models/${encodeURIComponent(modelName)}`, { method: "DELETE" });
}

export function listOAuthProviders(signal?: AbortSignal): Promise<{ providers: OAuthProviderProfile[] }> {
  return request("/api/v1/oauth/providers", { signal });
}

export function startOAuthFlow(payload: OAuthStartRequest): Promise<OAuthStartResponse> {
  return request("/api/v1/oauth/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getOAuthStatus(state: string): Promise<OAuthStatusResponse> {
  return request(`/api/v1/oauth/status/${encodeURIComponent(state)}`);
}

export function listOAuthModels(
  provider: string,
  state?: string | null,
  base_url?: string | null,
): Promise<OAuthModelsResponse> {
  const params = new URLSearchParams({ provider });
  if (state) params.set("state", state);
  if (base_url) params.set("base_url", base_url);
  return request(`/api/v1/oauth/models?${params.toString()}`);
}
