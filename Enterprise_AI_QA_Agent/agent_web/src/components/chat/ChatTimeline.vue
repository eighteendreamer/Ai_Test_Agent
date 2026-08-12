<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import AssistantMarkdown from "./AssistantMarkdown.vue";
import ExecutionTrace from "./ExecutionTrace.vue";
import { t } from "../../services/i18n";
import type { ChatMessage, ExecutionEvent, InputAttachment } from "../../types";
import { formatServerDateTime } from "../../utils/datetime";

const props = defineProps<{
  messages: ChatMessage[];
  activity?: ExecutionEvent[];
}>();

const EXECUTION_EVENT_TYPES = new Set([
  "runtime.turn_started",
  "graph.execution_started",
  "graph.route_selected",
  "graph.plan_built",
  "graph.context_built",
  "graph.prompt_assembled",
  "model.request_prepared",
  "model.response_received",
  "model.tool_calls_received",
  "tool.execution_started",
  "tool.execution_blocked",
  "tool.execution_completed",
  "tool.execution_failed",
  "tool.execution_denied",
  "graph.loop_prepared",
  "graph.response_ready",
  "graph.execution_completed",
  "assistant.stream.started",
  "assistant.stream.completed",
  "turn.completed",
  "turn.interrupted",
]);

const executionTrace = computed(() => {
  const source = Array.isArray(props.activity) ? props.activity : [];
  const seen = new Set<string>();
  return source
    .slice()
    .reverse()
    .filter((event) => {
      if (!EXECUTION_EVENT_TYPES.has(event.type)) return false;
      const turnId = String(event.payload?.turn_id || "").trim();
      if (!turnId) return false;
      const key = String(event.id || `${event.type}:${event.timestamp}:${turnId}`);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(-24);
});

const latestUserTurnId = computed(() => {
  for (let index = visibleMessages.value.length - 1; index >= 0; index -= 1) {
    const message = visibleMessages.value[index];
    if (message.role !== "user") continue;
    return String(message.metadata?.turn_id || "").trim();
  }
  return "";
});

const currentExecutionTrace = computed(() => {
  const turnId = latestUserTurnId.value;
  return turnId
    ? executionTrace.value.filter((event) => String(event.payload?.turn_id || "").trim() === turnId)
    : [];
});

const visibleMessages = computed(() =>
  props.messages.filter(
    (message) =>
      !isTransientToolMessage(message) &&
      !(
        message.role === "assistant" &&
        isStreamingAssistant(message) &&
        !String(message.content || "").trim()
      ),
  ),
);

type TimelineEntry =
  | { kind: "message"; message: ChatMessage };

function isProcessMessage(message: ChatMessage) {
  return message.role === "tool" || message.role === "system";
}

const timelineEntries = computed<TimelineEntry[]>(() => {
  return visibleMessages.value
    .filter((message) => !isProcessMessage(message))
    .map((message) => ({ kind: "message", message }));
});

const historyRef = ref<HTMLElement | null>(null);
const endRef = ref<HTMLElement | null>(null);
const scrollContainerRef = ref<HTMLElement | null>(null);

// Kun's stick-to-bottom scroll model (use-timeline-scroll.ts): within this
// distance of the bottom the timeline counts as pinned and snaps down as new
// content streams in; scrolling further up releases the pin.
const STICK_TO_BOTTOM_PX = 96;
let stickToBottom = true;
let scrollFrame = 0;
let resizeObserver: ResizeObserver | null = null;

const messageRenderSignature = computed(() =>
  visibleMessages.value
    .map((message) => {
      const deliveryStatus = String(message.metadata?.delivery_status || "").trim();
      return `${message.id}:${message.content.length}:${deliveryStatus}`;
    })
    .join("|"),
);

const lastUserTurnKey = computed(() => {
  for (let index = visibleMessages.value.length - 1; index >= 0; index -= 1) {
    if (visibleMessages.value[index].role === "user") {
      return visibleMessages.value[index].id;
    }
  }
  return "";
});

function handleScroll() {
  const container = scrollContainerRef.value;
  if (!container) {
    return;
  }
  const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
  stickToBottom = distanceToBottom < STICK_TO_BOTTOM_PX;
}

// rAF-throttled snap — streaming deltas arrive faster than paint, so one
// frame coalesces them instead of forcing a reflow per delta (Kun's
// use-timeline-scroll keeps the same discipline).
function scrollToEnd(force = false) {
  if (force) {
    stickToBottom = true;
  }
  if (!stickToBottom) {
    return;
  }
  if (scrollFrame) {
    cancelAnimationFrame(scrollFrame);
  }
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = 0;
    const container = scrollContainerRef.value;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  });
}

