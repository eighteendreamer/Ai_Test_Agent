// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSessionStore } from "./session";
import RuntimeStatusPanel from "../components/chat/RuntimeStatusPanel.vue";
import { api } from "../services/api";
import type { AgentTaskAccepted, SessionDetail } from "../types";

describe("durable Agent input queue", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    useSessionStore().disconnectEvents();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps the waiting assistant and disables another submit after a queued 202 receipt", async () => {
    class FakeEventSource {
      readyState = 1;
      onopen = null;
      onerror = null;
      onmessage = null;
      constructor(readonly url: string) {}
      close() { this.readyState = 2; }
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    const store = useSessionStore();
    const session = {
      id: "session-1",
      title: "Queue test",
      status: "idle",
      messages: [],
      pending_approvals: [],
      metadata: {},
    } as unknown as SessionDetail;
    store.session = session;
    vi.spyOn(api, "sendMessage").mockImplementation(async (_id, content, _mode, _attachments, _context, metadata) => ({
      session: {
        ...session,
        messages: [{
          id: "server-user-1", role: "user", content,
          created_at: "2026-09-29T00:00:00Z",
          metadata: { client_message_id: metadata?.client_message_id, delivery_status: "queued" },
        }],
        metadata: {
          control: { queued_input_count: 1 },
          pending_input_queue: [{ id: "task-1", metadata: { task_status: "queued" }, payload: { content } }],
        },
      },
      events: [],
      output: {
        id: "receipt_task-1", role: "system", content: "任务已接收，执行状态请查看编排轨迹。",
        created_at: "2026-09-29T00:00:00Z", metadata: { persist_transcript: false },
      },
      task: {
        id: "task-1", operation: "message", session_id: "session-1", turn_id: "turn-1",
        request_id: "req-1", trace_id: "trace-1", status: "queued", priority: 50,
        attempt_no: 0, created_at: "2026-09-29T00:00:00Z", updated_at: "2026-09-29T00:00:00Z",
      },
    } satisfies AgentTaskAccepted));

    await store.sendMessage("run a check");

    expect(store.isSending).toBe(false);
    expect(store.watcherPhase).toBe("queued");
    expect(store.isBusy).toBe(true);
    expect(store.messages.filter((message) => message.role === "assistant" && message.metadata?.delivery_status === "streaming"))
      .toHaveLength(1);
    expect(store.messages.find((message) => message.metadata?.is_optimistic_stream)?.metadata?.task_id)
      .toBe("task-1");

    store.applySession({ ...session, status: "running", messages: [], metadata: {} } as SessionDetail);
    expect(store.messages.some((message) => message.metadata?.is_optimistic_stream)).toBe(true);

    store.applySession({
      ...session,
      status: "completed",
      messages: [],
      metadata: {
        control: { queued_input_count: 1 },
        pending_input_queue: [{ id: "task-2", metadata: { task_status: "queued" }, payload: { content: "other task" } }],
      },
    } as SessionDetail);
    expect(store.isBusy).toBe(true);
    expect(store.messages.some((message) => message.role === "assistant" && message.metadata?.is_optimistic_stream))
      .toBe(false);
  });

  it("shows waiting instead of a previous turn's completed state", () => {
    const store = useSessionStore();
    store.session = {
      id: "session-1",
      status: "completed",
      pending_approvals: [],
      metadata: {
        control: { queued_input_count: 1 },
        pending_input_queue: [
          {
            id: "task-1",
            payload: { content: "next task" },
            metadata: { task_status: "queued" },
          },
        ],
      },
    } as unknown as SessionDetail;
    expect(store.queuedTurnCount).toBe(1);
    expect(store.watcherPhase).toBe("queued");
    const wrapper = mount(RuntimeStatusPanel);
    try {
      expect(wrapper.text()).toContain("WAIT");
    } finally {
      wrapper.unmount();
    }
    store.session.metadata = { control: { queued_input_count: 0 }, pending_input_queue: [] };
    expect(store.watcherPhase).toBe("completed");
  });

  it("keeps active execution and approval ahead of queued follow-up inputs", () => {
    const store = useSessionStore();
    store.session = {
      id: "session-1",
      status: "running",
      pending_approvals: [],
      metadata: { control: { queued_input_count: 2 } },
    } as unknown as SessionDetail;
    expect(store.watcherPhase).toBe("running");
    store.session.pending_approvals = [
      { id: "approval-1", status: "pending" },
    ] as SessionDetail["pending_approvals"];
    expect(store.watcherPhase).toBe("waiting_approval");
  });
});
