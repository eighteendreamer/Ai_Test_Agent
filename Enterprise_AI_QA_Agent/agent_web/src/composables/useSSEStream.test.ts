import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SSEStreamManager } from "./useSSEStream";
import type { ExecutionEvent } from "../types";

class FakeEventSource {
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];

  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  readyState = 1;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  emit(data: string, lastEventId = "") {
    this.onmessage?.({ data, lastEventId } as MessageEvent<string>);
  }
}

describe("session SSE transport", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports a malformed frame, keeps its cursor unchanged, and accepts the next valid event", () => {
    const manager = new SSEStreamManager();
    const events: ExecutionEvent[] = [];
    const errors: string[] = [];
    const warnings = vi.spyOn(console, "warn").mockImplementation(() => {});
    manager.bind({ onEvent: (event) => events.push(event), onConnect: () => {}, onError: (error) => errors.push(error) });
    manager.connect("session-1");
    const source = FakeEventSource.instances[0];

    expect(() => source.emit("{", "bad-1")).not.toThrow();
    expect(events).toEqual([]);
    expect(manager.cursorBySession["session-1"]).toBeUndefined();
    expect(errors).toHaveLength(1);
    expect(warnings).toHaveBeenCalled();

    source.emit(JSON.stringify({ id: "event-2", type: "turn.completed", session_id: "session-1", timestamp: "2026-09-24T00:00:00Z", payload: {} }), "event-2");
    expect(events).toHaveLength(1);
    expect(manager.cursorBySession["session-1"]).toBe("event-2");
    expect(source.readyState).toBe(1);

    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    source.onerror?.();
    vi.advanceTimersByTime(1500);
    expect(FakeEventSource.instances[1].url).toBe("/api/v1/sessions/session-1/events?last_event_id=event-2");
    manager.disconnect();
  });

  it("rejects a parseable frame with the wrong session instead of advancing the cursor", () => {
    const manager = new SSEStreamManager();
    const events: ExecutionEvent[] = [];
    const errors: string[] = [];
    vi.spyOn(console, "warn").mockImplementation(() => {});
    manager.bind({ onEvent: (event) => events.push(event), onConnect: () => {}, onError: (error) => errors.push(error) });
    manager.connect("session-1");

    expect(() => FakeEventSource.instances[0].emit(JSON.stringify({ id: "bad-2", type: "turn.completed", session_id: "session-2", timestamp: "2026-09-24T00:00:00Z", payload: {} }), "bad-2")).not.toThrow();
    expect(events).toEqual([]);
    expect(manager.cursorBySession["session-1"]).toBeUndefined();
    expect(errors).toHaveLength(1);
    manager.disconnect();
  });

  it("deduplicates a receipt event without advancing the SSE reconnect cursor", () => {
    const manager = new SSEStreamManager();
    const event: ExecutionEvent = {
      id: "event-from-receipt", type: "session.created", session_id: "session-1",
      timestamp: "2026-09-29T00:00:00Z", payload: {},
    };

    expect(manager.rememberReceiptEvent(event)).toBe(true);
    expect(manager.cursorBySession["session-1"]).toBeUndefined();
    expect(manager.rememberIncomingEvent(event)).toBe(false);
    expect(manager.cursorBySession["session-1"]).toBe("event-from-receipt");
  });

  it("scopes recent event IDs to the session and clears them on explicit disconnect", () => {
    const manager = new SSEStreamManager({ receivedIdLimit: 2 });
    const event = (sessionId: string, id: string): ExecutionEvent => ({
      id, type: "session.created", session_id: sessionId,
      timestamp: "2026-09-30T00:00:00Z", payload: {},
    });

    expect(manager.rememberReceiptEvent(event("session-1", "shared-id"))).toBe(true);
    expect(manager.rememberReceiptEvent(event("session-1", "shared-id"))).toBe(false);
    expect(manager.rememberReceiptEvent(event("session-2", "shared-id"))).toBe(true);
    expect(manager.rememberReceiptEvent(event("session-2", "next-id"))).toBe(true);
    expect(manager.rememberReceiptEvent(event("session-2", "third-id"))).toBe(true);
    expect(manager.rememberReceiptEvent(event("session-2", "shared-id"))).toBe(true);
    manager.connect("session-2");
    manager.connect("session-2", true);
    expect(manager.rememberReceiptEvent(event("session-2", "shared-id"))).toBe(false);

    manager.disconnect();
    expect(manager.receivedIds).toEqual([]);
    expect(manager.rememberReceiptEvent(event("session-2", "shared-id"))).toBe(true);
  });
});
