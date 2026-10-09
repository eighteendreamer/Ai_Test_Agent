<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import * as service from "../../../services/caseGeneration.api";
import type { DocumentParseSnapshotView } from "../../../types";
import type { DocumentGenerationScope, DocumentGenerationTaskRequest, GenerationGroup, GenerationGroupView } from "../../../services/caseGeneration.types";
import { assertGenerationGroup, assertGenerationReport, assertGenerationScope } from "./caseGenerationProduct";
import GenerationRetryPanel from "./GenerationRetryPanel.vue";
import { HttpError } from "../../../services/http";

const props = defineProps<{ view: DocumentParseSnapshotView; canWrite: boolean }>();
const objective = ref("依据完整需求文档生成各章节的测试用例草稿");
const mode = ref("api_testing"); const model = ref(""); const maxCases = ref(30);
const pending = ref<DocumentGenerationTaskRequest | null>(null);
const report = ref<GenerationGroupView | null>(null); const scope = ref<DocumentGenerationScope | null>(null);
const busy = ref(false); const error = ref(""); const warning = ref("");
const confirmedFailure = ref(false); const storageInvalid = ref(false);
let epoch = 0; let disposed = false; let timer: ReturnType<typeof setTimeout> | undefined;
const identity = () => JSON.stringify([props.view.snapshot.seed.project_id, props.view.snapshot.seed.document_id, props.view.snapshot.id]);
const context = () => ({ epoch, identity: identity(), project: props.view.snapshot.seed.project_id, document: props.view.snapshot.seed.document_id });
const current = (ctx: ReturnType<typeof context>) => !disposed && ctx.epoch === epoch && ctx.identity === identity();
const key = () => "qa-agent:document-generation:" + identity();
const finished = computed(() => !!report.value && report.value.modules.every(t => ["completed", "failed"].includes(t.status)));
const labels = { queued: "等待生成", running: "生成中", completed: "草稿已保存", failed: "生成失败" };
function stop() { if (timer) clearTimeout(timer); timer = undefined; }
function persist() {
  try { localStorage.setItem(key(), JSON.stringify({ request: pending.value, confirmedFailure: confirmedFailure.value })); }
  catch { warning.value = "浏览器无法保存请求，请保留下方请求编号，从任务列表或原请求回查。"; }
}
function show(err: unknown) { error.value = err instanceof Error ? err.message : "普通文档生成操作失败"; }
async function read(group: GenerationGroup, ctx: ReturnType<typeof context>, original: DocumentGenerationTaskRequest) {
  assertGenerationGroup(group, ctx.project, ctx.document);
  const [value, fixed] = await Promise.all([service.readCaseGeneration(ctx.project, ctx.document, group.id),
    service.readCaseGenerationScope(ctx.project, ctx.document, group.id)]);
  if (!current(ctx)) return;
  assertGenerationScope(fixed, group); assertGenerationReport(value, group, fixed);
  if (value.group.id !== group.id || fixed.implementation_version !== "document-generation-scope-v1"
    || group.creation_key !== original.creation_key || JSON.stringify({ ...fixed.request, creation_key: group.creation_key }) !==
      JSON.stringify({ parse_snapshot_id: original.parse_snapshot_id, objective: original.objective, mode_key: original.mode_key,
        model_key: original.model_key, max_cases_per_module: original.max_cases_per_module, creation_key: original.creation_key })) {
    throw new Error("返回的生成范围与原普通文档请求不一致，请保留原请求回查");
  }
  scope.value = fixed; report.value = value;
  if (value.modules.some(t => ["queued", "running"].includes(t.status))) timer = setTimeout(() => { void recover(); }, 1500);
}
async function submit() {
  if (!props.canWrite || props.view.result.status !== "complete" || busy.value || pending.value || storageInvalid.value) return;
  const ctx = context(); busy.value = true; error.value = ""; stop();
  const requestId = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const original: DocumentGenerationTaskRequest = { creation_key: "document-generation-" + requestId,
    parse_snapshot_id: props.view.snapshot.id, objective: objective.value, mode_key: mode.value,
    model_key: model.value || null, max_cases_per_module: maxCases.value };
  pending.value = original; persist();
  try { await read(await service.submitDocumentGeneration(ctx.project, ctx.document, original), ctx, original); }
  catch (err) { if (current(ctx)) {
    if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) { confirmedFailure.value = true; persist(); }
    show(err);
  } }
  finally { if (current(ctx)) busy.value = false; }
}
async function recover() {
  if (!pending.value || busy.value) return;
  const ctx = context(); const original = pending.value; busy.value = true; error.value = ""; stop();
  try { await read(await service.getCaseGenerationSubmission(ctx.project, ctx.document, original.creation_key), ctx, original); }
  catch (err) { if (current(ctx)) show(err); }
  finally { if (current(ctx)) busy.value = false; }
}
function newRequest() {
  if ((!finished.value && !confirmedFailure.value) || busy.value) return;
  stop(); pending.value = null; report.value = null; scope.value = null; confirmedFailure.value = false; error.value = ""; persist();
}
watch(identity, () => {
  ++epoch; stop(); busy.value = false; pending.value = null; report.value = null; scope.value = null; error.value = ""; warning.value = ""; confirmedFailure.value = false; storageInvalid.value = false;
  try {
    const raw = localStorage.getItem(key());
    const stored = raw ? JSON.parse(raw) : null;
    const saved = stored?.request;
    if (saved) {
      if (saved.parse_snapshot_id !== props.view.snapshot.id || typeof saved.creation_key !== "string" || !saved.creation_key) throw new Error("保存的生成请求与当前快照不一致");
      pending.value = saved; confirmedFailure.value = stored.confirmedFailure === true;
      if (!confirmedFailure.value) void recover();
    }
  } catch (err) { storageInvalid.value = true; show(err); }
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; stop(); });
</script>

