<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const results = computed(() => {
  const primary = sessionStore.verificationMeta?.verification_results;
  if (Array.isArray(primary) && primary.length > 0) {
    return primary.slice().reverse().slice(0, 8);
  }
  return (sessionStore.session?.verification_results ?? []).slice().reverse().slice(0, 8);
});

function tone(status: string) {
  if (status === "passed") return "online";
  if (status === "partial" || status === "not_run") return "degraded";
  return "offline";
}

function statusLabel(status: string) {
  switch (status) {
    case "passed":
      return "通过";
    case "failed":
      return "失败";
    case "partial":
      return "部分通过";
    case "not_run":
      return "未执行";
    default:
      return status;
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel verification-panel">
    <div class="observability-head">
      <div>
        <strong>验证结果</strong>
        <p>这里展示从执行证据中独立派生出的结构化验证结论。</p>
      </div>
      <span class="registry-tag light">{{ results.length }} 条结果</span>
    </div>

    <div v-if="results.length" class="tool-activity-list">
      <article v-for="item in results" :key="item.id" class="tool-activity-item">
        <div class="tool-activity-head">
          <div>
            <strong>{{ item.verifier }}</strong>
            <p>{{ item.summary }}</p>
          </div>
          <span :class="['runtime-status-square-dot', `is-${tone(item.status)}`]"></span>
        </div>
        <dl class="tool-activity-grid">
          <div>
            <dt>状态</dt>
            <dd>{{ statusLabel(item.status) }}</dd>
          </div>
          <div>
            <dt>断言数</dt>
            <dd>{{ item.assertion_count }}</dd>
          </div>
          <div>
            <dt>通过</dt>
            <dd>{{ item.passed_count }}</dd>
          </div>
          <div>
            <dt>失败</dt>
            <dd>{{ item.failed_count }}</dd>
          </div>
          <div v-if="item.evidence?.length">
            <dt>证据</dt>
            <dd>{{ item.evidence.length }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <div v-else class="settings-empty">当前还没有记录到验证结果。</div>
  </section>
</template>