onMounted(() => {
  scrollContainerRef.value = historyRef.value?.closest(".prototype-content") as HTMLElement | null;
  scrollContainerRef.value?.addEventListener("scroll", handleScroll, { passive: true });
  // Growing content (images decoding, code blocks highlighting) keeps the
  // pinned viewport glued to the tail.
  resizeObserver = new ResizeObserver(() => scrollToEnd());
  if (historyRef.value) {
    resizeObserver.observe(historyRef.value);
  }
  scrollToEnd(true);
});

onBeforeUnmount(() => {
  scrollContainerRef.value?.removeEventListener("scroll", handleScroll);
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (scrollFrame) {
    cancelAnimationFrame(scrollFrame);
    scrollFrame = 0;
  }
});

// A freshly submitted user turn always re-pins, even if the user was reading
// older history when pressing Enter (Kun issue #603).
watch(lastUserTurnKey, async (value, previous) => {
  if (!value || value === previous) {
    return;
  }
  await nextTick();
  scrollToEnd(true);
});

// Content growth (streaming deltas, new blocks) snaps only while pinned.
watch(messageRenderSignature, async () => {
  await nextTick();
  scrollToEnd();
});

function messageKind(message: ChatMessage) {
  return String(message.metadata?.message_kind || "").trim();
}

function assistantKindLabel(message: ChatMessage) {
  const kind = messageKind(message);
  if (kind === "task_notification") return t("chat.worker_notification");
  if (kind === "coordinator_assignment") return t("chat.worker_assignment");
  return "";
}

function labelForMessage(message: ChatMessage) {
  const kind = messageKind(message);
  if (kind === "task_notification") return t("chat.worker_notification");
  if (kind === "coordinator_assignment") return t("chat.worker_assignment");
  if (message.role === "system") return t("chat.system");
  return t("chat.event");
}

function attachmentsForMessage(message: ChatMessage): InputAttachment[] {
  const attachments = message.metadata?.attachments;
  if (!Array.isArray(attachments)) {
    return [];
  }
  return attachments.filter(
    (item): item is InputAttachment =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as InputAttachment).name === "string",
  );
}

