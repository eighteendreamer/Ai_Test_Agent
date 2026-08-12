<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import ApprovalPanel from "../../../components/chat/ApprovalPanel.vue";
import ChatComposer from "../../../components/chat/ChatComposer.vue";
import ChatTimeline from "../../../components/chat/ChatTimeline.vue";
import RuntimeStatusPanel from "../../../components/chat/RuntimeStatusPanel.vue";
import { useSessionStore } from "../../../stores/session";

const sessionStore = useSessionStore();
const hasPendingApprovals = computed(() => sessionStore.pendingApprovals.length > 0);
const composerAnchorRef = ref<HTMLElement | null>(null);
const composerAnchorHeight = ref(196);
const runtimePanelSize = ref(112);
let resizeObserver: ResizeObserver | null = null;

const layoutStyle = computed(() => ({
  "--composer-safe-space": `${composerAnchorHeight.value + 32}px`,
}));

const composerAnchorStyle = computed(() => ({
  "--runtime-panel-size": `${runtimePanelSize.value}px`,
  "--composer-anchor-height": `${composerAnchorHeight.value}px`,
}));

function updateRuntimeLayout() {
  const height = composerAnchorRef.value?.offsetHeight ?? 0;
  composerAnchorHeight.value = height > 0 ? Math.round(height) : 10;
  runtimePanelSize.value = Math.max(112, Math.round(height || 112));
}

onMounted(() => {
  updateRuntimeLayout();
  if (!composerAnchorRef.value) {
    return;
  }

  resizeObserver = new ResizeObserver(() => {
    updateRuntimeLayout();
  });
  resizeObserver.observe(composerAnchorRef.value);
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
});
</script>

<template>
  <section class="view-home view-home-conversation">
    <div class="home-center-wrap home-center-wrap-conversation" :style="layoutStyle">
      <div class="home-thread-shell home-thread-shell-active">
        <ChatTimeline :messages="sessionStore.messages" />
        <p v-if="sessionStore.error" class="error-text home-inline-error">{{ sessionStore.error }}</p>
      </div>

      <div class="home-composer-dock home-composer-dock-active">
        <div
          ref="composerAnchorRef"
          class="home-composer-anchor"
          :class="{ 'home-composer-anchor-with-approval': hasPendingApprovals }"
          :style="composerAnchorStyle"
        >
          <RuntimeStatusPanel />
          <ChatComposer :docked="true" />
          <Transition name="runtime-panel-transition">
            <ApprovalPanel v-if="hasPendingApprovals" />
          </Transition>
        </div>
      </div>
    </div>
  </section>
</template>
