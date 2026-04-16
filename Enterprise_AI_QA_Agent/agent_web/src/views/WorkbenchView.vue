<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import ApprovalPanel from "../components/chat/ApprovalPanel.vue";
import ChatComposer from "../components/chat/ChatComposer.vue";
import ChatTimeline from "../components/chat/ChatTimeline.vue";
import RuntimeStatusPanel from "../components/chat/RuntimeStatusPanel.vue";
import { useSessionStore } from "../stores/session";

const sessionStore = useSessionStore();
const hasConversation = computed(() => sessionStore.messages.length > 0);
const heroTitle = "御策天检";
const heroSubtitle = "输入自然语言指令，AI 将全权进行意图分析、页面探索与用例生成";
const composerAnchorRef = ref<HTMLElement | null>(null);
const composerAnchorHeight = ref(196);
const runtimePanelSize = ref(112);
let resizeObserver: ResizeObserver | null = null;

const layoutStyle = computed(() => ({
  "--composer-safe-space": `${composerAnchorHeight.value + 32}px`,
}));

const composerAnchorStyle = computed(() => ({
  "--runtime-panel-size": `${runtimePanelSize.value}px`,
}));

function updateRuntimeLayout() {
  const height = composerAnchorRef.value?.offsetHeight ?? 0;
  composerAnchorHeight.value = Math.max(160, Math.round(height || 196));
  runtimePanelSize.value = Math.max(96, Math.round(height || 112));
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
  <section class="view-home" :class="{ 'view-home-conversation': hasConversation }">
    <div
      class="home-center-wrap"
      :class="{ 'home-center-wrap-conversation': hasConversation }"
      :style="layoutStyle"
    >
      <Transition name="home-hero-transition">
        <div v-if="!hasConversation" class="home-hero">
          <div class="home-logo-box">
            <i class="fa-solid fa-spider"></i>
          </div>
          <h1 class="home-title">{{ heroTitle }}</h1>
          <p class="home-subtitle">{{ heroSubtitle }}</p>
        </div>
      </Transition>

      <div class="home-thread-shell" :class="{ 'home-thread-shell-active': hasConversation }">
        <ChatTimeline :messages="sessionStore.messages" />
        <p v-if="sessionStore.error" class="error-text home-inline-error">{{ sessionStore.error }}</p>
      </div>

      <ApprovalPanel />

      <div class="home-composer-dock" :class="{ 'home-composer-dock-active': hasConversation }">
        <div ref="composerAnchorRef" class="home-composer-anchor" :style="composerAnchorStyle">
          <Transition name="runtime-panel-transition">
            <RuntimeStatusPanel v-if="hasConversation" />
          </Transition>
          <ChatComposer :docked="hasConversation" />
        </div>
      </div>
    </div>
  </section>
</template>
