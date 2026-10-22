<script setup lang="ts">
import { ref, watch } from "vue";
import JsonContentTree from "../../projects/JsonContentTree.vue";
import TestCaseContentDialog from "../../projects/TestCaseContentDialog.vue";
import GenerationRetryPanel from "./GenerationRetryPanel.vue";
import { useGenerationTask } from "./useGenerationTask";
import type { TestCaseRecord } from "../../../types";
const props = withDefaults(defineProps<{ projectId: string; documentId: string; groupId: string; taskId: string; parentTaskId?: string; retryKey?: string }>(), { parentTaskId: "", retryKey: "" });
const { task, group, scope, origin, versions, response, busy, error, permissionError, canWrite, load, readVersion, readResponse } = useGenerationTask(props);
const reviewTarget = ref<{ testCase: TestCaseRecord; versionId: string } | null>(null);
const identity = () => JSON.stringify([props.projectId, props.documentId, props.groupId, props.taskId, props.parentTaskId, props.retryKey]);
watch(identity, () => { reviewTarget.value = null; });
async function reviewVersion(versionId: string) {
  const requestedIdentity = identity();
  const testCase = await readVersion(versionId);
  if (testCase && identity() === requestedIdentity) reviewTarget.value = { testCase, versionId };
}
const labels = { queued: "等待生成", running: "生成处理中", completed: "草稿或停用候选已保存", failed: "生成任务失败" };
const kinds = { update: "更新草稿", create: "新建草稿", retirement_candidate: "停用候选" };
</script>

<template>
  <article aria-label="固定生成任务详情" class="generation-task">
    <p>项目：{{ projectId }} · 文档：{{ documentId }}</p><p>生成组：{{ groupId }} · 任务：{{ taskId }}</p>
    <button class="secondary-btn" :disabled="busy" @click="load">刷新固定生成任务</button>
    <p v-if="busy" role="status">正在读取固定生成记录…</p><p v-if="error" role="alert">{{ error }}</p>
    <p v-if="permissionError" role="alert">{{ permissionError }}；新重试禁用，已保存历史继续可读。</p>
    <template v-if="task && group && scope">
      <p class="generation-task-status" role="status">{{ labels[task.status] }} · {{ task.message }} · {{ task.phase }} · 第 {{ task.attempt_no }} 次领取</p>
      <p v-if="task.error_code">失败代码：{{ task.error_code }}</p>
      <p>{{ kinds[scope.modules.find(item => item.id === task!.module_id)!.kind] }} · 模块 {{ task.module_id }} · 原件版本 {{ task.version_id }}</p>
      <template v-if="scope.implementation_version === 'incremental-generation-scope-v1'">
        <p>固定需求：{{ scope.requirement.title }} · V{{ scope.requirement.version }} · {{ scope.requirement.id }}</p><p>{{ scope.requirement.statement }}</p>
      </template>
      <template v-else>
        <p>普通文档生成 · 固定解析快照 {{ scope.parse_snapshot.id }} · {{ scope.request.objective }}</p>
        <p>章节：{{ scope.segments.find(item => item.module_id === task!.module_id)?.title }}</p>
      </template>
      <p>来源限制：{{ scope.basis_limitations.join('、') || '本范围未声明限制' }}</p>
      <p>结果需逐版本评审和明确启用；设计覆盖、实际执行和环境是否生效分别确认。</p>
      <section v-if="origin" aria-label="固定重试来源"><p>原失败任务：{{ origin.retry.parent_task_id }}</p>
        <p>原请求编号：{{ origin.retry.creation_key }} · {{ origin.retry.strategy === 'saved_response' ? '复用已保存响应' : '执行原固定请求' }}</p>
        <details><summary>完整重试关系</summary><pre>{{ JSON.stringify(origin.retry, null, 2) }}</pre></details>
      </section>
      <section v-for="reference in task.checkpoint?.receipt?.versions || []" :key="reference.version_id">
        <p>固定草稿 {{ reference.case_id }} · V{{ reference.version }} · {{ reference.version_id }}</p>
        <button class="secondary-btn" :disabled="busy" @click="readVersion(reference.version_id)">读取完整任务草稿 {{ reference.version_id }}</button>
        <button class="secondary-btn" :disabled="busy" @click="reviewVersion(reference.version_id)">评审与修订此草稿 {{ reference.version_id }}</button>
        <section v-if="versions[reference.version_id]" :aria-label="`完整任务草稿 ${reference.version_id}`">
          <JsonContentTree :value="versions[reference.version_id].content_json" label="完整 JSON" />
          <details open><summary>完整草稿版本与自身说明</summary><pre>{{ JSON.stringify(versions[reference.version_id], null, 2) }}</pre></details>
        </section>
      </section>
      <details v-if="task.checkpoint?.receipt?.retirement_target"><summary>固定停用候选（待评审）</summary><pre>{{ JSON.stringify(task.checkpoint.receipt.retirement_target, null, 2) }}</pre></details>
      <button v-if="task.checkpoint?.response" class="secondary-btn" :disabled="busy" @click="readResponse">读取固定任务原响应</button>
      <details v-if="response" open><summary>固定任务完整原响应</summary><pre>{{ JSON.stringify(response, null, 2) }}</pre></details>
      <details><summary>完整固定生成范围与来源</summary><pre>{{ JSON.stringify(scope, null, 2) }}</pre></details>
      <details><summary>任务与结果收据</summary><pre>{{ JSON.stringify(task, null, 2) }}</pre></details>
      <GenerationRetryPanel v-if="task.status === 'failed'" :key="task.id" :parent="task" :group="group" :scope="scope" :can-write="canWrite" :disabled="busy" />
    </template>
    <TestCaseContentDialog v-if="reviewTarget" :key="`${reviewTarget.testCase.id}:${reviewTarget.versionId}`"
      :test-case="reviewTarget.testCase" :initial-version-id="reviewTarget.versionId" :can-write="canWrite"
      @close="reviewTarget = null" @saved="load" />
  </article>
</template>

<style scoped>
.generation-task { margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 440px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
