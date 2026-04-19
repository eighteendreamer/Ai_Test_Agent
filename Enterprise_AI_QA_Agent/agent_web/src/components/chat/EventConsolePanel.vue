<script setup lang="ts">
import { computed, ref } from "vue";

import type { ExecutionEvent, PendingInputQueueEntry } from "../../types";
import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();
const eventsExpanded = ref(false);

const timeline = computed(() => {
  if (sessionStore.replayTimeline.length > 0) {
    return sessionStore.replayTimeline.slice(0, 16);
  }
  return sessionStore.activity.slice(0, 16);
});

const queueEntries = computed(() => sessionStore.pendingInputQueue.slice(0, 6));

const transcriptStats = computed(() => {
  const summary = sessionStore.transcriptSummary;
  return [
    { key: "conversation", label: "会话消息", value: summary.conversation_count, tone: "neutral" },
    { key: "tool", label: "工具消息", value: summary.tool_count, tone: "tool" },
    { key: "error", label: "错误消息", value: summary.error_count, tone: "error" },
    { key: "eligible", label: "可回填上下文", value: summary.context_eligible_count, tone: "success" },
    { key: "queue", label: "排队轮次", value: sessionStore.queuedTurnCount, tone: "queue" },
  ];
});

const transcriptEntries = computed(() => sessionStore.recentTranscriptEntries.slice(0, 6));

