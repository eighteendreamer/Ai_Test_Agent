<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const jobs = computed(() => sessionStore.recentToolJobs);
const artifacts = computed(() =>
  sessionStore.sessionArtifacts
    .slice()
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 6),
);

function tone(status: string) {
  if (status === "completed") return "online";
  if (status === "running" || status === "waiting_approval" || status === "partial") return "degraded";
  if (status === "failed" || status === "denied" || status === "cancelled") return "offline";
  return "online";
}

function artifactPreview(artifact: { label?: string | null; artifact_type: string; path: string }) {
  return artifact.label || artifact.artifact_type || artifact.path;
}

function statusLabel(status: string) {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "运行中";
    case "waiting_approval":
      return "待审批";
    case "resume_requested":
      return "待恢复";
    case "retry_requested":
      return "待重试";
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
      return status;
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel tool-job-panel">
    <div class="observability-head">
      <div>
        <strong>工具任务</strong>
        <p>展示当前会话的真实执行任务，以及已经落库的运行产物。</p>
      </div>
      <span class="registry-tag light">{{ jobs.length }} 个任务</span>
    </div>

    <div v-if="jobs.length" class="tool-job-list">
      <button
        v-for="job in jobs"
        :key="job.id"
        type="button"
        class="tool-job-item"
        :class="{ 'is-active': sessionStore.selectedToolJob?.id === job.id }"
        @click="sessionStore.inspectToolJob(job.id)"
      >
        <div class="tool-job-item-head">
          <strong>{{ job.tool_name }}</strong>
          <span :class="['runtime-status-square-dot', `is-${tone(job.status)}`]"></span>
        </div>
        <p>{{ job.summary }}</p>
        <div class="tool-job-item-meta">
          <span>{{ statusLabel(job.status) }}</span>
          <span>{{ job.artifact_count }} 个产物</span>
        </div>
      </button>
    </div>
    <div v-else class="settings-empty">当前还没有持久化的工具任务。</div>

    <div v-if="sessionStore.selectedToolJob" class="tool-job-detail">
      <div class="tool-job-detail-head">
        <strong>{{ sessionStore.selectedToolJob.tool_name }}</strong>
        <span class="registry-tag light">{{ statusLabel(sessionStore.selectedToolJob.status) }}</span>
      </div>
      <p class="settings-muted">{{ sessionStore.selectedToolJob.summary }}</p>
      <dl class="snapshot-trace-grid">
        <div>
          <dt>任务 ID</dt>
          <dd>{{ sessionStore.selectedToolJob.id }}</dd>
        </div>
        <div>
          <dt>追踪 ID</dt>
          <dd>{{ sessionStore.selectedToolJob.trace_id }}</dd>
        </div>
        <div>
          <dt>轮次 ID</dt>
          <dd>{{ sessionStore.selectedToolJob.turn_id }}</dd>
        </div>
        <div>
          <dt>尝试次数</dt>
          <dd>{{ sessionStore.selectedToolJob.attempt }}</dd>
        </div>
      </dl>
    </div>

    <div class="tool-artifact-strip">
      <strong>运行产物</strong>
      <div v-if="artifacts.length" class="tool-artifact-list">
        <article v-for="artifact in artifacts" :key="artifact.id" class="tool-artifact-item">
          <strong>{{ artifactPreview(artifact) }}</strong>
          <p>{{ artifact.path }}</p>
        </article>
      </div>
      <div v-else class="settings-empty">当前还没有已存储的产物。</div>
    </div>
  </section>
</template>
