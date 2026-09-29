// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../services/api";
import type { AgentTaskAccepted, ExecutionEvent, SessionDetail } from "../types";
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

  close() { this.readyState = 2; }

  emit(event: ExecutionEvent) {
    this.onmessage?.({ data: JSON.stringify(event), lastEventId: event.id } as MessageEvent<string>);
  }
}

function acceptedReceipt(event: ExecutionEvent): AgentTaskAccepted {
  const createdAt = "2026-09-29T00:00:00Z";
  const session = {
    id: "session-1", title: "Receipt test", status: "idle", messages: [],
    pending_approvals: [], metadata: {},
  } as unknown as SessionDetail;
  return {
    session,
    events: [event],
    output: {
      id: "receipt_task-1", role: "system", content: "任务已接收，执行状态请查看编排轨迹。",
      created_at: createdAt, metadata: { persist_transcript: false },
    },
    task: {
      id: "task-1", operation: "message", session_id: "session-1", turn_id: "turn-1",
      request_id: "req-1", trace_id: "trace-1", status: "queued", priority: 50,
      attempt_no: 0, created_at: createdAt, updated_at: createdAt,
    },
  };
}

describe("HTTP receipt and SSE event deduplication", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    useSessionStore().disconnectEvents();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it.each(["receipt-first", "sse-first"])("shows one durable event when %s", async (order) => {
    const store = useSessionStore();
    const event: ExecutionEvent = {
      id: `session-created-${order}`, type: "session.created", session_id: "session-1",
      timestamp: "2026-09-29T00:00:00Z", payload: {},
    };
    const receipt = acceptedReceipt(event);
    store.session = receipt.session;
    store.connectEvents();
    let resolveReceipt: (value: AgentTaskAccepted) => void = () => {};
    vi.spyOn(api, "sendMessage").mockImplementation(() => new Promise((resolve) => {
      resolveReceipt = resolve;
    }));

    const submitted = store.sendMessage("run a check");
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/api/v1/sessions/session-1/events");
    expect(source.onmessage).toBeTypeOf("function");
    if (order === "receipt-first") {
      resolveReceipt(receipt);
      await submitted;
      expect(store.activity.filter((item) => item.id === event.id)).toHaveLength(1);
      source.emit(event);
    } else {
      source.emit(event);
      expect(store.activity.filter((item) => item.id === event.id)).toHaveLength(1);
      resolveReceipt(receipt);
      await submitted;
    }

    expect(store.activity.filter((item) => item.id === event.id)).toHaveLength(1);
  });

  it("deduplicates events in a manual resume receipt", async () => {
    const store = useSessionStore();
    const event: ExecutionEvent = {
      id: "session-created-before-resume", type: "session.created", session_id: "session-1",
      timestamp: "2026-09-29T00:00:00Z", payload: {},
    };
    const receipt = acceptedReceipt(event);
    store.session = { ...receipt.session, is_resumable: true };
    store.connectEvents();
    vi.spyOn(api, "resumeSession").mockResolvedValue(receipt);

    await store.resumeCurrentSession();
    FakeEventSource.instances[0].emit(event);

    expect(store.activity.filter((item) => item.id === event.id)).toHaveLength(1);
  });
});
