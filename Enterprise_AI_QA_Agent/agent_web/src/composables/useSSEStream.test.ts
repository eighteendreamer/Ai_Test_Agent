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
});
