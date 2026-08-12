import { defineStore } from "pinia";

import { api } from "../services/api";
import { serverDateTimestamp } from "../utils/datetime";
import type {
  AgentDescriptor,
  ChatMessage,
  CodeReviewDebateProgressMeta,
  CodeReviewReportMeta,
  ExecutionEvent,
  InputAttachment,
  ModeDescriptor,
  PendingInputQueueEntry,
  PendingCompletionWorkerMeta,
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
type TranscriptBucket = "conversation" | "tool" | "error";

interface TranscriptSummaryView {
  conversation_count: number;
  tool_count: number;
  error_count: number;
  hidden_count: number;
  context_eligible_count: number;
}

interface TranscriptEntryView {
  id: string;
  role: ChatMessage["role"];
  content: string;
  created_at: string;
  transcript_bucket: TranscriptBucket;
  context_eligible: boolean;
  response_mode: string;
}

function messageDeliveryStatus(message: ChatMessage | undefined) {
  return String(message?.metadata?.delivery_status || "").trim();
}

function toolMessageCallId(message: ChatMessage | undefined) {
  return String(message?.metadata?.tool_call_id || "").trim();
}

function toolMessageKey(message: ChatMessage | undefined) {
  return String(message?.metadata?.tool_key || "").trim();
}

function isTransientToolMessage(message: ChatMessage | undefined) {
  return message?.role === "tool" && message?.metadata?.transient_tool_event === true;
}

function hasStreamingAssistantMessage(messages: ChatMessage[]) {
  return messages.some(
    (message) =>
      message.role === "assistant" &&
      messageDeliveryStatus(message) === "streaming" &&
      Boolean(String(message.content || "").trim()),
  );
}

function normalizeTranscriptBucket(value: unknown): TranscriptBucket {
  const bucket = String(value || "").trim();
  if (bucket === "tool" || bucket === "error") {
    return bucket;
  }
  return "conversation";
}

function buildTranscriptSummary(messages: ChatMessage[]): TranscriptSummaryView {
  const summary: TranscriptSummaryView = {
    conversation_count: 0,
    tool_count: 0,
    error_count: 0,
    hidden_count: 0,
    context_eligible_count: 0,
  };

  for (const message of messages) {
    const content = String(message.content || "").trim();
    const bucket = normalizeTranscriptBucket(message.metadata?.transcript_bucket);
    const contextEligible = message.metadata?.context_eligible !== false && Boolean(content);

    if (!content) {
      summary.hidden_count += 1;
      continue;
    }

    if (bucket === "tool") {
      summary.tool_count += 1;
    } else if (bucket === "error") {
      summary.error_count += 1;
    } else {
      summary.conversation_count += 1;
    }

    if (contextEligible) {
      summary.context_eligible_count += 1;
    }
  }

  return summary;
}

function buildTranscriptEntries(messages: ChatMessage[], limit = 8): TranscriptEntryView[] {
  return messages
    .filter((message) => String(message.content || "").trim())
    .slice(-limit)
    .reverse()
    .map((message) => ({
      id: message.id,
      role: message.role,
      content: String(message.content || "").trim(),
      created_at: message.created_at,
      transcript_bucket: normalizeTranscriptBucket(message.metadata?.transcript_bucket),
      context_eligible: message.metadata?.context_eligible !== false,
      response_mode: String(message.metadata?.response_mode || "ok").trim() || "ok",
    }));
}

function normalizePendingInputQueue(value: unknown): PendingInputQueueEntry[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((item) => {
      const payload =
        item.payload && typeof item.payload === "object" && !Array.isArray(item.payload)
          ? (item.payload as Record<string, unknown>)
          : {};
      const metadata =
        item.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata)
          ? (item.metadata as Record<string, unknown>)
          : {};
      return {
        id: String(item.id || ""),
        created_at: String(item.created_at || ""),
        busy_status: String(item.busy_status || ""),
        queue_behavior: String(item.queue_behavior || ""),
        interrupt_policy: String(item.interrupt_policy || ""),
        reason: String(item.reason || ""),
        payload: {
          content: typeof payload.content === "string" ? payload.content : "",
          submit_mode: typeof payload.submit_mode === "string" ? payload.submit_mode : "",
          command_name: typeof payload.command_name === "string" ? payload.command_name : null,
          message_kind: typeof payload.message_kind === "string" ? payload.message_kind : "",
        },
        metadata,
      };
    })
    .filter((item) => Boolean(item.id));
}

