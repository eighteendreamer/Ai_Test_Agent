import type { ExecutionEvent } from "../types";
import { eventTurnId } from "../utils/executionEvent";

export const ASSISTANT_DELTA_INTERVAL_MS = 40;
const MAX_BUFFERED_EVENTS = 256;
const MAX_BUFFERED_CHARACTERS = 16384;

// Like session.scheduleRefresh, separate arrival frequency from UI updates.
// Only consecutive deltas for the same answer may be joined; controls flush first.
export class AssistantDeltaBuffer {
  private events: ExecutionEvent[] = [];
  private characters = 0;
  private timer: number | null = null;

  constructor(private readonly onFlush: (events: ExecutionEvent[]) => void) {}

  push(event: ExecutionEvent): void {
    const first = this.events[0];
    if (first && (
      first.session_id !== event.session_id ||
      eventTurnId(first) !== eventTurnId(event) ||
      first.payload.message_id !== event.payload.message_id
    )) {
      this.flush();
    }
    this.events.push(event);
    this.characters += String(event.payload.delta || "").length;
    if (this.events.length >= MAX_BUFFERED_EVENTS || this.characters >= MAX_BUFFERED_CHARACTERS) {
      this.flush();
    } else if (this.timer === null) {
      this.timer = window.setTimeout(() => this.flush(), ASSISTANT_DELTA_INTERVAL_MS);
    }
  }

  flush(): void {
    const events = this.events;
    this.clear();
    if (events.length) this.onFlush(events);
  }

  clear(): number {
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    const count = this.events.length;
    this.events = [];
    this.characters = 0;
    return count;
  }
}
