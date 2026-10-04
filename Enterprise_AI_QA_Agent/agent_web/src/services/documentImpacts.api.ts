import type {
  DocumentImpactAnalysisPolicy, DocumentImpactBatchPolicy, DocumentImpactInvocationPolicy,
  DocumentImpactPage, DocumentImpactPolicy, DocumentImpactSettings, DocumentImpactSnapshot,
  DocumentImpactSnapshotView, DocumentImpactTaskRecord, DocumentImpactTaskRequest,
} from "./documentImpacts.types";
import { request } from "./http";

const projectPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`;
const documentPath = (projectId: string, documentId: string) => `${projectPath(projectId)}/documents/${encodeURIComponent(documentId)}`;

export function getDocumentImpactSettings(projectId: string): Promise<DocumentImpactSettings<DocumentImpactPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-settings`);
}
export function saveDocumentImpactSettings(projectId: string, payload: DocumentImpactPolicy): Promise<DocumentImpactSettings<DocumentImpactPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-settings`, { method: "PUT", body: JSON.stringify(payload) });
}
export function getDocumentImpactAnalysisSettings(projectId: string): Promise<DocumentImpactSettings<DocumentImpactAnalysisPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-analysis-settings`);
}
export function saveDocumentImpactAnalysisSettings(projectId: string, payload: DocumentImpactAnalysisPolicy): Promise<DocumentImpactSettings<DocumentImpactAnalysisPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-analysis-settings`, { method: "PUT", body: JSON.stringify(payload) });
}
export function getDocumentImpactBatchSettings(projectId: string): Promise<DocumentImpactSettings<DocumentImpactBatchPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-batch-settings`);
}
export function saveDocumentImpactBatchSettings(projectId: string, payload: DocumentImpactBatchPolicy): Promise<DocumentImpactSettings<DocumentImpactBatchPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-batch-settings`, { method: "PUT", body: JSON.stringify(payload) });
}
export function getDocumentImpactInvocationSettings(projectId: string): Promise<DocumentImpactSettings<DocumentImpactInvocationPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-invocation-settings`);
}
export function saveDocumentImpactInvocationSettings(projectId: string, payload: DocumentImpactInvocationPolicy): Promise<DocumentImpactSettings<DocumentImpactInvocationPolicy>> {
  return request(`${projectPath(projectId)}/document-impact-invocation-settings`, { method: "PUT", body: JSON.stringify(payload) });
}
export function submitDocumentImpactTask(projectId: string, documentId: string, payload: DocumentImpactTaskRequest): Promise<DocumentImpactTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/impact-tasks`, { method: "POST", body: JSON.stringify(payload) });
}
export function getDocumentImpactTask(projectId: string, documentId: string, taskId: string): Promise<DocumentImpactTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/impact-tasks/${encodeURIComponent(taskId)}`);
}
export function getDocumentImpactSubmission(projectId: string, documentId: string, creationKey: string): Promise<DocumentImpactTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/impact-tasks/by-request?${new URLSearchParams({ creation_key: creationKey })}`);
}
export function readDocumentImpactResult(projectId: string, documentId: string, taskId: string): Promise<DocumentImpactSnapshotView> {
  return request(`${documentPath(projectId, documentId)}/impact-tasks/${encodeURIComponent(taskId)}/result`);
}
export function readDocumentImpactSnapshot(projectId: string, documentId: string, snapshotId: string): Promise<DocumentImpactSnapshotView> {
  return request(`${documentPath(projectId, documentId)}/impact-snapshots/${encodeURIComponent(snapshotId)}`);
}
function catalogQuery(versionId?: string, offset = 0): string {
  const query = new URLSearchParams({ limit: "20", offset: String(offset) });
  if (versionId) query.set("version_id", versionId);
  return query.toString();
}
export function listDocumentImpactTasks(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentImpactPage<DocumentImpactTaskRecord>> {
  return request(`${documentPath(projectId, documentId)}/impact-tasks?${catalogQuery(versionId, offset)}`);
}
export function listDocumentImpactSnapshots(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentImpactPage<DocumentImpactSnapshot>> {
  return request(`${documentPath(projectId, documentId)}/impact-snapshots?${catalogQuery(versionId, offset)}`);
}
