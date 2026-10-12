<script setup lang="ts">
import { computed } from "vue";

import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const toolResults = computed(() => sessionStore.latestToolResults.slice().reverse().slice(0, 8));

function artifactCount(output: Record<string, unknown>) {
  const artifacts = output?.artifacts;
  return Array.isArray(artifacts) ? artifacts.length : 0;
}

function fileArtifactLabel(output: Record<string, unknown>) {
  const count = artifactCount(output);
  return count > 0 ? `${count} ${t("toolActivity.files_unit")}` : t("toolActivity.no_files");
}

function metricCount(output: Record<string, unknown>) {
  const metrics = output?.metrics;
  return metrics && typeof metrics === "object" && !Array.isArray(metrics)
    ? Object.keys(metrics).length
    : 0;
}

function tone(status: string) {
  if (status === "completed") return "online";
  if (status === "partial" || status === "waiting_approval" || status === "waiting_children") return "degraded";
  if (status === "denied" || status === "failed") return "offline";
  return "online";
}

function statusLabel(status: string) {
  switch (status) {
    case "completed":
      return t("toolActivity.status_completed");
    case "partial":
      return t("toolActivity.status_partial");
    case "waiting_approval":
      return t("toolActivity.status_waiting_approval");
    case "waiting_children":
      return t("toolActivity.status_waiting_children");
    case "denied":
      return t("toolActivity.status_denied");
    case "failed":
      return t("toolActivity.status_failed");
    default:
      return status;
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel tool-activity-panel">
    <div class="observability-head">
      <div>
        <strong>{{ t("toolActivity.title") }}</strong>
        <p>{{ t("toolActivity.desc") }}</p>
      </div>
      <span class="registry-tag light">{{ toolResults.length }} {{ t("toolActivity.tools_unit") }}</span>
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
            <dt>{{ t("toolActivity.col_status") }}</dt>
            <dd>{{ statusLabel(tool.status) }}</dd>
          </div>
          <div>
            <dt>{{ t("toolActivity.col_file_artifacts") }}</dt>
            <dd>{{ fileArtifactLabel(tool.output) }}</dd>
          </div>
          <div>
            <dt>{{ t("toolActivity.col_metrics") }}</dt>
            <dd>{{ metricCount(tool.output) }}</dd>
          </div>
          <div v-if="tool.trace_id">
            <dt>{{ t("toolActivity.col_trace_id") }}</dt>
            <dd>{{ tool.trace_id }}</dd>
          </div>
          <div v-if="tool.approval_id">
            <dt>{{ t("toolActivity.col_approval_id") }}</dt>
            <dd>{{ tool.approval_id }}</dd>
          </div>
          <div v-if="tool.job_id">
            <dt>{{ t("toolActivity.col_job_id") }}</dt>
            <dd>{{ tool.job_id }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <div v-else class="settings-empty">{{ t("toolActivity.empty") }}</div>
  </section>
</template>
