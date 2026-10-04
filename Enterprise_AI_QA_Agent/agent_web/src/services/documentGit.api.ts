import type {
  ApiDocVersionResult, GitDocumentImportRequest, GitDocumentResolution, GitDocumentResolveRequest,
  GitDocumentSettings, GitDocumentSettingsRecord,
} from "../types";
import { request } from "./http";

function projectPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

export function getDocumentGitSettings(projectId: string): Promise<GitDocumentSettingsRecord> {
  return request(`${projectPath(projectId)}/document-git-settings`);
}

export function saveDocumentGitSettings(projectId: string, payload: GitDocumentSettings): Promise<GitDocumentSettingsRecord> {
  return request(`${projectPath(projectId)}/document-git-settings`, { method: "PUT", body: JSON.stringify(payload) });
}

export function resolveDocumentGitSource(projectId: string, payload: GitDocumentResolveRequest): Promise<GitDocumentResolution> {
  return request(`${projectPath(projectId)}/document-git/resolve`, { method: "POST", body: JSON.stringify(payload) });
}

export function importDocumentGitSource(projectId: string, payload: GitDocumentImportRequest): Promise<ApiDocVersionResult> {
  return request(`${projectPath(projectId)}/document-git/import`, { method: "POST", body: JSON.stringify(payload) });
}

export function getDocumentGitSubmission(projectId: string, requestId: string, documentId?: string): Promise<ApiDocVersionResult> {
  const query = new URLSearchParams({ request_id: requestId });
  if (documentId) query.set("document_id", documentId);
  return request(`${projectPath(projectId)}/document-git/submissions?${query}`);
}
