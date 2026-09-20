// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";
import { useSessionStore } from "./session";
import RuntimeStatusPanel from "../components/chat/RuntimeStatusPanel.vue";
import type { SessionDetail } from "../types";

describe("durable Agent input queue", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
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
