import { request } from "./http";
import type { BusinessApplicabilityRequest, BusinessApplicabilityReview, BusinessApplicabilityPage } from "./businessApplicability.types";

const reviews = (projectId: string, environmentId: string, snapshotId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/business-environments/${encodeURIComponent(environmentId)}/snapshots/${encodeURIComponent(snapshotId)}/applicability-reviews`;

export function submitBusinessApplicability(projectId: string, environmentId: string, snapshotId: string,
  payload: BusinessApplicabilityRequest): Promise<BusinessApplicabilityReview> {
  return request(reviews(projectId, environmentId, snapshotId), { method: "POST", body: JSON.stringify(payload) });
}
export function getBusinessApplicabilityByRequest(projectId: string, environmentId: string, snapshotId: string,
  requestId: string): Promise<BusinessApplicabilityReview> {
  return request(`${reviews(projectId, environmentId, snapshotId)}/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
export function getBusinessApplicability(projectId: string, environmentId: string, snapshotId: string,
  reviewId: string): Promise<BusinessApplicabilityReview> {
  return request(`${reviews(projectId, environmentId, snapshotId)}/${encodeURIComponent(reviewId)}`);
}
export function listBusinessApplicability(projectId: string, environmentId: string, snapshotId: string,
  versionId: string, limit = 20, offset = 0): Promise<BusinessApplicabilityPage> {
  return request(`${reviews(projectId, environmentId, snapshotId)}?${new URLSearchParams({
    requirement_version_id: versionId, limit: String(limit), offset: String(offset),
  })}`);
}
