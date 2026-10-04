import type {
  DocumentParsingSettings, DocumentParsingSettingsRecord, DocumentParseTaskRequest,
  DocumentParseTaskRecord, DocumentParseSnapshotView, DocumentParseTaskPage,
  DocumentParseSnapshotPage, DocumentParseFailureView, DocumentParserCapability,
} from "../types";
import { request } from "./http";

function projectPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

function documentPath(projectId: string, documentId: string): string {
  return `${projectPath(projectId)}/documents/${encodeURIComponent(documentId)}`;
}

export function getDocumentParsingSettings(projectId: string): Promise<DocumentParsingSettingsRecord> {
  return request(`${projectPath(projectId)}/document-parsing-settings`);
}

export function getDocumentParserCapabilities(projectId: string): Promise<DocumentParserCapability[]> {
  return request(`${projectPath(projectId)}/document-parsers`);
}

export function saveDocumentParsingSettings(projectId: string, payload: DocumentParsingSettings): Promise<DocumentParsingSettingsRecord> {
  return request(`${projectPath(projectId)}/document-parsing-settings`, { method: "PUT", body: JSON.stringify(payload) });
}

export function submitDocumentParseTask(projectId: string, documentId: string, payload: DocumentParseTaskRequest): Promise<DocumentParseTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/parse-tasks`, { method: "POST", body: JSON.stringify(payload) });
}

export function getDocumentParseTask(projectId: string, documentId: string, taskId: string): Promise<DocumentParseTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/parse-tasks/${encodeURIComponent(taskId)}`);
}

export function getDocumentParseSubmission(projectId: string, documentId: string, creationKey: string): Promise<DocumentParseTaskRecord> {
  return request(`${documentPath(projectId, documentId)}/parse-tasks/by-request?${new URLSearchParams({ creation_key: creationKey })}`);
}

export function readDocumentParseSnapshot(projectId: string, documentId: string, snapshotId: string): Promise<DocumentParseSnapshotView> {
  return request(`${documentPath(projectId, documentId)}/parse-snapshots/${encodeURIComponent(snapshotId)}`);
}

function catalogQuery(versionId?: string, offset = 0): string {
  const query = new URLSearchParams({ limit: "20", offset: String(offset) });
  if (versionId) query.set("version_id", versionId);
  return query.toString();
}

export function listDocumentParseTasks(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentParseTaskPage> {
  return request(`${documentPath(projectId, documentId)}/parse-tasks?${catalogQuery(versionId, offset)}`);
}

export function listDocumentParseSnapshots(projectId: string, documentId: string, versionId?: string, offset = 0): Promise<DocumentParseSnapshotPage> {
  return request(`${documentPath(projectId, documentId)}/parse-snapshots?${catalogQuery(versionId, offset)}`);
}

export function readDocumentParseFailure(projectId: string, documentId: string, taskId: string): Promise<DocumentParseFailureView> {
  return request(`${documentPath(projectId, documentId)}/parse-tasks/${encodeURIComponent(taskId)}/failure`);
}
