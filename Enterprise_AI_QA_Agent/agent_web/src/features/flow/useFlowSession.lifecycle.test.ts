// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, nextTick, ref, type Ref } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ExecutionEvent, SessionFlowResponse } from "../../types";
import { useFlowSession } from "./useFlowSession";

const api = vi.hoisted(() => ({
  getSessionFlow: vi.fn(),
  connectEvents: vi.fn(),
  listSessionEvents: vi.fn(),
  listSessionSnapshots: vi.fn(),
  listToolJobs: vi.fn(),
  listArtifacts: vi.fn(),
  getSession: vi.fn(),
}));
vi.mock("../../services/api", () => ({ api }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function flow(id: string, eventId = id): SessionFlowResponse {
  return {
    session_id: id,
    turn_id: "",
    stages: [],
    workers: [],
    edges: [],
    events: [{ id: eventId, type: "turn.completed", session_id: id, timestamp: "2026-09-30T00:00:00Z", payload: {} }],
    graph_state: null,
    snapshot_id: null,
    tool_jobs: [],
    artifacts: [],
  };
}

function mountFlow(sessionId: Ref<string>) {
  let session!: ReturnType<typeof useFlowSession>;
  const Host = defineComponent({
    setup() {
      session = useFlowSession(sessionId, ref(""));
      return () => h("div");
    },
  });
  const wrapper = mount(Host);
  return { wrapper, session: () => session };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.connectEvents.mockImplementation(() => ({ close: vi.fn(), onerror: null }));
});
afterEach(() => vi.useRealTimers());

it("keeps the newer session when the previous flow response arrives last", async () => {
  const old = deferred<SessionFlowResponse>();
  const current = deferred<SessionFlowResponse>();
  api.getSessionFlow.mockImplementation((id: string) => id === "old" ? old.promise : current.promise);
  const id = ref("old");
  const { wrapper, session } = mountFlow(id);
  try {
    id.value = "current";
    await nextTick();
    current.resolve(flow("current"));
    await flushPromises();
    expect(session().events.value.map(event => event.id)).toEqual(["current"]);

    old.resolve(flow("old"));
    await flushPromises();
    expect(session().events.value.map(event => event.id)).toEqual(["current"]);
    expect(api.connectEvents.mock.calls.map(([sessionId]) => sessionId)).toEqual(["current"]);
    expect(api.connectEvents.mock.results[0].value.close).not.toHaveBeenCalled();
  } finally {
    wrapper.unmount();
  }
});

it("ignores late SSE callbacks from a closed session", async () => {
  api.getSessionFlow.mockImplementation(async (id: string) => flow(id));
  const id = ref("old");
  const { wrapper, session } = mountFlow(id);
  try {
    await flushPromises();
    const oldCallback = api.connectEvents.mock.calls[0][1] as (event: ExecutionEvent) => void;
    id.value = "current";
    await nextTick();
    await flushPromises();
    oldCallback(flow("old", "late-old").events[0]);
    expect(session().events.value.map(event => event.id)).toEqual(["current"]);
  } finally {
    wrapper.unmount();
  }
});

it("does not connect an SSE stream after the view is destroyed", async () => {
  const pending = deferred<SessionFlowResponse>();
  api.getSessionFlow.mockReturnValue(pending.promise);
  const { wrapper } = mountFlow(ref("old"));
  wrapper.unmount();
  pending.resolve(flow("old"));
  await flushPromises();
  expect(api.connectEvents).not.toHaveBeenCalled();
});

it("does not enter history fallback when a rejected response belongs to an old session", async () => {
  const old = deferred<SessionFlowResponse>();
  api.getSessionFlow.mockImplementation((id: string) => id === "old" ? old.promise : Promise.resolve(flow(id)));
  const id = ref("old");
  const { wrapper, session } = mountFlow(id);
  try {
    id.value = "current";
    await nextTick();
    await flushPromises();
    old.reject(new Error("old endpoint failed"));
    await flushPromises();
    expect(api.listSessionEvents).not.toHaveBeenCalled();
    expect(session().events.value.map(event => event.id)).toEqual(["current"]);
  } finally {
    wrapper.unmount();
  }
});

it("keeps the latest inspector refresh when two responses overlap", async () => {
  const firstRefresh = deferred<SessionFlowResponse>();
  api.getSessionFlow.mockResolvedValueOnce(flow("current"))
    .mockReturnValueOnce(firstRefresh.promise)
    .mockResolvedValueOnce({ ...flow("current"), graph_state: { marker: "latest" } });
  const { wrapper, session } = mountFlow(ref("current"));
  try {
    await flushPromises();
    vi.useFakeTimers();
    const onEvent = api.connectEvents.mock.calls[0][1] as (event: ExecutionEvent) => void;
    onEvent(flow("current", "refresh-1").events[0]);
    await vi.advanceTimersByTimeAsync(400);
    onEvent(flow("current", "refresh-2").events[0]);
    await vi.advanceTimersByTimeAsync(400);
    expect(session().graphState.value).toEqual({ marker: "latest" });
    firstRefresh.resolve({ ...flow("current"), graph_state: { marker: "stale" } });
    await vi.advanceTimersByTimeAsync(0);
    expect(session().graphState.value).toEqual({ marker: "latest" });
  } finally {
    wrapper.unmount();
  }
});
