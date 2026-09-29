import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useSessionStore } from "./session";

describe("session tool execution events", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("binds an optimistic user message from a top-level turn ID", () => {
    const store = useSessionStore();
    store.messages = [{
      id: "pending-user", role: "user", content: "Run tests",
      created_at: "2026-09-29T00:00:00Z",
      metadata: { is_optimistic: true, delivery_status: "pending" },
    }];

    store.applyStreamingEvent({
      id: "turn-started", type: "runtime.turn_started", session_id: "session-1",
      timestamp: "2026-09-29T00:00:01Z", turn_id: "turn-1", payload: {},
    });

    expect(store.messages[0].metadata.turn_id).toBe("turn-1");
  });

  it("finalizes a running tool when the backend skips it after an interrupt", () => {
    const store = useSessionStore();
    store.messages = [
      {
        id: "tool-message-1",
        role: "tool",
        content: JSON.stringify({ status: "running", summary: "Tool execution started." }),
        created_at: "2026-09-23T10:00:00Z",
        metadata: {
          tool_key: "browser.open",
          tool_call_id: "call-1",
          transient_tool_event: true,
          delivery_status: "streaming",
          tool_progress_status: "running",
          tool_progress_finalized: "false",
        },
      },
    ];

    store.applyStreamingEvent({
      id: "event-1",
      type: "tool.execution_skipped",
      session_id: "session-1",
      timestamp: "2026-09-23T10:00:01Z",
      payload: {
        turn_id: "turn-1",
        tool_key: "browser.open",
        interrupt_reason: "user_requested",
      },
    });

    const message = store.messages[0];
    expect(message.metadata.tool_progress_status).toBe("skipped");
    expect(message.metadata.tool_progress_finalized).toBe("true");
    expect(JSON.parse(message.content).status).toBe("skipped");
  });

  it("shows safety gate denial and approval wait from tool execution events", () => {
    const store = useSessionStore();
    const common = {
      session_id: "session-1",
      timestamp: "2026-09-24T10:00:00Z",
    };

    store.applyStreamingEvent({
      ...common,
      id: "event-denied",
      type: "tool.execution_denied",
      payload: {
        turn_id: "turn-1",
        call_id: "call-denied",
        tool_key: "cli-executor",
        message: "Dangerous command denied.",
        permission_reason_code: "safety_gate_denied",
      },
    });
    store.applyStreamingEvent({
      ...common,
      id: "event-approval",
      type: "tool.execution_blocked",
      payload: {
        turn_id: "turn-1",
        call_id: "call-approval",
        tool_key: "mock-target-runner",
        message: "Waiting for approval.",
        permission_source: "safety_gate",
        permission_reason_code: "safety_gate_approval_required",
      },
    });

    const messages = store.messages.filter((message) => message.role === "tool");
    expect(messages.map((message) => JSON.parse(message.content).status)).toEqual([
      "denied",
      "waiting_approval",
    ]);
    expect(messages.map((message) => message.metadata.tool_progress_status)).toEqual([
      "denied",
      "waiting_approval",
    ]);
  });
});
