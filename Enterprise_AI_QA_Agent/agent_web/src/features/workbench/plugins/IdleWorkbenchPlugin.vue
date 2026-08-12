<script setup lang="ts">
import { computed } from "vue";

import ChatComposer from "../../../components/chat/ChatComposer.vue";
import { t } from "../../../services/i18n";
import { useSessionStore } from "../../../stores/session";

const sessionStore = useSessionStore();
const heroTitle = computed(() => t("home.title"));
const heroSubtitle = computed(() => t("home.subtitle"));
</script>

<template>
  <section class="view-home">
    <div class="home-center-wrap">
      <div class="home-hero">
        <div class="home-logo-box">
          <img src="/logo.svg" alt="" class="brand-logo brand-logo-home" />
        </div>
        <h1 class="home-title">{{ heroTitle }}</h1>
        <p class="home-subtitle">{{ heroSubtitle }}</p>
      </div>

      <div class="home-thread-shell">
        <p v-if="sessionStore.error" class="error-text home-inline-error">{{ sessionStore.error }}</p>
      </div>

      <div class="home-composer-dock">
        <div class="home-composer-anchor">
          <ChatComposer :docked="false" />
        </div>
      </div>
    </div>
  </section>
</template>
