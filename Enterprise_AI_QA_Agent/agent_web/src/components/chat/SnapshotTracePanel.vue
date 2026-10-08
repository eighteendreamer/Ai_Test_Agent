<script setup lang="ts">
import { computed } from "vue";

import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";
import { formatServerTime } from "../../utils/datetime";

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
  const modelCalls = graphState.value.model_call_count;
  const controlState =
    typeof graphState.value.control_state === "string" ? graphState.value.control_state : "--";
  const terminationReason =
    typeof graphState.value.termination_reason === "string"
      ? graphState.value.termination_reason
      : "--";

  return [
    { label: t("snapshot.trace_id"), value: sessionStore.latestTraceId || "--" },
    { label: t("snapshot.turn_id"), value: sessionStore.latestTurnId || "--" },
    { label: t("snapshot.stage"), value: snapshot.value?.stage ?? "--" },
    { label: t("snapshot.version"), value: snapshot.value ? `v${snapshot.value.version}` : "--" },
    { label: t("snapshot.control_state"), value: controlState },
    { label: t("snapshot.loop"), value: `${loopIteration}/${maxIterations}` },
    ...(typeof modelCalls === "number"
      ? [{ label: t("snapshot.model_calls"), value: `${modelCalls}/${maxIterations}` }]
      : []),
    { label: t("snapshot.tool_count"), value: String(toolCount) },
    { label: t("snapshot.approval_count"), value: String(pendingApprovals) },
    { label: t("snapshot.termination"), value: terminationReason },
  ];
});
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel snapshot-trace-panel">
    <div class="observability-head">
      <div>
        <strong>{{ t("snapshot.title") }}</strong>
        <p>{{ t("snapshot.desc") }}</p>
      </div>
      <span class="registry-tag light">
        {{
          snapshot?.created_at
            ? formatServerTime(snapshot.created_at, t("snapshot.no_snapshot"))
            : t("snapshot.no_snapshot")
        }}
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
