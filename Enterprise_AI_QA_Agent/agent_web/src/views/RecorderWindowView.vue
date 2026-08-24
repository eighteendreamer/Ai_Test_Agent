<script setup lang="ts">
/**
 * UI 录制窗口控制条（方案 9.2，P0-9）。
 *
 * 本页面渲染在录制 BrowserWindow 顶部 56px（CONTROL_BAR_HEIGHT），
 * 下方由主进程 WebContentsView 加载目标产品页面。
 * 四按钮驱动后端 RecorderSessionService 状态机（POST /control），
 * 状态徽标轮询 GET /api/v1/recordings/{id}。
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { t } from "../services/i18n";

interface RecordingDetail {
  id: string;
  status: string;
  step_count: number;
  entry_url: string;
}

const route = useRoute();
const recordingId = computed(() => String(route.query.recording_id || "").trim());
const entryUrl = computed(() => String(route.query.entry_url || "").trim());

const recording = ref<RecordingDetail | null>(null);
const currentUrl = ref("");
const loadError = ref("");
const pendingAction = ref("");
const destroyArmed = ref(false);
let destroyArmTimer: number | null = null;
let pollTimer: number | null = null;

const isDesktop = computed(() => Boolean(window.qaAgentDesktop?.isDesktop));

const statusKey = computed(() => `recorder.status_${recording.value?.status || "unknown"}`);
const statusTone = computed(() => {
  switch (recording.value?.status) {
    case "active":
      return "active";
    case "paused":
      return "paused";
    case "finalizing":
      return "busy";
    case "completed":
      return "done";
    case "failed":
      return "failed";
    case "discarded":
      return "discarded";
    default:
      return "idle";
  }
});

const canStart = computed(() => recording.value?.status === "ready");
const canPause = computed(() => recording.value?.status === "active");
const canResume = computed(() => recording.value?.status === "paused");
const canStop = computed(() => ["active", "paused"].includes(recording.value?.status || ""));
const canDestroy = computed(() =>
  ["ready", "active", "paused", "launching"].includes(recording.value?.status || ""),
);
const isTerminal = computed(() =>
  ["completed", "failed", "discarded"].includes(recording.value?.status || ""),
);
const finalized = computed(() => recording.value?.status === "finalizing");

async function fetchDetail() {
  if (!recordingId.value) {
    return;
  }
  try {
    const response = await fetch(`/api/v1/recordings/${encodeURIComponent(recordingId.value)}`);
    if (!response.ok) {
      loadError.value = `HTTP ${response.status}`;
      return;
    }
    recording.value = (await response.json()) as RecordingDetail;
    loadError.value = "";
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error);
  }
  if (window.qaAgentDesktop?.recorder) {
    try {
      const state = await window.qaAgentDesktop.recorder.getState(recordingId.value);
      if (state) {
        currentUrl.value = state.currentUrl || currentUrl.value;
      }
    } catch {
      // 桌面桥不可用时（如纯浏览器预览）忽略
    }
  }
}

async function sendControl(action: "start" | "pause" | "resume" | "stop" | "destroy") {
  if (!recordingId.value || pendingAction.value) {
    return;
  }
  pendingAction.value = action;
  try {
    const response = await fetch(
      `/api/v1/recordings/${encodeURIComponent(recordingId.value)}/control`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action }),
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      loadError.value =
        body?.detail || body?.message || `${t("recorder.control_failed")} (HTTP ${response.status})`;
    } else {
      loadError.value = "";
    }
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error);
  } finally {
    pendingAction.value = "";
    await fetchDetail();
  }
}

function onDestroyClick() {
  if (!destroyArmed.value) {
    // 内联二次确认：第一次点击进入待确认态，3s 未确认自动复位。
    destroyArmed.value = true;
    if (destroyArmTimer !== null) {
      window.clearTimeout(destroyArmTimer);
    }
    destroyArmTimer = window.setTimeout(() => {
      destroyArmed.value = false;
    }, 3000);
    return;
  }
  if (destroyArmTimer !== null) {
    window.clearTimeout(destroyArmTimer);
    destroyArmTimer = null;
  }
  destroyArmed.value = false;
  void sendControl("destroy");
}

async function closeWindow() {
  if (window.qaAgentDesktop?.recorder) {
    try {
      await window.qaAgentDesktop.recorder.close(recordingId.value);
      return;
    } catch {
      // fallthrough
    }
  }
  window.close();
}

onMounted(() => {
  void fetchDetail();
  pollTimer = window.setInterval(() => {
    void fetchDetail();
  }, 2000);
});

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (destroyArmTimer !== null) {
    window.clearTimeout(destroyArmTimer);
    destroyArmTimer = null;
  }
});
</script>

<template>
  <div class="recorder-shell">
    <header class="recorder-bar">
      <div class="recorder-brand">
        <span class="recorder-dot" :data-tone="statusTone"></span>
        <div class="recorder-meta">
          <div class="recorder-title-row">
            <span class="recorder-title">{{ t("recorder.window_title") }}</span>
            <span class="recorder-status" :data-tone="statusTone">{{ t(statusKey) }}</span>
            <span class="recorder-steps">
              {{ t("recorder.steps", { count: recording?.step_count ?? 0 }) }}
            </span>
          </div>
          <div class="recorder-url" :title="currentUrl || entryUrl">
            {{ currentUrl || entryUrl || t("recorder.url_pending") }}
          </div>
        </div>
      </div>

      <div class="recorder-actions">
        <button
          type="button"
          class="recorder-btn primary"
          :disabled="!canStart || pendingAction !== ''"
          @click="sendControl('start')"
        >
          {{ t("recorder.action_start") }}
        </button>
        <button
          type="button"
          class="recorder-btn"
          :disabled="!canPause || pendingAction !== ''"
          @click="sendControl('pause')"
        >
          {{ t("recorder.action_pause") }}
        </button>
        <button
          type="button"
          class="recorder-btn"
          :disabled="!canResume || pendingAction !== ''"
          @click="sendControl('resume')"
        >
          {{ t("recorder.action_resume") }}
        </button>
        <button
          type="button"
          class="recorder-btn"
          :disabled="!canStop || pendingAction !== ''"
          @click="sendControl('stop')"
        >
          {{ t("recorder.action_stop") }}
        </button>
        <button
          type="button"
          class="recorder-btn danger"
          :class="{ armed: destroyArmed }"
          :disabled="!canDestroy || pendingAction !== ''"
          :title="t('recorder.destroy_hint')"
          @click="onDestroyClick"
        >
          {{ destroyArmed ? t("recorder.action_destroy_confirm") : t("recorder.action_destroy") }}
        </button>
        <button
          v-if="isTerminal || finalized"
          type="button"
          class="recorder-btn ghost"
          @click="closeWindow"
        >
          {{ t("recorder.action_close_window") }}
        </button>
      </div>
    </header>

    <div v-if="!isDesktop" class="recorder-overlay">
      <p>{{ t("recorder.desktop_only") }}</p>
    </div>
    <div v-if="loadError" class="recorder-error" :title="loadError">{{ loadError }}</div>
    <div v-if="finalized" class="recorder-finalizing">{{ t("recorder.finalizing_hint") }}</div>
  </div>
</template>

<style scoped>
.recorder-shell {
  position: relative;
  flex: 1;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #f3f4f6;
  font-family: var(--app-font-family, system-ui, sans-serif);
}

/* 控制条固定 56px，与主进程 CONTROL_BAR_HEIGHT 对齐（WebContentsView 从其下沿开始）。 */
.recorder-bar {
  box-sizing: border-box;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px;
  background: #111827;
  color: #f9fafb;
}

