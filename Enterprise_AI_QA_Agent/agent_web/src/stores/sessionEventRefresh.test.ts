// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../services/api";
import type { SessionDetail } from "../types";
import { useSessionStore } from "./session";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.readyState = 2;
  }

  emit(type: string) {
    const event = {
      id: `event-${type}`,
      type,
      session_id: "session-1",
      timestamp: "2026-09-27T00:00:00Z",
      payload: {},
    };
    this.onmessage?.({ data: JSON.stringify(event), lastEventId: event.id } as MessageEvent<string>);
  }
}

describe("session SSE refresh after durable state changes", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
  });

  afterEach(() => {
    useSessionStore().disconnectEvents();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it.each([
    ["approval.decision_applied", true],
    ["runtime.turn_completed", true],
    ["runtime.turn_failed", true],
    ["runtime.turn_interrupted", true],
    ["worker.interrupted", false],
  ])("refreshes the session for %s", async (type, includeTooling) => {
    const store = useSessionStore();
    const oldSession = {
      id: "session-1",
      title: "SSE test",
      status: "waiting_approval",
      messages: [],
      pending_approvals: [{ id: "approval-1", status: "pending" }],
      metadata: {},
    } as unknown as SessionDetail;
    store.session = oldSession;
    const refreshedSession = {
      ...oldSession,
      status: "completed",
      pending_approvals: [],
    } as SessionDetail;
    const getSession = vi.spyOn(api, "getSession").mockResolvedValue(refreshedSession);
    const refreshTooling = vi.spyOn(store, "refreshToolingData").mockResolvedValue();

    store.connectEvents();
    expect(FakeEventSource.instances[0].url).toContain("/api/v1/sessions/session-1/events");
    FakeEventSource.instances[0].emit(type);
    await vi.advanceTimersByTimeAsync(220);

    expect(getSession).toHaveBeenCalledWith("session-1");
    expect(store.session?.status).toBe("completed");
    expect(store.pendingApprovals).toEqual([]);
    expect(refreshTooling).toHaveBeenCalledWith(includeTooling);
  });
});
