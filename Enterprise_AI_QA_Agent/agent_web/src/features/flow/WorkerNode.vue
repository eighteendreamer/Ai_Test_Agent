<script setup lang="ts">
import type { NodeProps } from "@vue-flow/core";
import { Handle, Position } from "@vue-flow/core";

import { t } from "../../services/i18n";
import type { FlowNodeStatus } from "./stages";

export interface WorkerNodeData {
  workerId: string;
  title: string;
  agentKey: string;
  status: FlowNodeStatus;
  statusLabel: string;
  canDrill: boolean;
}

const props = defineProps<NodeProps<WorkerNodeData>>();
</script>

<template>
  <div :class="['worker-node', `is-${props.data.status}`]">
    <Handle type="target" :position="Position.Left" :connectable="false" />
    <div class="worker-node-kicker">{{ t("flow.worker.kicker") }}</div>
    <div class="stage-node-head">
      <span class="stage-node-dot"></span>
      <strong>{{ props.data.title }}</strong>
    </div>
    <div class="stage-node-status">
      {{ props.data.agentKey }}
      <template v-if="props.data.statusLabel"> · {{ props.data.statusLabel }}</template>
    </div>
    <Handle type="source" :position="Position.Right" :connectable="false" />
  </div>
</template>

<style scoped>
.worker-node {
  min-width: 188px;
  max-width: 240px;
  padding: 12px 14px;
  border: 1px dashed var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
}

.worker-node-kicker {
  margin-bottom: 6px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

.stage-node-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-node-head strong {
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
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

.worker-node.is-running {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16);
}

.worker-node.is-running .stage-node-dot {
  background: #2563eb;
}

.worker-node.is-done {
  border-color: rgba(22, 163, 74, 0.45);
}

.worker-node.is-done .stage-node-dot {
  background: #16a34a;
}

.worker-node.is-failed {
  border-color: rgba(220, 38, 38, 0.5);
}

.worker-node.is-failed .stage-node-dot {
  background: #dc2626;
}

.worker-node.is-waiting_approval {
  border-color: rgba(217, 119, 6, 0.5);
}

.worker-node.is-waiting_approval .stage-node-dot {
  background: #d97706;
}
</style>
