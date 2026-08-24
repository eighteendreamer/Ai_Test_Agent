<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { NConfigProvider, NDialogProvider, NMessageProvider, darkTheme } from "naive-ui";
import type { GlobalThemeOverrides } from "naive-ui";

import AppSidebar from "./components/layout/AppSidebar.vue";
import AppTopBar from "./components/layout/AppTopBar.vue";
import { publishFlowSession } from "./features/flow/openFlowWindow";
import { getLocale, t } from "./services/i18n";
import { useAppStore } from "./stores/app";
import { useSessionStore } from "./stores/session";

const CodeReviewProgressPanel = defineAsyncComponent(() => import("./components/chat/CodeReviewProgressPanel.vue"));
const EventConsolePanel = defineAsyncComponent(() => import("./components/chat/EventConsolePanel.vue"));
const SnapshotTracePanel = defineAsyncComponent(() => import("./components/chat/SnapshotTracePanel.vue"));
const SmokeTestingResultPanel = defineAsyncComponent(() => import("./components/chat/SmokeTestingResultPanel.vue"));
const ToolActivityPanel = defineAsyncComponent(() => import("./components/chat/ToolActivityPanel.vue"));
const ToolJobPanel = defineAsyncComponent(() => import("./components/chat/ToolJobPanel.vue"));
const VerificationPanel = defineAsyncComponent(() => import("./components/chat/VerificationPanel.vue"));

const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: "var(--app-font-family)",
    fontSize: "var(--app-font-size)",
    fontSizeMini: "var(--app-font-size-mini)",
    fontSizeTiny: "var(--app-font-size-tiny)",
    fontSizeSmall: "var(--app-font-size-small)",
    fontSizeMedium: "var(--app-font-size-medium)",
    fontSizeLarge: "var(--app-font-size-large)",
    fontSizeHuge: "var(--app-font-size-huge)",
    primaryColor: "#111827",
    primaryColorHover: "#000000",
    primaryColorPressed: "#000000",
    primaryColorSuppl: "#111827",
    textColorBase: "#1F2937",
    textColor2: "#6B7280",
    borderColor: "#E5E7EB",
  },
  Dialog: {
    actionPositiveColor: "#111827",
    actionPositiveColorHover: "#000000",
    actionPositiveColorPressed: "#000000",
    actionPositiveColorFocus: "#000000",
    actionPositiveTextColor: "#ffffff",
    actionPositiveTextColorHover: "#ffffff",
    actionPositiveTextColorPressed: "#ffffff",
    actionPositiveTextColorFocus: "#ffffff",
    iconColorWarning: "#111827",
  },
  Dropdown: {
    color: "#ffffff",
    optionTextColor: "#6B7280",
    optionTextColorHover: "#111827",
    optionTextColorActive: "#111827",
    optionTextColorChildActive: "#111827",
    optionColorHover: "#F9FAFB",
    optionColorActive: "#F3F4F6",
    dividerColor: "#E5E7EB",
  },
};

const route = useRoute();
const appStore = useAppStore();
const sessionStore = useSessionStore();

const activeTheme = computed(() => (appStore.theme === "dark" ? darkTheme : null));
const activeThemeOverrides = computed<GlobalThemeOverrides>(() => {
  if (appStore.theme === "dark") {
    return {
      common: {
        fontFamily: "var(--app-font-family)",
        fontSize: "var(--app-font-size)",
        fontSizeMini: "var(--app-font-size-mini)",
        fontSizeTiny: "var(--app-font-size-tiny)",
        fontSizeSmall: "var(--app-font-size-small)",
        fontSizeMedium: "var(--app-font-size-medium)",
        fontSizeLarge: "var(--app-font-size-large)",
        fontSizeHuge: "var(--app-font-size-huge)",
        primaryColor: "#ffffff",
        primaryColorHover: "#e5e5e5",
        primaryColorPressed: "#cccccc",
        primaryColorSuppl: "#ffffff",
        textColorBase: "#f5f5f5",
        textColor2: "#9a9a9a",
        borderColor: "#1c1c1c",
        popoverColor: "#050505",
        bodyColor: "#000000",
        cardColor: "#050505",
      },
      Dialog: {
        actionPositiveColor: "#ffffff",
        actionPositiveColorHover: "#e5e5e5",
        actionPositiveColorPressed: "#cccccc",
        actionPositiveColorFocus: "#e5e5e5",
        actionPositiveTextColor: "#000000",
        actionPositiveTextColorHover: "#000000",
        actionPositiveTextColorPressed: "#000000",
        actionPositiveTextColorFocus: "#000000",
        iconColorWarning: "#ffffff",
      },
      Dropdown: {
        color: "#050505",
        optionTextColor: "#9a9a9a",
        optionTextColorHover: "#f5f5f5",
        optionTextColorActive: "#ffffff",
        optionTextColorChildActive: "#ffffff",
        optionColorHover: "#141414",
        optionColorActive: "#101010",
        dividerColor: "#1c1c1c",
      },
    };
  }
  return lightThemeOverrides;
});
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

