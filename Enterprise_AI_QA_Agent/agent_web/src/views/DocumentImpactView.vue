<script setup lang="ts">
import { useRoute } from "vue-router";
import DocumentImpactTask from "../features/tools/plugins/DocumentImpactTask.vue";
const route = useRoute();
const parameter = (name: string) => typeof route.params[name] === "string" ? route.params[name] as string : "";
</script>

<template>
  <section class="view-page impact-page">
    <header><h2>历史文档影响分析</h2><RouterLink :to="{ name: 'taskpool' }">返回任务池</RouterLink></header>
    <DocumentImpactTask :project-id="parameter('projectId')" :document-id="parameter('documentId')" :task-id="parameter('taskId')" />
  </section>
</template>

<style scoped>
.impact-page { padding: 20px; overflow: auto; min-height: 0; min-width: 0; background: var(--bg); color: var(--text); }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.impact-page :deep(.secondary-btn) { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: var(--surface); color: var(--text); font: inherit; }
.impact-page :deep(button:disabled) { opacity: 0.6; cursor: default; }
</style>
