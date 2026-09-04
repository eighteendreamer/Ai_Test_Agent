import type {
  HealthResponse,
  SponsorRecord,
  KnowledgeGraphResponse,
  KnowledgeProjectSummary,
  KnowledgeProjectDeleteResponse,
} from "../types";
import { request } from "./http";

export function getHealth(): Promise<HealthResponse> {
  return request("/api/v1/health");
}

export function listSponsors(): Promise<SponsorRecord[]> {
  return request("/api/v1/sponsors");
}

export function listKnowledgeProjects(): Promise<KnowledgeProjectSummary[]> {
  return request("/api/v1/knowledge/projects");
}

export function getKnowledgeGraph(projectId: string): Promise<KnowledgeGraphResponse> {
  const params = new URLSearchParams({ project_id: projectId });
  return request(`/api/v1/knowledge/graph?${params.toString()}`);
}

export function deleteKnowledgeProject(projectId: string): Promise<KnowledgeProjectDeleteResponse> {
  const params = new URLSearchParams({ project_id: projectId });
  return request(`/api/v1/knowledge/project?${params.toString()}`, { method: "DELETE" });
}
