<script setup lang="ts">
import { computed } from "vue";

import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";
import { serverDateTimestamp } from "../../utils/datetime";

const sessionStore = useSessionStore();

const jobs = computed(() => sessionStore.recentToolJobs);
const artifacts = computed(() =>
  sessionStore.sessionArtifacts
    .slice()
    .sort((a, b) => serverDateTimestamp(b.created_at) - serverDateTimestamp(a.created_at))
    .slice(0, 6),
);

function tone(status: string) {
  if (status === "completed") return "online";
  if (status === "running" || status === "waiting_approval" || status === "waiting_children" || status === "partial") return "degraded";
  if (status === "failed" || status === "denied" || status === "cancelled") return "offline";
  return "online";
}

function artifactPreview(artifact: { label?: string | null; artifact_type: string; path: string }) {
  return artifact.label || artifact.artifact_type || artifact.path;
}

function fileArtifactLabel(count: number) {
  return count > 0 ? `${count} ${t("toolJob.file_artifacts_unit")}` : t("toolJob.no_file_artifacts");
}

function statusLabel(status: string) {
  switch (status) {
    case "queued":
      return t("toolJob.status_queued");
    case "running":
      return t("toolJob.status_running");
    case "waiting_approval":
      return t("toolJob.status_waiting_approval");
    case "waiting_children":
      return t("toolJob.status_waiting_children");
    case "resume_requested":
      return t("toolJob.status_resume_requested");
    case "retry_requested":
      return t("toolJob.status_retry_requested");
    case "completed":
      return t("toolJob.status_completed");
    case "partial":
      return t("toolJob.status_partial");
    case "failed":
      return t("toolJob.status_failed");
    case "denied":
      return t("toolJob.status_denied");
    case "cancelled":
      return t("toolJob.status_cancelled");
    default:
      return status;
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel tool-job-panel">
    <div class="observability-head">
      <div>
        <strong>{{ t("toolJob.title") }}</strong>
        <p>{{ t("toolJob.desc") }}</p>
      </div>
      <span class="registry-tag light">{{ jobs.length }} {{ t("toolJob.jobs_unit") }}</span>
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
          <span>{{ fileArtifactLabel(job.artifact_count) }}</span>
        </div>
      </button>
    </div>
    <div v-else class="settings-empty">{{ t("toolJob.empty") }}</div>

    <div v-if="sessionStore.selectedToolJob" class="tool-job-detail">
      <div class="tool-job-detail-head">
        <strong>{{ sessionStore.selectedToolJob.tool_name }}</strong>
        <span class="registry-tag light">{{ statusLabel(sessionStore.selectedToolJob.status) }}</span>
      </div>
      <p class="settings-muted">{{ sessionStore.selectedToolJob.summary }}</p>
      <dl class="snapshot-trace-grid">
        <div>
          <dt>{{ t("toolJob.job_id") }}</dt>
          <dd>{{ sessionStore.selectedToolJob.id }}</dd>
        </div>
        <div>
          <dt>{{ t("toolJob.trace_id") }}</dt>
          <dd>{{ sessionStore.selectedToolJob.trace_id }}</dd>
        </div>
        <div>
          <dt>{{ t("toolJob.turn_id") }}</dt>
          <dd>{{ sessionStore.selectedToolJob.turn_id }}</dd>
        </div>
        <div>
          <dt>{{ t("toolJob.attempt_count") }}</dt>
          <dd>{{ sessionStore.selectedToolJob.attempt }}</dd>
        </div>
      </dl>
    </div>

    <div class="tool-artifact-strip">
      <strong>{{ t("toolJob.file_artifacts") }}</strong>
      <div v-if="artifacts.length" class="tool-artifact-list">
        <article v-for="artifact in artifacts" :key="artifact.id" class="tool-artifact-item">
          <strong>{{ artifactPreview(artifact) }}</strong>
          <p>{{ artifact.path }}</p>
        </article>
      </div>
      <div v-else class="settings-empty">{{ t("toolJob.no_artifacts_stored") }}</div>
    </div>
  </section>
</template>
