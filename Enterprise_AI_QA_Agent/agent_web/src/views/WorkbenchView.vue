<script setup lang="ts">
import { computed } from "vue";

import { workbenchPlugins, type WorkbenchPluginKey } from "../features/workbench/plugins";
import { useSessionStore } from "../stores/session";

const sessionStore = useSessionStore();
const activePluginKey = computed<WorkbenchPluginKey>(() =>
  sessionStore.messages.length > 0 ? "conversation" : "idle",
);
const activePlugin = computed(() => workbenchPlugins[activePluginKey.value]);
</script>

<template>
  <component :is="activePlugin.component" :key="activePlugin.key" />
</template>
