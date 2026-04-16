import { defineStore } from "pinia";

import { api } from "../services/api";
import type {
  AgentDescriptor,
  ChatMessage,
  ExecutionEvent,
  SessionDetail,
  SessionWatcherPhase,
  ToolApprovalRequest,
  ToolDescriptor,
  WorkerDispatchRecord,
  WorkerFailureGuard,
} from "../types";

function messageDeliveryStatus(message: ChatMessage | undefined) {
  return String(message?.metadata?.delivery_status || "").trim();
}

function mergeSessionMessages(serverMessages: ChatMessage[], localMessages: ChatMessage[]) {
  const serverIds = new Set(serverMessages.map((message) => message.id));
  const localById = new Map(localMessages.map((message) => [message.id, message]));

  const merged = serverMessages.map((serverMessage) => {
    const localMessage = localById.get(serverMessage.id);
    if (!localMessage) {
      return serverMessage;
    }

    const localStatus = messageDeliveryStatus(localMessage);
    const serverStatus = messageDeliveryStatus(serverMessage);
    const shouldPreferLocalContent =
      serverMessage.role === "assistant" &&
      (localStatus === "streaming" || localStatus === "completed") &&
      localMessage.content.length >= serverMessage.content.length &&
      serverStatus !== "completed";

    if (!shouldPreferLocalContent) {
      return serverMessage;
    }

    return {
      ...serverMessage,
      content: localMessage.content,
      metadata: {
        ...serverMessage.metadata,
        ...localMessage.metadata,
        delivery_status: localStatus,
      },
    };
  });

  const localOnlyMessages = localMessages.filter((message) => {
    if (serverIds.has(message.id)) {
      return false;
    }
    if (message.role !== "assistant") {
      return false;
    }
    const status = messageDeliveryStatus(message);
    return status === "streaming" || status === "completed";
  });

  return [...merged, ...localOnlyMessages];
}

