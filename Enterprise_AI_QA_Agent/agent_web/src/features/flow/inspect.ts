import type { ExecutionEvent, SessionSnapshot, ToolArtifactRecord, ToolJobRecord, WorkerDispatchRecord } from "../../types";
import { compareEvents, payloadText, selectTurnEvents, type FlowStageId } from "./stages";

export type InspectPresence = "missing" | "empty" | "ok";

export interface InspectValue<T> {
  presence: InspectPresence;
  value: T | null;
}

export interface InspectLogItem {
  id: string;
  type: string;
  timestamp: string;
  message: string;
}

export interface InspectToolItem {
  id: string;
  name: string;
  status: string;
  summary: string;
}

export interface InspectSkillItem {
  key: string;
  source: "requested" | "resolved";
}

export interface InspectPromptView {
  prompt: InspectValue<string>;
  sections: InspectValue<Array<{ title: string; body: string }>>;
}

export interface InspectOutputView {
  text: InspectValue<string>;
}

export interface InspectArtifactItem {
  id: string;
  label: string;
  type: string;
  path: string;
}

function hasOwn(record: Record<string, unknown> | null, key: string): boolean {
  return Boolean(record && Object.prototype.hasOwnProperty.call(record, key));
}

function readField<T>(record: Record<string, unknown> | null, key: string): InspectValue<T> {
  if (!hasOwn(record, key)) {
    return { presence: "missing", value: null };
  }
  const raw = record?.[key];
  if (raw == null) {
    return { presence: "empty", value: null };
  }
  if (typeof raw === "string" && !raw.trim()) {
    return { presence: "empty", value: null };
  }
  if (Array.isArray(raw) && raw.length === 0) {
    return { presence: "empty", value: null };
  }
  return { presence: "ok", value: raw as T };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string {
  return value == null ? "" : String(value).trim();
}

function sectionView(section: unknown): { title: string; body: string } {
  if (typeof section === "string") {
    return { title: "", body: section };
  }
  const record = asRecord(section);
  if (!record) {
    return { title: "", body: section == null ? "" : String(section) };
  }
  const title = asString(record.key || record.title || record.name);
  const body = record.content ?? record.text ?? record.body;
  if (typeof body === "string") {
    return { title, body };
  }
  return { title, body: JSON.stringify(record, null, 2) };
}

export function pickSnapshotForTurn(snapshots: SessionSnapshot[], turnId: string): SessionSnapshot | null {
  if (snapshots.length === 0) {
    return null;
  }
  const ranked = snapshots.slice().sort((left, right) => {
    const byVersion = Number(right.version || 0) - Number(left.version || 0);
    if (byVersion !== 0) {
      return byVersion;
    }
    return String(right.created_at || "").localeCompare(String(left.created_at || ""));
  });
  if (!turnId) {
    return ranked[0] ?? null;
  }
  return ranked.find((snapshot) => asString(snapshot.graph_state?.turn_id) === turnId) ?? null;
}

export interface InspectWorkerField {
  key: string;
  labelKey: string;
  presence: InspectPresence;
  value: string;
}

const WORKER_OUTPUT_FIELDS: Array<{ key: keyof WorkerDispatchRecord; labelKey: string }> = [
  { key: "description", labelKey: "flow.worker.field.description" },
  { key: "agent_key", labelKey: "flow.worker.field.agent_key" },
  { key: "status", labelKey: "flow.worker.field.status" },
  { key: "task_id", labelKey: "flow.worker.field.task_id" },
  { key: "child_session_id", labelKey: "flow.worker.field.child_session_id" },
  { key: "model_key", labelKey: "flow.worker.field.model_key" },
  { key: "dispatch_role", labelKey: "flow.worker.field.dispatch_role" },
  { key: "source_stage", labelKey: "flow.worker.field.source_stage" },
];

export function inspectWorkerOutput(worker: WorkerDispatchRecord | null): InspectValue<InspectWorkerField[]> {
  if (!worker) {
    return { presence: "missing", value: null };
  }
  const items: InspectWorkerField[] = [];
  for (const field of WORKER_OUTPUT_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(worker, field.key)) {
      continue;
    }
    const raw = worker[field.key];
    if (raw == null) {
      items.push({ key: field.key, labelKey: field.labelKey, presence: "empty", value: "" });
      continue;
    }
    const text = String(raw).trim();
    items.push({
      key: field.key,
      labelKey: field.labelKey,
      presence: text ? "ok" : "empty",
      value: text,
    });
  }
  if (items.length === 0) {
    return { presence: "missing", value: null };
  }
  return { presence: "ok", value: items };
}

export function inspectWorkerLogs(
  events: ExecutionEvent[],
  worker: { task_id?: string; child_session_id?: string },
  turnId: string,
): InspectValue<InspectLogItem[]> {
  const taskId = String(worker.task_id || "").trim();
  const childId = String(worker.child_session_id || "").trim();
  if (!taskId && !childId) {
    return { presence: "missing", value: null };
  }
  const matched = selectTurnEvents(events, turnId)
    .filter((event) => {
      const eventTaskId = payloadText(event, "task_id");
      const eventChildId = payloadText(event, "child_session_id");
      return (taskId && eventTaskId === taskId) || (childId && eventChildId === childId);
    })
    .slice()
    .sort(compareEvents)
    .map((event, index) => ({
      id: String(event.id || `${event.type}-${index}`),
      type: event.type,
      timestamp: event.timestamp,
      message: payloadText(event, "message"),
    }));
  if (matched.length === 0) {
    return { presence: "empty", value: [] };
  }
  return { presence: "ok", value: matched };
}

