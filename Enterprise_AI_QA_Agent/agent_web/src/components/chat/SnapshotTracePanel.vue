<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const snapshot = computed(() => sessionStore.session?.last_snapshot ?? null);
const graphState = computed(() => sessionStore.latestSnapshotGraphState);

const rows = computed(() => {
  const toolCount = sessionStore.latestToolResults.length;
  const pendingApprovals = Array.isArray(graphState.value.pending_approvals)
    ? graphState.value.pending_approvals.length
    : 0;
  const loopIteration = Number(graphState.value.loop_iteration ?? 0);
  const maxIterations = Number(graphState.value.max_iterations ?? 0);
  const controlState = typeof graphState.value.control_state === "string" ? graphState.value.control_state : "--";
  const terminationReason = typeof graphState.value.termination_reason === "string" ? graphState.value.termination_reason : "--";

  return [
    { label: "追踪 ID", value: sessionStore.latestTraceId || "--" },
    { label: "轮次 ID", value: sessionStore.latestTurnId || "--" },
    { label: "阶段", value: snapshot.value?.stage ?? "--" },
    { label: "版本", value: snapshot.value ? `v${snapshot.value.version}` : "--" },
    { label: "控制态", value: controlState },
    { label: "循环", value: `${loopIteration}/${maxIterations}` },
    { label: "工具数", value: String(toolCount) },
    { label: "审批数", value: String(pendingApprovals) },
    { label: "结束态", value: terminationReason },
  ];
});
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel">
    <div class="observability-head">
      <div>
        <strong>快照追踪</strong>
        <p>展示当前运行态对应的快照、追踪和循环元信息。</p>
      </div>
      <span class="registry-tag light">
        {{ snapshot?.created_at ? new Date(snapshot.created_at).toLocaleTimeString("zh-CN", { hour12: false }) : "暂无快照" }}
      </span>
    </div>

    <dl class="snapshot-trace-grid">
      <div v-for="item in rows" :key="item.label">
        <dt>{{ item.label }}</dt>
        <dd>{{ item.value }}</dd>
      </div>
    </dl>
  </section>
</template>
