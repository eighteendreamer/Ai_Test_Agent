<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import EventConsolePanel from "./components/chat/EventConsolePanel.vue";
import SnapshotTracePanel from "./components/chat/SnapshotTracePanel.vue";
import ToolActivityPanel from "./components/chat/ToolActivityPanel.vue";
import ToolJobPanel from "./components/chat/ToolJobPanel.vue";
import VerificationPanel from "./components/chat/VerificationPanel.vue";
import AppSidebar from "./components/layout/AppSidebar.vue";
import AppTopBar from "./components/layout/AppTopBar.vue";
import { useAppStore } from "./stores/app";
import { useSessionStore } from "./stores/session";

const route = useRoute();
const appStore = useAppStore();
const sessionStore = useSessionStore();
const logExpanded = ref(false);
const runtimeConsoleTab = ref("logs");
let healthPollTimer: number | null = null;

function previewText(value: string, limit = 48) {
  const normalized = String(value || "").trim().replace(/\s+/g, " ");
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(0, limit - 3))}...`;
}

const pageLabel = computed(() => String(route.meta.label ?? "Session Workspace"));
const runtimeBadge = computed(() => sessionStore.session?.status ?? "idle");
const isHomeRoute = computed(() => route.name === "home");
const runtimeConsoleTabs = [
  { key: "logs", label: "运行日志" },
  { key: "events", label: "事件控制台" },
  { key: "tools", label: "工具活动" },
  { key: "snapshot", label: "快照追踪" },
  { key: "jobs", label: "工具任务" },
  { key: "verification", label: "验证结果" },
] as const;

const runtimeLines = computed(() => {
  const workerDispatches = sessionStore.workerDispatches;
  const failureGuard = sessionStore.workerFailureGuard;
  const queuedCount = sessionStore.queuedTurnCount;
  const nextQueuedTurn = sessionStore.nextQueuedTurn;
  const queueLine = `[queue] pending=${queuedCount}${
    nextQueuedTurn
      ? ` next=${previewText(
          String(nextQueuedTurn.payload.content || nextQueuedTurn.payload.command_name || "queued turn"),
          42,
        )} mode=${nextQueuedTurn.queue_behavior || "enqueue_if_busy"}`
      : ""
  }`;
  if (sessionStore.activity.length > 0) {
    return [
      `[watcher] phase=${sessionStore.watcherPhase} sync=${sessionStore.watcherLastSyncLabel} failures=${sessionStore.watcherFailures}`,
      `[approvals] pending=${sessionStore.pendingApprovals.length} workers=${workerDispatches.length}`,
      queueLine,
      ...(failureGuard?.blocked
        ? [`[guard] blocked count=${failureGuard.count ?? 0} last_error=${failureGuard.last_error ?? "unknown"}`]
        : []),
      ...sessionStore.activity.map((event) => {
        const details = Object.entries(event.payload ?? {})
          .filter(([, value]) => value !== null && value !== undefined && value !== "")
          .slice(0, 4)
          .map(([key, value]) => `${key}=${String(value)}`)
          .join(" ");

        return `[${new Date(event.timestamp).toLocaleTimeString("zh-CN", {
          hour12: false,
        })}] ${event.type}${details ? ` ${details}` : ""}`;
      }),
    ];
  }

  if (sessionStore.session) {
    return [
      `[session] id=${sessionStore.session.id}`,
      `[status] ${sessionStore.session.status} / ${sessionStore.session.session_mode} / ${sessionStore.session.runtime_mode}`,
      `[watcher] phase=${sessionStore.watcherPhase} sync=${sessionStore.watcherLastSyncLabel} failures=${sessionStore.watcherFailures}`,
      `[agent] ${sessionStore.session.selected_agent ?? sessionStore.selectedAgentKey}`,
      `[approvals] pending=${sessionStore.pendingApprovals.length} workers=${workerDispatches.length}`,
      queueLine,
      `[messages] total=${sessionStore.messages.length}`,
    ];
  }

  return ["Waiting for runtime events..."];
});

watch(
  () => route.fullPath,
  () => {
    if (route.name !== "home") {
      logExpanded.value = false;
    }
    runtimeConsoleTab.value = "logs";
  },
);

onMounted(async () => {
  await Promise.all([appStore.fetchSystemStatus(), sessionStore.bootstrap()]);
  sessionStore.startWatcher();
  healthPollTimer = window.setInterval(() => {
    void appStore.fetchSystemStatus();
  }, 15000);
});

onBeforeUnmount(() => {
  if (healthPollTimer !== null) {
    window.clearInterval(healthPollTimer);
    healthPollTimer = null;
  }
  sessionStore.stopWatcher();
  sessionStore.eventSource?.close();
});
</script>

<template>
  <div class="prototype-shell">
    <AppSidebar />
    <main class="prototype-main">
      <AppTopBar :label="pageLabel" :system-status="appStore.systemStatus" />
      <div class="prototype-content">
        <RouterView />
      </div>
      <section :class="['log-panel', { expanded: logExpanded }]">
        <header class="log-panel-head" @click="logExpanded = !logExpanded">
          <div class="log-panel-title">
            &gt;_ Runtime Event Console
            <span class="log-badge">{{ runtimeBadge }}</span>
          </div>
          <div
            v-if="isHomeRoute && sessionStore.session"
            class="runtime-console-tabs runtime-console-tabs-inline"
            @click.stop
          >
            <button
              v-for="tab in runtimeConsoleTabs"
              :key="tab.key"
              type="button"
              class="runtime-console-tab"
              :class="{ active: runtimeConsoleTab === tab.key }"
              @click.stop="runtimeConsoleTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>
          <i :class="['fa-solid', logExpanded ? 'fa-chevron-down' : 'fa-chevron-up', 'log-panel-toggle']"></i>
        </header>

        <div v-if="logExpanded" class="log-panel-body">
          <template v-if="isHomeRoute && sessionStore.session">
            <div class="runtime-console-content">
              <div v-if="runtimeConsoleTab === 'logs'" class="runtime-console-raw-log">
                <div v-for="(line, index) in runtimeLines" :key="`${index}-${line}`">
                  {{ line }}
                </div>
                <div class="log-cursor-line">
                  <span class="system-chip">system</span>
                  runtime-console-ready
                  <span class="cursor-blink"></span>
                </div>
              </div>
              <EventConsolePanel v-else-if="runtimeConsoleTab === 'events'" />
              <ToolActivityPanel v-else-if="runtimeConsoleTab === 'tools'" />
              <SnapshotTracePanel v-else-if="runtimeConsoleTab === 'snapshot'" />
              <ToolJobPanel v-else-if="runtimeConsoleTab === 'jobs'" />
              <VerificationPanel v-else-if="runtimeConsoleTab === 'verification'" />
            </div>
          </template>

          <template v-else>
            <div class="runtime-console-raw-log">
              <div v-for="(line, index) in runtimeLines" :key="`${index}-${line}`">
                {{ line }}
              </div>
              <div class="log-cursor-line">
                <span class="system-chip">system</span>
                runtime-console-ready
                <span class="cursor-blink"></span>
              </div>
            </div>
          </template>
        </div>
      </section>
    </main>
  </div>
</template>