export function inspectLogs(events: ExecutionEvent[], stageId: FlowStageId, turnId: string): InspectValue<InspectLogItem[]> {
  const matched = selectTurnEvents(events, turnId)
    .filter((event) => payloadText(event, "phase") === stageId)
    .slice()
    .sort(compareEvents)
    .map((event, index) => ({
      id: String(event.id || `${event.type}-${index}`),
      type: event.type,
      timestamp: event.timestamp,
      message: payloadText(event, "message"),
    }));
  if (matched.length === 0) {
    return { presence: "empty", value: [] };
  }
  return { presence: "ok", value: matched };
}

export function inspectTools(
  stageId: FlowStageId,
  graphState: Record<string, unknown> | null,
  jobs: ToolJobRecord[],
  turnId: string,
): InspectValue<InspectToolItem[]> {
  const items: InspectToolItem[] = [];

  if (stageId === "tool_executor" || stageId === "model_invoker" || stageId === "permission_gate") {
    const turnJobs = jobs.filter((job) => !turnId || job.turn_id === turnId);
    for (const job of turnJobs) {
      items.push({
        id: job.id,
        name: job.tool_name || job.tool_key,
        status: job.status,
        summary: job.summary || job.error_message || "",
      });
    }

    const resultKey = stageId === "permission_gate" ? "permission_decisions" : "tool_results";
    const extra = stageId === "model_invoker" ? readField<unknown[]>(graphState, "model_tool_calls") : readField<unknown[]>(graphState, resultKey);
    if (extra.presence === "ok" && extra.value) {
      extra.value.forEach((item, index) => {
        const record = asRecord(item);
        items.push({
          id: `snapshot-${index}-${asString(record?.call_id || record?.tool_key || index)}`,
          name: asString(record?.tool_name || record?.tool_key || record?.name) || `item-${index + 1}`,
          status: asString(record?.status || record?.decision),
          summary: asString(record?.summary || record?.reason || record?.message),
        });
      });
    }

    if (items.length > 0) {
      return { presence: "ok", value: items };
    }
    if (turnJobs.length === 0 && extra.presence === "missing" && jobs.length === 0 && !graphState) {
      return { presence: "missing", value: null };
    }
    return { presence: "empty", value: [] };
  }

  return { presence: "empty", value: [] };
}

export function inspectSkills(graphState: Record<string, unknown> | null): InspectValue<InspectSkillItem[]> {
  const requested = readField<unknown[]>(graphState, "requested_skill_keys");
  const resolved = readField<unknown[]>(graphState, "resolved_skill_keys");
  if (requested.presence === "missing" && resolved.presence === "missing") {
    return { presence: "missing", value: null };
  }
  const items: InspectSkillItem[] = [];
  for (const key of requested.value ?? []) {
    const text = asString(key);
    if (text) {
      items.push({ key: text, source: "requested" });
    }
  }
  for (const key of resolved.value ?? []) {
    const text = asString(key);
    if (text) {
      items.push({ key: text, source: "resolved" });
    }
  }
  if (items.length === 0) {
    return { presence: "empty", value: [] };
  }
  return { presence: "ok", value: items };
}

export function inspectSkillBlocks(graphState: Record<string, unknown> | null): InspectValue<string[]> {
  const blocks = readField<unknown[]>(graphState, "skill_prompt_blocks");
  if (blocks.presence !== "ok" || !blocks.value) {
    return { presence: blocks.presence, value: blocks.presence === "empty" ? [] : null };
  }
  return {
    presence: "ok",
    value: blocks.value.map((item) => asString(item)).filter(Boolean),
  };
}

export function inspectPrompt(graphState: Record<string, unknown> | null): InspectPromptView {
  const prompt = readField<string>(graphState, "system_prompt");
  const rawSections = readField<unknown[]>(graphState, "system_prompt_sections");
  return {
    prompt,
    sections:
      rawSections.presence === "ok" && rawSections.value
        ? { presence: "ok", value: rawSections.value.map(sectionView) }
        : { presence: rawSections.presence, value: rawSections.presence === "empty" ? [] : null },
  };
}

const OUTPUT_FIELD_BY_STAGE: Partial<Record<FlowStageId, string>> = {
  context_builder: "context_bundle",
  router: "selected_agent_key",
  planner: "plan_steps",
  permission_gate: "permission_decisions",
  prompt_assembler: "system_prompt",
  model_invoker: "model_response_text",
  tool_executor: "tool_results",
  finalizer: "final_response",
  responder: "final_response",
};

export function inspectOutput(stageId: FlowStageId, graphState: Record<string, unknown> | null): InspectOutputView {
  const field = OUTPUT_FIELD_BY_STAGE[stageId];
  if (!field) {
    return { text: { presence: "missing", value: null } };
  }
  const raw = readField<unknown>(graphState, field);
  if (raw.presence !== "ok" || raw.value == null) {
    return { text: { presence: raw.presence, value: null } };
  }
  if (typeof raw.value === "string") {
    return { text: { presence: "ok", value: raw.value } };
  }
  return { text: { presence: "ok", value: JSON.stringify(raw.value, null, 2) } };
}

export function inspectArtifacts(
  artifacts: ToolArtifactRecord[],
  turnId: string,
  loaded: boolean,
): InspectValue<InspectArtifactItem[]> {
  if (!loaded) {
    return { presence: "missing", value: null };
  }
  const matched = artifacts
    .filter((item) => !turnId || item.turn_id === turnId)
    .map((item) => ({
      id: item.id,
      label: item.label || item.tool_key || item.id,
      type: item.artifact_type,
      path: item.path,
    }));
  if (matched.length === 0) {
    return { presence: "empty", value: [] };
  }
  return { presence: "ok", value: matched };
}
