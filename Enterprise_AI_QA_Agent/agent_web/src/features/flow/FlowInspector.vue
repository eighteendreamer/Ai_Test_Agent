<script setup lang="ts">
import { computed, ref, watch } from "vue";

import DropdownSelect from "../../components/common/DropdownSelect.vue";
import { t } from "../../services/i18n";
import type { ExecutionEvent, ToolArtifactRecord, ToolJobRecord, WorkerDispatchRecord } from "../../types";
import { formatServerDateTime } from "../../utils/datetime";
import {
  inspectArtifacts,
  inspectLogs,
  inspectOutput,
  inspectPrompt,
  inspectSkillBlocks,
  inspectSkills,
  inspectTools,
  inspectWorkerLogs,
  inspectWorkerOutput,
  type InspectPresence,
} from "./inspect";
import { FLOW_STATUS_LABEL_KEYS, flowStageTitle, type FlowNodeStatus } from "./stages";
import { workerLabel } from "./workers";

const props = defineProps<{
  open: boolean;
  stageId: string;
  worker: WorkerDispatchRecord | null;
  status: FlowNodeStatus | "";
  events: ExecutionEvent[];
  graphState: Record<string, unknown> | null;
  toolJobs: ToolJobRecord[];
  artifacts: ToolArtifactRecord[];
  artifactsLoaded: boolean;
  turnId: string;
}>();

const emit = defineEmits<{
  close: [];
  drill: [sessionId: string];
}>();

type InspectorTab = "logs" | "tools" | "skills" | "prompt" | "output" | "artifacts";

const activeTab = ref<InspectorTab>("logs");
const selectedPromptSection = ref<string | number>("full");

const tabs = computed(() => [
  { key: "logs" as const, label: t("flow.inspector.tab_logs") },
  { key: "tools" as const, label: t("flow.inspector.tab_tools") },
  { key: "skills" as const, label: t("flow.inspector.tab_skills") },
  { key: "prompt" as const, label: t("flow.inspector.tab_prompt") },
  { key: "output" as const, label: t("flow.inspector.tab_output") },
  { key: "artifacts" as const, label: t("flow.inspector.tab_artifacts") },
]);

const isWorker = computed(() => Boolean(props.worker));
const childSessionId = computed(() => String(props.worker?.child_session_id || "").trim());
const stageTitle = computed(() => {
  if (props.worker) {
    return workerLabel(props.worker) || t("flow.worker.untitled");
  }
  return props.stageId ? t(flowStageTitle(props.stageId)) : t("flow.inspector.not_selected");
});
const statusLabel = computed(() =>
  props.status ? t(FLOW_STATUS_LABEL_KEYS[props.status]) : "",
);
const inspectorMeta = computed(() =>
  statusLabel.value ? `${stageTitle.value} · ${statusLabel.value}` : stageTitle.value,
);

const logs = computed(() => {
  if (props.worker) {
    return inspectWorkerLogs(props.events, props.worker, props.turnId);
  }
  return props.stageId ? inspectLogs(props.events, props.stageId, props.turnId) : { presence: "empty" as const, value: [] };
});
const tools = computed(() =>
  props.stageId
    ? inspectTools(props.stageId, props.graphState, props.toolJobs, props.turnId)
    : { presence: "empty" as const, value: [] },
);
const skills = computed(() => inspectSkills(props.graphState));
const skillBlocks = computed(() => inspectSkillBlocks(props.graphState));
const prompt = computed(() => inspectPrompt(props.graphState));
const promptSectionOptions = computed(() => [
  { label: t("flow.inspector.tab_prompt"), value: "full" as const },
  ...(prompt.value.sections.value ?? []).map((section, index) => ({
    label: section.title || `${t("flow.inspector.tab_prompt")} ${index + 1}`,
    value: index,
  })),
]);
const selectedPromptSectionTitle = computed(() =>
  promptSectionOptions.value.find((option) => option.value === selectedPromptSection.value)?.label
    || t("flow.inspector.tab_prompt"),
);
const selectedPromptSectionBody = computed(() => {
  if (selectedPromptSection.value === "full") {
    return prompt.value.prompt.value || "";
  }
  return prompt.value.sections.value?.[selectedPromptSection.value]?.body || "";
});
const output = computed(() =>
  props.stageId ? inspectOutput(props.stageId, props.graphState) : { text: { presence: "missing" as const, value: null } },
);
const artifacts = computed(() => inspectArtifacts(props.artifacts, props.turnId, props.artifactsLoaded));
const workerOutput = computed(() => inspectWorkerOutput(props.worker));

watch(
  () => [props.stageId, props.worker?.task_id] as const,
  () => {
    activeTab.value = "logs";
    selectedPromptSection.value = "full";
  },
);

watch(
  () => promptSectionOptions.value,
  (options) => {
    if (!options.some((option) => option.value === selectedPromptSection.value)) {
      selectedPromptSection.value = "full";
    }
  },
  { deep: true },
);

