<script setup lang="ts">
import type { NodeProps } from "@vue-flow/core";
import { Handle, Position } from "@vue-flow/core";

import type { FlowNodeStatus, FlowStageId } from "./stages";

export interface StageNodeData {
  stageId: FlowStageId;
  title: string;
  status: FlowNodeStatus;
  statusLabel: string;
}

const props = defineProps<NodeProps<StageNodeData>>();
</script>

<template>
  <div :class="['stage-node', `is-${props.data.status}`]">
    <Handle type="target" :position="Position.Left" :connectable="false" />
    <div class="stage-node-head">
      <span class="stage-node-dot"></span>
      <strong>{{ props.data.title }}</strong>
    </div>
    <div class="stage-node-status">{{ props.data.statusLabel }}</div>
    <Handle type="source" :position="Position.Right" :connectable="false" />
  </div>
</template>

<style scoped>
.stage-node {
  min-width: 176px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
}

.stage-node-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-node-head strong {
  font-size: 13px;
  font-weight: 700;
}

.stage-node-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--muted);
  flex-shrink: 0;
}

.stage-node-status {
  margin-top: 6px;
  font-size: 11px;
  color: var(--muted);
}

.stage-node.is-running {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16);
}

.stage-node.is-running .stage-node-dot {
  background: #2563eb;
  animation: stage-node-pulse 1.2s ease-in-out infinite;
}

.stage-node.is-done {
  border-color: rgba(22, 163, 74, 0.45);
}

.stage-node.is-done .stage-node-dot {
  background: #16a34a;
}

.stage-node.is-failed {
  border-color: rgba(220, 38, 38, 0.5);
}

.stage-node.is-failed .stage-node-dot,
.stage-node.is-failed .stage-node-status {
  color: #dc2626;
}

.stage-node.is-failed .stage-node-dot {
  background: #dc2626;
}

.stage-node.is-waiting_approval {
  border-color: rgba(217, 119, 6, 0.5);
}

.stage-node.is-waiting_approval .stage-node-dot {
  background: #d97706;
}

@keyframes stage-node-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.82);
  }
}
</style>
