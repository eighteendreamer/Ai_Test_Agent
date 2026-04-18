<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const workerDispatchCount = computed(() => sessionStore.workerDispatches.length);
const pendingApprovalCount = computed(() => sessionStore.pendingApprovals.length);
const isBlocked = computed(() => Boolean(sessionStore.workerFailureGuard?.blocked));
const sessionStatus = computed(() => sessionStore.session?.status ?? "idle");

const phaseLabel = computed(() => {
  switch (sessionStore.watcherPhase) {
    case "running":
      return "运行中";
    case "waiting_approval":
      return "待审批";
    case "interrupted":
      return "已中断";
    case "failed":
      return "阻塞中";
    case "completed":
      return "已完成";
    default:
      return "空闲";
  }
});

const dialValue = computed(() => {
  switch (sessionStore.watcherPhase) {
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
  { label: "状态", value: sessionStatusLabel(sessionStatus.value) },
  { label: "同步", value: sessionStore.watcherLastSyncLabel || "--:--:--" },
  { label: "审批", value: String(pendingApprovalCount.value) },
  { label: "子任务", value: String(workerDispatchCount.value) },
]);

function toneForStatus(status: string) {
  if (status === "completed") return "online";
  if (status === "running" || status === "waiting_approval" || status === "interrupted" || status === "partial") {
    return "degraded";
  }
  return "offline";
}

function sessionStatusLabel(status: string) {
  switch (status) {
    case "running":
      return "运行中";
    case "waiting_approval":
      return "待审批";
    case "interrupted":
      return "已中断";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    default:
      return "空闲";
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