function drillIntoWorker() {
  if (childSessionId.value) {
    emit("drill", childSessionId.value);
  }
}

function presenceText(presence: InspectPresence) {
  if (presence === "missing") {
    return t("flow.inspector.not_carried");
  }
  return t("flow.inspector.empty");
}

function previewText(value: string | null | undefined, limit = 180) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit)}…`;
}
</script>

<template>
  <transition name="drawer-fade">
    <div v-if="open" class="drawer-backdrop" @click="emit('close')"></div>
  </transition>
  <transition name="drawer-slide">
    <aside v-if="open" class="detail-drawer flow-inspector">
      <div class="detail-drawer-head">
        <div>
          <h3>{{ t("flow.inspector.title") }}</h3>
          <span class="sidebar-meta">{{ inspectorMeta }}</span>
        </div>
        <div class="flow-inspector-head-actions">
          <button
            v-if="isWorker && childSessionId"
            type="button"
            class="flow-reset-btn"
            @click="drillIntoWorker"
          >
            {{ t("flow.worker.drill") }}
          </button>
          <button class="icon-btn" type="button" :aria-label="t('flow.inspector.close')" @click="emit('close')">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </div>

      <div v-if="isWorker" class="flow-inspector-worker-meta">
        <div class="detail-list-item">
          <span class="property-key">{{ t("flow.worker.agent") }}</span>
          <span class="property-value">{{ worker?.agent_key || t("flow.inspector.not_carried") }}</span>
        </div>
        <div class="detail-list-item">
          <span class="property-key">{{ t("flow.worker.child_session") }}</span>
          <span class="property-value">{{ childSessionId || t("flow.inspector.not_carried") }}</span>
        </div>
      </div>

      <div v-if="stageId || worker" class="detail-content">
        <div class="runtime-console-tabs flow-inspector-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            type="button"
            class="runtime-console-tab"
            :class="{ active: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>

        <section v-if="activeTab === 'logs'" class="flow-inspector-section">
          <div v-if="logs.presence !== 'ok'" class="empty-state small">{{ presenceText(logs.presence) }}</div>
          <div v-else class="detail-list">
            <div v-for="item in logs.value" :key="item.id" class="detail-list-item flow-inspector-log">
              <span class="property-key">{{ item.type }}</span>
              <span class="property-value">{{ formatServerDateTime(item.timestamp) }}</span>
              <p v-if="item.message">{{ item.message }}</p>
              <p v-else class="flow-inspector-missing">{{ t("flow.inspector.not_carried") }}</p>
            </div>
          </div>
        </section>

        <section v-else-if="activeTab === 'tools'" class="flow-inspector-section">
          <div v-if="isWorker" class="empty-state small">{{ t("flow.inspector.not_carried") }}</div>
          <div v-else-if="tools.presence !== 'ok'" class="empty-state small">{{ presenceText(tools.presence) }}</div>
          <div v-else class="detail-list">
            <div v-for="item in tools.value" :key="item.id" class="detail-list-item">
              <span class="property-key">{{ item.name }}</span>
              <span class="property-value">{{ item.status || t("flow.inspector.not_carried") }}</span>
              <p v-if="item.summary">{{ item.summary }}</p>
            </div>
          </div>
        </section>

        <section v-else-if="activeTab === 'skills'" class="flow-inspector-section">
          <div v-if="isWorker" class="empty-state small">{{ t("flow.inspector.not_carried") }}</div>
          <div v-else-if="skills.presence !== 'ok'" class="empty-state small">{{ presenceText(skills.presence) }}</div>
          <div v-else class="detail-list">
            <div v-for="item in skills.value" :key="`${item.source}-${item.key}`" class="detail-list-item">
              <span class="property-key">{{ item.key }}</span>
              <span class="property-value">{{
                item.source === "resolved" ? t("flow.inspector.skill_resolved") : t("flow.inspector.skill_requested")
              }}</span>
            </div>
          </div>
          <div
            v-if="!isWorker && skillBlocks.presence === 'ok' && skillBlocks.value?.length"
            class="flow-inspector-prewrap"
          >
            <details v-for="(block, index) in skillBlocks.value" :key="`skill-block-${index}`" class="flow-inspector-disclosure">
              <summary>
                <span class="flow-inspector-disclosure-title">{{ t("flow.inspector.tab_skills") }} {{ index + 1 }}</span>
                <span class="flow-inspector-disclosure-preview">{{ previewText(block) }}</span>
              </summary>
              <pre>{{ block }}</pre>
            </details>
          </div>
        </section>

        <section v-else-if="activeTab === 'prompt'" class="flow-inspector-section">
          <div v-if="isWorker" class="empty-state small">{{ t("flow.inspector.not_carried") }}</div>
          <div v-else-if="prompt.prompt.presence !== 'ok' && prompt.sections.presence !== 'ok'" class="empty-state small">
            {{ presenceText(prompt.prompt.presence) }}
          </div>
          <div v-else class="flow-inspector-prompt-view">
            <div class="flow-inspector-prompt-toolbar">
              <span class="flow-inspector-control-label">{{ t("flow.inspector.tab_prompt") }}</span>
              <DropdownSelect
                v-model="selectedPromptSection"
                :options="promptSectionOptions"
                button-class="flow-inspector-prompt-select"
              />
            </div>
            <details class="flow-inspector-disclosure">
              <summary>
                <span class="flow-inspector-disclosure-title">{{ selectedPromptSectionTitle }}</span>
                <span class="flow-inspector-disclosure-preview">{{ previewText(selectedPromptSectionBody) }}</span>
              </summary>
              <pre>{{ selectedPromptSectionBody }}</pre>
            </details>
          </div>
        </section>

        <section v-else-if="activeTab === 'output'" class="flow-inspector-section">
          <div v-if="isWorker && workerOutput.presence !== 'ok'" class="empty-state small">
            {{ presenceText(workerOutput.presence) }}
          </div>
          <div v-else-if="isWorker" class="detail-list">
            <div v-for="item in workerOutput.value" :key="item.key" class="detail-list-item">
              <span class="property-key">{{ t(item.labelKey) }}</span>
              <span class="property-value">
                {{ item.presence === "ok" ? item.value : presenceText(item.presence) }}
              </span>
            </div>
          </div>
          <div v-else-if="output.text.presence !== 'ok'" class="empty-state small">
            {{ presenceText(output.text.presence) }}
          </div>
          <details v-else class="flow-inspector-disclosure">
            <summary>
              <span class="flow-inspector-disclosure-title">{{ t("flow.inspector.tab_output") }}</span>
              <span class="flow-inspector-disclosure-preview">{{ previewText(output.text.value) }}</span>
            </summary>
            <pre>{{ output.text.value }}</pre>
          </details>
        </section>

        <section v-else class="flow-inspector-section">
          <div v-if="isWorker" class="empty-state small">{{ t("flow.inspector.not_carried") }}</div>
          <div v-else-if="artifacts.presence !== 'ok'" class="empty-state small">{{ presenceText(artifacts.presence) }}</div>
          <div v-else class="detail-list">
            <div v-for="item in artifacts.value" :key="item.id" class="detail-list-item">
              <span class="property-key">{{ item.label }}</span>
              <span class="property-value">{{ item.type }}</span>
              <p>{{ item.path }}</p>
            </div>
          </div>
        </section>
      </div>
      <div v-else class="empty-state">{{ t("flow.inspector.select_hint") }}</div>
    </aside>
  </transition>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.28);
  z-index: 40;
}

.detail-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(420px, calc(100vw - 24px));
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: -12px 0 32px rgba(15, 23, 42, 0.16);
  z-index: 41;
  display: flex;
  flex-direction: column;
  padding: 18px 16px 16px;
  gap: 16px;
  overflow: hidden;
}

.detail-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-drawer-head h3 {
  margin: 0 0 4px;
  font-size: 18px;
}

.flow-inspector-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
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

.flow-inspector-worker-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sidebar-meta {
  color: var(--muted);
  font-size: 12px;
}

.detail-content,
.detail-drawer > .empty-state {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-list-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-soft);
}

.property-key {
  color: var(--muted);
  font-size: 12px;
}

.property-value {
  overflow-wrap: anywhere;
  line-height: 1.45;
  font-size: 12px;
}

.empty-state {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 12px;
  color: var(--muted);
  background: var(--surface-soft);
  padding: 16px;
}

.empty-state.small {
  min-height: 72px;
}

.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 0.2s ease;
}

.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 0.24s ease;
}

.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}

.flow-inspector-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.flow-inspector-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.flow-inspector-prompt-view {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.flow-inspector-prompt-toolbar {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
}

.flow-inspector-control-label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.flow-inspector-prompt-select {
  min-height: 34px;
  font-size: 12px;
}

.flow-inspector-log {
  flex-wrap: wrap;
}

.flow-inspector-log p,
.detail-list-item p {
  width: 100%;
  margin: 6px 0 0;
  color: var(--text);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.flow-inspector-missing {
  color: var(--muted) !important;
}

.flow-inspector-pre,
.flow-inspector-prewrap pre {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-soft);
  color: var(--text);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.flow-inspector-disclosure {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-soft);
  overflow: hidden;
}

.flow-inspector-disclosure summary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 10px 12px;
  color: var(--text);
  cursor: pointer;
  list-style-position: inside;
}

.flow-inspector-disclosure summary::marker {
  color: var(--muted);
}

.flow-inspector-disclosure summary:hover {
  background: color-mix(in srgb, var(--surface-soft) 72%, var(--surface));
}

.flow-inspector-disclosure-title {
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.flow-inspector-disclosure-preview {
  min-width: 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  word-break: break-word;
}

.flow-inspector-disclosure > pre {
  margin: 0 12px 12px;
  max-height: 420px;
  overflow: auto;
}

.flow-inspector-prewrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.flow-inspector-prewrap strong {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
}
</style>
