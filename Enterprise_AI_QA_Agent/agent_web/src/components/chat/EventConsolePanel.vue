<script setup lang="ts">
import { computed, ref } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();
const eventsExpanded = ref(false);

const timeline = computed(() => {
  if (sessionStore.replayTimeline.length > 0) {
    return sessionStore.replayTimeline.slice(0, 16);
  }
  return sessionStore.activity.slice(0, 16);
});

const transcriptStats = computed(() => {
  const summary = sessionStore.transcriptSummary;
  return [
    { key: "conversation", label: "会话消息", value: summary.conversation_count, tone: "neutral" },
    { key: "tool", label: "工具消息", value: summary.tool_count, tone: "tool" },
    { key: "error", label: "错误消息", value: summary.error_count, tone: "error" },
    { key: "eligible", label: "可回喂上下文", value: summary.context_eligible_count, tone: "success" },
  ];
});

const transcriptEntries = computed(() => sessionStore.recentTranscriptEntries.slice(0, 6));

function eventMessage(payload: Record<string, unknown>) {
  const message = payload?.message;
  if (typeof message === "string" && message.trim()) {
    return message;
  }
  const summary = payload?.summary;
  if (typeof summary === "string" && summary.trim()) {
    return summary;
  }
  const phase = payload?.phase;
  if (typeof phase === "string" && phase.trim()) {
    return phase;
  }
  return "";
}

function bucketLabel(bucket: string) {
  if (bucket === "tool") return "工具证据";
  if (bucket === "error") return "错误记录";
  return "会话内容";
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
</script>

<template>
  <section v-if="sessionStore.session" class="event-console-panel">
    <div class="event-console-head">
      <div>
        <strong>事件控制台</strong>
        <p>
          快照：{{ sessionStore.session.last_snapshot?.stage ?? "暂无" }}
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

    <div class="event-console-transcript">
      <div class="event-console-section-head">
        <strong>最近 Transcript Buckets</strong>
        <span>区分可回看证据与可回喂上下文</span>
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
                {{ item.context_eligible ? "可回喂" : "仅展示" }}
              </span>
            </div>
            <span>{{ new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false }) }}</span>
          </div>
          <p>{{ item.content }}</p>
        </article>
        <div v-if="transcriptEntries.length === 0" class="settings-empty">当前还没有可展示的 transcript 记录。</div>
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
          <strong>{{ event.type }}</strong>
          <span>{{ new Date(event.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }}</span>
        </div>
        <p>{{ eventMessage(event.payload) }}</p>
      </article>
      <div v-if="timeline.length === 0" class="settings-empty">当前还没有运行事件。</div>
    </div>
  </section>
</template>
