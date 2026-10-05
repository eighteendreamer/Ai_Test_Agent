<script setup lang="ts">
import { useRoute } from "vue-router";
import CaseGenerationTask from "../features/tools/plugins/CaseGenerationTask.vue";
const route = useRoute();
const parameter = (name: string) => typeof route.params[name] === "string" ? route.params[name] as string : "";
const query = (name: string) => typeof route.query[name] === "string" ? route.query[name] as string : "";
</script>

<template>
  <section class="view-page generation-page">
    <header><h2>固定用例生成任务</h2><RouterLink :to="{ name: 'taskpool' }">返回任务池</RouterLink></header>
    <CaseGenerationTask :project-id="parameter('projectId')" :document-id="parameter('documentId')"
      :group-id="parameter('groupId')" :task-id="parameter('taskId')" :parent-task-id="query('parent_task_id')" :retry-key="query('retry_key')" />
  </section>
</template>

<style scoped>
.generation-page { padding: 20px; overflow: auto; min-height: 0; min-width: 0; background: var(--bg); color: var(--text); }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.generation-page :deep(.secondary-btn) { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: var(--surface); color: var(--text); font: inherit; }
.generation-page :deep(button:disabled) { opacity: 0.6; cursor: default; }
</style>
