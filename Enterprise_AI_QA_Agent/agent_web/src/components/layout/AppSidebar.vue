<script setup lang="ts">
const navItems = [
  { to: "/home", icon: "fa-house", title: "首页 (会话界面)" },
  { to: "/taskpool", icon: "fa-list-check", title: "任务池" },
  { to: "/knowledge", icon: "fa-database", title: "页面知识库 (RAG)" },
  { to: "/tools", icon: "fa-toolbox", title: "Skills 与工具" },
  { to: "/reports", icon: "fa-file-contract", title: "综合评估报告" },
  { to: "/settings", icon: "fa-gear", title: "统一系统配置" },
];
</script>

<template>
  <nav class="left-nav">
    <div class="left-nav-logo" title="Enterprise AI QA">
      <i class="fa-solid fa-spider"></i>
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

    <div class="left-nav-user">
      <i class="fa-regular fa-user"></i>
    </div>
  </nav>
</template>