const pageLabel = computed(() => {
  if (route.name === "flow") {
    return t("nav.flow");
  }
  if (route.name === "recorder-window") {
    return t("recorder.window_title");
  }
  return String(route.meta.label ?? "Session Workspace");
});
const runtimeBadge = computed(() => sessionStore.session?.status ?? "idle");
const isHomeRoute = computed(() => route.name === "home");
const isFlowRoute = computed(() => route.name === "flow");
const isRecorderRoute = computed(() => route.name === "recorder-window");
const isBareShell = computed(() => isFlowRoute.value || isRecorderRoute.value);
const showRuntimeConsole = computed(() => route.name !== "settings" && !isBareShell.value);
const runtimeConsoleTabs = computed(() => [
  { key: "logs", label: t("console.title") },
  { key: "events", label: t("console.events") },
  { key: "tools", label: t("console.tools") },
  { key: "snapshot", label: t("console.snapshots") },
  { key: "jobs", label: t("console.jobs") },
  { key: "smoke_results", label: "冒烟测试结果" },
  { key: "verification", label: t("console.verification") },
  { key: "review_progress", label: t("console.review_progress") },
] as const);

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

        return `[${new Date(event.timestamp).toLocaleTimeString(getLocale(), {
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

  return [t("console.waiting")];
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

watch(
  () => [route.name, sessionStore.session?.id ?? "", sessionStore.latestTurnId] as const,
  ([routeName, sessionId, turnId]) => {
    if (routeName === "flow" || routeName === "recorder-window") {
      return;
    }
    publishFlowSession({ sessionId, turnId });
  },
);

onMounted(async () => {
  healthPollTimer = window.setInterval(() => {
    void appStore.fetchSystemStatus();
  }, 15000);

  if (isBareShell.value) {
    await appStore.fetchSystemStatus({ refreshCatalog: false });
    return;
  }

  const [, catalog] = await Promise.all([
    appStore.fetchSystemStatus({ refreshCatalog: false }),
    sessionStore.bootstrap(),
  ]);
  appStore.agents = catalog.agents;
  appStore.tools = catalog.tools;
  appStore.lastCheckedAt = new Date().toISOString();
  sessionStore.startWatcher();
});

onBeforeUnmount(() => {
  if (healthPollTimer !== null) {
    window.clearInterval(healthPollTimer);
    healthPollTimer = null;
  }
  sessionStore.stopWatcher();
  sessionStore.disconnectEvents();
});
</script>

<template>
  <n-config-provider :theme="activeTheme" :theme-overrides="activeThemeOverrides">
  <n-message-provider>
    <n-dialog-provider>
      <div :class="['prototype-shell', { 'is-flow-shell': isFlowRoute, 'is-recorder-shell': isRecorderRoute }]">
        <AppSidebar v-if="!isBareShell" />
        <main class="prototype-main">
          <AppTopBar v-if="!isRecorderRoute" :label="pageLabel" :system-status="appStore.systemStatus" />
          <div class="prototype-content">
            <RouterView v-slot="{ Component, route: viewRoute }">
              <Transition name="route-page" mode="out-in">
                <component :is="Component" :key="viewRoute.path" />
              </Transition>
            </RouterView>
          </div>
          <section v-if="showRuntimeConsole" :class="['log-panel', { expanded: logExpanded }]">
            <header class="log-panel-head" @click="logExpanded = !logExpanded">
              <div class="log-panel-title">
                &gt;_ {{ t("console.panel_title") }}
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
                      <span class="system-chip">{{ t("console.system_chip") }}</span>
                      {{ t("console.ready_line") }}
                      <span class="cursor-blink"></span>
                    </div>
                  </div>
                  <EventConsolePanel v-else-if="runtimeConsoleTab === 'events'" />
                  <ToolActivityPanel v-else-if="runtimeConsoleTab === 'tools'" />
                  <CodeReviewProgressPanel v-else-if="runtimeConsoleTab === 'review_progress'" />
                  <SnapshotTracePanel v-else-if="runtimeConsoleTab === 'snapshot'" />
                  <ToolJobPanel v-else-if="runtimeConsoleTab === 'jobs'" />
                  <SmokeTestingResultPanel v-else-if="runtimeConsoleTab === 'smoke_results'" />
                  <VerificationPanel v-else-if="runtimeConsoleTab === 'verification'" />
                </div>
              </template>

              <template v-else>
                <div class="runtime-console-raw-log">
                  <div v-for="(line, index) in runtimeLines" :key="`${index}-${line}`">
                    {{ line }}
                  </div>
                  <div class="log-cursor-line">
                    <span class="system-chip">{{ t("console.system_chip") }}</span>
                    {{ t("console.ready_line") }}
                    <span class="cursor-blink"></span>
                  </div>
                </div>
              </template>
            </div>
          </section>
        </main>
      </div>
    </n-dialog-provider>
  </n-message-provider>
  </n-config-provider>
</template>
