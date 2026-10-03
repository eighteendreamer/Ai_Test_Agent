import type {
  ProjectRecord,
  ProjectPage,
  ProjectOverview,
  ProjectExecutionSummary,
  LegacySmokeRunPage,
  LegacySmokeScopeBinding,
  TestCasePage,
  TestCaseLifecycleStatus,
  TestCasePriority,
  TestCaseGenerationResponse,
  TestCaseRecord,
  TestCaseVersionRecord,
  TestCaseVersionCreateRequest,
  GenerationTemplatePayload,
  GenerationTemplateRecord,
  CaseImportOptions,
  CaseImportSettingsRecord,
  CaseImportSheet,
  CaseImportSourceRecord,
  CaseImportBatchView,
  TestSuitePage,
  TestSuiteBundle,
  TestRunDetail,
  TestRunPage,
  TestRunStatus,
  TestCaseResultRecord,
  RegressionBatchPage,
  RegressionContext,
  RegressionFailurePage,
  RegressionFailureStatus,
  ResourceQuotaRecord,
  ResourceQuotaType,
  ResourceOccupancyResponse,
} from "../types";
import { request } from "./http";

export function listProjects(params: { status?: string; query?: string; limit?: number; offset?: number } = {}): Promise<ProjectPage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 100),
    offset: String(params.offset ?? 0),
  });
  if (params.status) query.set("status", params.status);
  if (params.query) query.set("query", params.query);
  return request(`/api/v1/projects?${query.toString()}`);
}

export function createProject(payload: {
  project_key: string;
  name: string;
  description?: string | null;
  base_url?: string | null;
  graph_scope_key?: string | null;
}): Promise<ProjectRecord> {
  return request("/api/v1/projects", { method: "POST", body: JSON.stringify(payload) });
}

export function updateProject(
  projectId: string,
  payload: Partial<Pick<ProjectRecord, "name" | "description" | "base_url" | "graph_scope_key">>,
): Promise<ProjectRecord> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function archiveProject(projectId: string): Promise<ProjectRecord> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/archive`, { method: "POST" });
}

export function getProjectOverview(projectId: string): Promise<ProjectOverview> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/overview`);
}

export function getProjectExecutionSummary(projectId: string, signal?: AbortSignal): Promise<ProjectExecutionSummary> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/execution-summary`, { signal });
}

export function listResourceQuotas(projectId: string, signal?: AbortSignal): Promise<ResourceQuotaRecord[]> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/resource-quotas`, { signal });
}

export function upsertResourceQuota(
  projectId: string,
  resourceType: ResourceQuotaType,
  limit: number,
): Promise<ResourceQuotaRecord> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/resource-quotas`, {
    method: "PUT",
    body: JSON.stringify({ resource_type: resourceType, limit }),
  });
}

export function getResourceOccupancy(projectId?: string, signal?: AbortSignal): Promise<ResourceOccupancyResponse> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return request(`/api/v1/resource-occupancy${query}`, { signal });
}

export function listLegacySmokeRuns(
  projectId: string,
  params: { cursor?: string; limit?: number } = {},
): Promise<LegacySmokeRunPage> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 50) });
  if (params.cursor) query.set("cursor", params.cursor);
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/legacy-smoke-runs?${query.toString()}`);
}

export function bindLegacySmokeScope(projectId: string, projectScope: string): Promise<LegacySmokeScopeBinding> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/legacy-smoke-bindings`, {
    method: "PUT",
    body: JSON.stringify({ project_scope: projectScope }),
  });
}

export function unbindLegacySmokeScope(projectId: string, projectScope: string): Promise<void> {
  return request(
    `/api/v1/projects/${encodeURIComponent(projectId)}/legacy-smoke-bindings/${encodeURIComponent(projectScope)}`,
    { method: "DELETE" },
  );
}

export function listTestCases(
  projectId: string,
  params: {
    status?: TestCaseLifecycleStatus;
    mode_key?: string;
    priority?: TestCasePriority;
    query?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<TestCasePage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.status) query.set("status", params.status);
  if (params.mode_key) query.set("mode_key", params.mode_key);
  if (params.priority) query.set("priority", params.priority);
  if (params.query) query.set("query", params.query);
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-cases?${query.toString()}`);
}

export function generateTestCases(
  projectId: string,
  payload: {
    objective: string;
    mode_key: string;
    model_key?: string | null;
    api_doc_ids?: string[];
    include_knowledge_graph?: boolean;
    include_history?: boolean;
    max_cases?: number;
  },
): Promise<TestCaseGenerationResponse> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-cases/generate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitTestCaseReview(caseId: string): Promise<TestCaseRecord> {
  return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/submit-review`, { method: "POST" });
}

export function activateTestCase(caseId: string, versionId?: string | null): Promise<TestCaseRecord> {
  return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/activate`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId ?? null }),
  });
}

