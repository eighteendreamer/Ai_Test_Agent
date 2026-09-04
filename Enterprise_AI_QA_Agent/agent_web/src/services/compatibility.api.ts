import type {
  CompatibilityArtifactRecord,
  CompatibilityExecutionReport,
  CompatibilityQueuedTask,
  CompatibilityRunnerCleanupResponse,
  CompatibilityRunnerRecord,
  CompatibilityRunnerTaskSummary,
  CompatibilityTestRunnerOutput,
  CompatibilityTaskRequeueResponse,
} from "../types/compatibility-testing";
import { request } from "./http";

export function listCompatibilityRunners(): Promise<CompatibilityRunnerRecord[]> {
  return request("/api/v1/compatibility/runners");
}

export function cleanupCompatibilityRunners(payload: {
  older_than_seconds?: number;
  runner_ids?: string[];
} = {}): Promise<CompatibilityRunnerCleanupResponse> {
  return request("/api/v1/compatibility/runners/cleanup", { method: "POST", body: JSON.stringify(payload) });
}

export function draftCompatibilityPlan(
  payload: Record<string, unknown>,
): Promise<CompatibilityTestRunnerOutput> {
  return request("/api/v1/compatibility/plans/draft", { method: "POST", body: JSON.stringify(payload) });
}

export function dispatchCompatibilityPlan(
  payload: Record<string, unknown>,
): Promise<CompatibilityTestRunnerOutput> {
  return request("/api/v1/compatibility/plans/dispatch", { method: "POST", body: JSON.stringify(payload) });
}

export function registerCompatibilityRunner(payload: {
  runner_id: string;
  name?: string;
  os?: string;
  capabilities?: string[];
  devices?: string[];
  max_parallel?: number;
  metadata?: Record<string, unknown>;
}): Promise<CompatibilityRunnerRecord> {
  return request("/api/v1/compatibility/runners/register", { method: "POST", body: JSON.stringify(payload) });
}

export function heartbeatCompatibilityRunner(
  runnerId: string,
  payload: {
    status?: string;
    active_task_ids?: string[];
    metadata?: Record<string, unknown>;
  },
): Promise<CompatibilityRunnerRecord> {
  return request(`/api/v1/compatibility/runners/${runnerId}/heartbeat`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function pollCompatibilityRunnerTasks(
  runnerId: string,
  limit = 1,
): Promise<{ runner_id: string; tasks: CompatibilityQueuedTask[] }> {
  return request(
    `/api/v1/compatibility/runners/${runnerId}/tasks/poll?limit=${encodeURIComponent(String(limit))}`,
    { method: "POST" },
  );
}

export function reportCompatibilityRunnerTask(
  runnerId: string,
  taskId: string,
  payload: {
    status: string;
    result?: Record<string, unknown>;
    artifacts?: Record<string, unknown>[];
    error?: string | null;
    metadata?: Record<string, unknown>;
  },
): Promise<CompatibilityQueuedTask> {
  return request(`/api/v1/compatibility/runners/${runnerId}/tasks/${taskId}/report`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function uploadCompatibilityArtifact(
  runnerId: string,
  taskId: string,
  payload: {
    filename: string;
    content_base64: string;
    type?: string;
    label?: string;
    mime_type?: string | null;
    metadata?: Record<string, unknown>;
  },
): Promise<CompatibilityArtifactRecord> {
  return request(`/api/v1/compatibility/runners/${runnerId}/tasks/${taskId}/artifacts/upload`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listCompatibilityRunnerTasks(
  params: { dispatch_id?: string; runner_id?: string } = {},
): Promise<CompatibilityQueuedTask[]> {
  const query = new URLSearchParams();
  if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
  if (params.runner_id) query.set("runner_id", params.runner_id);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/v1/compatibility/tasks${suffix}`);
}

export function requeueCompatibilityTasks(payload: {
  task_ids?: string[];
  dispatch_id?: string | null;
  runner_id?: string | null;
  statuses?: string[];
  reason?: string;
}): Promise<CompatibilityTaskRequeueResponse> {
  return request("/api/v1/compatibility/tasks/requeue", { method: "POST", body: JSON.stringify(payload) });
}

export function listCompatibilityArtifacts(
  params: { task_id?: string; dispatch_id?: string; runner_id?: string; artifact_type?: string } = {},
): Promise<CompatibilityArtifactRecord[]> {
  const query = new URLSearchParams();
  if (params.task_id) query.set("task_id", params.task_id);
  if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
  if (params.runner_id) query.set("runner_id", params.runner_id);
  if (params.artifact_type) query.set("artifact_type", params.artifact_type);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/v1/compatibility/artifacts${suffix}`);
}

export function getCompatibilitySummary(
  params: { dispatch_id?: string; runner_id?: string } = {},
): Promise<CompatibilityRunnerTaskSummary> {
  const query = new URLSearchParams();
  if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
  if (params.runner_id) query.set("runner_id", params.runner_id);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/v1/compatibility/summary${suffix}`);
}

export function getCompatibilityReport(
  params: { dispatch_id?: string; runner_id?: string } = {},
): Promise<CompatibilityExecutionReport> {
  const query = new URLSearchParams();
  if (params.dispatch_id) query.set("dispatch_id", params.dispatch_id);
  if (params.runner_id) query.set("runner_id", params.runner_id);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/v1/compatibility/report${suffix}`);
}
