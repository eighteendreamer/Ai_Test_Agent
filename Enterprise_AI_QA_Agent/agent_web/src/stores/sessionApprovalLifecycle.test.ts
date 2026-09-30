// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../services/api";
import type { SessionDetail, ToolApprovalRequest } from "../types";
import { useSessionStore } from "./session";

function approval(): ToolApprovalRequest {
  return {
    id: "approval-1",
    session_id: "session-1",
    tool_key: "shell",
    tool_name: "Shell",
    reason: "Requires approval",
    status: "pending",
    created_at: "2026-09-30T10:00:00Z",
    metadata: {},
  };
}

function sessionDetail(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "session-1",
    title: "Approval test",
    status: "waiting_approval",
    messages: [],
    pending_approvals: [approval()],
    metadata: {},
    ...overrides,
  } as unknown as SessionDetail;
}

describe("approval and watcher refresh lifecycle", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    const store = useSessionStore();
    store.stopWatcher();
    store.disconnectEvents();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not restore a resolved approval from a stale scheduled refresh", async () => {
    const store = useSessionStore();
    const currentSession = sessionDetail();
    const staleSession = sessionDetail();
    store.session = currentSession;
    vi.spyOn(api, "resolveApproval").mockResolvedValue({
      ...approval(),
      status: "approved",
    });
    vi.spyOn(api, "getSession")
      .mockResolvedValueOnce(staleSession)
      .mockResolvedValueOnce(sessionDetail({ pending_approvals: [] }));
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();

    await store.resolveApproval("approval-1", "approved");
    expect(store.pendingApprovals).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(120);

    expect(store.pendingApprovals).toHaveLength(0);
    expect(store.resolvedApprovalIdsBySession["session-1"]).toEqual(["approval-1"]);

    await store.refreshSession();

    expect(store.pendingApprovals).toHaveLength(0);
    expect(store.resolvedApprovalIdsBySession["session-1"]).toBeUndefined();
  });

  it("invalidates an in-flight session refresh when the watcher stops", async () => {
    const store = useSessionStore();
    const currentSession = sessionDetail({
      title: "Current state",
      status: "running",
      pending_approvals: [],
    });
    const staleSession = sessionDetail({
      title: "Stale response",
      status: "completed",
      pending_approvals: [],
    });
    store.session = currentSession;
    let resolveResponse!: (session: SessionDetail) => void;
    const response = new Promise<SessionDetail>((resolve) => {
      resolveResponse = resolve;
    });
    vi.spyOn(api, "getSession").mockReturnValue(response);
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();

    const refresh = store.refreshSession();
    expect(store.refreshInFlight).toBe(true);
    store.stopWatcher();
    const clearedInFlightImmediately = !store.refreshInFlight;

    resolveResponse(staleSession);
    await refresh;

    expect(clearedInFlightImmediately).toBe(true);
    expect(store.session?.title).toBe("Current state");
    expect(store.session?.status).toBe("running");
  });

  it("does not apply tooling results returned after the watcher stops", async () => {
    const store = useSessionStore();
    store.session = sessionDetail();
    let resolveJobs!: (jobs: Awaited<ReturnType<typeof api.listToolJobs>>) => void;
    const jobsResponse = new Promise<Awaited<ReturnType<typeof api.listToolJobs>>>(
      (resolve) => {
        resolveJobs = resolve;
      },
    );
    vi.spyOn(api, "listToolJobs").mockReturnValue(jobsResponse);
    vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
    vi.spyOn(api, "listVerifications").mockResolvedValue(
      {} as Awaited<ReturnType<typeof api.listVerifications>>,
    );

    const refresh = store.refreshToolingData(true);
    store.stopWatcher();
    resolveJobs([{ id: "stale-job" }] as Awaited<ReturnType<typeof api.listToolJobs>>);
    await refresh;

    expect(store.toolJobs).toEqual([]);
    expect(store.toolingRefreshInFlight).toBe(false);
  });
});
