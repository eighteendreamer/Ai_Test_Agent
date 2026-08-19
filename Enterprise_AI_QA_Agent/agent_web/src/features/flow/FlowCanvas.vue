<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { Edge, Node, NodeDragEvent, NodeMouseEvent, VueFlowStore } from "@vue-flow/core";
import { Panel, VueFlow } from "@vue-flow/core";

import { t } from "../../services/i18n";
import {
  clearSavedPositions,
  defaultStagePositions,
  mergeStagePositions,
  saveSavedPositions,
} from "./layout";
import StageNode from "./StageNode.vue";
import type { StageNodeData } from "./StageNode.vue";
import {
  FLOW_STAGE_EDGES,
  FLOW_STAGE_LABEL_KEYS,
  FLOW_STAGES,
  FLOW_STATUS_LABEL_KEYS,
  type FlowNodeStatus,
  type FlowStageId,
} from "./stages";

const props = defineProps<{
  sessionId: string;
  turnId: string;
  statuses: Record<FlowStageId, FlowNodeStatus>;
  selectedStageId?: FlowStageId | "";
}>();

const emit = defineEmits<{
  select: [stageId: FlowStageId];
}>();

type StageGraphNode = Node<StageNodeData, Record<string, never>, "stage">;

const nodes = ref<StageGraphNode[]>([]);
const edges = ref<Edge[]>([]);
let flowStore: VueFlowStore | null = null;

function applyFitView() {
  void flowStore?.fitView({ padding: 0.2 });
}

function statusLabel(status: FlowNodeStatus) {
  return t(FLOW_STATUS_LABEL_KEYS[status]);
}

function collectPositions(current: StageGraphNode[]): Partial<Record<FlowStageId, { x: number; y: number }>> {
  const positions: Partial<Record<FlowStageId, { x: number; y: number }>> = {};
  for (const node of current) {
    if (FLOW_STAGES.includes(node.id as FlowStageId)) {
      positions[node.id as FlowStageId] = { x: node.position.x, y: node.position.y };
    }
  }
  return positions;
}

function buildNodes(positions: Record<FlowStageId, { x: number; y: number }>): StageGraphNode[] {
  return FLOW_STAGES.map((stageId) => ({
    id: stageId,
    type: "stage",
    position: positions[stageId],
    draggable: true,
    connectable: false,
    selected: props.selectedStageId === stageId,
    data: {
      stageId,
      title: t(FLOW_STAGE_LABEL_KEYS[stageId]),
      status: props.statuses[stageId],
      statusLabel: statusLabel(props.statuses[stageId]),
    },
  }));
}

function buildEdges(): Edge[] {
  return FLOW_STAGE_EDGES.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    animated: props.statuses[edge.target] === "running" || props.statuses[edge.target] === "waiting_approval",
    style: {
      stroke: "var(--border)",
    },
  }));
}

function applyGraph(resetPositions: boolean) {
  const positions = resetPositions
    ? defaultStagePositions()
    : mergeStagePositions(props.sessionId, props.turnId);
  nodes.value = buildNodes(positions);
  edges.value = buildEdges();
}

function persistCurrentPositions() {
  if (!props.sessionId) {
    return;
  }
  saveSavedPositions(props.sessionId, props.turnId, collectPositions(nodes.value));
}

function onNodeClick(event: NodeMouseEvent) {
  if (FLOW_STAGES.includes(event.node.id as FlowStageId)) {
    emit("select", event.node.id as FlowStageId);
  }
}

function onNodeDragStop(event: NodeDragEvent) {
  const draggedId = event.node.id as FlowStageId;
  nodes.value = nodes.value.map((node) =>
    node.id === draggedId
      ? {
          ...node,
          position: { x: event.node.position.x, y: event.node.position.y },
        }
      : node,
  );
  persistCurrentPositions();
}

function resetLayout() {
  clearSavedPositions(props.sessionId, props.turnId);
  applyGraph(true);
  void nextTick(() => {
    applyFitView();
  });
}

function onInit(instance: VueFlowStore) {
  flowStore = instance;
  applyFitView();
}

watch(
  () => [props.sessionId, props.turnId] as const,
  () => {
    applyGraph(false);
    void nextTick(() => {
      applyFitView();
    });
  },
  { immediate: true },
);

watch(
  () => props.selectedStageId,
  (selectedStageId) => {
    nodes.value = nodes.value.map((node) => ({
      ...node,
      selected: node.id === selectedStageId,
    }));
  },
);

watch(
  () => props.statuses,
  (nextStatuses) => {
    nodes.value = nodes.value.map((node) => {
      const status = nextStatuses[node.id as FlowStageId];
      return {
        ...node,
        selected: props.selectedStageId === node.id,
        data: {
          ...node.data,
          title: t(FLOW_STAGE_LABEL_KEYS[node.id as FlowStageId]),
          status,
          statusLabel: statusLabel(status),
        },
      };
    });
    edges.value = edges.value.map((edge) => ({
      ...edge,
      animated: nextStatuses[edge.target as FlowStageId] === "running"
        || nextStatuses[edge.target as FlowStageId] === "waiting_approval",
    }));
  },
  { deep: true },
);
</script>

<template>
  <VueFlow
    v-model:nodes="nodes"
    v-model:edges="edges"
    :nodes-connectable="false"
    :edges-updatable="false"
    :connect-on-click="false"
    :delete-key-code="null"
    :min-zoom="0.35"
    :max-zoom="1.6"
    :fit-view-on-init="true"
    class="flow-canvas"
    @init="onInit"
    @node-click="onNodeClick"
    @node-drag-stop="onNodeDragStop"
  >
    <template #node-stage="nodeProps">
      <StageNode v-bind="nodeProps" />
    </template>
    <Panel position="top-right" class="flow-canvas-panel">
      <button type="button" class="flow-reset-btn" @click="resetLayout">
        {{ t("flow.reset_layout") }}
      </button>
    </Panel>
  </VueFlow>
</template>

<style scoped>
.flow-canvas {
  width: 100%;
  height: 100%;
  background: var(--bg);
}

.flow-canvas-panel {
  margin: 12px;
}

.flow-reset-btn {
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  cursor: pointer;
}

.flow-reset-btn:hover {
  border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
}
</style>

<style>
.vue-flow__node.selected .stage-node {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}
</style>
