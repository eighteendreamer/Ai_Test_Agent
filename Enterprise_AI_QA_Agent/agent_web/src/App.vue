<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppSidebar from "./components/layout/AppSidebar.vue";
import AppTopBar from "./components/layout/AppTopBar.vue";
import { useAppStore } from "./stores/app";
import { useSessionStore } from "./stores/session";

const route = useRoute();
const router = useRouter();
const appStore = useAppStore();
const sessionStore = useSessionStore();
const logExpanded = ref(false);
let healthPollTimer: number | null = null;

const pageLabel = computed(() => String(route.meta.label ?? "Session Workspace"));
const runtimeBadge = computed(() => sessionStore.session?.status ?? "idle");
const runtimeLines = computed(() => {
  if (sessionStore.activity.length > 0) {
    return sessionStore.activity.map((event) => {
      const details = Object.entries(event.payload ?? {})
        .filter(([, value]) => value !== null && value !== undefined && value !== "")
        .slice(0, 4)
        .map(([key, value]) => `${key}=${String(value)}`)
        .join(" ");

      return `[${new Date(event.timestamp).toLocaleTimeString("zh-CN", {
        hour12: false,
      })}] ${event.type}${details ? ` ${details}` : ""}`;
    });
  }

  if (sessionStore.session) {
    return [
      `[session] id=${sessionStore.session.id}`,
      `[status] ${sessionStore.session.status} / ${sessionStore.session.session_mode} / ${sessionStore.session.runtime_mode}`,
      `[agent] ${sessionStore.session.selected_agent ?? sessionStore.selectedAgentKey}`,
      `[messages] total=${sessionStore.messages.length}`,
    ];
  }

  return ["Waiting for runtime events..."];
});

function handleQuickRun() {
  router.push("/home");
}

watch(
  () => route.fullPath,
  () => {
    if (route.name !== "home") {
      logExpanded.value = false;
    }
  },
);

onMounted(async () => {
  await Promise.all([appStore.fetchSystemStatus(), sessionStore.bootstrap()]);
  healthPollTimer = window.setInterval(() => {
    void appStore.fetchSystemStatus();
  }, 15000);
});

onBeforeUnmount(() => {
  if (healthPollTimer !== null) {
    window.clearInterval(healthPollTimer);
    healthPollTimer = null;
  }
  sessionStore.eventSource?.close();
});
</script>

<template>
  <div class="prototype-shell">
    <AppSidebar />
    <main class="prototype-main">
      <AppTopBar :label="pageLabel" :system-status="appStore.systemStatus" @quick-run="handleQuickRun" />
      <div class="prototype-content">
        <RouterView />
      </div>
      <section :class="['log-panel', { expanded: logExpanded }]">
        <header class="log-panel-head" @click="logExpanded = !logExpanded">
          <div class="log-panel-title">
            &gt;_ Runtime Event Console
            <span class="log-badge">{{ runtimeBadge }}</span>
          </div>
          <i :class="['fa-solid', logExpanded ? 'fa-chevron-down' : 'fa-chevron-up', 'log-panel-toggle']"></i>
        </header>
        <div v-if="logExpanded" class="log-panel-body">
          <div v-for="(line, index) in runtimeLines" :key="`${index}-${line}`">
            {{ line }}
          </div>
          <div class="log-cursor-line">
            <span class="system-chip">system</span>
            runtime-console-ready
            <span class="cursor-blink"></span>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