<template>
  <section class="document-generation" aria-label="普通文档模块生成">
    <h4>普通文档生成用例</h4>
    <p>使用当前解析快照，按章节生成草稿。每个模块结合完整文档理解规则，草稿需评审后启用。</p>
    <p>固定解析快照 {{ view.snapshot.id }} · 原件版本 {{ view.snapshot.seed.version_id }}</p>
    <p v-if="view.result.status !== 'complete'">解析尚不完整，请先处理解析结果。</p>
    <fieldset :disabled="busy || !!pending || !canWrite">
      <label>生成目标<textarea v-model="objective" aria-label="普通文档生成目标" /></label>
      <label>用例模式元数据<input v-model="mode" aria-label="普通文档用例模式" /></label>
      <label>每模块最多草稿数<input v-model.number="maxCases" type="number" min="1" max="200" aria-label="普通文档每模块草稿数" /></label>
      <label>模型配置编号（留空使用默认）<input v-model="model" aria-label="普通文档生成模型" /></label>
    </fieldset>
    <button class="primary-btn" :disabled="busy || !!pending || storageInvalid || !canWrite || view.result.status !== 'complete'" @click="submit">提交普通文档模块生成</button>
    <button v-if="pending" class="secondary-btn" :disabled="busy" @click="recover">回查原普通文档生成</button>
    <button v-if="(finished || confirmedFailure) && canWrite" class="secondary-btn" :disabled="busy" @click="newRequest">{{ confirmedFailure ? '修改被拒绝的生成请求' : '新建生成请求' }}</button>
    <p v-if="pending">原请求编号：{{ pending.creation_key }}</p>
    <p v-if="busy" role="status">正在处理生成请求…</p><p v-if="error" role="alert">{{ error }}</p><p v-if="warning" role="alert">{{ warning }}</p>
    <template v-if="report && scope">
      <p role="status">模块进度 {{ report.modules.filter(t => ['completed', 'failed'].includes(t.status)).length }} / {{ report.modules.length }}；成功 {{ report.modules.filter(t => t.status === 'completed').length }}</p>
      <p>{{ scope.basis_limitations.join('；') }}</p>
      <article v-for="task in report.modules" :key="task.id">
        <p>{{ scope.segments.find(s => s.module_id === task.module_id)?.title }} · {{ labels[task.status] }} · {{ task.message }}</p>
        <RouterLink :to="{ name: 'case-generation-task', params: { projectId: report.group.project_id, documentId: report.group.document_id, groupId: report.group.id, taskId: task.id } }">查看固定模块、完整草稿及原响应</RouterLink>
        <GenerationRetryPanel v-if="task.status === 'failed'" :parent="task" :group="report.group" :scope="scope" :can-write="canWrite" :disabled="busy" />
      </article>
      <details><summary>完整固定文档与模块来源</summary><pre>{{ JSON.stringify(scope, null, 2) }}</pre></details>
    </template>
  </section>
</template>

<style scoped>
.document-generation { margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; } label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
article { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; } [role="alert"] { color: var(--red); }
</style>
