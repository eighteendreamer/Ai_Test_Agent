import type { ExecutionEvent } from "../../types";

export const FLOW_STAGES = [
  "context_builder",
  "router",
  "planner",
  "permission_gate",
  "prompt_assembler",
  "model_invoker",
  "tool_executor",
  "finalizer",
  "responder",
] as const;

export type FlowStageId = (typeof FLOW_STAGES)[number];

export type FlowNodeStatus = "pending" | "running" | "done" | "failed" | "waiting_approval";

export interface FlowStageRecord {
  id: string;
  phase: string;
  status: FlowNodeStatus;
}

export interface FlowStageEdge {
  id: string;
  source: string;
  target: string;
  kind: "stage";
}

export const FLOW_STAGE_LABEL_KEYS: Record<FlowStageId, string> = {
  context_builder: "flow.stage.context_builder",
  router: "flow.stage.router",
  planner: "flow.stage.planner",
  permission_gate: "flow.stage.permission_gate",
  prompt_assembler: "flow.stage.prompt_assembler",
  model_invoker: "flow.stage.model_invoker",
  tool_executor: "flow.stage.tool_executor",
  finalizer: "flow.stage.finalizer",
  responder: "flow.stage.responder",
};

export const FLOW_STATUS_LABEL_KEYS: Record<FlowNodeStatus, string> = {
  pending: "flow.status.pending",
  running: "flow.status.running",
  done: "flow.status.done",
  failed: "flow.status.failed",
  waiting_approval: "flow.status.waiting_approval",
};

const FLOW_STAGE_NODE_PREFIX = "stage:";

