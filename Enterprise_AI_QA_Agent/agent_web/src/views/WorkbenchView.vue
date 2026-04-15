<script setup lang="ts">
import { computed } from "vue";

import ChatComposer from "../components/chat/ChatComposer.vue";
import ChatTimeline from "../components/chat/ChatTimeline.vue";
import { useSessionStore } from "../stores/session";

const sessionStore = useSessionStore();
const hasConversation = computed(() => sessionStore.messages.length > 0);
</script>

<template>
  <section class="view-home" :class="{ 'view-home-conversation': hasConversation }">
    <div class="home-center-wrap" :class="{ 'home-center-wrap-conversation': hasConversation }">
      <div v-if="!hasConversation" class="home-hero">
        <div class="home-logo-box">
          <i class="fa-solid fa-spider"></i>
        </div>
       <h1 class="home-title">御策天检</h1>
        <p class="home-subtitle">
            输入自然语言指令，AI 将全权进行意图分析、页面探索与用例生成
        </p>
      </div>

      <div class="home-thread-shell" :class="{ 'home-thread-shell-active': hasConversation }">
        <ChatTimeline :messages="sessionStore.messages" />
        <p v-if="sessionStore.error" class="error-text home-inline-error">{{ sessionStore.error }}</p>
      </div>

      <div class="home-composer-dock" :class="{ 'home-composer-dock-active': hasConversation }">
        <ChatComposer :docked="hasConversation" />
      </div>
    </div>
  </section>
</template>