function previewText(value: string, limit = 96) {
  const normalized = String(value || "").trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(0, limit - 3))}...`;
}

function queueBehaviorLabel(value: string) {
  if (value === "interrupt_then_retry") return "中断后重试";
  if (value === "enqueue_if_busy") return "忙时排队";
  if (value === "reject_when_busy") return "忙时拒绝";
  return value || "忙时排队";
}

function statusLabel(value: string) {
  if (!value) return "待处理";
  const normalized = value.replace(/_/g, " ");
  const mapping: Record<string, string> = {
    running: "运行中",
    "waiting approval": "等待审批",
    interrupted: "已中断",
    completed: "已完成",
    failed: "失败",
    idle: "空闲",
    busy: "忙碌中",
    "interrupt active turn": "中断当前轮次",
    "wait for active turn": "等待当前轮次结束",
  };
  return mapping[normalized] || normalized;
}

function eventMessage(event: ExecutionEvent) {
  const payload = event.payload ?? {};

  if (event.type === "input.queued") {
    const queuedTurnId = String(payload.queued_turn_id || "").trim();
    const busyStatus = statusLabel(String(payload.busy_status || "busy").trim());
    const queueDepth = String(payload.queue_depth || "0").trim();
    const queueBehavior = queueBehaviorLabel(String(payload.queue_behavior || "enqueue_if_busy").trim());
    return `轮次 ${queuedTurnId || "（待分配）"} 已进入队列，当前会话状态为 ${busyStatus}，队列深度 ${queueDepth}，模式 ${queueBehavior}。`;
  }

  if (event.type === "input.dequeued") {
    const queueEntryId = String(payload.queue_entry_id || "").trim();
    const remaining = String(payload.remaining_queue_depth || "0").trim();
    return `排队输入 ${queueEntryId || "（未知）"} 已出队，剩余 ${remaining} 项。`;
  }

  if (event.type === "queue.interrupted_turn_superseded") {
    const supersededTurnId = String(payload.superseded_turn_id || "").trim();
    const nextTurnId = String(payload.next_turn_id || "").trim();
    return `已中断轮次 ${supersededTurnId || "（未知）"} 被排队轮次 ${nextTurnId || "（未知）"} 顶替。`;
  }

  if (
    event.type === "runtime.interrupt_requested" &&
    String(payload.source || "").trim() === "queued_input"
  ) {
    const queueEntryId = String(payload.queue_entry_id || "").trim();
    return `已请求中断当前执行，以便排队输入 ${queueEntryId || "（未知）"} 下一步运行。`;
  }

  const message = payload.message;
  if (typeof message === "string" && message.trim()) {
    return message;
  }
  const summary = payload.summary;
  if (typeof summary === "string" && summary.trim()) {
    return summary;
  }
  const phase = payload.phase;
  if (typeof phase === "string" && phase.trim()) {
    return phase;
  }
  return "";
}

function bucketLabel(bucket: string) {
  if (bucket === "tool") return "工具";
  if (bucket === "error") return "错误";
  return "会话";
}

function bucketClass(bucket: string) {
  if (bucket === "tool") return "tool";
  if (bucket === "error") return "error";
  return "conversation";
}

function roleLabel(role: string) {
  if (role === "user") return "用户";
  if (role === "assistant") return "助手";
  if (role === "system") return "系统";
  if (role === "tool") return "工具";
  return role;
}

function queueEntryTitle(entry: PendingInputQueueEntry) {
  const content = previewText(String(entry.payload.content || ""), 92);
  if (content) {
    return content;
  }
  const commandName = String(entry.payload.command_name || "").trim();
  if (commandName) {
    return `/${commandName}`;
  }
  return "排队轮次";
}

function eventToneClass(event: ExecutionEvent) {
  if (
    event.type === "input.queued" ||
    event.type === "input.dequeued" ||
    event.type === "queue.interrupted_turn_superseded" ||
    (event.type === "runtime.interrupt_requested" &&
      String(event.payload?.source || "").trim() === "queued_input")
  ) {
    return "queue";
  }
  if (event.type.includes("failed") || event.type.includes("denied")) {
    return "error";
  }
  if (event.type.includes("tool") || event.type.includes("approval")) {
    return "tool";
  }
  return "conversation";
}

function eventToneLabel(event: ExecutionEvent) {
  const tone = eventToneClass(event);
  if (tone === "queue") return "队列";
  if (tone === "error") return "错误";
  if (tone === "tool") return "工具";
  return "事件";
}
</script>

<template>
  <section v-if="sessionStore.session" class="event-console-panel">
    <div class="event-console-head">
      <div>
        <strong>事件控制台</strong>
        <p>
          快照 {{ sessionStore.session.last_snapshot?.stage ?? "暂无" }}
          · v{{ sessionStore.session.last_snapshot?.version ?? 0 }}
        </p>
      </div>
      <span class="registry-tag light">
        {{ sessionStore.replayTimeline.length > 0 ? "回放视图" : "实时视图" }}
      </span>
    </div>

    <div class="event-console-stats">
      <article
        v-for="item in transcriptStats"
        :key="item.key"
        class="event-console-stat"
        :class="`is-${item.tone}`"
      >
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </article>
    </div>

    <div class="event-console-queue">
      <div class="event-console-section-head">
        <strong>排队轮次</strong>
        <span>{{ sessionStore.queuedTurnCount }} 项待处理</span>
      </div>
      <div v-if="queueEntries.length > 0" class="event-console-queue-list">
        <article
          v-for="item in queueEntries"
          :key="item.id"
          class="event-console-queue-item"
        >
          <div class="event-console-queue-head">
            <strong>{{ queueEntryTitle(item) }}</strong>
            <span>{{ new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false }) }}</span>
          </div>
          <div class="event-console-queue-meta">
            <span class="registry-tag queue">{{ queueBehaviorLabel(item.queue_behavior) }}</span>
            <span class="registry-tag light">{{ statusLabel(item.busy_status) }}</span>
            <span class="registry-tag light">{{ statusLabel(item.interrupt_policy) }}</span>
          </div>
          <p>{{ item.reason || "排队输入正在等待当前轮次结束后继续执行。" }}</p>
        </article>
      </div>
      <div v-else class="settings-empty">当前会话没有排队轮次。</div>
    </div>

    <div class="event-console-transcript">
      <div class="event-console-section-head">
        <strong>最近转录</strong>
        <span>可回填上下文的消息会在这里持续可见</span>
      </div>
      <div class="event-console-transcript-list">
        <article
          v-for="item in transcriptEntries"
          :key="item.id"
          class="event-console-transcript-item"
        >
          <div class="event-console-transcript-head">
            <div class="event-console-transcript-meta">
              <span class="registry-tag" :class="bucketClass(item.transcript_bucket)">
                {{ bucketLabel(item.transcript_bucket) }}
              </span>
              <span class="event-console-transcript-role">{{ roleLabel(item.role) }}</span>
              <span class="registry-tag light" :class="{ success: item.context_eligible }">
                {{ item.context_eligible ? "可回填" : "仅展示" }}
              </span>
            </div>
            <span>{{ new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false }) }}</span>
          </div>
          <p>{{ item.content }}</p>
        </article>
        <div v-if="transcriptEntries.length === 0" class="settings-empty">当前还没有可展示的转录记录。</div>
      </div>
    </div>

    <button type="button" class="event-console-collapse" @click="eventsExpanded = !eventsExpanded">
      <div class="event-console-section-head">
        <strong>运行事件</strong>
        <span>{{ timeline.length }} 条</span>
      </div>
      <i :class="['fa-solid', eventsExpanded ? 'fa-chevron-up' : 'fa-chevron-down']"></i>
    </button>

    <div v-if="eventsExpanded" class="event-console-list">
      <article
        v-for="event in timeline"
        :key="`${event.type}-${event.timestamp}-${String(event.payload?.step || '')}`"
        class="event-console-item"
      >
        <div class="event-console-item-head">
          <div class="event-console-item-meta">
            <strong>{{ event.type }}</strong>
            <span class="registry-tag" :class="eventToneClass(event)">{{ eventToneLabel(event) }}</span>
          </div>
          <span>{{ new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false }) }}</span>
        </div>
        <p>{{ eventMessage(event) }}</p>
      </article>
      <div v-if="timeline.length === 0" class="settings-empty">当前还没有运行事件。</div>
    </div>
  </section>
</template>
