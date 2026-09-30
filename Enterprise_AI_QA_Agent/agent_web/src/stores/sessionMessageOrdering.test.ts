// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import type { ChatMessage, SessionDetail } from "../types";
import { useSessionStore } from "./session";

describe("session message reconciliation order", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("inserts an optimistic user message before later server messages by timestamp", () => {
    const store = useSessionStore();
    const optimisticMessage: ChatMessage = {
      id: "local-user",
      role: "user",
      content: "Run the check",
      created_at: "2026-09-30T10:00:01Z",
      metadata: { delivery_status: "sending" },
    };
    store.messages = [optimisticMessage];

    store.applySession({
      id: "session-1",
      status: "running",
      messages: [
        {
          id: "server-history",
          role: "user",
          content: "Earlier request",
          created_at: "2026-09-30T10:00:00Z",
          metadata: {},
        },
        {
          id: "server-response",
          role: "assistant",
          content: "Later response",
          created_at: "2026-09-30T10:00:02Z",
          metadata: {},
        },
      ],
      pending_approvals: [],
      metadata: {},
    } as unknown as SessionDetail);

    expect(store.messages.map((message) => message.id)).toEqual([
      "server-history",
      "local-user",
      "server-response",
    ]);
  });

  it("keeps the optimistic assistant after its persisted user when server time is later", () => {
    const store = useSessionStore();
    const clientMessageId = "client-message-1";
    store.messages = [
      {
        id: "local-user",
        role: "user",
        content: "Run the check",
        created_at: "2026-09-30T10:00:00Z",
        metadata: { delivery_status: "pending", client_message_id: clientMessageId },
      },
      {
        id: "local-assistant",
        role: "assistant",
        content: "",
        created_at: "2026-09-30T10:00:00.001Z",
        metadata: {
          delivery_status: "streaming",
          is_optimistic_stream: true,
          after_client_message_id: clientMessageId,
        },
      },
    ];

    store.applySession({
      id: "session-1",
      status: "running",
      messages: [
        {
          id: "server-user",
          role: "user",
          content: "Run the check",
          created_at: "2026-09-30T10:00:01Z",
          metadata: { client_message_id: clientMessageId },
        },
      ],
      pending_approvals: [],
      metadata: {},
    } as unknown as SessionDetail);

    expect(store.messages.map((message) => message.id)).toEqual([
      "server-user",
      "local-assistant",
    ]);
  });
});
