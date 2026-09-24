import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useSessionStore } from "./session";

describe("session tool execution events", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
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
});
