import { defineStore } from "pinia";

import { api } from "../services/api";
import type {
  AgentDescriptor,
  ChatMessage,
  ExecutionEvent,
  SessionDetail,
  SessionReplayResponse,
  SessionVerificationResponse,
  SessionWatcherPhase,
  ToolArtifactRecord,
  ToolApprovalRequest,
  ToolDescriptor,
  ToolExecutionSummary,
  ToolJobDetail,
  ToolJobRecord,
  WorkerDispatchRecord,
  WorkerFailureGuard,
} from "../types";

type SessionLifecycleStatus = SessionDetail["status"] | "";

function messageDeliveryStatus(message: ChatMessage | undefined) {
  return String(message?.metadata?.delivery_status || "").trim();
}

function createOptimisticAssistantMessage() {
  return {
    id: `temp-assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: "assistant" as const,
    content: "",
    created_at: new Date().toISOString(),
    metadata: {
      delivery_status: "streaming",
      is_optimistic_stream: true,
    },
  };
}

function mergeSessionMessages(
  serverMessages: ChatMessage[],
  localMessages: ChatMessage[],
  sessionStatus: SessionLifecycleStatus,
) {
  const serverIds = new Set(serverMessages.map((message) => message.id));
  const localById = new Map(localMessages.map((message) => [message.id, message]));
  const allowTransientLocalMessages =
    sessionStatus === "running" || sessionStatus === "waiting_approval" || sessionStatus === "interrupted";

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
    if (!allowTransientLocalMessages) {
      return false;
    }
    if (serverIds.has(message.id)) {
      return false;
    }
    if (message.role !== "assistant") {
      return false;
    }
    const status = messageDeliveryStatus(message);
    if (status !== "streaming") {
      return false;
    }
    return Boolean(message.content.trim()) || Boolean(message.metadata?.is_optimistic_stream);
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
    replayTimeline: [] as ExecutionEvent[],
    replayMeta: null as SessionReplayResponse | null,
    verificationMeta: null as SessionVerificationResponse | null,
    toolJobs: [] as ToolJobRecord[],
    sessionArtifacts: [] as ToolArtifactRecord[],
    selectedToolJob: null as ToolJobDetail | null,
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
    latestSnapshotGraphState(state): Record<string, unknown> {
      const value = state.session?.last_snapshot?.graph_state;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return {};
      }
      return value as Record<string, unknown>;
    },
    latestToolResults(): ToolExecutionSummary[] {
      const value = this.latestSnapshotGraphState.tool_results;
      if (!Array.isArray(value)) {
        return [];
      }
      return value.filter((item): item is ToolExecutionSummary => typeof item === "object" && item !== null) as ToolExecutionSummary[];
    },
    latestTraceId(): string {
      const value = this.latestSnapshotGraphState.trace_id;
      return typeof value === "string" ? value : "";
    },
    latestTurnId(): string {
      const value = this.latestSnapshotGraphState.turn_id;
      return typeof value === "string" ? value : "";
    },
    recentToolJobs(state): ToolJobRecord[] {
      return state.toolJobs.slice().sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)).slice(0, 8);
    },
    watcherPhase(): SessionWatcherPhase {
      if (!this.session) return "idle";
      if (this.pendingApprovals.length > 0) return "waiting_approval";
      if (this.session.is_interrupted || this.session.status === "interrupted") return "interrupted";
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
        return "未同步";
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
          await this.refreshToolingData();
          this.connectEvents();
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "初始化失败。";
      } finally {
        this.isBootstrapping = false;
      }
    },
    applySession(session: SessionDetail) {
      const mergedMessages = mergeSessionMessages(
        session.messages,
        this.messages,
        session.status,
      );
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
          event.type === "tool.execution_started" ||
          event.type === "tool.execution_completed" ||
          event.type === "tool.execution_failed" ||
          event.type === "tool.execution_denied" ||
          event.type === "verification.completed" ||
          event.type === "worker.task_notification_received" ||
          event.type === "worker.auto_stopped" ||
          event.type === "runtime.interrupt_requested" ||
          event.type === "turn.interrupted" ||
          event.type === "turn.resumed" ||
          event.type === "turn.completed" ||
          event.type === "turn.failed"
        ) {
          void this.refreshSession();
        }
      });
      this.eventSource.onerror = () => {
        this.error = "事件流已断开，请刷新会话后重新连接。";
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
        await this.refreshToolingData();
        this.watcherFailures = 0;
        this.watcherError = "";
        this.watcherLastSyncAt = new Date().toISOString();
      } catch (error) {
        this.watcherFailures += 1;
        this.watcherError = error instanceof Error ? error.message : "刷新会话失败。";
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
      const optimisticAssistantMessage = createOptimisticAssistantMessage();

      this.messages = [...this.messages, optimisticMessage, optimisticAssistantMessage];
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
        this.messages = this.messages
          .filter((message) => message.id !== optimisticAssistantMessage.id)
          .map((message) =>
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
    async interruptCurrentTurn() {
      if (!this.session || this.session.status !== "running") {
        return;
      }
      this.error = "";
      try {
        const detail = await api.interruptSession(
          this.session.id,
          "用户在工作台中请求中断当前执行。",
        );
        this.applySession(detail);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "中断会话失败。";
      }
    },
    async resumeCurrentSession() {
      if (!this.session || !this.session.is_resumable) {
        return;
      }
      this.error = "";
      try {
        const response = await api.resumeSession(
          this.session.id,
          "用户在工作台中恢复了当前会话。",
        );
        this.applySession(response.session);
        this.activity = [...response.events.slice().reverse(), ...this.activity].slice(0, 50);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "恢复会话失败。";
      }
    },
    async loadReplayTimeline() {
      if (!this.session) {
        return;
      }
      this.error = "";
      try {
        const replay = await api.replaySession(this.session.id);
        this.replayMeta = replay;
        this.replayTimeline = replay.events.slice().reverse();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "加载回放时间线失败。";
      }
    },
    async refreshToolingData() {
      if (!this.session) {
        return;
      }
      try {
        const [jobs, artifacts] = await Promise.all([
          api.listToolJobs(this.session.id),
          api.listArtifacts(this.session.id),
        ]);
        this.toolJobs = jobs;
        this.sessionArtifacts = artifacts;
        this.verificationMeta = await api.listVerifications(this.session.id);
        if (this.selectedToolJob) {
          const refreshed = await api.getToolJobDetail(this.session.id, this.selectedToolJob.id);
          this.selectedToolJob = refreshed;
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "刷新工具任务数据失败。";
      }
    },
    async inspectToolJob(jobId: string) {
      if (!this.session) {
        return;
      }
      this.error = "";
      try {
        this.selectedToolJob = await api.getToolJobDetail(this.session.id, jobId);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "加载工具任务详情失败。";
      }
    },
    applyStreamingEvent(event: ExecutionEvent) {
      if (
        event.type === "turn.interrupted" ||
        event.type === "runtime.interrupt_requested" ||
        event.type === "turn.resumed"
      ) {
        void this.refreshSession();
      }

      const messageId = String(event.payload?.message_id || "").trim();
      if (!messageId) {
        return;
      }

      if (event.type === "assistant.stream.started") {
        const streamingIndex = this.messages.findIndex(
          (message) =>
            message.role === "assistant" &&
            String(message.metadata?.delivery_status || "").trim() === "streaming" &&
            (Boolean(message.metadata?.is_optimistic_stream) || !message.content.trim()),
        );
        if (streamingIndex >= 0) {
          const nextMessages = [...this.messages];
          nextMessages[streamingIndex] = {
            ...nextMessages[streamingIndex],
            id: messageId,
            created_at: event.timestamp,
            metadata: {
              ...nextMessages[streamingIndex].metadata,
              turn_id: String(event.payload?.turn_id || ""),
              delivery_status: "streaming",
              is_optimistic_stream: false,
            },
          };
          this.messages = nextMessages;
          return;
        }

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
                is_optimistic_stream: false,
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
        const exists = this.messages.some((message) => message.id === messageId);
        if (!exists) {
          this.messages = [
            ...this.messages,
            {
              id: messageId,
              role: "assistant",
              content: delta,
              created_at: event.timestamp,
              metadata: {
                turn_id: String(event.payload?.turn_id || ""),
                delivery_status: "streaming",
                is_optimistic_stream: false,
              },
            },
          ];
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
                  is_optimistic_stream: false,
                },
              }
            : message,
        );
        return;
      }

      if (event.type === "assistant.stream.completed") {
        const hasExactMessage = this.messages.some((message) => message.id === messageId);
        if (!hasExactMessage) {
          this.messages = this.messages.filter(
            (message) =>
              !(
                message.role === "assistant" &&
                messageDeliveryStatus(message) === "streaming" &&
                Boolean(message.metadata?.is_optimistic_stream)
              ),
          );
          return;
        }
        this.messages = this.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                metadata: {
                  ...message.metadata,
                  delivery_status: "completed",
                  is_optimistic_stream: false,
                },
              }
            : message,
        );
      }
    },
  },
});
