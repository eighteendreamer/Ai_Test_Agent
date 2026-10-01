// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { flushPromises, mount } from "@vue/test-utils";

import ChatTimeline from "../components/chat/ChatTimeline.vue";
import { api } from "../services/api";
import { useSessionStore } from "./session";
import type { ChatMessage, ExecutionEvent, SessionDetail } from "../types";

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

  emit(event: ExecutionEvent) {
    this.onmessage?.({
      data: JSON.stringify(event),
      lastEventId: event.id,
    } as MessageEvent<string>);
  }
}

function assistantEvent(
  id: string,
  type: string,
  payload: Record<string, unknown>,
  sessionId = "session-stream-replay",
  turnId = "turn-stream-replay",
): ExecutionEvent {
  return {
    id,
    type,
    session_id: sessionId,
    timestamp: "2026-09-30T08:00:00Z",
    turn_id: turnId,
    payload: { turn_id: turnId, ...payload },
  };
}

function sessionDetail(
  id: string,
  status: string,
  messages: ChatMessage[],
): SessionDetail {
  return {
    id,
    title: "Assistant stream test",
    status,
    messages,
    pending_approvals: [],
    metadata: {},
  } as unknown as SessionDetail;
}

const mountedWrappers: ReturnType<typeof mount>[] = [];

