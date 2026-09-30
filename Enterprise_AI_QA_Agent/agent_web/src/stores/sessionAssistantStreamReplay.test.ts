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