function formatAttachmentSize(value: unknown) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) {
    return "";
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size} B`;
}

function localizedAttachmentSecondaryText(attachment: InputAttachment) {
  const formatLabel = String(attachment.metadata?.format_label || t("chat.session_attachment"));
  const sizeLabel = formatAttachmentSize(attachment.metadata?.size_bytes);
  return sizeLabel ? `${formatLabel} · ${sizeLabel}` : formatLabel;
}

function localizedDeliveryLabel(message: ChatMessage) {
  const deliveryStatus = String(message.metadata?.delivery_status || "").trim();
  if (deliveryStatus === "pending") return t("chat.sending_status");
  if (deliveryStatus === "streaming") return t("chat.streaming_status");
  if (deliveryStatus === "failed") return t("chat.failed_status");
  return "";
}

function isStreamingAssistant(message: ChatMessage) {
  return message.role === "assistant" && String(message.metadata?.delivery_status || "").trim() === "streaming";
}

function toolSummary(content: string) {
  try {
    const parsed = JSON.parse(content) as { summary?: string; status?: string };
    const summary = String(parsed.summary || "").trim();
    const status = String(parsed.status || "").trim();
    if (summary && status) return `${status}: ${summary}`;
    if (summary) return summary;
    if (status) return `status: ${status}`;
  } catch {
    return content.split("\n")[0]?.trim() || "Expand to view tool output";
  }
  return "Expand to view tool output";
}

function isTransientToolMessage(message: ChatMessage) {
  return message.role === "tool" && message.metadata?.transient_tool_event === true;
}

const RUNNING_TOOL_STATUSES = ["running", "queued", "waiting_approval", "partial"];
const FAILED_TOOL_STATUSES = ["failed", "denied", "cancelled"];

function toolToneClass(message: ChatMessage) {
  const status = parseToolPayload(message).status;
  if (RUNNING_TOOL_STATUSES.includes(status)) return "is-running";
  if (FAILED_TOOL_STATUSES.includes(status)) return "is-failed";
  return "";
}

function toolStatusChip(message: ChatMessage) {
  const status = parseToolPayload(message).status;
  if (!status || status === "completed") return "";
  return toolStatusLabel(status);
}

function toolSummaryLine(message: ChatMessage) {
  const payload = parseToolPayload(message);
  const headline = toolHeadline(message);
  const summary = payload.summary.trim();
  if (summary && summary !== headline) return summary;
  return toolStatusLabel(payload.status);
}

function toolHasDetail(message: ChatMessage) {
  return !isTransientToolMessage(message);
}

function toolStatusLabel(status: string) {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "运行中";
    case "waiting_approval":
      return "等待审批";
    case "completed":
      return "已完成";
    case "partial":
      return "部分完成";
    case "failed":
      return "失败";
    case "denied":
      return "已拒绝";
    case "cancelled":
      return "已取消";
    default:
      return status || "处理中";
  }
}

function parseToolPayload(message: ChatMessage) {
  try {
    const parsed = JSON.parse(message.content) as {
      status?: string;
      summary?: string;
      output?: Record<string, unknown>;
    };
    return {
      status: String(parsed.status || message.metadata?.status || message.metadata?.tool_progress_status || "").trim(),
      summary: String(parsed.summary || "").trim(),
      output:
        parsed.output && typeof parsed.output === "object" && !Array.isArray(parsed.output)
          ? parsed.output
          : {},
    };
  } catch {
    return {
      status: String(message.metadata?.status || message.metadata?.tool_progress_status || "").trim(),
      summary: toolSummary(message.content),
      output: {},
    };
  }
}

function toolHeadline(message: ChatMessage) {
  return String(message.metadata?.tool_name || message.metadata?.tool_key || "Tool").trim() || "Tool";
}
</script>

<template>
  <div ref="historyRef" class="home-history" v-if="visibleMessages.length || currentExecutionTrace.length">
    <template
      v-for="entry in timelineEntries"
      :key="entry.kind === 'message' ? entry.message.id : entry.id"
    >
      <!-- 用户消息：右对齐气泡（Kun ds-user-message / ds-user-message-bubble） -->
      <article
        v-if="entry.kind === 'message' && entry.message.role === 'user'"
        class="chat-turn chat-user-message"
      >
        <div
          v-if="attachmentsForMessage(entry.message).length"
          class="conversation-entry-attachments chat-user-attachments"
        >
          <div
            v-for="(attachment, index) in attachmentsForMessage(entry.message)"
            :key="`${entry.message.id}-${attachment.uri || attachment.name}-${index}`"
            class="conversation-entry-attachment"
          >
            <div class="conversation-entry-attachment-icon">
              <i class="fa-solid fa-file-lines"></i>
            </div>
            <div class="conversation-entry-attachment-copy">
              <strong>{{ attachment.name }}</strong>
              <span>{{ localizedAttachmentSecondaryText(attachment) }}</span>
            </div>
          </div>
        </div>
        <div class="chat-user-bubble">{{ entry.message.content }}</div>
        <div
          class="chat-message-meta chat-message-meta-end"
          :class="{ 'is-visible': Boolean(localizedDeliveryLabel(entry.message)) }"
        >
          <span>{{ formatServerDateTime(entry.message.created_at) }}</span>
          <span v-if="localizedDeliveryLabel(entry.message)">· {{ localizedDeliveryLabel(entry.message) }}</span>
        </div>
      </article>

      <ExecutionTrace
        v-if="entry.kind === 'message' && entry.message.role === 'user' && entry.message.id === lastUserTurnKey && currentExecutionTrace.length"
        :events="currentExecutionTrace"
        :turn-id="latestUserTurnId"
      />

      <!-- 执行总结：无卡片直排 markdown（Kun ds-chat-answer） -->
      <article
        v-else-if="entry.kind === 'message' && entry.message.role === 'assistant'"
        class="chat-turn chat-assistant-message"
      >
        <span v-if="assistantKindLabel(entry.message)" class="chat-kind-chip">{{ assistantKindLabel(entry.message) }}</span>
        <div
          :class="[
            'chat-answer',
            'conversation-entry-markdown',
            { 'conversation-entry-streaming': isStreamingAssistant(entry.message) },
          ]"
        >
          <AssistantMarkdown
            :content="entry.message.content"
            :streaming="isStreamingAssistant(entry.message)"
          />
        </div>
        <div v-if="!isStreamingAssistant(entry.message)" class="chat-message-meta">
          <span>{{ formatServerDateTime(entry.message.created_at) }}</span>
        </div>
      </article>

    </template>
    <div ref="endRef" class="conversation-end-sentinel" aria-hidden="true"></div>
  </div>
</template>

<style scoped>
.chat-user-attachments {
  justify-content: flex-end;
}

.conversation-entry-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.conversation-entry-attachment {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 220px;
  max-width: min(360px, 100%);
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.92);
}

.conversation-entry-attachment-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: rgba(37, 99, 235, 0.12);
  color: #2563eb;
  flex-shrink: 0;
}

.conversation-entry-attachment-copy {
  min-width: 0;
}

.conversation-entry-attachment-copy strong,
.conversation-entry-attachment-copy span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-entry-attachment-copy strong {
  color: #0f172a;
  font-size: 14px;
  font-weight: 700;
}

.conversation-entry-attachment-copy span {
  margin-top: 2px;
  color: #64748b;
  font-size: 12px;
}
</style>
