<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { Edge, Node, NodeDragEvent, NodeMouseEvent, VueFlowStore } from "@vue-flow/core";
import { Panel, VueFlow } from "@vue-flow/core";

import type { WorkerDispatchRecord } from "../../types";
import { t } from "../../services/i18n";
import {
  clearSavedPositions,
  defaultStagePositions,
  loadSavedPositions,
  mergeStagePositions,
  nextWorkerPosition,
  saveSavedPositions,
} from "./layout";
import StageNode from "./StageNode.vue";
import type { StageNodeData } from "./StageNode.vue";
import WorkerNode from "./WorkerNode.vue";
import type { WorkerNodeData } from "./WorkerNode.vue";
import {
  FLOW_STATUS_LABEL_KEYS,
  flowStageNodeId,
  flowStageTitle,
  type FlowStageEdge,
  type FlowStageRecord,
  type FlowNodeStatus,
} from "./stages";
import { isWorkerNodeId, workerFlowStatus, workerLabel, workerNodeId, workerSourceStage } from "./workers";

const props = defineProps<{
  sessionId: string;
  turnId: string;
  stages: FlowStageRecord[];
  stageEdges: FlowStageEdge[];
  workers: WorkerDispatchRecord[];
  selectedNodeId?: string;
}>();

const emit = defineEmits<{
  selectStage: [stageId: string];
  selectWorker: [workerId: string];
  drillWorker: [workerId: string];
}>();

type FlowGraphNode = Node<StageNodeData | WorkerNodeData>;

const nodes = ref<FlowGraphNode[]>([]);
const edges = ref<Edge[]>([]);
let flowStore: VueFlowStore | null = null;

function applyFitView() {
  void flowStore?.fitView({ padding: 0.2 });
}

function statusLabel(status: FlowNodeStatus) {
  return t(FLOW_STATUS_LABEL_KEYS[status]);
}

function collectPositions(current: FlowGraphNode[]): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  for (const node of current) {
    positions[node.id] = { x: node.position.x, y: node.position.y };
  }
  return positions;
}

function buildStageNodes(stages: FlowStageRecord[], positions: Record<string, { x: number; y: number }>): FlowGraphNode[] {
  return stages.map((stage) => ({
    id: stage.id,
    type: "stage",
    position: positions[stage.id],
    draggable: true,
    connectable: false,
    selected: props.selectedNodeId === stage.id,
    data: {
      stageId: stage.phase,
      title: t(flowStageTitle(stage.phase)),
      status: stage.status,
      statusLabel: statusLabel(stage.status),
    },
  }));
}

function buildWorkerNode(
  worker: WorkerDispatchRecord,
  index: number,
  position: { x: number; y: number },
): FlowGraphNode {
  const id = workerNodeId(worker, index);
  const status = workerFlowStatus(worker.status);
  return {
    id,
    type: "worker",
    position,
    draggable: true,
    connectable: false,
    selected: props.selectedNodeId === id,
    data: {
      workerId: id,
      title: workerLabel(worker) || t("flow.worker.untitled"),
      agentKey: worker.agent_key || t("flow.inspector.not_carried"),
      status,
      statusLabel: statusLabel(status),
      canDrill: Boolean(String(worker.child_session_id || "").trim()),
    },
  };
}

function buildStageEdges(): Edge[] {
  const statuses = new Map(props.stages.map((stage) => [stage.id, stage.status]));
  return props.stageEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    animated: statuses.get(edge.target) === "running" || statuses.get(edge.target) === "waiting_approval",
    style: {
      stroke: "var(--border)",
    },
  }));
}

function buildWorkerEdges(workers: WorkerDispatchRecord[]): Edge[] {
  return workers.map((worker, index) => {
    const target = workerNodeId(worker, index);
    const source = flowStageNodeId(workerSourceStage(worker));
    const status = workerFlowStatus(worker.status);
    return {
      id: `e-${source}-${target}`,
      source,
      target,
      animated: status === "running" || status === "waiting_approval",
      style: {
        stroke: "var(--border)",
        strokeDasharray: "6 4",
      },
    };
  });
}

function occupiedPositions(current: FlowGraphNode[]): Array<{ x: number; y: number }> {
  return current.map((node) => node.position);
}

function syncWorkerNodes(resetPositions: boolean) {
  const saved = resetPositions ? {} : loadSavedPositions(props.sessionId, props.turnId);
  const stageNodes = nodes.value.filter((node) => !isWorkerNodeId(node.id));
  const existingWorkers = new Map(nodes.value.filter((node) => isWorkerNodeId(node.id)).map((node) => [node.id, node]));
  const nextWorkers: FlowGraphNode[] = [];
  const occupied = occupiedPositions(resetPositions ? stageNodes : [...stageNodes, ...existingWorkers.values()]);

  props.workers.forEach((worker, index) => {
    const id = workerNodeId(worker, index);
    const existing = existingWorkers.get(id);
    const savedPosition = saved[id];
    const position = resetPositions
      ? nextWorkerPosition([...occupied, ...nextWorkers.map((node) => node.position)], stageNodes.length)
      : existing?.position ?? savedPosition ?? nextWorkerPosition(
        [...occupied, ...nextWorkers.map((node) => node.position)],
        stageNodes.length,
      );
    const node = buildWorkerNode(worker, index, position);
    nextWorkers.push(node);
    occupied.push(position);
  });

  nodes.value = [...stageNodes, ...nextWorkers];
  edges.value = [...buildStageEdges(), ...buildWorkerEdges(props.workers)];
}

function applyGraph(resetPositions: boolean) {
  const positions = resetPositions
    ? defaultStagePositions(props.stages.map((stage) => stage.id))
    : mergeStagePositions(props.sessionId, props.turnId, props.stages.map((stage) => stage.id));
  nodes.value = buildStageNodes(props.stages, positions);
  syncWorkerNodes(resetPositions);
}

function persistCurrentPositions() {
  if (!props.sessionId) {
    return;
  }
  saveSavedPositions(props.sessionId, props.turnId, collectPositions(nodes.value));
}

function onNodeClick(event: NodeMouseEvent) {
  if (!isWorkerNodeId(event.node.id)) {
    const stageId = String((event.node.data as { stageId?: unknown })?.stageId || "").trim();
    if (stageId) {
      emit("selectStage", stageId);
    }
    return;
  }
  if (isWorkerNodeId(event.node.id)) {
    emit("selectWorker", event.node.id);
  }
}

function onNodeDoubleClick(event: NodeMouseEvent) {
  if (isWorkerNodeId(event.node.id)) {
    emit("drillWorker", event.node.id);
  }
}

function onNodeDragStop(event: NodeDragEvent) {
  nodes.value = nodes.value.map((node) =>
    node.id === event.node.id
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
  () => props.workers,
  () => {
    syncWorkerNodes(false);
  },
  { deep: true },
);

watch(
  () => props.stages,
  () => {
    applyGraph(false);
  },
  { deep: true },
);

watch(
  () => props.selectedNodeId,
  (selectedNodeId) => {
    nodes.value = nodes.value.map((node) => ({
      ...node,
      selected: node.id === selectedNodeId,
    }));
  },
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
    @node-double-click="onNodeDoubleClick"
    @node-drag-stop="onNodeDragStop"
  >
    <template #node-stage="nodeProps">
      <StageNode v-bind="nodeProps" />
    </template>
    <template #node-worker="nodeProps">
      <WorkerNode v-bind="nodeProps" />
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
.vue-flow__node.selected .stage-node,
.vue-flow__node.selected .worker-node {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}
</style>
