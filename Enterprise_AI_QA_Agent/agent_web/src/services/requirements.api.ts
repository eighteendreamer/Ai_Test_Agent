import { request } from "./http";
import type {
  RequirementBundle, RequirementMatrix, RequirementPage, RequirementReview,
  RequirementReviewRequest, RequirementVersion,
  RequirementCoverageRequest, RequirementCoverageReview,
  RequirementExecutionRequest, RequirementExecutionReview, RequirementExecutionCandidate,
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
export function readRequirementMatrix(projectId: string, documentId: string, requirementId: string, versionId?: string, coverageId?: string, executionId?: string): Promise<RequirementMatrix> {
  const params = new URLSearchParams();
  if (versionId) params.set("version_id", versionId);
  if (coverageId) params.set("coverage_id", coverageId);
  if (executionId) params.set("execution_id", executionId);
  const query = params.size ? `?${params}` : "";
  return request(`${documentPath(projectId, documentId)}/requirements/${encodeURIComponent(requirementId)}/matrix${query}`);
}
const coveragePath = (projectId: string, documentId: string, requirementId: string, versionId: string) =>
  `${documentPath(projectId, documentId)}/requirements/${encodeURIComponent(requirementId)}/versions/${encodeURIComponent(versionId)}/coverage-reviews`;
export function submitRequirementCoverage(projectId: string, documentId: string, requirementId: string, versionId: string, payload: RequirementCoverageRequest): Promise<RequirementCoverageReview> {
  return request(coveragePath(projectId, documentId, requirementId, versionId), { method: "POST", body: JSON.stringify(payload) });
}
export function listRequirementCoverage(projectId: string, documentId: string, requirementId: string, versionId: string, limit = 20, offset = 0): Promise<RequirementPage<RequirementCoverageReview>> {
  return request(`${coveragePath(projectId, documentId, requirementId, versionId)}?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function getRequirementCoverageSubmission(projectId: string, documentId: string, requestId: string): Promise<RequirementCoverageReview> {
  return request(`${documentPath(projectId, documentId)}/requirement-coverage-reviews/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
const executionPath = (projectId: string, documentId: string, requirementId: string, versionId: string) =>
  `${documentPath(projectId, documentId)}/requirements/${encodeURIComponent(requirementId)}/versions/${encodeURIComponent(versionId)}`;
export function submitRequirementExecution(projectId: string, documentId: string, requirementId: string, versionId: string, payload: RequirementExecutionRequest): Promise<RequirementExecutionReview> {
  return request(`${executionPath(projectId, documentId, requirementId, versionId)}/execution-reviews`, { method: "POST", body: JSON.stringify(payload) });
}
export function getRequirementExecutionSubmission(projectId: string, documentId: string, requestId: string): Promise<RequirementExecutionReview> {
  return request(`${documentPath(projectId, documentId)}/requirement-execution-reviews/by-request?${new URLSearchParams({ request_id: requestId })}`);
}
export function listRequirementExecution(projectId: string, documentId: string, requirementId: string, versionId: string, limit = 20, offset = 0): Promise<RequirementPage<RequirementExecutionReview>> {
  return request(`${executionPath(projectId, documentId, requirementId, versionId)}/execution-reviews?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`);
}
export function listRequirementExecutionCandidates(projectId: string, documentId: string, requirementId: string, versionId: string, coverageId: string, limit = 20, offset = 0): Promise<RequirementPage<RequirementExecutionCandidate>> {
  return request(`${executionPath(projectId, documentId, requirementId, versionId)}/execution-candidates?${new URLSearchParams({ coverage_id: coverageId, limit: String(limit), offset: String(offset) })}`);
}
