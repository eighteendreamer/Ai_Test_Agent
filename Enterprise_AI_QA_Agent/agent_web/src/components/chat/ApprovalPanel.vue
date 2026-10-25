<script setup lang="ts">
import { computed } from "vue";
import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";
import type { FrameworkApprovalProgress } from "../../types";
import ApprovalDecisionCard from "./ApprovalDecisionCard.vue";

const sessionStore = useSessionStore();
const progress = computed(() => (sessionStore.session?.framework_approval_progress || [])
  .filter((item) => item.phase !== "awaiting_decision"));

function progressLabel(item: FrameworkApprovalProgress) {
  const state = item.phase === "applied" && item.outcome && item.outcome !== "tool_result"
    ? item.outcome : item.phase;
  return t(`approvalPanel.progress_${state}`);
}
</script>

<template>
  <section v-if="sessionStore.pendingApprovals.length || progress.length" class="approval-panel" aria-live="polite">
    <ApprovalDecisionCard v-for="approval in sessionStore.pendingApprovals" :key="approval.id" :approval="approval" />
    <article v-for="item in progress" :key="item.approval_id" class="approval-progress-card">
      <strong>{{ item.tool_name }}</strong>
      <span>{{ item.decision === "pending" ? t("approvalPanel.pending") : t(`approvalPanel.decision_${item.decision}`) }}</span>
      <p>{{ progressLabel(item) }}</p>
    </article>
  </section>
</template>

<style scoped>
.approval-panel {
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
}
.approval-panel :deep(.approval-sidecard) {
  flex-shrink: 0;
}
.approval-progress-card {
  flex-shrink: 0;
  padding: 12px;
  border: 1px solid var(--border-color, #d1d5db);
  border-radius: 12px;
  background: var(--bg-surface, #fff);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.approval-progress-card strong, .approval-progress-card span {
  display: block;
}
.approval-progress-card p {
  margin: 6px 0 0;
}
</style>