export const useSessionStore = defineStore("session", {
  state: () => ({
    session: null as SessionDetail | null,
    messages: [] as ChatMessage[],
    activity: [] as ExecutionEvent[],
    agents: [] as AgentDescriptor[],
    tools: [] as ToolDescriptor[],
    selectedAgentKey: "coordinator",
    isBootstrapping: false,
    isSending: false,
    resolvingApprovalIds: [] as string[],
    refreshTimer: null as number | null,
    refreshInFlight: false,
    watcherFailures: 0,
    watcherError: "",
    watcherLastSyncAt: "",
    error: "",
    eventSource: null as EventSource | null,
  }),
  getters: {
    activeAgent(state) {
      return state.agents.find((item) => item.key === state.selectedAgentKey) ?? null;
    },
    isAssistantStreaming(state) {
      return state.messages.some(
        (message) =>
          message.role === "assistant" &&
          String(message.metadata?.delivery_status || "").trim() === "streaming",
      );
    },
    isBusy(state): boolean {
      return (
        state.isSending ||
        state.session?.status === "running" ||
        state.session?.status === "waiting_approval" ||
        state.messages.some(
          (message) =>
            message.role === "assistant" &&
            String(message.metadata?.delivery_status || "").trim() === "streaming",
        )
      );
    },
    pendingApprovals(state): ToolApprovalRequest[] {
      return state.session?.pending_approvals ?? [];
    },
    isResolvingApproval(state) {
      return (approvalId: string) => state.resolvingApprovalIds.includes(approvalId);
    },
    workerDispatches(state): WorkerDispatchRecord[] {
      const value = state.session?.metadata?.worker_dispatches;
      if (!Array.isArray(value)) {
        return [];
      }
      return value.filter((item): item is WorkerDispatchRecord => typeof item === "object" && item !== null) as WorkerDispatchRecord[];
    },
    workerFailureGuard(state): WorkerFailureGuard | null {
      const value = state.session?.metadata?.worker_failure_guard;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      return value as WorkerFailureGuard;
    },
    watcherPhase(): SessionWatcherPhase {
      if (!this.session) return "idle";
      if (this.pendingApprovals.length > 0) return "waiting_approval";
      if (this.workerFailureGuard?.blocked || this.session.status === "failed") return "failed";
      if (
        this.session.status === "running" ||
        this.workerDispatches.some((item) => item.status === "running")
      ) {
        return "running";
      }
      if (this.session.status === "completed") return "completed";
      return "idle";
    },
    watcherLastSyncLabel(state): string {
      if (!state.watcherLastSyncAt) {
        return "not synced";
      }
      return new Date(state.watcherLastSyncAt).toLocaleTimeString("zh-CN", { hour12: false });
    },
  },
  actions: {
    async bootstrap() {
      if (this.isBootstrapping) {
        return;
      }

      this.isBootstrapping = true;
      this.error = "";
      try {
        const [agents, tools] = await Promise.all([
          api.listAgents(),
          api.listTools(),
        ]);
        this.agents = agents;
        this.tools = tools;
        this.selectedAgentKey = agents[0]?.key ?? "coordinator";

        if (!this.session) {
          const session = await api.createSession(
            "Enterprise Intelligent QA Session",
            this.selectedAgentKey,
          );
          this.applySession(session);
          this.connectEvents();
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Initialization failed.";
      } finally {
        this.isBootstrapping = false;
      }
    },
    applySession(session: SessionDetail) {
      const mergedMessages = mergeSessionMessages(session.messages, this.messages);
      this.session = session;
      this.messages = mergedMessages;
      this.selectedAgentKey = session.selected_agent ?? this.selectedAgentKey;
    },
    connectEvents() {
      if (!this.session) {
        return;
      }
      this.eventSource?.close();
      this.eventSource = api.connectEvents(this.session.id, (event) => {
        this.activity = [event, ...this.activity].slice(0, 50);
        this.applyStreamingEvent(event);
        if (
          event.type === "approval.created" ||
          event.type === "approval.resolved" ||
          event.type === "worker.task_notification_received" ||
          event.type === "worker.auto_stopped" ||
          event.type === "turn.completed" ||
          event.type === "turn.failed"
        ) {
          void this.refreshSession();
        }
      });
      this.eventSource.onerror = () => {
        this.error = "Event stream disconnected. Refresh the session to reconnect.";
      };
    },
    async refreshSession() {
      if (!this.session || this.refreshInFlight) {
        return;
      }
      this.refreshInFlight = true;
      try {
        const detail = await api.getSession(this.session.id);
        this.applySession(detail);
        this.watcherFailures = 0;
        this.watcherError = "";
        this.watcherLastSyncAt = new Date().toISOString();
      } catch (error) {
        this.watcherFailures += 1;
        this.watcherError = error instanceof Error ? error.message : "Failed to refresh session.";
      } finally {
        this.refreshInFlight = false;
      }
    },
    startWatcher() {
      if (this.refreshTimer !== null) {
        return;
      }
      void this.refreshSession();
      this.refreshTimer = window.setInterval(() => {
        void this.refreshSession();
      }, 3000);
    },
    stopWatcher() {
      if (this.refreshTimer !== null) {
        window.clearInterval(this.refreshTimer);
        this.refreshTimer = null;
      }
    },
    async sendMessage(content: string) {
      if (!this.session || !content.trim()) {
        return;
      }

      const trimmedContent = content.trim();
      const optimisticMessage: ChatMessage = {
        id: `temp-user-${Date.now()}`,
        role: "user",
        content: trimmedContent,
        created_at: new Date().toISOString(),
        metadata: {
          delivery_status: "pending",
        },
      };

      this.messages = [...this.messages, optimisticMessage];
      this.isSending = true;
      this.error = "";
      try {
        const response = await api.sendMessage(
          this.session.id,
          trimmedContent,
          this.selectedAgentKey,
        );
        this.applySession(response.session);
        this.activity = [...response.events.slice().reverse(), ...this.activity].slice(0, 50);
      } catch (error) {
        this.messages = this.messages.map((message) =>
          message.id === optimisticMessage.id
            ? {
                ...message,
                metadata: {
                  ...message.metadata,
                  delivery_status: "failed",
                },
              }
            : message,
        );
        this.error = error instanceof Error ? error.message : "Failed to send message.";
      } finally {
        this.isSending = false;
      }
    },
    async resolveApproval(
      approvalId: string,
      decision: "approved" | "denied",
      reason?: string,
    ) {
      if (!this.session || this.resolvingApprovalIds.includes(approvalId)) {
        return;
      }

      this.resolvingApprovalIds = [...this.resolvingApprovalIds, approvalId];
      this.error = "";
      try {
        await api.resolveApproval(this.session.id, approvalId, decision, reason);
        await this.refreshSession();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Failed to resolve approval.";
      } finally {
        this.resolvingApprovalIds = this.resolvingApprovalIds.filter((item) => item !== approvalId);
      }
    },
    applyStreamingEvent(event: ExecutionEvent) {
      const messageId = String(event.payload?.message_id || "").trim();
      if (!messageId) {
        return;
      }

      if (event.type === "assistant.stream.started") {
        const exists = this.messages.some((message) => message.id === messageId);
        if (!exists) {
          this.messages = [
            ...this.messages,
            {
              id: messageId,
              role: "assistant",
              content: "",
              created_at: event.timestamp,
              metadata: {
                turn_id: String(event.payload?.turn_id || ""),
                delivery_status: "streaming",
              },
            },
          ];
        }
        return;
      }

      if (event.type === "assistant.stream.delta") {
        const delta = String(event.payload?.delta || "");
        if (!delta) {
          return;
        }
        this.messages = this.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                content: `${message.content}${delta}`,
                metadata: {
                  ...message.metadata,
                  delivery_status: "streaming",
                },
              }
            : message,
        );
        return;
      }

      if (event.type === "assistant.stream.completed") {
        this.messages = this.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                metadata: {
                  ...message.metadata,
                  delivery_status: "completed",
                },
              }
            : message,
        );
      }
    },
  },
});
