<script setup lang="ts">
import { computed, ref, watch } from "vue";
import JsonContentTree from "../../projects/JsonContentTree.vue";
import TestCaseContentDialog from "../../projects/TestCaseContentDialog.vue";
import GenerationRetryPanel from "./GenerationRetryPanel.vue";
import { useGenerationTask } from "./useGenerationTask";
import type { GenerationCoverageReviewItem } from "../../../services/caseGeneration.types";
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
const coverage = computed(() => task.value?.checkpoint?.receipt?.generation_review ?? null);
const coverageCandidates = computed(() => {
  const module = scope.value?.modules.find(item => item.id === task.value?.module_id);
  const generation = module?.generation as Record<string, unknown> | null | undefined;
  const invocation = generation?.invocation as Record<string, unknown> | undefined;
  const messages = invocation?.messages;
  const message = Array.isArray(messages) ? messages[0] as Record<string, unknown> | undefined : undefined;
  if (typeof message?.content !== "string") return {};
  try {
    const envelope = JSON.parse(message.content) as Record<string, unknown>;
    const context = envelope.evidence_context as Record<string, unknown> | undefined;
    const document = context?.document_generation as Record<string, unknown> | undefined;
    const targets = document?.coverage_targets;
    if (!Array.isArray(targets)) return {};
    return Object.fromEntries(targets.flatMap(value => {
      if (typeof value !== "object" || value === null) return [];
      const candidate = value as Record<string, unknown>;
      return typeof candidate.id === "string" && typeof candidate.statement === "string"
        ? [[candidate.id, candidate.statement]] : [];
    }));
  } catch { return {}; }
});
const mappingLabels = { complete: "结构核对完整，语义仍待评审", incomplete: "结构核对不完整", empty_inventory: "没有可核对的来源语句" };
const dispositionLabels = { in_scope: "模块内候选", out_of_scope: "模型判断不属于本模块", uncertain: "模型判断不确定", not_reported: "模型未处理" };
function candidateText(id: string) { return coverageCandidates.value[id] ?? "固定范围中未找到该候选文本"; }
function sourceLabel(item: GenerationCoverageReviewItem) {
  const source = item.source;
  if (source.kind === "line") return `第 ${source.start_line ?? "?"}–${source.end_line ?? "?"} 行`;
  if (source.kind === "page") return `第 ${source.page ?? "?"} 页`;
  return `${source.part ?? "DOCX"} · ${source.path.join("/")}`;
}
function expectedPath(path: (string | number)[]) {
  return "/" + path.map(token => String(token).replace(/~/g, "~0").replace(/\//g, "~1")).join("/");
}
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
      <section v-if="coverage" aria-label="来源语句到用例预期的覆盖核对">
        <h4>来源语句与用例预期映射（待人工评审）</h4>
        <p>{{ mappingLabels[coverage.mapping_state] }} · {{ coverage.items.length }} 个候选 · 快照 {{ coverage.parse_snapshot_id }}</p>
        <p>这份回执只验证固定来源位置和 JSON 预期路径是否存在，不代表规则被正确理解或已经通过测试。</p>
        <ul v-if="coverage.warnings.length"><li v-for="warning in coverage.warnings" :key="warning">{{ warning }}</li></ul>
        <table v-if="coverage.items.length">
          <thead><tr><th>固定来源候选</th><th>AI 建议</th><th>用例预期路径</th><th>结构问题</th></tr></thead>
          <tbody><tr v-for="item in coverage.items" :key="item.candidate_id">
            <td><small>{{ sourceLabel(item) }} · {{ item.scope }}</small><p>{{ candidateText(item.candidate_id) }}</p></td>
            <td>{{ dispositionLabels[item.model_disposition] }}<p v-if="item.rationale">{{ item.rationale }}</p></td>
            <td><ul v-if="item.step_refs.length"><li v-for="(reference, index) in item.step_refs" :key="index">
              {{ reference.case_key }} · 步骤 {{ reference.step_order }} · {{ reference.expected_field }}
              <span v-if="reference.expected_path">· content_json{{ expectedPath(reference.expected_path) }}</span>
              <span v-else>· 未解析为有效 JSON 路径</span>
            </li></ul><span v-else>无映射</span></td>
            <td>{{ item.validation_issues.join('、') || '无' }}</td>
          </tr></tbody>
        </table>
        <p v-if="coverage.unknown_candidate_ids.length">未知候选编号：{{ coverage.unknown_candidate_ids.join('、') }}</p>
      </section>
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
