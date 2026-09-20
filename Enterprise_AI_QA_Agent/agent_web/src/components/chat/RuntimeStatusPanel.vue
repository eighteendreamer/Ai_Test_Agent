<script setup lang="ts">
import { computed } from "vue";

import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const workerDispatchCount = computed(() => sessionStore.workerDispatches.length);
const pendingApprovalCount = computed(() => sessionStore.pendingApprovals.length);
const isBlocked = computed(() => Boolean(sessionStore.workerFailureGuard?.blocked));
const sessionStatus = computed(() => sessionStore.watcherPhase ?? sessionStore.session?.status ?? "idle");

const phaseLabel = computed(() => {
  switch (sessionStore.watcherPhase) {
    case "queued":
      return t("eventConsole.queued_turns");
    case "running":
      return t("runtime.running");
    case "waiting_approval":
      return t("runtime.waiting_approval");
    case "interrupted":
      return t("runtime.interrupted");
    case "failed":
      return t("runtime.blocked");
    case "completed":
      return t("runtime.completed");
    default:
      return t("runtime.idle");
  }
});

const dialValue = computed(() => {
  switch (sessionStore.watcherPhase) {
    case "queued":
      return "WAIT";
    case "running":
      return "RUN";
    case "waiting_approval":
      return "AUTH";
    case "interrupted":
      return "PAUSE";
    case "failed":
      return "STOP";
    case "completed":
      return "DONE";
    default:
      return "IDLE";
  }
});

const compactMeta = computed(() => {
  if (isBlocked.value) {
    return "ERR";
  }
  if (pendingApprovalCount.value > 0) {
    return `A${pendingApprovalCount.value}`;
  }
  if (sessionStore.session?.is_interrupted) {
    return "INT";
  }
  if (workerDispatchCount.value > 0) {
    return `W${workerDispatchCount.value}`;
  }
  return sessionStore.watcherLastSyncLabel;
});

const statRows = computed(() => [
  { label: t("runtime.status"), value: sessionStatusLabel(sessionStatus.value) },
  { label: t("runtime.sync"), value: sessionStore.watcherLastSyncLabel || "--:--:--" },
  { label: t("runtime.approvals"), value: String(pendingApprovalCount.value) },
  { label: t("runtime.subtasks"), value: String(workerDispatchCount.value) },
]);

function toneForStatus(status: string) {
  if (status === "completed") return "online";
  if (status === "queued" || status === "running" || status === "waiting_approval" || status === "interrupted" || status === "partial") {
    return "degraded";
  }
  return "offline";
}

function sessionStatusLabel(status: string) {
  switch (status) {
    case "queued":
      return t("eventConsole.queued_turns");
    case "running":
      return t("runtime.running");
    case "waiting_approval":
      return t("runtime.waiting_approval");
    case "interrupted":
      return t("runtime.interrupted");
    case "completed":
      return t("runtime.completed");
    case "failed":
      return t("runtime.failed");
    default:
      return t("runtime.idle");
  }
}
</script>

<template>
  <section v-if="sessionStore.session" class="runtime-status-panel">
    <div :class="['runtime-status-square', `is-${toneForStatus(sessionStore.watcherPhase)}`]">
      <div class="runtime-status-square-top">
        <span class="runtime-status-square-code">{{ dialValue }}</span>
        <span v-if="pendingApprovalCount > 0" class="runtime-status-square-dot is-degraded"></span>
        <span v-else-if="isBlocked" class="runtime-status-square-dot is-offline"></span>
        <span v-else class="runtime-status-square-dot" :class="`is-${toneForStatus(sessionStore.watcherPhase)}`"></span>
      </div>
      <strong class="runtime-status-square-label">{{ phaseLabel }}</strong>
      <div class="runtime-status-square-stats">
        <div v-for="item in statRows" :key="item.label" class="runtime-status-square-row">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>
      <span class="runtime-status-square-meta">{{ compactMeta }}</span>
    </div>
  </section>
</template>
