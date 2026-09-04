import type {
  MCPServerDescriptor,
  ManagedMCPServerDescriptor,
  MCPServerCreateRequest,
  MCPServerImportRequest,
  MCPServerImportResponse,
  MCPServerUpdateRequest,
  MCPProviderDescriptor,
  ManagedMCPToolsResponse,
  ManagedMCPResourcesResponse,
  ManagedMCPPromptsResponse,
  ManagedMCPTestResponse,
  ManagedMCPToolCallRequest,
  ManagedMCPToolCallResponse,
} from "../types";
import { request } from "./http";

export function listMcpServers(): Promise<MCPServerDescriptor[]> {
  return request("/api/v1/registry/mcp");
}

export function listManagedMcpServers(): Promise<ManagedMCPServerDescriptor[]> {
  return request("/api/v1/registry/mcp/managed");
}

export function createManagedMcpServer(payload: MCPServerCreateRequest): Promise<ManagedMCPServerDescriptor> {
  return request("/api/v1/registry/mcp/managed", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function importManagedMcpServers(payload: MCPServerImportRequest): Promise<MCPServerImportResponse> {
  return request("/api/v1/registry/mcp/managed/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateManagedMcpServer(
  serverKey: string,
  payload: MCPServerUpdateRequest,
): Promise<ManagedMCPServerDescriptor> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteManagedMcpServer(serverKey: string): Promise<{ ok: boolean; deleted_id: string }> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}`, { method: "DELETE" });
}

export function confirmManagedMcpStdio(serverKey: string): Promise<ManagedMCPServerDescriptor> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/confirm-stdio`, { method: "POST" });
}

export function reconnectManagedMcpServer(serverKey: string): Promise<ManagedMCPServerDescriptor> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/reconnect`, { method: "POST" });
}

export function listMcpProviders(): Promise<MCPProviderDescriptor[]> {
  return request("/api/v1/registry/mcp/providers");
}

export function listManagedMcpTools(serverKey: string): Promise<ManagedMCPToolsResponse> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/tools`);
}

export function listManagedMcpResources(serverKey: string): Promise<ManagedMCPResourcesResponse> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/resources`);
}

export function listManagedMcpPrompts(serverKey: string): Promise<ManagedMCPPromptsResponse> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/prompts`);
}

export function testManagedMcpServer(serverKey: string): Promise<ManagedMCPTestResponse> {
  return request(`/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/test`, { method: "POST" });
}

export function callManagedMcpTool(
  serverKey: string,
  toolName: string,
  payload: ManagedMCPToolCallRequest,
): Promise<ManagedMCPToolCallResponse> {
  return request(
    `/api/v1/registry/mcp/managed/${encodeURIComponent(serverKey)}/tools/${encodeURIComponent(toolName)}/call`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}
