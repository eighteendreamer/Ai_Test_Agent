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
    useSessionStore().stopWatcher();
    useSessionStore().disconnectEvents();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it.each([
    ["approval.decision_queued", true],
    ["approval.decision_delivered", true],
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

  it("stops periodic refresh after repeated missing-session failures", async () => {
    const store = useSessionStore();
    store.session = {
      id: "session-1", title: "Deleted session", status: "running",
      messages: [], pending_approvals: [], metadata: {},
    } as unknown as SessionDetail;
    const getSession = vi.spyOn(api, "getSession").mockRejectedValue(new Error("Session not found"));

    store.startWatcher();
    await vi.advanceTimersByTimeAsync(55000);
    expect(store.watcherFailures).toBe(10);
    const callsAtLimit = getSession.mock.calls.length;
    await vi.advanceTimersByTimeAsync(15000);
    expect(getSession).toHaveBeenCalledTimes(callsAtLimit);
  });

  it("clears the exhausted SSE error after an explicit connection succeeds", () => {
    const store = useSessionStore();
    store.session = {
      id: "session-1", title: "Recovered session", status: "running",
      messages: [], pending_approvals: [], metadata: {},
    } as unknown as SessionDetail;
    store.error = "事件流连续断开，已停止自动重连。请手动重试或刷新页面。";

    store.connectEvents(true);
    FakeEventSource.instances[0].onopen?.();

    expect(store.error).toBe("");
  });

  it("refreshes durable facts on SSE reconnect even when no event was replayed", () => {
    const store = useSessionStore();
    store.session = { id: "session-1", pending_approvals: [], messages: [], metadata: {} } as unknown as SessionDetail;
    const refresh = vi.spyOn(store, "scheduleRefresh").mockImplementation(() => {});
    store.connectEvents(true);
    FakeEventSource.instances[0].onopen?.();
    expect(refresh).toHaveBeenCalledWith(true);
  });
});
