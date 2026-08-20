import type { XYPosition } from "@vue-flow/core";

import { FLOW_STAGES } from "./stages";

const STORAGE_PREFIX = "qa-agent-flow-layout:";
const NODE_WIDTH = 196;
const GAP_X = 72;
const MAIN_Y = 168;
const TOOL_Y = 348;
const WORKER_Y = 80;
const WORKER_GAP_Y = 120;

export function defaultStagePositions(stageIds: string[] = [...FLOW_STAGES]): Record<string, XYPosition> {
  const x = (index: number) => 40 + index * (NODE_WIDTH + GAP_X);
  return Object.fromEntries(stageIds.map((stageId, index) => [
    stageId,
    { x: x(index), y: stageId === "tool_executor" ? TOOL_Y : MAIN_Y },
  ]));
}

export function layoutStorageKey(sessionId: string, turnId: string): string {
  return `${STORAGE_PREFIX}${sessionId}:${turnId || "latest"}`;
}

function isPosition(value: unknown): value is XYPosition {
  if (!value || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  return Number.isFinite(Number(record.x)) && Number.isFinite(Number(record.y));
}

export function loadSavedPositions(sessionId: string, turnId: string): Record<string, XYPosition> {
  if (!sessionId || typeof localStorage === "undefined") {
    return {};
  }
  try {
    const raw = localStorage.getItem(layoutStorageKey(sessionId, turnId));
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const next: Record<string, XYPosition> = {};
    for (const [key, candidate] of Object.entries(parsed)) {
      if (isPosition(candidate)) {
        next[key] = { x: Number(candidate.x), y: Number(candidate.y) };
      }
    }
    return next;
  } catch {
    return {};
  }
}

export function saveSavedPositions(
  sessionId: string,
  turnId: string,
  positions: Record<string, XYPosition>,
): void {
  if (!sessionId || typeof localStorage === "undefined") {
    return;
  }
  try {
    localStorage.setItem(layoutStorageKey(sessionId, turnId), JSON.stringify(positions));
  } catch (error) {
    console.warn("[flow] failed to persist node positions", error);
  }
}

export function clearSavedPositions(sessionId: string, turnId: string): void {
  if (!sessionId || typeof localStorage === "undefined") {
    return;
  }
  localStorage.removeItem(layoutStorageKey(sessionId, turnId));
}

export function mergeStagePositions(
  sessionId: string,
  turnId: string,
  stageIds: string[] = [...FLOW_STAGES],
): Record<string, XYPosition> {
  const defaults = defaultStagePositions(stageIds);
  const saved = loadSavedPositions(sessionId, turnId);
  const merged = { ...defaults };
  for (const stage of stageIds) {
    const position = saved[stage];
    if (position) {
      merged[stage] = position;
    }
  }
  return merged;
}

function overlaps(left: XYPosition, right: XYPosition): boolean {
  return Math.abs(left.x - right.x) < 80 && Math.abs(left.y - right.y) < 80;
}

export function nextWorkerPosition(occupied: XYPosition[], stageCount = 1): XYPosition {
  const workerX = 40 + Math.max(stageCount, 1) * (NODE_WIDTH + GAP_X);
  for (let index = 0; index < 48; index += 1) {
    const candidate = { x: workerX, y: WORKER_Y + index * WORKER_GAP_Y };
    if (!occupied.some((item) => overlaps(item, candidate))) {
      return candidate;
    }
  }
  return { x: workerX, y: WORKER_Y + occupied.length * WORKER_GAP_Y };
}