export function listTestCaseVersions(caseId: string): Promise<TestCaseVersionRecord[]> {
  return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/versions`);
}

export function createTestCaseVersion(caseId: string, payload: TestCaseVersionCreateRequest): Promise<TestCaseVersionRecord> {
  return request(`/api/v1/test-cases/${encodeURIComponent(caseId)}/versions`, { method: "POST", body: JSON.stringify(payload) });
}

export function getTestCaseGenerationTemplate(projectId: string, useDefault = false): Promise<GenerationTemplateRecord> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-case-generation-template?use_default=${useDefault}`);
}

export function saveTestCaseGenerationTemplate(projectId: string, payload: GenerationTemplatePayload): Promise<GenerationTemplateRecord> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/test-case-generation-template`, { method: "PUT", body: JSON.stringify(payload) });
}

function caseImportPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/test-case-imports`;
}

export function getCaseImportSettings(projectId: string): Promise<CaseImportSettingsRecord> {
  return request(`${caseImportPath(projectId)}/settings`);
}

export function saveCaseImportSettings(projectId: string, payload: CaseImportSettingsRecord["payload"]): Promise<CaseImportSettingsRecord> {
  return request(`${caseImportPath(projectId)}/settings`, { method: "PUT", body: JSON.stringify(payload) });
}

export function uploadCaseImport(projectId: string, filename: string, contentBase64: string): Promise<CaseImportSourceRecord> {
  return request(`${caseImportPath(projectId)}/sources`, { method: "POST", body: JSON.stringify({ filename, content_base64: contentBase64 }) });
}

export function inspectCaseImportSheets(projectId: string, sourceId: string, headerRow: number): Promise<CaseImportSheet[]> {
  return request(`${caseImportPath(projectId)}/sources/${encodeURIComponent(sourceId)}/sheets?header_row=${headerRow}`);
}

export function previewCaseImport(projectId: string, sourceId: string, options: CaseImportOptions): Promise<CaseImportBatchView> {
  return request(`${caseImportPath(projectId)}/sources/${encodeURIComponent(sourceId)}/previews`, { method: "POST", body: JSON.stringify(options) });
}

export function getCaseImportBatch(projectId: string, batchId: string): Promise<CaseImportBatchView> {
  return request(`${caseImportPath(projectId)}/batches/${encodeURIComponent(batchId)}`);
}

export function commitCaseImport(projectId: string, batchId: string): Promise<CaseImportBatchView> {
  return request(`${caseImportPath(projectId)}/batches/${encodeURIComponent(batchId)}/commit`, { method: "POST" });
}

export function listTestSuites(
  projectId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<TestSuitePage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/suites?${query.toString()}`);
}

export function createTestSuite(
  projectId: string,
  payload: {
    name: string;
    description?: string | null;
    items: Array<{ case_id: string; case_version_id: string }>;
  },
): Promise<TestSuiteBundle> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/suites`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createTestRun(
  suiteId: string,
  payload: { mode_key: string; session_id?: string | null },
): Promise<TestRunDetail> {
  return request(`/api/v1/suites/${encodeURIComponent(suiteId)}/runs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function resolveTestRunItemApproval(
  itemId: string,
  approvalId: string,
  decision: "approved" | "denied",
  reason?: string,
): Promise<TestCaseResultRecord> {
  return request(`/api/v1/run-items/${encodeURIComponent(itemId)}/approval`, {
    method: "POST",
    body: JSON.stringify({ approval_id: approvalId, decision, reason: reason || null }),
  });
}

export function listTestRuns(
  projectId: string,
  params: { status?: TestRunStatus; limit?: number; offset?: number } = {},
): Promise<TestRunPage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.status) query.set("status", params.status);
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/runs?${query.toString()}`);
}

export function getTestRun(runId: string): Promise<TestRunDetail> {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export function cancelTestRun(runId: string, reason = "Cancelled by operator"): Promise<TestRunDetail> {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function reconcileCancelledRunResources(runId: string): Promise<TestRunDetail> {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/reconcile-resources`, { method: "POST" });
}

export function createRegressionTestRun(
  runId: string,
  payload: {
    result_ids?: string[];
    version_overrides?: Record<string, string>;
    session_id?: string | null;
  } = {},
): Promise<TestRunDetail> {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/regression`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listRegressionFailures(
  projectId: string,
  params: { failure_status?: RegressionFailureStatus; mode_key?: string; cursor?: string; limit?: number } = {},
): Promise<RegressionFailurePage> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 50) });
  if (params.failure_status) query.set("failure_status", params.failure_status);
  if (params.mode_key) query.set("mode_key", params.mode_key);
  if (params.cursor) query.set("cursor", params.cursor);
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/regression-failures?${query.toString()}`);
}

export function getRegressionContext(resultId: string): Promise<RegressionContext> {
  return request(`/api/v1/test-results/${encodeURIComponent(resultId)}/regression-context`);
}

export function listRegressionBatches(
  resultId: string,
  params: { cursor?: string; limit?: number } = {},
): Promise<RegressionBatchPage> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 50) });
  if (params.cursor) query.set("cursor", params.cursor);
  return request(`/api/v1/test-results/${encodeURIComponent(resultId)}/regression-batches?${query.toString()}`);
}