.recorder-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.recorder-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  flex: none;
  background: #9ca3af;
}

.recorder-dot[data-tone="active"] {
  background: #22c55e;
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.2);
  animation: recorder-pulse 1.6s ease-in-out infinite;
}

.recorder-dot[data-tone="paused"] {
  background: #eab308;
}

.recorder-dot[data-tone="busy"] {
  background: #3b82f6;
  animation: recorder-pulse 1.2s ease-in-out infinite;
}

.recorder-dot[data-tone="done"] {
  background: #10b981;
}

.recorder-dot[data-tone="failed"] {
  background: #ef4444;
}

.recorder-dot[data-tone="discarded"] {
  background: #6b7280;
}

@keyframes recorder-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

.recorder-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.recorder-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.recorder-title {
  font-weight: 600;
  white-space: nowrap;
}

.recorder-status {
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  background: rgba(255, 255, 255, 0.12);
  white-space: nowrap;
}

.recorder-status[data-tone="active"] {
  background: rgba(34, 197, 94, 0.25);
}

.recorder-status[data-tone="failed"] {
  background: rgba(239, 68, 68, 0.3);
}

.recorder-steps {
  font-size: 11px;
  color: #9ca3af;
  white-space: nowrap;
}

.recorder-url {
  max-width: 46vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: #9ca3af;
}

.recorder-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.recorder-btn {
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.08);
  color: #f9fafb;
  border-radius: 8px;
  font-size: 12px;
  line-height: 28px;
  padding: 0 12px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.recorder-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.16);
}

.recorder-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.recorder-btn.primary {
  background: #f9fafb;
  border-color: #f9fafb;
  color: #111827;
  font-weight: 600;
}

.recorder-btn.primary:hover:not(:disabled) {
  background: #e5e7eb;
}

.recorder-btn.danger {
  border-color: rgba(239, 68, 68, 0.6);
  color: #fca5a5;
}

.recorder-btn.danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.25);
}

.recorder-btn.danger.armed {
  background: #ef4444;
  border-color: #ef4444;
  color: #ffffff;
}

.recorder-btn.ghost {
  border-color: transparent;
  background: transparent;
  color: #9ca3af;
}

.recorder-overlay {
  position: absolute;
  inset: 56px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  font-size: 13px;
}

.recorder-error {
  position: absolute;
  right: 12px;
  bottom: 10px;
  max-width: 40vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px 10px;
  border-radius: 6px;
  background: rgba(239, 68, 68, 0.92);
  color: #ffffff;
  font-size: 11px;
}

.recorder-finalizing {
  position: absolute;
  left: 50%;
  top: 40%;
  transform: translate(-50%, -50%);
  padding: 8px 18px;
  border-radius: 8px;
  background: rgba(17, 24, 39, 0.88);
  color: #f9fafb;
  font-size: 13px;
}
</style>
