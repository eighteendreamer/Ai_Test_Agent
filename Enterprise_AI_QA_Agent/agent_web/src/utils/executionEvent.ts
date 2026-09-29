import type { ExecutionEvent } from "../types";

export function eventTurnId(event: ExecutionEvent): string {
  const topLevel = String(event.turn_id ?? "").trim();
  return topLevel || String(event.payload?.turn_id ?? "").trim();
}
