<script setup lang="ts">
/**
 * 录制实时时间线（方案 9.3，P0-10）。
 *
 * 数据面：轮询 GET /api/v1/recordings 按当前 agent session 过滤出最新录制，
 * 拉详情渲染步骤流（动作类型 + 元素语义名 + 截图引用）。
 * 录制结束后展示固化指标与图谱入口。
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  getRecordingDetail,
  isUserActionEvent,
  listRecordings,
  recorderEventLabel,
  type RecordingDetail,
} from "../../features/recorder/recordingApi";
import { t } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();
const router = useRouter();

const recording = ref<RecordingDetail | null>(null);
const loading = ref(false);
let pollTimer: number | null = null;

const ACTIVE_STATUSES = new Set(["launching", "ready", "active", "paused", "finalizing"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "discarded"]);

const status = computed(() => recording.value?.status || "");
const isActive = computed(() => ACTIVE_STATUSES.has(status.value));
const isTerminal = computed(() => TERMINAL_STATUSES.has(status.value));
const steps = computed(() => (recording.value?.events ?? []).filter(isUserActionEvent));
const showPanel = computed(() => Boolean(recording.value) && (isActive.value || isTerminal.value));

const statusKey = computed(() => `recorder.status_${status.value || "unknown"}`);

const finalizeSummary = computed(() => {
  const metrics = recording.value?.finalize_metrics;
  if (!metrics || typeof metrics !== "object") {
    return "";
  }
  const record = metrics as Record<string, unknown>;
  const parts: string[] = [];
  const actionCount = Number(record.action_count ?? record.actions ?? 0);
  if (actionCount > 0) {
    parts.push(t("recordingPanel.actions_written", { count: actionCount }));
  }
  const pageCount = Number(record.page_count ?? record.pages ?? 0);
  if (pageCount > 0) {
    parts.push(t("recordingPanel.pages_written", { count: pageCount }));
  }
  const elementCount = Number(record.element_count ?? record.elements ?? 0);
  if (elementCount > 0) {
    parts.push(t("recordingPanel.elements_written", { count: elementCount }));
  }
  if (record.reconciled === true) {
    parts.push(t("recordingPanel.reconciled"));
  }
  return parts.join(" · ");
});

function eventIcon(type: string): string {
  switch (type) {
    case "click":
    case "dblclick":
      return "fa-solid fa-arrow-pointer";
    case "fill":
      return "fa-solid fa-keyboard";
    case "key":
      return "fa-solid fa-file-lines";
    case "submit":
      return "fa-solid fa-paper-plane";
    case "scroll":
      return "fa-solid fa-arrows-up-down";
    case "navigate":
      return "fa-solid fa-location-arrow";
    case "file_change":
      return "fa-solid fa-file-arrow-up";
    default:
      return "fa-solid fa-circle-dot";
  }
}

async function refresh() {
  const sessionId = sessionStore.session?.id;
  if (!sessionId) {
    return;
  }
  loading.value = true;
  try {
    const items = await listRecordings(20);
    const matched = items.find((item) => item.session_id === sessionId) ?? null;
    if (!matched) {
      // 会话关闭后保留已取到的终态录制；无任何匹配则隐藏面板
      if (!recording.value || !TERMINAL_STATUSES.has(recording.value.status)) {
        recording.value = null;
      }
      return;
    }
    if (recording.value?.id === matched.id && TERMINAL_STATUSES.has(matched.status)) {
      // 终态内容不再变化，只更新元数据，避免重复拉详情
      recording.value = { ...recording.value, ...matched, events: recording.value.events };
      return;
    }
    recording.value = await getRecordingDetail(matched.id);
  } catch {
    // 后端不可达：保留已有内容，下轮重试
  } finally {
    loading.value = false;
  }
}

function openKnowledgeGraph() {
  void router.push("/knowledge");
}

onMounted(() => {
  void refresh();
  pollTimer = window.setInterval(() => {
    void refresh();
  }, 3000);
});

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
});
</script>

<template>
  <Transition name="runtime-panel-transition">
    <section v-if="showPanel" class="recording-panel">
      <header class="recording-panel-head">
        <span class="recording-panel-icon"><i class="fa-solid fa-video"></i></span>
        <div class="recording-panel-title">
          <strong>{{ t("recordingPanel.title") }}</strong>
          <span class="recording-panel-sub">
            {{ t(statusKey) }} · {{ t("recorder.steps", { count: steps.length }) }}
          </span>
        </div>
        <i
          :class="['fa-solid', loading ? 'fa-spinner fa-spin' : 'fa-circle', 'recording-panel-state']"
        ></i>
      </header>

      <div class="recording-panel-steps">
        <div v-if="steps.length === 0" class="recording-panel-empty">
          {{ isActive ? t("recordingPanel.waiting_steps") : t("recordingPanel.no_steps") }}
        </div>
        <div v-for="step in steps.slice(-30).reverse()" :key="step.seq" class="recording-step">
          <i :class="eventIcon(step.type)"></i>
          <span class="recording-step-type">{{ step.type }}</span>
          <span class="recording-step-label" :title="recorderEventLabel(step)">
            {{ recorderEventLabel(step) }}
          </span>
          <img
            v-if="step.screenshot_ref"
            :src="String(step.screenshot_ref)"
            class="recording-step-shot"
            loading="lazy"
            alt=""
          />
        </div>
      </div>

      <footer v-if="isTerminal" class="recording-panel-foot">
        <p v-if="finalizeSummary" class="recording-panel-metrics">{{ finalizeSummary }}</p>
        <button
          v-if="status === 'completed'"
          type="button"
          class="secondary-btn recording-panel-link"
          @click="openKnowledgeGraph"
        >
          {{ t("recordingPanel.open_graph") }}
        </button>
      </footer>
    </section>
  </Transition>
</template>

<style scoped>
.recording-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  background: rgba(248, 250, 252, 0.9);
  max-height: 220px;
}

.recording-panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.recording-panel-icon {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.9);
  color: #f8fafc;
  font-size: 11px;
}

.recording-panel-title {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.recording-panel-title strong {
  font-size: 12px;
  color: #0f172a;
}

.recording-panel-sub {
  font-size: 10px;
  color: #64748b;
}

.recording-panel-state {
  margin-left: auto;
  font-size: 8px;
  color: #22c55e;
}

.recording-panel-steps {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-height: 40px;
}

.recording-panel-empty {
  font-size: 11px;
  color: #94a3b8;
  padding: 6px 2px;
}

.recording-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #334155;
  min-width: 0;
}

.recording-step > i {
  flex: none;
  width: 14px;
  font-size: 9px;
  color: #475569;
  text-align: center;
}

.recording-step-type {
  flex: none;
  font-family: var(--font-mono, "Consolas", monospace);
  font-size: 9px;
  color: #64748b;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 4px;
  padding: 1px 5px;
}

.recording-step-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recording-step-shot {
  flex: none;
  width: 28px;
  height: 18px;
  object-fit: cover;
  border-radius: 3px;
  border: 1px solid rgba(15, 23, 42, 0.15);
}

.recording-panel-foot {
  display: flex;
  align-items: center;
  gap: 10px;
}

.recording-panel-metrics {
  margin: 0;
  flex: 1;
  min-width: 0;
  font-size: 10px;
  color: #16a34a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recording-panel-link {
  flex: none;
  min-height: 26px;
  padding: 4px 10px;
  font-size: 11px;
}
</style>
