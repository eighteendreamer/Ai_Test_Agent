<script setup lang="ts">
import { computed } from "vue";

import { useAppStore } from "../../stores/app";
import { t } from "../../services/i18n";

const appStore = useAppStore();

const navItems = computed(() => [
  { to: "/home", icon: "fa-house", title: t("nav.home") },
  { to: "/taskpool", icon: "fa-list-check", title: t("nav.taskpool") },
  { to: "/projects", icon: "fa-diagram-project", title: t("nav.projects") },
  { to: "/knowledge", icon: "fa-database", title: t("nav.knowledge") },
  { to: "/tools", icon: "fa-toolbox", title: t("nav.tools") },
  { to: "/reports", icon: "fa-file-contract", title: t("nav.reports") },
]);

const themeIcon = computed(() =>
  appStore.theme === "dark" ? "fa-sun" : "fa-moon",
);

const themeTitle = computed(() =>
  appStore.theme === "dark" ? "切换到浅色主题" : "切换到深色主题",
);
</script>

<template>
  <nav class="left-nav">
    <div class="left-nav-logo" title="Enterprise AI QA">
      <img src="/logo.svg" alt="Enterprise AI QA" class="brand-logo brand-logo-sidebar" />
    </div>

    <div class="left-nav-buttons">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="left-nav-btn"
        active-class="left-nav-btn-active"
        :title="item.title"
      >
        <i class="fa-solid" :class="item.icon"></i>
      </RouterLink>
    </div>

    <button
      type="button"
      class="left-nav-theme-btn"
      :title="themeTitle"
      @click="appStore.toggleTheme()"
    >
      <i class="fa-solid" :class="themeIcon"></i>
    </button>

    <RouterLink
      to="/settings"
      class="left-nav-user left-nav-user-link"
      active-class="left-nav-user-active"
      :title="t('nav.settings')"
    >
      <i class="fa-solid fa-gear"></i>
    </RouterLink>
  </nav>
</template>
