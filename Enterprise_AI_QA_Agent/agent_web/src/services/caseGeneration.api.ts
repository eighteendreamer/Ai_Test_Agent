import { request } from "./http";
import type {
  GenerationGroup, GenerationGroupPage, GenerationGroupView, GenerationResponse,
  GenerationScope, GenerationTask, GenerationTaskPolicy, GenerationTaskRequest, GenerationTaskSettings,
} from "./caseGeneration.types";

const projectPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`;
const documentPath = (projectId: string, documentId: string) =>
  `${projectPath(projectId)}/documents/${encodeURIComponent(documentId)}`;

export function getGenerationSettings(projectId: string): Promise<GenerationTaskSettings> {
  return request(`${projectPath(projectId)}/case-generation-settings`);
}
export function saveGenerationSettings(projectId: string, payload: GenerationTaskPolicy): Promise<GenerationTaskSettings> {
  return request(`${projectPath(projectId)}/case-generation-settings`, { method: "PUT", body: JSON.stringify(payload) });
}
export function submitCaseGeneration(projectId: string, documentId: string, payload: GenerationTaskRequest): Promise<GenerationGroup> {
  return request(`${documentPath(projectId, documentId)}/case-generations`, { method: "POST", body: JSON.stringify(payload) });
}
export function getCaseGenerationSubmission(projectId: string, documentId: string, creationKey: string): Promise<GenerationGroup> {
  return request(`${documentPath(projectId, documentId)}/case-generations/by-request?${new URLSearchParams({ creation_key: creationKey })}`);
}
export function listCaseGenerations(projectId: string, documentId: string, limit = 20, offset = 0): Promise<GenerationGroupPage> {
  return request(`${documentPath(projectId, documentId)}/case-generations?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function readCaseGeneration(projectId: string, documentId: string, groupId: string): Promise<GenerationGroupView> {
  return request(`${documentPath(projectId, documentId)}/case-generations/${encodeURIComponent(groupId)}`);
}
export function readCaseGenerationScope(projectId: string, documentId: string, groupId: string): Promise<GenerationScope> {
  return request(`${documentPath(projectId, documentId)}/case-generations/${encodeURIComponent(groupId)}/scope`);
}
export function getCaseGenerationTask(projectId: string, documentId: string, taskId: string): Promise<GenerationTask> {
  return request(`${documentPath(projectId, documentId)}/case-generation-tasks/${encodeURIComponent(taskId)}`);
}
export function readCaseGenerationResponse(projectId: string, documentId: string, taskId: string): Promise<GenerationResponse> {
  return request(`${documentPath(projectId, documentId)}/case-generation-tasks/${encodeURIComponent(taskId)}/response`);
}