export function flowStageNodeId(phase: string): string {
  const normalized = String(phase || "").trim();
  if (isFlowStageId(normalized)) {
    return normalized;
  }
  const safe = normalized.replace(/[^a-zA-Z0-9_.:-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
  return `${FLOW_STAGE_NODE_PREFIX}${safe}`;
}

export function flowStageTitle(phase: string): string {
  const normalized = String(phase || "").trim();
  const labelKey = FLOW_STAGE_LABEL_KEYS[normalized as FlowStageId];
  if (labelKey) {
    return labelKey;
  }
  return normalized
    .replace(/[_.:-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase()) || "Unknown stage";
}

const OPTIONAL_STAGES = new Set<FlowStageId>(["tool_executor"]);

const STAGE_SET = new Set<string>(FLOW_STAGES);

const RUNNING_TYPES = new Set([
  "graph.execution_started",
  "model.request_prepared",
  "model.tool_calls_received",
  "tool.execution_started",
  "graph.loop_continuing",
  "graph.loop_prepared",
]);

const WAITING_TYPES = new Set(["graph.waiting_for_approval"]);

const FAILED_TYPES = new Set([
  "tool.execution_failed",
  "tool.execution_denied",
  "tool.execution_blocked",
  "turn.failed",
  "graph.context_budget_exhausted",
  "graph.max_iterations_reached",
]);

const DONE_TYPES = new Set([
  "graph.context_built",
  "graph.route_selected",
  "graph.plan_built",
  "graph.permission_evaluated",
  "graph.prompt_assembled",
  "model.response_received",
  "tool.execution_completed",
  "tool.execution_skipped",
  "graph.execution_completed",
  "graph.response_ready",
  "runtime.turn_completed",
  "turn.completed",
]);

export function isFlowStageId(value: string): value is FlowStageId {
  return STAGE_SET.has(value);
}

export function payloadText(event: ExecutionEvent, key: string): string {
  const value = event.payload?.[key];
  return value == null ? "" : String(value).trim();
}

function eventTimestamp(event: ExecutionEvent): number {
  const parsed = Date.parse(event.timestamp);
  return Number.isFinite(parsed) ? parsed : 0;
}

function eventStep(event: ExecutionEvent): number {
  const raw = event.payload?.step;
  const numeric = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function compareEvents(left: ExecutionEvent, right: ExecutionEvent): number {
  const byTime = eventTimestamp(left) - eventTimestamp(right);
  if (byTime !== 0) {
    return byTime;
  }
  return eventStep(left) - eventStep(right);
}

export function resolveLatestTurnId(events: ExecutionEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const turnId = payloadText(events[index], "turn_id");
    if (turnId) {
      return turnId;
    }
  }
  return "";
}

export function selectTurnEvents(events: ExecutionEvent[], turnId = ""): ExecutionEvent[] {
  const target = turnId || resolveLatestTurnId(events);
  if (!target) {
    return events;
  }
  return events.filter((event) => {
    const eventTurnId = payloadText(event, "turn_id");
    return !eventTurnId || eventTurnId === target;
  });
}

export function statusFromEventType(eventType: string): FlowNodeStatus | null {
  if (WAITING_TYPES.has(eventType)) {
    return "waiting_approval";
  }
  if (FAILED_TYPES.has(eventType)) {
    return "failed";
  }
  if (RUNNING_TYPES.has(eventType)) {
    return "running";
  }
  if (DONE_TYPES.has(eventType)) {
    return "done";
  }
  return null;
}

function eventPhase(event: ExecutionEvent): string {
  const phase = payloadText(event, "phase");
  if (phase) {
    return phase;
  }
  return event.type === "runtime.turn_started" ? "context_builder" : "";
}

export function projectFlowNodes(
  events: ExecutionEvent[],
  turnId = "",
  workers: Array<{ source_stage?: string }> = [],
): { stages: FlowStageRecord[]; edges: FlowStageEdge[] } {
  const stages: FlowStageRecord[] = [];
  const byId = new Map<string, FlowStageRecord>();
  const edges: FlowStageEdge[] = [];
  const edgeIds = new Set<string>();
  let previousId = "";

  const ensureStage = (phase: string): FlowStageRecord => {
    const id = flowStageNodeId(phase);
    const existing = byId.get(id);
    if (existing) {
      return existing;
    }
    const next = { id, phase, status: "pending" as FlowNodeStatus };
    byId.set(id, next);
    stages.push(next);
    return next;
  };

  const addEdge = (source: string, target: string) => {
    if (!source || source === target) {
      return;
    }
    const id = `e-${source}-${target}`;
    if (edgeIds.has(id)) {
      return;
    }
    edgeIds.add(id);
    edges.push({ id, source, target, kind: "stage" });
  };

  const ordered = selectTurnEvents(events, turnId).slice().sort(compareEvents);
  for (const event of ordered) {
    const phase = eventPhase(event);
    if (phase) {
      const stage = ensureStage(phase);
      stage.status = statusFromEventType(event.type) || "running";
      addEdge(previousId, stage.id);
      previousId = stage.id;
    }

    if (event.type === "runtime.turn_completed" || event.type === "turn.completed") {
      for (const stage of stages) {
        if (stage.status === "running" || stage.status === "waiting_approval") {
          stage.status = "done";
        }
      }
    } else if (event.type === "turn.failed") {
      for (const stage of stages) {
        if (stage.status === "running" || stage.status === "waiting_approval") {
          stage.status = "failed";
        }
      }
    }
  }

  for (const worker of workers) {
    const source = String(worker.source_stage || "").trim() || "tool_executor";
    ensureStage(source);
  }

  return { stages, edges };
}

function emptyStatuses(): Record<FlowStageId, FlowNodeStatus> {
  return {
    context_builder: "pending",
    router: "pending",
    planner: "pending",
    permission_gate: "pending",
    prompt_assembler: "pending",
    model_invoker: "pending",
    tool_executor: "pending",
    finalizer: "pending",
    responder: "pending",
  };
}

function markPriorStagesDone(statuses: Record<FlowStageId, FlowNodeStatus>, current: FlowStageId) {
  const currentIndex = FLOW_STAGES.indexOf(current);
  for (let index = 0; index < currentIndex; index += 1) {
    const stage = FLOW_STAGES[index];
    if (OPTIONAL_STAGES.has(stage)) {
      continue;
    }
    if (statuses[stage] === "pending") {
      statuses[stage] = "done";
    }
  }
}

export function projectStageStatuses(
  events: ExecutionEvent[],
  turnId = "",
): Record<FlowStageId, FlowNodeStatus> {
  const statuses = emptyStatuses();
  const ordered = selectTurnEvents(events, turnId).slice().sort(compareEvents);

  for (const event of ordered) {
    const status = statusFromEventType(event.type);
    const phase = payloadText(event, "phase");

    if (status && isFlowStageId(phase)) {
      statuses[phase] = status;
      if (status !== "pending") {
        markPriorStagesDone(statuses, phase);
      }
      continue;
    }

    if (event.type === "runtime.turn_started" && statuses.context_builder === "pending") {
      statuses.context_builder = "running";
      continue;
    }

    if (event.type === "runtime.turn_completed" || event.type === "turn.completed") {
      for (const stage of FLOW_STAGES) {
        if (statuses[stage] === "running" || statuses[stage] === "waiting_approval") {
          statuses[stage] = "done";
        }
      }
      if (statuses.responder === "pending" && statuses.finalizer === "done") {
        statuses.responder = "done";
      }
      continue;
    }

    if (event.type === "turn.failed") {
      for (const stage of FLOW_STAGES) {
        if (statuses[stage] === "running" || statuses[stage] === "waiting_approval") {
          statuses[stage] = "failed";
        }
      }
    }
  }

  return statuses;
}
