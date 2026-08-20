import type { WorkerDispatchRecord } from "../../types";
import { type FlowNodeStatus } from "./stages";

export const WORKER_NODE_PREFIX = "worker:";

export function workerNodeId(worker: WorkerDispatchRecord, index = 0): string {
  const taskId = String(worker.task_id || "").trim();
  if (taskId) {
    return `${WORKER_NODE_PREFIX}${taskId}`;
  }
  const childId = String(worker.child_session_id || "").trim();
  if (childId) {
    return `${WORKER_NODE_PREFIX}${childId}`;
  }
  return `${WORKER_NODE_PREFIX}anonymous-${index}`;
}

export function isWorkerNodeId(value: string): boolean {
  return value.startsWith(WORKER_NODE_PREFIX);
}

function asRecordArray(value: unknown): WorkerDispatchRecord[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is WorkerDispatchRecord => Boolean(item) && typeof item === "object");
}

function mergeWorkers(groups: WorkerDispatchRecord[][]): WorkerDispatchRecord[] {
  const merged = new Map<string, WorkerDispatchRecord>();
  const ordered: WorkerDispatchRecord[] = [];
  for (const group of groups) {
    group.forEach((worker, index) => {
      const key = workerNodeId(worker, index);
      const current = merged.get(key);
      if (!current) {
        merged.set(key, worker);
        ordered.push(worker);
        return;
      }
      merged.set(key, { ...current, ...worker });
      const at = ordered.findIndex((item) => workerNodeId(item) === key);
      if (at >= 0) {
        ordered[at] = merged.get(key) as WorkerDispatchRecord;
      }
    });
  }
  return ordered;
}

export function collectWorkerDispatches(
  metadata: Record<string, unknown> | null,
  graphState: Record<string, unknown> | null,
  turnId: string,
): WorkerDispatchRecord[] {
  const merged = mergeWorkers([
    asRecordArray(metadata?.worker_dispatches),
    asRecordArray(graphState?.worker_dispatches),
  ]);
  if (!turnId) {
    return merged;
  }
  return merged.filter((worker) => {
    const parentTurnId = String(worker.parent_turn_id || "").trim();
    return !parentTurnId || parentTurnId === turnId;
  });
}

export function workerFlowStatus(status: string): FlowNodeStatus {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "running" || normalized === "in_progress") {
    return "running";
  }
  if (normalized === "waiting_approval" || normalized === "waiting") {
    return "waiting_approval";
  }
  if (normalized === "completed" || normalized === "success" || normalized === "done") {
    return "done";
  }
  if (normalized === "failed" || normalized === "error" || normalized === "denied" || normalized === "cancelled") {
    return "failed";
  }
  return "pending";
}

export function workerSourceStage(worker: WorkerDispatchRecord): string {
  const source = String(worker.source_stage || "").trim();
  return source || "tool_executor";
}

export function workerLabel(worker: WorkerDispatchRecord): string {
  return String(worker.description || worker.agent_key || worker.task_id || "").trim();
}
