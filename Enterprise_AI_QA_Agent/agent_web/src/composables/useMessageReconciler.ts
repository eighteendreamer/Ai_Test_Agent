import type { ChatMessage, ExecutionEvent } from "../types";

export function hasStreamingAssistantMessage(messages: ChatMessage[]): boolean {
  return messages.some(
    (message) =>
      message.role === "assistant" &&
      String(message.metadata?.delivery_status || "").trim() === "streaming",
  );
}

export function mergeMessages(
  serverMessages: ChatMessage[],
  localMessages: ChatMessage[],
  sessionStatus: string,
): ChatMessage[] {
  const serverIds = new Set(serverMessages.map((m) => m.id));
  const serverClientMessageIds = new Set(
    serverMessages
      .map((m) => String(m.metadata?.client_message_id || "").trim())
      .filter(Boolean),
  );
  const serverToolCallIds = new Set(
    serverMessages
      .map((m) => String(m.metadata?.tool_call_id || "").trim())
      .filter(Boolean),
  );
  const serverApprovalIds = new Set(
    serverMessages
      .map((m) => String(m.metadata?.approval_id || "").trim())
      .filter(Boolean),
  );
  const serverToolKeys = new Set(
    serverMessages
      .filter((m) => m.role === "tool")
      .map((m) => String(m.metadata?.tool_key || "").trim())
      .filter(Boolean),
  );

  const localById = new Map(localMessages.map((m) => [m.id, m]));
  const localByClientMessageId = new Map(
    localMessages
      .filter((m) => String(m.metadata?.client_message_id || "").trim())
      .map((m) => [String(m.metadata?.client_message_id), m]),
  );

  const isActiveSessionStatus = (status: string) =>
    status === "running" || status === "waiting_approval" || status === "idle";

  const allowTransientLocalMessages = isActiveSessionStatus(sessionStatus);

  const merged = serverMessages.map((serverMessage) => {
    const localMatch = localById.get(serverMessage.id);
    if (localMatch) return { ...serverMessage, metadata: { ...localMatch.metadata, ...serverMessage.metadata } };

    const clientMessageId = String(serverMessage.metadata?.client_message_id || "").trim();
    if (clientMessageId) {
      const localByCmid = localByClientMessageId.get(clientMessageId);
      if (localByCmid) return { ...serverMessage, metadata: { ...localByCmid.metadata, ...serverMessage.metadata } };
    }

    return serverMessage;
  });

  const localOnlyMessages = localMessages.filter((localMessage) => {
    if (serverIds.has(localMessage.id)) return false;

    const clientMessageId = String(localMessage.metadata?.client_message_id || "").trim();
    if (clientMessageId && serverClientMessageIds.has(clientMessageId)) return false;

    if (localMessage.role === "assistant") {
      const toolCallId = String(localMessage.metadata?.tool_call_id || "").trim();
      if (toolCallId && serverToolCallIds.has(toolCallId)) return false;
    }

    if (localMessage.role === "tool") {
      const approvalId = String(localMessage.metadata?.approval_id || "").trim();
      if (approvalId && serverApprovalIds.has(approvalId)) return false;
      const toolKey = String(localMessage.metadata?.tool_key || "").trim();
      if (toolKey && serverToolKeys.has(toolKey)) return false;
    }

    if (!allowTransientLocalMessages) return false;

    return true;
  });

  return [...merged, ...localOnlyMessages];
}

export function applyEventToMessages(
  messages: ChatMessage[],
  event: ExecutionEvent,
): ChatMessage[] {
  const eventType = event.type;

  if (eventType === "model.response_chunk") {
    const turnId = String(event.payload?.turn_id || "").trim();
    if (!turnId) return messages;

    const chunk = String(event.payload?.delta || "");
    const existingIdx = messages.findIndex(
      (m) => m.role === "assistant" && String(m.metadata?.turn_id || "") === turnId,
    );

    if (existingIdx >= 0) {
      const existing = messages[existingIdx];
      const updated = {
        ...existing,
        content: String(existing.content || "") + chunk,
        metadata: { ...existing.metadata, delivery_status: "streaming" },
      };
      return [...messages.slice(0, existingIdx), updated, ...messages.slice(existingIdx + 1)];
    }

    const newMessage: ChatMessage = {
      id: `streaming-${turnId}`,
      role: "assistant",
      content: chunk,
      metadata: { turn_id: turnId, delivery_status: "streaming" },
    } as ChatMessage;
    return [...messages, newMessage];
  }

  if (eventType === "turn.completed" || eventType === "turn.failed") {
    return messages.map((m) => {
      if (m.role === "assistant" && String(m.metadata?.delivery_status || "") === "streaming") {
        return { ...m, metadata: { ...m.metadata, delivery_status: "delivered" } };
      }
      return m;
    });
  }

  return messages;
}
