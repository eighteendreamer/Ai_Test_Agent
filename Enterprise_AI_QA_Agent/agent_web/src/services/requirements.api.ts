import { request } from "./http";
import type {
  RequirementBundle, RequirementMatrix, RequirementPage, RequirementReview,
  RequirementReviewRequest, RequirementVersion,
} from "./requirements.types";

const documentPath = (projectId: string, documentId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`;

export function submitRequirementReview(projectId: string, documentId: string, snapshotId: string, payload: RequirementReviewRequest): Promise<RequirementReview> {
  return request(`${documentPath(projectId, documentId)}/impact-snapshots/${encodeURIComponent(snapshotId)}/reviews`,
    { method: "POST", body: JSON.stringify(payload) });
}
export function getRequirementReviewSubmission(projectId: string, documentId: string, requestId: string): Promise<RequirementReview> {
  return request(`${documentPath(projectId, documentId)}/requirement-reviews/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
export function listRequirementReviews(projectId: string, documentId: string, snapshotId: string, limit = 20, offset = 0): Promise<RequirementPage<RequirementReview>> {
  return request(`${documentPath(projectId, documentId)}/impact-snapshots/${encodeURIComponent(snapshotId)}/reviews?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function listRequirements(projectId: string, documentId: string, limit = 20, offset = 0): Promise<RequirementPage<RequirementBundle>> {
  return request(`${documentPath(projectId, documentId)}/requirements?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function listRequirementVersions(projectId: string, documentId: string, requirementId: string): Promise<RequirementVersion[]> {
  return request(`${documentPath(projectId, documentId)}/requirements/${encodeURIComponent(requirementId)}/versions`);
}
export function readRequirementMatrix(projectId: string, documentId: string, requirementId: string, versionId?: string): Promise<RequirementMatrix> {
  const query = versionId ? `?${new URLSearchParams({ version_id: versionId })}` : "";
  return request(`${documentPath(projectId, documentId)}/requirements/${encodeURIComponent(requirementId)}/matrix${query}`);
}
