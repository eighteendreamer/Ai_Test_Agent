<script setup lang="ts">
import { computed } from "vue";

import type { HealthResponse } from "../../types";

const props = defineProps<{
  label: string;
  health: HealthResponse | null;
}>();

defineEmits<{
  (event: "quick-run"): void;
}>();

const serviceLabel = computed(() => (props.health?.status === "ok" ? "服务在线" : "等待连接"));
</script>

<template>
  <header class="top-status-bar">
    <div class="top-status-path">
      <i class="fa-solid fa-spider"></i>
      <span>御策天检 / {{ props.label }}</span>
    </div>
    <div class="top-status-actions">
      <span class="service-indicator">
        <span class="service-dot"></span>
        {{ serviceLabel }}
      </span>
      <div class="status-divider"></div>
      <button class="quick-run-btn" @click="$emit('quick-run')">
        <i class="fa-solid fa-play"></i>
        一键测试
      </button>
    </div>
  </header>
</template>
