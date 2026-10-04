<script setup lang="ts">
import { useRoute } from "vue-router";
import DocumentDifferenceTask from "../features/tools/plugins/DocumentDifferenceTask.vue";

const route = useRoute();
const parameter = (name: string) => typeof route.params[name] === "string" ? route.params[name] as string : "";
</script>

<template>
  <section class="view-page difference-page">
    <header><h2>历史文档比较</h2><RouterLink :to="{ name: 'taskpool' }">返回任务池</RouterLink></header>
    <DocumentDifferenceTask :project-id="parameter('projectId')" :document-id="parameter('documentId')" :task-id="parameter('taskId')" />
  </section>
</template>

<style scoped>
.difference-page { padding: 20px; overflow: auto; min-height: 0; min-width: 0; background: var(--bg); color: var(--text); }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.difference-page :deep(.secondary-btn), .difference-page :deep(select) {
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px;
  background: var(--surface); color: var(--text); font: inherit;
}
.difference-page :deep(button:disabled) { opacity: 0.6; cursor: default; }
</style>
