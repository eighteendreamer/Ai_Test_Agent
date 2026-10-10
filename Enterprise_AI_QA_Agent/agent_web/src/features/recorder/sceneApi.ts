import { request } from "../../services/http";
import type { TestCaseRecord, TestCaseVersionRecord, TestCaseVersionCreateRequest } from "../../types";

export interface SceneStep { source_seq: number; action: string; expected: string | null; alias: string | null }
export interface SceneAlias { name: string; source_seq: number; description: string }
export interface SceneContent {
  name: string; description: string; status: "draft" | "organized" | "archived";
  steps: SceneStep[]; aliases: SceneAlias[]; assertions: TestCaseVersionRecord["assertions"];
  preconditions: string[]; execution_notes: string;
}
export interface SceneBundle {
  scene_id: string; project_id: string; current_version_id: string;
  version: { id: string; scene_id: string; project_id: string; version: number; parent_version_id: string | null;
    request_id: string; request_hash: string; content_hash: string; created_at: string; content: SceneContent;
    source: { recording_id: string; event_count: number; excluded_sequences: number[];
      original_draft: TestCaseVersionCreateRequest & {title: string; case_key: string}; finalize_metrics: Record<string, unknown>; change_reason: string } };
}
export interface SceneSummary { scene_id: string; name: string; status: string; current_version_id: string; version: number; step_count: number }
export interface SceneEdit { request_id: string; parent_version_id: string; change_reason: string; content: SceneContent }
const base = (projectId: string) => `/api/v1/recordings/scenes/projects/${encodeURIComponent(projectId)}`;
export const scenesApi = {
  list: (projectId: string, offset = 0) => request<{items: SceneSummary[]; has_more: boolean}>(`${base(projectId)}?offset=${offset}`),
  create: (projectId: string, payload: {request_id: string; recording_id: string}) => request<SceneBundle>(base(projectId), {method: "POST", body: JSON.stringify(payload)}),
  recoverCreation: (projectId: string, requestId: string) => request<SceneBundle | null>(`${base(projectId)}/by-request/${encodeURIComponent(requestId)}`),
  get: (projectId: string, sceneId: string, versionId?: string) => request<SceneBundle>(`${base(projectId)}/${encodeURIComponent(sceneId)}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`),
  versions: (projectId: string, sceneId: string) => request<Array<{id: string; version: number}>>(`${base(projectId)}/${encodeURIComponent(sceneId)}/versions`),
  save: (projectId: string, sceneId: string, payload: SceneEdit) => request<SceneBundle>(`${base(projectId)}/${encodeURIComponent(sceneId)}/versions`, {method: "POST", body: JSON.stringify(payload)}),
  recoverVersion: (projectId: string, sceneId: string, requestId: string) => request<SceneBundle | null>(`${base(projectId)}/${encodeURIComponent(sceneId)}/versions/by-request/${encodeURIComponent(requestId)}`),
  draft: (projectId: string, sceneId: string, versionId: string, read = false) => request<{case: TestCaseRecord; version: TestCaseVersionRecord} | null>(`${base(projectId)}/${encodeURIComponent(sceneId)}/versions/${encodeURIComponent(versionId)}/case-draft`, {method: read ? "GET" : "POST"}),
};
