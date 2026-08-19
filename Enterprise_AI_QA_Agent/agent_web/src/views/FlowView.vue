<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import FlowCanvas from "../features/flow/FlowCanvas.vue";
import FlowInspector from "../features/flow/FlowInspector.vue";
import { subscribeFlowSession } from "../features/flow/openFlowWindow";
import { useFlowSession } from "../features/flow/useFlowSession";
import { t } from "../services/i18n";
import type { FlowStageId } from "../features/flow/stages";

const route = useRoute();
const router = useRouter();

const sessionId = computed(() => String(route.query.session || "").trim());
const turnId = computed(() => String(route.query.turn || "").trim());
const {
  events,
  toolJobs,
  artifacts,
  sideDataLoaded,
  loading,
  error,
  resolvedTurnId,
  statuses,
  graphState,
} = useFlowSession(sessionId, turnId);

const selectedStageId = ref<FlowStageId | "">("");
const inspectorOpen = ref(false);

let stopSessionBridge: (() => void) | null = null;

function applyFlowQuery(nextSessionId: string, nextTurnId: string) {
  if (sessionId.value === nextSessionId && turnId.value === nextTurnId) {
    return;
  }

  const query: Record<string, string> = {};
  if (nextSessionId) {
    query.session = nextSessionId;
  }
  if (nextTurnId) {
    query.turn = nextTurnId;
  }
  void router.replace({ name: "flow", query });
}

onMounted(() => {
  stopSessionBridge = subscribeFlowSession(({ sessionId: nextSessionId, turnId: nextTurnId }) => {
    applyFlowQuery(nextSessionId || "", nextTurnId || "");
  });
});

onBeforeUnmount(() => {
  stopSessionBridge?.();
  stopSessionBridge = null;
});

function openInspector(stageId: FlowStageId) {
  selectedStageId.value = stageId;
  inspectorOpen.value = true;
}

function closeInspector() {
  inspectorOpen.value = false;
}

watch(sessionId, () => {
  selectedStageId.value = "";
  inspectorOpen.value = false;
});
</script>

<template>
  <div class="flow-page">
    <div v-if="!sessionId" class="flow-page-empty">
      <i class="fa-solid fa-diagram-project flow-page-icon"></i>
      <h1>{{ t("flow.title") }}</h1>
      <p>{{ t("flow.no_session") }}</p>
    </div>

    <template v-else>
      <header class="flow-toolbar">
        <div class="flow-toolbar-copy">
          <strong>{{ t("flow.title") }}</strong>
          <span>{{ t("flow.bound_session", { session: sessionId }) }}</span>
          <span v-if="resolvedTurnId">{{ t("flow.bound_turn", { turn: resolvedTurnId }) }}</span>
        </div>
        <div class="flow-toolbar-meta">
          <span v-if="loading">{{ t("flow.loading") }}</span>
          <span v-else-if="error" class="flow-toolbar-error">{{ error }}</span>
          <span v-else>{{ t("flow.live_hint") }}</span>
        </div>
      </header>
      <div class="flow-canvas-wrap">
        <FlowCanvas
          :session-id="sessionId"
          :turn-id="resolvedTurnId"
          :statuses="statuses"
          :selected-stage-id="selectedStageId"
          @select="openInspector"
        />
        <FlowInspector
          :open="inspectorOpen"
          :stage-id="selectedStageId"
          :status="selectedStageId ? statuses[selectedStageId] : ''"
          :events="events"
          :graph-state="graphState"
          :tool-jobs="toolJobs"
          :artifacts="artifacts"
          :artifacts-loaded="sideDataLoaded"
          :turn-id="resolvedTurnId"
          @close="closeInspector"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.flow-page {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  color: var(--text);
}

.flow-page-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  max-width: 520px;
  margin: 0 auto;
  padding: 0 24px;
  text-align: center;
}

.flow-page-icon {
  margin-bottom: 16px;
  font-size: 28px;
  color: var(--muted);
}

.flow-page-empty h1 {
  margin: 0 0 12px;
  font-size: 20px;
  font-weight: 700;
}

.flow-page-empty p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.flow-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.flow-toolbar-copy {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: baseline;
}

.flow-toolbar-copy strong {
  font-size: 14px;
}

.flow-toolbar-copy span,
.flow-toolbar-meta {
  font-size: 12px;
  color: var(--muted);
}

.flow-toolbar-error {
  color: #dc2626;
}

.flow-canvas-wrap {
  flex: 1;
  min-height: 0;
}
</style>
