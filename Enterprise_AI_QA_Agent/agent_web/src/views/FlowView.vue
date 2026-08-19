<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import FlowCanvas from "../features/flow/FlowCanvas.vue";
import FlowInspector from "../features/flow/FlowInspector.vue";
import { subscribeFlowSession } from "../features/flow/openFlowWindow";
import { useFlowSession } from "../features/flow/useFlowSession";
import { workerFlowStatus, workerLabel, workerNodeId } from "../features/flow/workers";
import { t } from "../services/i18n";
import type { FlowStageId } from "../features/flow/stages";

interface FlowCrumb {
  sessionId: string;
  turnId: string;
  label: string;
}

const route = useRoute();
const router = useRouter();

const rootSessionId = computed(() => String(route.query.session || "").trim());
const rootTurnId = computed(() => String(route.query.turn || "").trim());
const viewingSessionId = ref(rootSessionId.value);
const viewingTurnId = ref(rootTurnId.value);
const viewingLabel = ref("");
const crumbs = ref<FlowCrumb[]>([]);

const {
  events,
  workers,
  toolJobs,
  artifacts,
  sideDataLoaded,
  loading,
  error,
  resolvedTurnId,
  statuses,
  graphState,
} = useFlowSession(viewingSessionId, viewingTurnId);

const selectedStageId = ref<FlowStageId | "">("");
const selectedWorkerId = ref("");
const inspectorOpen = ref(false);

const selectedWorker = computed(
  () => workers.value.find((worker, index) => workerNodeId(worker, index) === selectedWorkerId.value) ?? null,
);
const selectedNodeId = computed(() => selectedWorkerId.value || selectedStageId.value);
const selectedStatus = computed(() => {
  if (selectedWorker.value) {
    return workerFlowStatus(selectedWorker.value.status);
  }
  return selectedStageId.value ? statuses.value[selectedStageId.value] : "";
});
const isChildView = computed(() => crumbs.value.length > 0);

let stopSessionBridge: (() => void) | null = null;

function applyFlowQuery(nextSessionId: string, nextTurnId: string) {
  if (rootSessionId.value === nextSessionId && rootTurnId.value === nextTurnId) {
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

function closeInspector() {
  inspectorOpen.value = false;
}

function resetSelection() {
  selectedStageId.value = "";
  selectedWorkerId.value = "";
  closeInspector();
}

function resetToRoot() {
  crumbs.value = [];
  viewingLabel.value = t("flow.breadcrumb.root");
  viewingSessionId.value = rootSessionId.value;
  viewingTurnId.value = rootTurnId.value;
  resetSelection();
}

function drillInto(childSessionId: string, label: string) {
  const nextSessionId = String(childSessionId || "").trim();
  if (!nextSessionId || nextSessionId === viewingSessionId.value) {
    return;
  }
  crumbs.value = [
    ...crumbs.value,
    {
      sessionId: viewingSessionId.value,
      turnId: viewingTurnId.value || resolvedTurnId.value,
      label: crumbs.value.length === 0 ? t("flow.breadcrumb.root") : viewingLabel.value,
    },
  ];
  viewingLabel.value = label || t("flow.breadcrumb.child");
  viewingSessionId.value = nextSessionId;
  viewingTurnId.value = "";
  resetSelection();
}

function goToCrumb(index: number) {
  const frame = crumbs.value[index];
  if (!frame) {
    return;
  }
  viewingLabel.value = frame.label;
  viewingSessionId.value = frame.sessionId;
  viewingTurnId.value = frame.turnId;
  crumbs.value = crumbs.value.slice(0, index);
  resetSelection();
}

function openStageInspector(stageId: FlowStageId) {
  selectedStageId.value = stageId;
  selectedWorkerId.value = "";
  inspectorOpen.value = true;
}

function openWorkerInspector(workerId: string) {
  selectedWorkerId.value = workerId;
  selectedStageId.value = "";
  inspectorOpen.value = true;
}

function drillSelectedWorker(workerId?: string) {
  const id = workerId || selectedWorkerId.value;
  const worker = workers.value.find((item, index) => workerNodeId(item, index) === id);
  const childSessionId = String(worker?.child_session_id || "").trim();
  if (!worker || !childSessionId) {
    return;
  }
  drillInto(childSessionId, workerLabel(worker) || t("flow.worker.untitled"));
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

watch(
  [rootSessionId, rootTurnId],
  () => {
    resetToRoot();
  },
  { immediate: true },
);
</script>

<template>
  <div class="flow-page">
    <div v-if="!rootSessionId" class="flow-page-empty">
      <i class="fa-solid fa-diagram-project flow-page-icon"></i>
      <h1>{{ t("flow.title") }}</h1>
      <p>{{ t("flow.no_session") }}</p>
    </div>

    <template v-else>
      <header class="flow-toolbar">
        <div class="flow-toolbar-copy">
          <strong>{{ t("flow.title") }}</strong>
          <nav v-if="isChildView" class="flow-breadcrumb" :aria-label="t('flow.breadcrumb.root')">
            <template v-for="(crumb, index) in crumbs" :key="`${crumb.sessionId}-${index}`">
              <button type="button" class="flow-breadcrumb-btn" @click="goToCrumb(index)">
                {{ crumb.label }}
              </button>
              <span class="flow-breadcrumb-sep" aria-hidden="true">/</span>
            </template>
            <span class="flow-breadcrumb-current">{{ viewingLabel || t("flow.breadcrumb.child") }}</span>
          </nav>
          <span>{{ t("flow.bound_session", { session: viewingSessionId }) }}</span>
          <span v-if="resolvedTurnId">{{ t("flow.bound_turn", { turn: resolvedTurnId }) }}</span>
          <span v-if="isChildView">{{ t("flow.viewing_child") }}</span>
        </div>
        <div class="flow-toolbar-meta">
          <span v-if="loading">{{ t("flow.loading") }}</span>
          <span v-else-if="error" class="flow-toolbar-error">{{ error }}</span>
          <span v-else>{{ t("flow.live_hint") }}</span>
        </div>
      </header>
      <div class="flow-canvas-wrap">
        <FlowCanvas
          :session-id="viewingSessionId"
          :turn-id="resolvedTurnId"
          :statuses="statuses"
          :workers="workers"
          :selected-node-id="selectedNodeId"
          @select-stage="openStageInspector"
          @select-worker="openWorkerInspector"
          @drill-worker="drillSelectedWorker"
        />
        <FlowInspector
          :open="inspectorOpen"
          :stage-id="selectedStageId"
          :worker="selectedWorker"
          :status="selectedStatus"
          :events="events"
          :graph-state="graphState"
          :tool-jobs="toolJobs"
          :artifacts="artifacts"
          :artifacts-loaded="sideDataLoaded"
          :turn-id="resolvedTurnId"
          @close="closeInspector"
          @drill="drillInto($event, selectedWorker ? workerLabel(selectedWorker) : t('flow.breadcrumb.child'))"
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

.flow-breadcrumb {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.flow-breadcrumb-btn {
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  color: var(--accent);
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.flow-breadcrumb-sep,
.flow-breadcrumb-current {
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
