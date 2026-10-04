import type {
  DocumentDifferenceSettings, DocumentDifferenceSettingsRecord, DocumentDifferenceTaskRequest,
  DocumentDifferenceTaskRecord, DocumentDifferenceSnapshotView, DocumentDifferenceTaskPage,
  DocumentDifferenceSnapshotPage, DocumentDifferenceFailureView,
} from "../types";
import { request } from "./http";

function projectPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

function documentPath(projectId: string, documentId: string): string {
  return `${projectPath(projectId)}/documents/${encodeURIComponent(documentId)}`;
}

export function getDocumentDifferenceSettings(projectId: string): Promise<DocumentDifferenceSettingsRecord> {
  return request(`${projectPath(projectId)}/document-difference-settings`);
}

export function saveDocumentDifferenceSettings(projectId: string, payload: DocumentDifferenceSettings): Promise<DocumentDifferenceSettingsRecord> {
  return request(`${projectPath(projectId)}/document-difference-settings`, { method: "PUT", body: JSON.stringify(payload) });
}

export function submitDocumentDifferenceTask(projectId: string, documentId: string, payload: DocumentDifferenceTaskRequest): Promise<DocumentDifferenceTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/difference-tasks`, { method: "POST", body: JSON.stringify(payload) });
}

export function getDocumentDifferenceTask(projectId: string, documentId: string, taskId: string): Promise<DocumentDifferenceTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/difference-tasks/${encodeURIComponent(taskId)}`);
}

export function getDocumentDifferenceSubmission(projectId: string, documentId: string, creationKey: string): Promise<DocumentDifferenceTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/difference-tasks/by-request?${new URLSearchParams({ creation_key: creationKey })}`);
}

export function readDocumentDifferenceSnapshot(projectId: string, documentId: string, snapshotId: string): Promise<DocumentDifferenceSnapshotView> {
  return request(`${documentPath(projectId, documentId)}/difference-snapshots/${encodeURIComponent(snapshotId)}`);
}

function catalogQuery(versionId?: string, offset = 0): string {
  const query = new URLSearchParams({ limit: "20", offset: String(offset) });
  if (versionId) query.set("version_id", versionId);
  return query.toString();
}

export function listDocumentDifferenceTasks(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentDifferenceTaskPage> {
  return request(`${documentPath(projectId, documentId)}/difference-tasks?${catalogQuery(versionId, offset)}`);
}

export function listDocumentDifferenceSnapshots(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentDifferenceSnapshotPage> {
  return request(`${documentPath(projectId, documentId)}/difference-snapshots?${catalogQuery(versionId, offset)}`);
}

export function readDocumentDifferenceFailure(projectId: string, documentId: string, taskId: string): Promise<DocumentDifferenceFailureView> {
  return request(`${documentPath(projectId, documentId)}/difference-tasks/${encodeURIComponent(taskId)}/failure`);
}