describe("assistant SSE replay after loading a persisted transcript", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      return window.setTimeout(() => callback(0), 0);
    });
    vi.stubGlobal("cancelAnimationFrame", (timer: number) => window.clearTimeout(timer));
  });

  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
    useSessionStore().stopWatcher();
    useSessionStore().disconnectEvents();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("does not append historical deltas to an already loaded completed answer", () => {
    const store = useSessionStore();
    const persistedMessage = {
      id: "assistant-message-1",
      role: "assistant",
      content: "已经持久化的完整答案。",
      created_at: "2026-09-30T08:00:00Z",
      metadata: {
        turn_id: "turn-stream-replay",
        response_mode: "ok",
        transcript_bucket: "conversation",
      },
    };
    store.session = {
      id: "session-stream-replay",
      title: "SSE replay",
      status: "completed",
      messages: [persistedMessage],
      pending_approvals: [],
      metadata: {},
    } as unknown as SessionDetail;
    store.messages = [persistedMessage] as unknown as SessionDetail["messages"];

    store.connectEvents();
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/api/v1/sessions/session-stream-replay/events");

    source.emit(assistantEvent("event-started", "assistant.stream.started", {
      message_id: "assistant-message-1",
    }));
    source.emit(assistantEvent("event-delta-1", "assistant.stream.delta", {
      message_id: "assistant-message-1",
      delta: "已经持久化的",
    }));
    source.emit(assistantEvent("event-delta-2", "assistant.stream.delta", {
      message_id: "assistant-message-1",
      delta: "完整答案。",
    }));
    source.emit(assistantEvent("event-completed", "assistant.stream.completed", {
      message_id: "assistant-message-1",
      response_length: "已经持久化的完整答案。".length,
    }));

    expect(store.messages.filter((message) => message.role === "assistant")).toHaveLength(1);
    expect(store.messages.find((message) => message.id === "assistant-message-1")?.content)
      .toBe("已经持久化的完整答案。");
  });

  it("coalesces a burst of real SSE callback deltas while preserving the first and final text", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const source = FakeEventSource.instances[0];
    const apply = vi.spyOn(store, "applyStreamingEvent");
    source.emit(assistantEvent("burst-start", "assistant.stream.started", { message_id: "burst-answer" }));
    apply.mockClear();
    for (let index = 0; index < 200; index += 1) {
      source.emit(assistantEvent(`burst-${index}`, "assistant.stream.delta", {
        message_id: "burst-answer", delta: `${index},`,
      }));
    }
    expect(store.messages[0].content).toBe("0,");
    expect(apply).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(40);
    expect(apply).toHaveBeenCalledTimes(2);
    source.emit(assistantEvent("last-delta", "assistant.stream.delta", { message_id: "burst-answer", delta: "end" }));
    source.emit(assistantEvent("burst-completed", "assistant.stream.completed", { message_id: "burst-answer" }));
    expect(store.messages[0].content).toBe(Array.from({ length: 200 }, (_, i) => `${i},`).join("") + "end");
    expect(store.messages[0].metadata.delivery_status).toBe("completed");
    expect(store.activity[0].id).toBe("burst-completed");
    expect(store.activity[1].id).toBe("last-delta");
    expect(store.activity).toHaveLength(50);
  });

  it("flushes interleaved answers and turns without joining their text", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const source = FakeEventSource.instances[0];
    const emit = (id: string, messageId: string, delta: string, turnId = "turn-stream-replay") =>
      source.emit(assistantEvent(id, "assistant.stream.delta", { message_id: messageId, delta }, "session-stream-replay", turnId));
    emit("a-first", "answer-a", "A");
    emit("a-pending", "answer-a", "1");
    emit("b-first", "answer-b", "B");
    expect(store.messagesById.get("answer-a")?.content).toBe("A1");
    emit("b-pending", "answer-b", "2");
    emit("a-next", "answer-a", "3");
    emit("a-new-turn", "answer-a", "4", "another-turn");
    expect(store.messagesById.get("answer-a")?.content).toBe("A13");
    expect(store.messagesById.get("answer-b")?.content).toBe("B2");
    await vi.advanceTimersByTimeAsync(40);
    expect(store.messagesById.get("answer-a")?.content).toBe("A134");
    expect(store.activity.slice(0, 3).map((event) => event.id)).toEqual(["a-new-turn", "a-next", "b-pending"]);
  });

  it("keeps the message list and unrelated messages stable when reducing a delta", () => {
    const store = useSessionStore();
    store.messages = [{ id: "history", role: "user", content: "history", created_at: "2026-09-30T08:00:00Z", metadata: {} }];
    store.applyStreamingEvent(assistantEvent("stable-start", "assistant.stream.started", { message_id: "stable-answer" }));
    const list = store.messages;
    const history = store.messages[0];
    store.applyStreamingEvent(assistantEvent("stable-delta", "assistant.stream.delta", { message_id: "stable-answer", delta: "new" }));
    expect(store.messages).toBe(list);
    expect(store.messages[0]).toBe(history);
    expect(store.messages[1].content).toBe("new");
  });

  it("flushes bounded buffers even when the browser does not run its timer", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const source = FakeEventSource.instances[0];
    source.emit(assistantEvent("bounded-start", "assistant.stream.started", { message_id: "bounded-answer" }));
    for (let i = 0; i < 1000; i += 1) {
      source.emit(assistantEvent(`bounded-${i}`, "assistant.stream.delta", { message_id: "bounded-answer", delta: "x" }));
    }
    expect(store.messages[0].content.length).toBe(769);
    source.emit(assistantEvent("large-delta", "assistant.stream.delta", { message_id: "bounded-answer", delta: "y".repeat(20000) }));
    expect(store.messages[0].content).toBe("x".repeat(1000) + "y".repeat(20000));
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(100);
    expect(store.messages[0].content.length).toBe(21000);
  });

  it("discards an old session buffer before a different session is displayed", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const oldSource = FakeEventSource.instances[0];
    oldSource.emit(assistantEvent("switch-start", "assistant.stream.started", { message_id: "switch-answer" }));
    oldSource.emit(assistantEvent("switch-first", "assistant.stream.delta", { message_id: "switch-answer", delta: "old" }));
    oldSource.emit(assistantEvent("switch-pending", "assistant.stream.delta", { message_id: "switch-answer", delta: "pending" }));
    store.applySession(sessionDetail("session-next", "completed", []));
    store.connectEvents();
    await vi.advanceTimersByTimeAsync(100);
    expect(store.messages).toEqual([]);
    expect(store.activity.some((event) => event.id === "switch-pending")).toBe(false);
  });

  it("flushes received text on disconnect and deduplicates it after reconnect", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const source = FakeEventSource.instances[0];
    source.emit(assistantEvent("error-start", "assistant.stream.started", { message_id: "error-answer" }));
    source.emit(assistantEvent("error-first", "assistant.stream.delta", { message_id: "error-answer", delta: "first" }));
    const pending = assistantEvent("error-pending", "assistant.stream.delta", { message_id: "error-answer", delta: "pending" });
    source.emit(pending);
    source.onerror?.();
    expect(store.messages[0].content).toBe("firstpending");
    await vi.advanceTimersByTimeAsync(1500);
    const reconnected = FakeEventSource.instances[1];
    expect(reconnected.url).toContain("last_event_id=error-pending");
    reconnected.emit(pending);
    reconnected.emit(assistantEvent("error-final", "assistant.stream.completed", { message_id: "error-answer" }));
    expect(store.messages[0].content).toBe("firstpending");
    expect(store.messages[0].metadata.delivery_status).toBe("completed");
  });

  it("uses an authoritative final snapshot even with a pending delta timer", async () => {
    const store = useSessionStore();
    store.session = sessionDetail("session-stream-replay", "running", []);
    store.connectEvents();
    const source = FakeEventSource.instances[0];
    source.emit(assistantEvent("snapshot-start", "assistant.stream.started", { message_id: "snapshot-answer" }));
    source.emit(assistantEvent("snapshot-first", "assistant.stream.delta", { message_id: "snapshot-answer", delta: "prefix" }));
    source.emit(assistantEvent("snapshot-pending", "assistant.stream.delta", { message_id: "snapshot-answer", delta: "pending" }));
    store.applySession(sessionDetail("session-stream-replay", "completed", [{
      id: "snapshot-answer", role: "assistant", content: "authoritative final answer",
      created_at: "2026-09-30T08:00:00Z", metadata: { response_mode: "ok", delivery_status: "completed" },
    }]));
    await vi.advanceTimersByTimeAsync(100);
    expect(store.messages[0].content).toBe("authoritative final answer");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("continues appending deltas when existing assistant text has no final response marker", () => {
    const store = useSessionStore();
    const partialMessage = {
      id: "assistant-message-partial",
      role: "assistant",
      content: "已有分片",
      created_at: "2026-09-30T08:00:00Z",
      metadata: { turn_id: "turn-stream-replay" },
    };
    store.session = {
      id: "session-stream-replay",
      title: "SSE replay",
      status: "running",
      messages: [partialMessage],
      pending_approvals: [],
      metadata: {},
    } as unknown as SessionDetail;
    store.messages = [partialMessage] as unknown as SessionDetail["messages"];

    store.connectEvents();
    const source = FakeEventSource.instances[0];
    source.emit(assistantEvent("partial-start", "assistant.stream.started", {
      message_id: "assistant-message-partial",
    }));
    source.emit(assistantEvent("partial-delta", "assistant.stream.delta", {
      message_id: "assistant-message-partial",
      delta: "仍在生成",
    }));
    store.disconnectEvents();

    const updatedMessage = store.messages.find((message) => message.id === "assistant-message-partial");
    expect(updatedMessage?.content).toBe("已有分片仍在生成");
    expect(updatedMessage?.metadata?.delivery_status).toBe("streaming");
  });

  it("renders live deltas once and replaces them with the final server transcript", async () => {
    const store = useSessionStore();
    const sessionId = "session-stream-live";
    const turnId = "turn-stream-live";
    const assistantMessageId = "assistant-message-live";
    const userMessage: ChatMessage = {
      id: "user-message-live",
      role: "user",
      content: "请给出流式测试答案。",
      created_at: "2026-09-30T08:00:00Z",
      metadata: { turn_id: turnId },
    };
    const finalAssistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: "assistant",
      content: "后端持久化的最终答案。",
      created_at: "2026-09-30T08:00:01Z",
      metadata: {
        turn_id: turnId,
        response_mode: "ok",
        transcript_bucket: "conversation",
      },
    };
    const initialSession = sessionDetail(sessionId, "running", [userMessage]);
    const finalSession = sessionDetail(sessionId, "completed", [userMessage, finalAssistantMessage]);
    store.session = initialSession;
    store.messages = [userMessage];
    const getSession = vi.spyOn(api, "getSession").mockResolvedValue(finalSession);
    vi.spyOn(store, "refreshToolingData").mockResolvedValue();

    const Host = defineComponent({
      setup: () => () => h(ChatTimeline, { messages: store.messages, activity: store.activity }),
    });
    const wrapper = mount(Host);
    mountedWrappers.push(wrapper);

    store.connectEvents();
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain(`/api/v1/sessions/${sessionId}/events`);
    source.emit(assistantEvent("live-start", "assistant.stream.started", {
      message_id: assistantMessageId,
    }, sessionId, turnId));
    source.emit(assistantEvent("live-delta-1", "assistant.stream.delta", {
      message_id: assistantMessageId,
      delta: "模型分片",
    }, sessionId, turnId));
    await nextTick();
    expect(wrapper.text()).toContain("模型分片");

    const secondDelta = assistantEvent("live-delta-2", "assistant.stream.delta", {
      message_id: assistantMessageId,
      delta: "实时显示。",
    }, sessionId, turnId);
    source.emit(secondDelta);
    source.emit(secondDelta);
    await vi.advanceTimersByTimeAsync(40);
    await nextTick();
    expect(store.messages.find((message) => message.id === assistantMessageId)?.content)
      .toBe("模型分片实时显示。");

    source.emit(assistantEvent("live-stream-completed", "assistant.stream.completed", {
      message_id: assistantMessageId,
      response_length: "模型分片实时显示。".length,
    }, sessionId, turnId));
    source.emit(assistantEvent("live-turn-completed", "runtime.turn_completed", {}, sessionId, turnId));
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    await nextTick();

    expect(getSession).toHaveBeenCalledWith(sessionId);
    expect(store.messages.find((message) => message.id === assistantMessageId)?.content)
      .toBe("后端持久化的最终答案。");
    expect(wrapper.text()).toContain("后端持久化的最终答案。");
  });
});
