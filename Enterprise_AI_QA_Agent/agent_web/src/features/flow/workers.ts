import type { WorkerDispatchRecord } from "../../types";
import { type FlowNodeStatus } from "./stages";

export const WORKER_NODE_PREFIX = "worker:";

function workerIdentity(
  worker: WorkerDispatchRecord,
  index = 0,
  fallbackParentTurnId = "",
): [string, string, string] {
  const parentTurnId = String(worker.parent_turn_id || "").trim() || fallbackParentTurnId.trim();
  const taskId = String(worker.task_id || "").trim();
  if (taskId) {
    return [parentTurnId, "task", taskId];
  }
  const childId = String(worker.child_session_id || "").trim();
  if (childId) {
    return [parentTurnId, "child", childId];
  }
  return [parentTurnId, "anonymous", String(index)];
}

export function workerDispatchKey(worker: WorkerDispatchRecord, index = 0): string {
  return JSON.stringify(workerIdentity(worker, index));
}

export function workerNodeId(worker: WorkerDispatchRecord, index = 0): string {
  const [parentTurnId, kind, identity] = workerIdentity(worker, index);
  if (!parentTurnId) {
    if (kind === "anonymous") {
      return `${WORKER_NODE_PREFIX}anonymous-${identity}`;
    }
    return `${WORKER_NODE_PREFIX}${encodeURIComponent(identity)}`;
  }
  return `${WORKER_NODE_PREFIX}turn:${encodeURIComponent(parentTurnId)}:${kind}:${encodeURIComponent(identity)}`;
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

function mergeWorkers(groups: WorkerDispatchRecord[][], fallbackParentTurnId = ""): WorkerDispatchRecord[] {
  const ordered: WorkerDispatchRecord[] = [];
  const indexByKey = new Map<string, number>();
  for (const group of groups) {
    group.forEach((worker, index) => {
      const key = JSON.stringify(workerIdentity(worker, index, fallbackParentTurnId));
      const existingIndex = indexByKey.get(key);
      if (existingIndex === undefined) {
        indexByKey.set(key, ordered.length);
        ordered.push(worker);
        return;
      }
      ordered[existingIndex] = { ...ordered[existingIndex], ...worker };
    });
  }
  return ordered;
}

export function collectWorkerDispatches(
  metadata: Record<string, unknown> | null,
  graphState: Record<string, unknown> | null,
  turnId: string,
): WorkerDispatchRecord[] {
  const groups = [
    asRecordArray(metadata?.worker_dispatches),
    asRecordArray(graphState?.worker_dispatches),
  ];
  if (!turnId) {
    return mergeWorkers(groups);
  }
  const turnGroups = groups.map((group) => group.filter((worker) => {
    const parentTurnId = String(worker.parent_turn_id || "").trim();
    return !parentTurnId || parentTurnId === turnId;
  }));
  return mergeWorkers(turnGroups, turnId);
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
