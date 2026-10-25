// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../services/api";
import type { SessionDetail } from "../types";
import { useSessionStore } from "./session";

function detail(phase = "awaiting_decision"): SessionDetail {
  return {
    id: "session-1", title: "Native approval", status: "waiting_approval", messages: [], metadata: {},
    pending_approvals: (phase === "awaiting_decision" ? ["A", "B"] : ["A"]).map((id) => ({
      id, session_id: "session-1", tool_key: "inspect", tool_name: id, status: "pending",
      reason: "Inspect value", created_at: "2026-10-26T00:00:00Z", metadata: {},
    })),
    framework_approval_progress: [{ approval_id: "B", turn_id: "turn-1", tool_key: "inspect", tool_name: "B",
      phase, decision: phase === "awaiting_decision" ? "pending" : "approved", tool_status: "waiting_approval" }],
  } as unknown as SessionDetail;
}

describe("durable framework approval progress", () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); });
  afterEach(() => {
    useSessionStore().stopWatcher(); useSessionStore().disconnectEvents();
    vi.useRealTimers(); vi.restoreAllMocks();
  });

  it.each(["approved", "denied"] as const)("shows an accepted %s decision without claiming execution", async (decision) => {
    const store = useSessionStore();
    store.applySession(detail());
    store.applyStreamingEvent({ id: "blocked", type: "tool.execution_blocked", session_id: "session-1",
      timestamp: "2026-10-26T00:00:00Z", payload: { approval_id: "B", tool_key: "inspect" } });
    const accepted = detail("decision_submitted");
    accepted.framework_approval_progress![0].decision = decision;
    vi.spyOn(api, "resolveApproval").mockResolvedValue({ session: accepted, events: [],
      task: { id: "task-B", status: "queued" } } as unknown as Awaited<ReturnType<typeof api.resolveApproval>>);
    await store.resolveApproval("B", decision);
    expect(store.session?.framework_approval_progress?.[0].phase).toBe("decision_submitted");
    expect(store.pendingApprovals.map((item) => item.id)).toEqual(["A"]);
    expect(JSON.parse(store.messages[0].content).status).toBe("decision_submitted");
    expect(store.messages[0].metadata.tool_progress_finalized).toBe("false");
  });

  it("discards a GET started before decision admission, then reads the latest durable phase", async () => {
    const store = useSessionStore();
    store.applySession(detail());
    let release!: (value: SessionDetail) => void;
    vi.spyOn(api, "getSession").mockReturnValueOnce(new Promise((resolve) => { release = resolve; }))
      .mockResolvedValue(detail("executing"));
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();
    vi.spyOn(api, "resolveApproval").mockResolvedValue({ session: detail("decision_submitted"), events: [],
      task: { id: "task-B", status: "queued" } } as unknown as Awaited<ReturnType<typeof api.resolveApproval>>);
    const stale = store.refreshSession();
    await store.resolveApproval("B", "approved");
    release(detail()); await stale;
    expect(store.session?.framework_approval_progress?.[0].phase).toBe("decision_submitted");
    await vi.advanceTimersByTimeAsync(120);
    expect(store.session?.framework_approval_progress?.[0].phase).toBe("executing");
  });

  it.each(["executing", "applying_result", "applied", "recovery_required"])("reconstructs %s from GET after reload", async (phase) => {
    const store = useSessionStore();
    store.applySession(detail());
    vi.spyOn(api, "getSession").mockResolvedValue(detail(phase));
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();
    await store.refreshSession();
    expect(store.session?.framework_approval_progress?.[0].phase).toBe(phase);
    expect(store.pendingApprovals.map((item) => item.id)).toEqual(["A"]);
  });

  it("invalidates a read between two overlapping decision responses", async () => {
    const store = useSessionStore(); store.applySession(detail());
    type Receipt = Awaited<ReturnType<typeof api.resolveApproval>>;
    let finishA!: (value: Receipt) => void;
    let finishB!: (value: Receipt) => void;
    vi.spyOn(api, "resolveApproval").mockReturnValueOnce(new Promise((resolve) => { finishA = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { finishB = resolve; }));
    const a = store.resolveApproval("A", "approved");
    const b = store.resolveApproval("B", "approved");
    const acceptedA = detail();
    acceptedA.framework_approval_progress = [{ ...detail("decision_submitted").framework_approval_progress![0], approval_id: "A" }];
    finishA({ session: acceptedA, events: [], task: { id: "task-A" } } as unknown as Receipt); await a;
    let release!: (value: SessionDetail) => void;
    vi.spyOn(api, "getSession").mockReturnValue(new Promise((resolve) => { release = resolve; }));
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();
    const stale = store.refreshSession();
    finishB({ session: detail("decision_submitted"), events: [], task: { id: "task-B" } } as unknown as Receipt); await b;
    release(detail()); await stale;
    expect(store.session?.framework_approval_progress?.map((item) => item.phase)).toEqual(["decision_submitted", "decision_submitted"]);
    expect(store.pendingApprovals).toEqual([]);
  });

  it("does not restart refresh after watcher stop during a decision request", async () => {
    const store = useSessionStore(); store.applySession(detail());
    let release!: (value: Awaited<ReturnType<typeof api.resolveApproval>>) => void;
    vi.spyOn(api, "resolveApproval").mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const get = vi.spyOn(api, "getSession");
    const pending = store.resolveApproval("B", "approved"); store.stopWatcher();
    release({ session: detail("decision_submitted"), events: [], task: { id: "task-B" } } as unknown as Awaited<ReturnType<typeof api.resolveApproval>>);
    await pending; await vi.advanceTimersByTimeAsync(200);
    expect(get).not.toHaveBeenCalled();
  });

  it("keeps a newer durable result when an earlier POST receipt arrives late", async () => {
    const store = useSessionStore(); store.applySession(detail());
    let release!: (value: Awaited<ReturnType<typeof api.resolveApproval>>) => void;
    vi.spyOn(api, "resolveApproval").mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const pending = store.resolveApproval("B", "approved");
    const newer = detail("applied");
    Object.assign(newer.framework_approval_progress![0], { observed_at: "2026-10-26T00:00:02Z" });
    store.applySession(newer);
    const older = detail("decision_submitted");
    Object.assign(older.framework_approval_progress![0], { observed_at: "2026-10-26T00:00:01Z" });
    release({ session: older, events: [], task: { id: "task-B" } } as unknown as Awaited<ReturnType<typeof api.resolveApproval>>);
    await pending;
    expect(store.session?.framework_approval_progress?.[0].phase).toBe("applied");
  });
});
