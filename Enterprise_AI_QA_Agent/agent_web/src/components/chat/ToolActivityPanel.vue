<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const toolResults = computed(() => sessionStore.latestToolResults.slice().reverse().slice(0, 8));

function artifactCount(output: Record<string, unknown>) {
  const artifacts = output?.artifacts;
  return Array.isArray(artifacts) ? artifacts.length : 0;
}

function metricCount(output: Record<string, unknown>) {
  const metrics = output?.metrics;
  return metrics && typeof metrics === "object" && !Array.isArray(metrics)
    ? Object.keys(metrics).length
    : 0;
}

function tone(status: string) {
  if (status === "completed") return "online";
  if (status === "partial" || status === "waiting_approval") return "degraded";
  if (status === "denied" || status === "failed") return "offline";
  return "online";
}

function statusLabel(status: string) {
  switch (status) {
    case "completed":
      return "已完成";
    case "partial":
      return "部分完成";
    case "waiting_approval":
      return "待审批";
    case "denied":
      return "已拒绝";
    case "failed":
      return "失败";
    default:
      return status;
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel">
    <div class="observability-head">
      <div>
        <strong>工具活动</strong>
        <p>展示当前快照中最近一次记录的工具执行情况。</p>
      </div>
      <span class="registry-tag light">{{ toolResults.length }} 个工具</span>
    </div>

    <div v-if="toolResults.length" class="tool-activity-list">
      <article
        v-for="tool in toolResults"
        :key="`${tool.call_id}-${tool.tool_key}`"
        class="tool-activity-item"
      >
        <div class="tool-activity-head">
          <div>
            <strong>{{ tool.tool_name || tool.tool_key }}</strong>
            <p>{{ tool.summary }}</p>
          </div>
          <span :class="['runtime-status-square-dot', `is-${tone(tool.status)}`]"></span>
        </div>

        <dl class="tool-activity-grid">
          <div>
            <dt>状态</dt>
            <dd>{{ statusLabel(tool.status) }}</dd>
          </div>
          <div>
            <dt>产物</dt>
            <dd>{{ artifactCount(tool.output) }}</dd>
          </div>
          <div>
            <dt>指标</dt>
            <dd>{{ metricCount(tool.output) }}</dd>
          </div>
          <div v-if="tool.trace_id">
            <dt>追踪 ID</dt>
            <dd>{{ tool.trace_id }}</dd>
          </div>
          <div v-if="tool.approval_id">
            <dt>审批单</dt>
            <dd>{{ tool.approval_id }}</dd>
          </div>
          <div v-if="tool.job_id">
            <dt>任务</dt>
            <dd>{{ tool.job_id }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <div v-else class="settings-empty">当前会话还没有记录到工具活动。</div>
  </section>
</template>
