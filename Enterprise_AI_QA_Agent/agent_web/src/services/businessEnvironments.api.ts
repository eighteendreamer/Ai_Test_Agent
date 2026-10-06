import { request } from "./http";
import type {
  BusinessEnvironmentCreateRequest, BusinessEnvironmentRecord, BusinessEnvironmentPage,
  BusinessTargetConfiguration, BusinessTargetConfigurationRecord,
  BusinessEnvironmentCaptureRequest, BusinessEnvironmentSnapshot, BusinessEnvironmentSnapshotPage,
  BusinessToolObservationRequest, BusinessToolObservation,
} from "./businessEnvironments.types";

const environments = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/business-environments`;
const environment = (projectId: string, environmentId: string) => `${environments(projectId)}/${encodeURIComponent(environmentId)}`;

export function registerBusinessEnvironment(projectId: string, payload: BusinessEnvironmentCreateRequest): Promise<BusinessEnvironmentRecord> {
  return request(environments(projectId), { method: "POST", body: JSON.stringify(payload) });
}
export function getBusinessEnvironmentRegistration(projectId: string, requestId: string): Promise<BusinessEnvironmentRecord> {
  return request(`${environments(projectId)}/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
export function listBusinessEnvironments(projectId: string, params: { status?: "active" | "archived"; limit?: number; offset?: number } = {}): Promise<BusinessEnvironmentPage> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 20), offset: String(params.offset ?? 0) });
  if (params.status) query.set("status", params.status);
  return request(`${environments(projectId)}?${query}`);
}
export function getBusinessEnvironment(projectId: string, environmentId: string): Promise<BusinessEnvironmentRecord> {
  return request(environment(projectId, environmentId));
}
export function archiveBusinessEnvironment(projectId: string, environmentId: string): Promise<BusinessEnvironmentRecord> {
  return request(`${environment(projectId, environmentId)}/archive`, { method: "POST" });
}
export function readBusinessTargetConfiguration(projectId: string, environmentId: string): Promise<BusinessTargetConfigurationRecord> {
  return request(`${environment(projectId, environmentId)}/configuration`);
}
export function saveBusinessTargetConfiguration(projectId: string, environmentId: string, payload: BusinessTargetConfiguration): Promise<BusinessTargetConfigurationRecord> {
  return request(`${environment(projectId, environmentId)}/configuration`, { method: "PUT", body: JSON.stringify(payload) });
}
export function captureBusinessEnvironment(projectId: string, environmentId: string, payload: BusinessEnvironmentCaptureRequest): Promise<BusinessEnvironmentSnapshot> {
  return request(`${environment(projectId, environmentId)}/snapshots`, { method: "POST", body: JSON.stringify(payload) });
}
export function getBusinessEnvironmentCapture(projectId: string, environmentId: string, requestId: string): Promise<BusinessEnvironmentSnapshot> {
  return request(`${environment(projectId, environmentId)}/snapshots/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
export function getBusinessEnvironmentSnapshot(projectId: string, environmentId: string, snapshotId: string): Promise<BusinessEnvironmentSnapshot> {
  return request(`${environment(projectId, environmentId)}/snapshots/${encodeURIComponent(snapshotId)}`);
}
export function listBusinessEnvironmentSnapshots(projectId: string, environmentId: string, limit = 20, offset = 0): Promise<BusinessEnvironmentSnapshotPage> {
  return request(`${environment(projectId, environmentId)}/snapshots?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function inspectBusinessToolObservation(projectId: string, environmentId: string, snapshotId: string,
  payload: BusinessToolObservationRequest): Promise<BusinessToolObservation> {
  return request(`${environment(projectId, environmentId)}/snapshots/${encodeURIComponent(snapshotId)}/tool-observations/inspect`,
    { method: "POST", body: JSON.stringify(payload) });
}
