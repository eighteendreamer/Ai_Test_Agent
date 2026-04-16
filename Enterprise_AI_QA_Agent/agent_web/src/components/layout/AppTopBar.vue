<script setup lang="ts">
import { computed } from "vue";

import type { ServiceCheckItem, SystemStatusSummary } from "../../types";

const props = defineProps<{
  label: string;
  systemStatus: SystemStatusSummary;
}>();

defineEmits<{
  (event: "quick-run"): void;
}>();

const failingChecks = computed(() =>
  props.systemStatus.checks.filter((check) => check.status !== "online"),
);

function statusIcon(check: ServiceCheckItem) {
  if (check.status === "online") {
    return "fa-circle-check";
  }
  if (check.status === "degraded") {
    return "fa-triangle-exclamation";
  }
  return "fa-circle-xmark";
}
</script>

<template>
  <header class="top-status-bar">
    <div class="top-status-path">
      <i class="fa-solid fa-spider"></i>
      <span>御策天检 / {{ props.label }}</span>
    </div>
    <div class="top-status-actions">
      <div class="service-indicator-wrap">
        <span :class="['service-indicator', `is-${props.systemStatus.tone}`]">
          <span class="service-dot"></span>
          <span>{{ props.systemStatus.label }}</span>
          <span class="service-count">
            {{ props.systemStatus.onlineCount }}/{{ props.systemStatus.totalCount }}
          </span>
        </span>
        <div class="service-tooltip">
          <div class="service-tooltip-head">
            <strong>系统连接状态</strong>
            <span v-if="failingChecks.length === 0">全部已连接</span>
            <span v-else>未就绪 {{ failingChecks.length }} 项</span>
          </div>
          <div class="service-tooltip-list">
            <div
              v-for="check in props.systemStatus.checks"
              :key="check.key"
              :class="['service-tooltip-item', `is-${check.status}`]"
            >
              <div class="service-tooltip-title">
                <i :class="['fa-solid', statusIcon(check)]"></i>
                <span>{{ check.label }}</span>
              </div>
              <div class="service-tooltip-detail">{{ check.detail }}</div>
              <div v-if="check.meta" class="service-tooltip-meta">{{ check.meta }}</div>
            </div>
          </div>
        </div>
      </div>
      <div class="status-divider"></div>
      <button class="quick-run-btn" @click="$emit('quick-run')">
        <i class="fa-solid fa-play"></i>
        一键测试
      </button>
    </div>
  </header>
</template>