function readQueuedInputCount(metadata: Record<string, unknown> | undefined): number {
  const control =
    metadata?.control && typeof metadata.control === "object" && !Array.isArray(metadata.control)
      ? (metadata.control as Record<string, unknown>)
      : null;
  const rawCount = Number(control?.queued_input_count || 0);
  return Number.isFinite(rawCount) && rawCount > 0 ? rawCount : 0;
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

const WATCHER_INTERVAL_MS = 5000;
const EVENT_REFRESH_DEBOUNCE_MS = 220;
const TOOLING_REFRESH_MIN_INTERVAL_MS = 10000;
const EVENT_RECONNECT_DELAY_MS = 1500;
const RECEIVED_EVENT_ID_LIMIT = 5000;

function isActiveSessionStatus(status: SessionLifecycleStatus) {
  return status === "running" || status === "waiting_approval";
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `client-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function mergeSessionMessages(
  serverMessages: ChatMessage[],
  localMessages: ChatMessage[],
  sessionStatus: SessionLifecycleStatus,
) {
  const serverIds = new Set(serverMessages.map((message) => message.id));
  const serverClientMessageIds = new Set(
    serverMessages
      .map((message) => String(message.metadata?.client_message_id || "").trim())
      .filter((value) => Boolean(value)),
  );
  const serverToolCallIds = new Set(
    serverMessages
      .map((message) => toolMessageCallId(message))
      .filter((value) => Boolean(value)),
  );
  const serverApprovalIds = new Set(
    serverMessages
      .map((message) => String(message.metadata?.approval_id || "").trim())
      .filter((value) => Boolean(value)),
  );
  const serverToolKeys = new Set(
    serverMessages
      .filter((message) => message.role === "tool")
      .map((message) => toolMessageKey(message))
      .filter((value) => Boolean(value)),
  );
  const localById = new Map(localMessages.map((message) => [message.id, message]));
  const localByClientMessageId = new Map(
    localMessages
      .map((message) => [String(message.metadata?.client_message_id || "").trim(), message] as const)
      .filter(([clientMessageId]) => Boolean(clientMessageId)),
  );
  const allowTransientLocalMessages = isActiveSessionStatus(sessionStatus);

  const merged = serverMessages.map((serverMessage) => {
    const localMessage =
      localById.get(serverMessage.id) ||
      localByClientMessageId.get(String(serverMessage.metadata?.client_message_id || "").trim());
    if (!localMessage) {
      return serverMessage;
    }

    const localStatus = messageDeliveryStatus(localMessage);
    const serverStatus = messageDeliveryStatus(serverMessage);
    const shouldPreferLocalContent =
      isActiveSessionStatus(sessionStatus) &&
      serverMessage.role === "assistant" &&
      (localStatus === "streaming" || localStatus === "completed") &&
      localMessage.content.length >= serverMessage.content.length &&
      serverStatus !== "completed";

    if (!shouldPreferLocalContent && serverMessage.role !== "user") {
      return serverMessage;
    }

    return {
      ...serverMessage,
      content: serverMessage.content || localMessage.content,
      metadata: {
        ...localMessage.metadata,
        ...serverMessage.metadata,
        ...(serverMessage.role === "user" ? { delivery_status: "sent" } : { delivery_status: localStatus }),
      },
    };
  });

  const localOnlyMessages = localMessages.filter((message) => {
    if (serverIds.has(message.id)) {
      return false;
    }
    const clientMessageId = String(message.metadata?.client_message_id || "").trim();
    if (clientMessageId && serverClientMessageIds.has(clientMessageId)) {
      return false;
    }
    if (message.role === "user") {
      // Keep the optimistic user turn visible while the synchronous backend
      // request is still orchestrating tools/model work. It is replaced by
      // the server message through client_message_id once persisted.
      return messageDeliveryStatus(message) !== "failed";
    }
    if (!allowTransientLocalMessages) {
      return false;
    }
    if (message.role === "assistant") {
      const status = messageDeliveryStatus(message);
      return status === "streaming" && Boolean(message.content.trim());
    }
    if (!isTransientToolMessage(message)) {
      return false;
    }
    const callId = toolMessageCallId(message);
    if (callId && serverToolCallIds.has(callId)) {
      return false;
    }
    const approvalId = String(message.metadata?.approval_id || "").trim();
    if (approvalId && serverApprovalIds.has(approvalId)) {
      return false;
    }
    const toolKey = toolMessageKey(message);
    if (!callId && toolKey && serverToolKeys.has(toolKey)) {
      return false;
    }
    return true;
  });

  return [...merged, ...localOnlyMessages];
}

function findToolMessageIndex(messages: ChatMessage[], event: ExecutionEvent) {
  const eventCallId = String(event.payload?.call_id || "").trim();
  const eventToolKey = String(event.payload?.tool_key || "").trim();
  const eventApprovalId = String(event.payload?.approval_id || "").trim();
  if (eventCallId) {
    const byCallId = messages.findIndex(
      (message) => isTransientToolMessage(message) && toolMessageCallId(message) === eventCallId,
    );
    if (byCallId >= 0) {
      return byCallId;
    }
  }
  if (eventApprovalId) {
    const byApprovalId = messages.findIndex(
      (message) =>
        isTransientToolMessage(message) &&
        String(message.metadata?.approval_id || "").trim() === eventApprovalId,
    );
    if (byApprovalId >= 0) {
      return byApprovalId;
    }
  }
  if (!eventToolKey) {
    return -1;
  }
  return messages.findIndex(
    (message) =>
      isTransientToolMessage(message) &&
      toolMessageKey(message) === eventToolKey &&
      String(message.metadata?.tool_progress_finalized || "").trim() !== "true",
  );
}

function createTransientToolMessage(event: ExecutionEvent, status: string, summary: string) {
  const toolName = String(event.payload?.tool_name || event.payload?.tool_key || "Tool").trim() || "Tool";
  const toolKey = String(event.payload?.tool_key || "").trim();
  const callId = String(event.payload?.call_id || "").trim();
  const toolJobId = String(event.payload?.tool_job_id || "").trim();
  const approvalId = String(event.payload?.approval_id || "").trim();
  return {
    id: `transient-tool-${callId || toolKey || Date.now()}`,
    role: "tool" as const,
    content: JSON.stringify(
      {
        status,
        summary,
        output: {
          event_type: event.type,
          tool_name: toolName,
          tool_key: toolKey,
        },
      },
      null,
      2,
    ),
    created_at: event.timestamp,
    metadata: {
      tool_key: toolKey,
      tool_name: toolName,
      tool_call_id: callId,
      tool_job_id: toolJobId,
      approval_id: approvalId,
      delivery_status: status === "running" ? "streaming" : "completed",
      transient_tool_event: true,
      tool_progress_status: status,
      tool_progress_finalized: status === "running" ? "false" : "true",
      persist_transcript: false,
      context_eligible: false,
      transcript_bucket: "tool",
    },
  };
}

function updateTransientToolMessageStatus(message: ChatMessage, status: string, summary: string): ChatMessage {
  let payload: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(message.content || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      payload = parsed as Record<string, unknown>;
    }
  } catch {
    payload = {};
  }

  return {
    ...message,
    content: JSON.stringify(
      {
        ...payload,
        status,
        summary,
      },
      null,
      2,
    ),
    metadata: {
      ...message.metadata,
      delivery_status: status === "running" ? "streaming" : "completed",
      tool_progress_status: status,
      tool_progress_finalized: status === "running" ? "false" : "true",
    },
  };
}

export const useSessionStore = defineStore("session", {
  state: () => ({
    session: null as SessionDetail | null,
    messages: [] as ChatMessage[],
    activity: [] as ExecutionEvent[],
    agents: [] as AgentDescriptor[],
    modes: [] as ModeDescriptor[],
    tools: [] as ToolDescriptor[],
    selectedAgentKey: "coordinator",
    selectedModeKey: "default",
    pendingModeKeySelection: "",
    isBootstrapping: false,
    isSending: false,
    resolvingApprovalIds: [] as string[],
    refreshTimer: null as number | null,
    scheduledRefreshTimer: null as number | null,
    scheduledToolingRefresh: false,
    refreshInFlight: false,
    toolingRefreshInFlight: false,
    lastToolingRefreshAt: "",
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
    eventStreamSessionId: "",
    eventReconnectTimer: null as number | null,
    eventCursorBySession: {} as Record<string, string>,
    receivedEventIds: [] as string[],
  }),
  getters: {
    activeAgent(state) {
      return state.agents.find((item) => item.key === state.selectedAgentKey) ?? null;
    },
    activeMode(state) {
      return state.modes.find((item) => item.key === state.selectedModeKey) ?? null;
    },
    isAssistantStreaming(state) {
      return hasStreamingAssistantMessage(state.messages);
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
      const value =
        state.session?.metadata?.worker_dispatches ??
        state.session?.last_snapshot?.graph_state?.worker_dispatches;
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
    codeReviewReportMeta(state): CodeReviewReportMeta | null {
      const value = state.session?.metadata?.code_review_report;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      return value as CodeReviewReportMeta;
    },
    pendingCompletionWorkerMeta(state): PendingCompletionWorkerMeta | null {
      const value = state.session?.metadata?.pending_completion_worker;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      return value as PendingCompletionWorkerMeta;
    },
    codeReviewDebateProgressMeta(state): CodeReviewDebateProgressMeta | null {
      const value =
        state.session?.metadata?.code_review_debate_progress ??
        state.session?.last_snapshot?.graph_state?.code_review_debate_progress;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      return value as CodeReviewDebateProgressMeta;
    },
    codeReviewWorkerStats() {
      const workerDispatches = this.workerDispatches;
      const reviewerDispatches = workerDispatches.filter((item) => !item.is_completion_worker);
      const completionDispatch = workerDispatches.find((item) => item.is_completion_worker) ?? null;
      const runningCount = reviewerDispatches.filter((item) => item.status === "running").length;
      const waitingApprovalCount = reviewerDispatches.filter((item) => item.status === "waiting_approval").length;
      const completedCount = reviewerDispatches.filter((item) => item.status === "completed").length;
      const failedCount = reviewerDispatches.filter((item) => item.status === "failed").length;
      return {
        reviewer_count: reviewerDispatches.length,
        running_count: runningCount,
        waiting_approval_count: waitingApprovalCount,
        completed_count: completedCount,
        failed_count: failedCount,
        completion_dispatch: completionDispatch,
      };
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
      return state.toolJobs
        .slice()
        .sort((a, b) => serverDateTimestamp(b.updated_at) - serverDateTimestamp(a.updated_at))
        .slice(0, 8);
    },
    transcriptSummary(state): TranscriptSummaryView {
      const fromReplay = state.replayMeta?.metadata?.transcript_summary;
      if (fromReplay && typeof fromReplay === "object" && !Array.isArray(fromReplay)) {
        return {
          conversation_count: Number((fromReplay as Record<string, unknown>).conversation_count || 0),
          tool_count: Number((fromReplay as Record<string, unknown>).tool_count || 0),
          error_count: Number((fromReplay as Record<string, unknown>).error_count || 0),
          hidden_count: Number((fromReplay as Record<string, unknown>).hidden_count || 0),
          context_eligible_count: Number((fromReplay as Record<string, unknown>).context_eligible_count || 0),
        };
      }
      return buildTranscriptSummary(state.messages);
    },
    recentTranscriptEntries(state): TranscriptEntryView[] {
      return buildTranscriptEntries(state.messages, 8);
    },
    pendingInputQueue(state): PendingInputQueueEntry[] {
      return normalizePendingInputQueue(state.session?.metadata?.pending_input_queue);
    },
    queuedTurnCount(state): number {
      const queueEntries = normalizePendingInputQueue(state.session?.metadata?.pending_input_queue);
      return Math.max(queueEntries.length, readQueuedInputCount(state.session?.metadata));
    },
    nextQueuedTurn(): PendingInputQueueEntry | null {
      return this.pendingInputQueue[0] ?? null;
    },
    watcherPhase(): SessionWatcherPhase {
      if (!this.session) return "idle";
      if (this.pendingApprovals.length > 0) return "waiting_approval";
      if (this.session.is_interrupted || this.session.status === "interrupted") return "interrupted";
      if (this.workerFailureGuard?.blocked || this.session.status === "failed") return "failed";
      if (
        this.isAssistantStreaming ||
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
        return {
          agents: this.agents,
          tools: this.tools,
          modes: this.modes,
        };
      }

      this.isBootstrapping = true;
      this.error = "";
      try {
        const [agents, tools, modes] = await Promise.all([
          api.listAgents(),
          api.listTools(),
          api.listModes(),
        ]);
        this.agents = agents;
        this.modes = modes;
        this.tools = tools;
        this.selectedAgentKey = agents[0]?.key ?? "coordinator";
        this.selectedModeKey = modes[0]?.key ?? "default";

        if (!this.session) {
          const session = await api.createSession(
            "Enterprise Intelligent QA Session",
            this.selectedModeKey,
          );
          this.applySession(session);
          await this.refreshToolingData();
          this.connectEvents();
        }
        return {
          agents,
          tools,
          modes,
        };
      } catch (error) {
        this.error = error instanceof Error ? error.message : "初始化失败。";
        return {
          agents: this.agents,
          tools: this.tools,
          modes: this.modes,
        };
      } finally {
        this.isBootstrapping = false;
      }
    },
    applySession(session: SessionDetail) {
      const previousSessionId = this.session?.id || "";
      const mergedMessages = mergeSessionMessages(
        session.messages,
        this.messages,
        session.status,
      );
      this.session = session;
      this.messages = mergedMessages;
      this.selectedAgentKey = session.selected_agent ?? this.selectedAgentKey;
      const sessionModeKey = String(session.mode_key || "").trim();
      if (previousSessionId && previousSessionId !== session.id) {
        this.pendingModeKeySelection = "";
      }
      if (this.pendingModeKeySelection) {
        this.selectedModeKey = this.pendingModeKeySelection;
        if (sessionModeKey === this.pendingModeKeySelection) {
          this.pendingModeKeySelection = "";
        }
        return;
      }
      this.selectedModeKey = sessionModeKey || this.selectedModeKey;
    },
    connectEvents(force = false) {
      if (!this.session) {
        return;
      }
      const sessionId = this.session.id;
      if (
        !force &&
        this.eventSource &&
        this.eventStreamSessionId === sessionId &&
        this.eventSource.readyState !== EventSource.CLOSED
      ) {
        return;
      }
      if (this.eventReconnectTimer !== null) {
        window.clearTimeout(this.eventReconnectTimer);
        this.eventReconnectTimer = null;
      }
      this.eventSource?.close();
      this.eventStreamSessionId = sessionId;
      const lastEventId = this.eventCursorBySession[sessionId] || "";
      const source = api.connectEvents(sessionId, (event) => {
        if (this.session?.id !== sessionId) {
          return;
        }
        if (!this.rememberIncomingEvent(event)) {
          return;
        }
        this.activity = [event, ...this.activity].slice(0, 50);
        this.applyStreamingEvent(event);
        // Debug: log all SSE events for notification troubleshooting
        if (event.type?.startsWith("turn.") || event.type?.startsWith("approval.")) {
          console.log("[SSE Event]", event.type, event.session_id);
        }
        if (
          event.type === "approval.created" ||
          event.type === "approval.resolved" ||
          event.type === "tool.execution_completed" ||
          event.type === "tool.execution_failed" ||
          event.type === "tool.execution_denied" ||
          event.type === "verification.completed" ||
          event.type === "turn.completed" ||
          event.type === "turn.failed"
        ) {
          // Desktop notifications for key events.
          if (event.type === "turn.completed" || event.type === "turn.failed") {
            import("../services/desktopNotifications").then(({ notifySessionComplete }) => {
              const sessionTitle = this.session?.title?.trim() || `会话 ${event.session_id.slice(0, 8)}`;
              const lastMessage = [...this.messages]
                .reverse()
                .find((m) => String(m.content || "").trim());
              const statusLabel = event.type === "turn.completed" ? "已完成" : "执行失败";
              const bodyPreview = lastMessage
                ? `${statusLabel} · ${String(lastMessage.content).trim().slice(0, 60)}`
                : `会话${statusLabel}`;
              notifySessionComplete(event.session_id, { title: sessionTitle, body: bodyPreview });
            });
          }
          if (event.type === "approval.created") {
            const toolName = String(event.payload?.tool_name || event.payload?.tool_key || "");
            import("../services/desktopNotifications").then(({ notifyApprovalRequired }) => {
              notifyApprovalRequired(event.session_id, String(event.payload?.approval_id || ""), toolName);
            });
          }
          this.scheduleRefresh(true);
          return;
        }
        if (
          event.type === "tool.execution_started" ||
          event.type === "worker.task_notification_received" ||
          event.type === "worker.auto_stopped" ||
          event.type === "input.queued" ||
          event.type === "input.dequeued" ||
          event.type === "queue.interrupted_turn_superseded" ||
          event.type === "runtime.interrupt_requested" ||
          event.type === "turn.interrupted" ||
          event.type === "turn.resumed"
        ) {
          this.scheduleRefresh(false);
        }
      }, lastEventId);
      source.onopen = () => {
        if (this.session?.id !== sessionId || this.eventSource !== source) {
          return;
        }
        if (this.error === "事件流已断开，正在尝试重新连接。") {
          this.error = "";
        }
      };
      source.onerror = () => {
        if (this.eventSource !== source) {
          return;
        }
        source.close();
        this.eventSource = null;
        this.error = "事件流已断开，正在尝试重新连接。";
        this.scheduleEventReconnect(sessionId);
      };
      this.eventSource = source;
    },
    scheduleEventReconnect(sessionId = this.session?.id || "") {
      if (!sessionId || this.eventReconnectTimer !== null) {
        return;
      }
      this.eventReconnectTimer = window.setTimeout(() => {
        this.eventReconnectTimer = null;
        if (this.session?.id === sessionId) {
          this.connectEvents(true);
        }
      }, EVENT_RECONNECT_DELAY_MS);
    },
    ensureEventStreamConnected() {
      if (!this.session) {
        return;
      }
      if (
        !this.eventSource ||
        this.eventStreamSessionId !== this.session.id ||
        this.eventSource.readyState === EventSource.CLOSED
      ) {
        this.connectEvents(true);
      }
    },
    rememberIncomingEvent(event: ExecutionEvent) {
      const eventId = String(event.id || "").trim();
      if (!eventId) {
        return true;
      }
      if (this.receivedEventIds.includes(eventId)) {
        return false;
      }
      this.receivedEventIds = [...this.receivedEventIds, eventId].slice(-RECEIVED_EVENT_ID_LIMIT);
      this.eventCursorBySession = {
        ...this.eventCursorBySession,
        [event.session_id]: eventId,
      };
      return true;
    },
    disconnectEvents() {
      if (this.eventReconnectTimer !== null) {
        window.clearTimeout(this.eventReconnectTimer);
        this.eventReconnectTimer = null;
      }
      this.eventSource?.close();
      this.eventSource = null;
      this.eventStreamSessionId = "";
    },
    scheduleRefresh(includeTooling = false, delayMs = EVENT_REFRESH_DEBOUNCE_MS) {
      if (!this.session) {
        return;
      }
      this.scheduledToolingRefresh = this.scheduledToolingRefresh || includeTooling;
      if (this.scheduledRefreshTimer !== null) {
        return;
      }
      this.scheduledRefreshTimer = window.setTimeout(() => {
        const nextIncludeTooling = this.scheduledToolingRefresh;
        this.scheduledRefreshTimer = null;
        this.scheduledToolingRefresh = false;
        void this.refreshSession({ includeTooling: nextIncludeTooling });
      }, delayMs);
    },
    async refreshSession(options: { includeTooling?: boolean } = {}) {
      if (!this.session) {
        return;
      }
      if (this.refreshInFlight) {
        this.scheduledToolingRefresh = this.scheduledToolingRefresh || Boolean(options.includeTooling);
        return;
      }
      this.refreshInFlight = true;
      try {
        const detail = await api.getSession(this.session.id);
        this.applySession(detail);
        await this.refreshToolingData(Boolean(options.includeTooling));
        this.watcherFailures = 0;
        this.watcherError = "";
        this.watcherLastSyncAt = new Date().toISOString();
      } catch (error) {
        this.watcherFailures += 1;
        this.watcherError = error instanceof Error ? error.message : "刷新会话失败。";
      } finally {
        this.refreshInFlight = false;
        if (this.scheduledToolingRefresh) {
          this.scheduleRefresh(this.scheduledToolingRefresh, EVENT_REFRESH_DEBOUNCE_MS);
        }
      }
    },
    startWatcher() {
      if (this.refreshTimer !== null) {
        return;
      }
      this.scheduleRefresh(false, 0);
      this.refreshTimer = window.setInterval(() => {
        this.scheduleRefresh(false);
      }, WATCHER_INTERVAL_MS);
    },
    stopWatcher() {
      if (this.refreshTimer !== null) {
        window.clearInterval(this.refreshTimer);
        this.refreshTimer = null;
      }
      if (this.scheduledRefreshTimer !== null) {
        window.clearTimeout(this.scheduledRefreshTimer);
        this.scheduledRefreshTimer = null;
      }
      this.scheduledToolingRefresh = false;
    },
    setModeKey(modeKey: string) {
      const normalized = String(modeKey || "").trim();
      if (!normalized) {
        return;
      }
      this.selectedModeKey = normalized;
      this.pendingModeKeySelection = normalized;
      if (!this.session) {
        return;
      }
      void this.persistSessionPreferences({ mode_key: normalized });
    },
    async persistSessionPreferences(payload: {
      mode_key?: string | null;
      preferred_model?: string | null;
      selected_agent?: string | null;
      metadata?: Record<string, unknown> | null;
    }) {
      if (!this.session) {
        return;
      }
      this.error = "";
      try {
        const session = await api.updateSession(this.session.id, payload);
        this.applySession(session);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Failed to update session preferences.";
      }
    },
    async sendMessage(
      content: string,
      attachments: InputAttachment[] = [],
      options: {
        context?: Record<string, unknown>;
        metadata?: Record<string, unknown>;
        modeKey?: string;
      } = {},
    ) {
      const trimmedContent = content.trim();
      if (!this.session || (!trimmedContent && attachments.length === 0)) {
        return;
      }

      const optimisticMessage: ChatMessage = {
        id: `temp-user-${Date.now()}`,
        role: "user",
        content: trimmedContent,
        created_at: new Date().toISOString(),
        metadata: {
          delivery_status: "pending",
          client_message_id: createClientMessageId(),
          is_optimistic: true,
          attachment_count: attachments.length,
          attachments,
        },
      };
      const clientMessageId = String(optimisticMessage.metadata?.client_message_id || "");
      const optimisticAssistantMessage = createOptimisticAssistantMessage();

      this.messages = [...this.messages, optimisticMessage, optimisticAssistantMessage];
      this.isSending = true;
      this.error = "";
      this.ensureEventStreamConnected();
      try {
        const response = await api.sendMessage(
          this.session.id,
          trimmedContent,
          options.modeKey || this.selectedModeKey,
          attachments,
          options.context,
          {
            ...options.metadata,
            client_message_id: clientMessageId,
          },
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
        if (this.session) {
          this.session.pending_approvals = this.session.pending_approvals.filter((item) => item.id !== approvalId);
        }
        const nextStatus = decision === "approved" ? "running" : "denied";
        const nextSummary =
          decision === "approved"
            ? "Approval granted. Tool execution is running."
            : "Approval denied. Tool execution was skipped.";
        this.messages = this.messages.map((message) => {
          const messageApprovalId = String(message.metadata?.approval_id || "").trim();
          if (!isTransientToolMessage(message) || messageApprovalId !== approvalId) {
            return message;
          }
          return updateTransientToolMessageStatus(message, nextStatus, nextSummary);
        });
        this.scheduleRefresh(true, 120);
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
    async refreshToolingData(force = false) {
      if (!this.session || this.toolingRefreshInFlight) {
        return;
      }
      if (!force && this.lastToolingRefreshAt) {
        const elapsed = Date.now() - Date.parse(this.lastToolingRefreshAt);
        if (Number.isFinite(elapsed) && elapsed < TOOLING_REFRESH_MIN_INTERVAL_MS) {
          return;
        }
      }
      this.toolingRefreshInFlight = true;
      try {
        const [jobs, artifacts, verifications] = await Promise.all([
          api.listToolJobs(this.session.id),
          api.listArtifacts(this.session.id),
          api.listVerifications(this.session.id),
        ]);
        this.toolJobs = jobs;
        this.sessionArtifacts = artifacts;
        this.verificationMeta = verifications;
        this.lastToolingRefreshAt = new Date().toISOString();
        if (this.selectedToolJob) {
          const refreshed = await api.getToolJobDetail(this.session.id, this.selectedToolJob.id);
          this.selectedToolJob = refreshed;
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "刷新工具任务数据失败。";
      } finally {
        this.toolingRefreshInFlight = false;
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
      const eventTurnId = String(event.payload?.turn_id || "").trim();
      if (eventTurnId) {
        let pendingUserIndex = -1;
        for (let index = this.messages.length - 1; index >= 0; index -= 1) {
          const message = this.messages[index];
          if (
            message.role === "user" &&
            (messageDeliveryStatus(message) === "pending" || message.metadata?.is_optimistic === true) &&
            !String(message.metadata?.turn_id || "").trim()
          ) {
            pendingUserIndex = index;
            break;
          }
        }
        if (pendingUserIndex >= 0) {
          const nextMessages = [...this.messages];
          nextMessages[pendingUserIndex] = {
            ...nextMessages[pendingUserIndex],
            metadata: {
              ...nextMessages[pendingUserIndex].metadata,
              turn_id: eventTurnId,
              is_optimistic: true,
            },
          };
          this.messages = nextMessages;
        }
      }

      if (
        event.type === "turn.interrupted" ||
        event.type === "runtime.interrupt_requested" ||
        event.type === "turn.resumed"
      ) {
        this.scheduleRefresh(false);
      }

      if (
        event.type === "tool.execution_started" ||
        event.type === "tool.execution_blocked" ||
        event.type === "tool.execution_completed" ||
        event.type === "tool.execution_failed" ||
        event.type === "tool.execution_denied"
      ) {
        const statusMap: Record<string, string> = {
          "tool.execution_started": "running",
          "tool.execution_blocked": "waiting_approval",
          "tool.execution_completed": "completed",
          "tool.execution_failed": "failed",
          "tool.execution_denied": "denied",
        };
        const status = statusMap[event.type] || "running";
        const fallbackSummary =
          event.type === "tool.execution_started"
            ? "Tool execution started."
            : event.type === "tool.execution_blocked"
              ? "Tool is waiting for approval."
              : event.type === "tool.execution_completed"
                ? "Tool execution completed."
                : event.type === "tool.execution_denied"
                  ? "Tool execution was denied."
                  : "Tool execution failed.";
        const summary = String(event.payload?.summary || event.payload?.message || fallbackSummary).trim() || fallbackSummary;
        const index = findToolMessageIndex(this.messages, event);
        const nextMessage = createTransientToolMessage(event, status, summary);

        if (index >= 0) {
          const nextMessages = [...this.messages];
          nextMessages[index] = {
            ...nextMessages[index],
            ...nextMessage,
            id: nextMessages[index].id,
            created_at: nextMessages[index].created_at || nextMessage.created_at,
            metadata: {
              ...nextMessages[index].metadata,
              ...nextMessage.metadata,
            },
          };
          this.messages = nextMessages;
        } else {
          this.messages = [...this.messages, nextMessage];
        }

        return;
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
