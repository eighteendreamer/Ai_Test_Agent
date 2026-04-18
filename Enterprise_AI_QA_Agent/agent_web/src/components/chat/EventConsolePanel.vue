<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const timeline = computed(() => {
  if (sessionStore.replayTimeline.length > 0) {
    return sessionStore.replayTimeline.slice(0, 16);
  }
  return sessionStore.activity.slice(0, 16);
});

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
</script>

<template>
  <section v-if="sessionStore.session" class="event-console-panel">
    <div class="event-console-head">
      <div>
        <strong>事件控制台</strong>
        <p>
          快照：
          {{ sessionStore.session.last_snapshot?.stage ?? "无" }}
          · v{{ sessionStore.session.last_snapshot?.version ?? 0 }}
        </p>
      </div>
      <span class="registry-tag light">
        {{ sessionStore.replayTimeline.length > 0 ? "回放" : "实时" }}
      </span>
    </div>

    <div class="event-console-list">
      <article
        v-for="event in timeline"
        :key="`${event.type}-${event.timestamp}-${String(event.payload?.step || '')}`"
        class="event-console-item"
      >
        <div class="event-console-item-head">
          <strong>{{ event.type }}</strong>
          <span>{{ new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false }) }}</span>
        </div>
        <p>{{ eventMessage(event.payload) }}</p>
      </article>
      <div v-if="timeline.length === 0" class="settings-empty">当前还没有运行事件。</div>
    </div>
  </section>
</template>
