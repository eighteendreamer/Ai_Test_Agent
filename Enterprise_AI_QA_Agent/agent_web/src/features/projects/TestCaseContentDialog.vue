<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";

const props = withDefaults(defineProps<{ testCase: TestCaseRecord; initialVersionId?: string; canWrite?: boolean }>(), { canWrite: true });
const emit = defineEmits<{ close: []; saved: [] }>();
const versions = ref<TestCaseVersionRecord[]>([]);
const versionId = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const tab = ref<"steps" | "tree" | "raw" | "edit">("tree");
const editor = ref("");
const notes = ref("");
const record = ref(props.testCase);
const reviewing = ref(false);
const storageWarning = ref("");
const rejected = ref(false);
type ReviewOperation = { action: "submitted" | "activated"; versionId: string; requestId: string; expectedActive: string | null };
const pending = ref<ReviewOperation | null>(null);
const labels = { draft: "草稿", pending_review: "待评审", approved: "已确认" };
function reviewStatus(version: TestCaseVersionRecord) {
  return record.value.version_reviews?.[version.id]?.status
    ?? (record.value.active_version_id === version.id ? "approved"
      : record.value.lifecycle_status === "pending_review" && version.version === record.value.latest_version ? "pending_review" : "draft");
}
function storageKey() { return `case-version-review:${props.testCase.project_id}:${props.testCase.id}`; }
function persistOperation() {
  try {
    if (pending.value) window.sessionStorage.setItem(storageKey(), JSON.stringify(pending.value));
    else window.sessionStorage.removeItem(storageKey());
    storageWarning.value = "";
  } catch { storageWarning.value = "浏览器无法保存评审请求；关闭前请记录请求编号，再核对版本操作记录。"; }
}
function restoreOperation() {
  try {
    const raw = window.sessionStorage.getItem(storageKey());
    if (!raw) return;
    const value = JSON.parse(raw);
    if (!["submitted", "activated"].includes(value.action) || typeof value.versionId !== "string" || !value.versionId
      || typeof value.requestId !== "string" || !value.requestId || value.requestId.length > 160
      || (value.expectedActive !== null && typeof value.expectedActive !== "string")) throw new Error("已保存的评审请求格式无效，请核对版本操作记录");
    pending.value = { action: value.action, versionId: value.versionId, requestId: value.requestId, expectedActive: value.expectedActive };
  } catch (err) { storageWarning.value = err instanceof Error ? err.message : "评审请求恢复失败"; }
}
function resolveOperation() {
  const operation = pending.value;
  if (!operation) return false;
  const found = record.value.version_reviews?.[operation.versionId]?.events.some(event =>
    event.action === operation.action && event.request_id === operation.requestId);
  if (!found) return false;
  pending.value = null;
  rejected.value = false;
  persistOperation();
  return true;
}
const current = computed(() => versions.value.find(item => item.id === versionId.value));
const content = computed(() => {
  const version = current.value;
  return version?.content_json ?? { preconditions: version?.preconditions ?? [], steps: version?.steps ?? [],
    assertions: version?.assertions ?? [], test_data: version?.test_data ?? {}, cleanup: version?.cleanup ?? [] };
});
const steps = computed(() => Array.isArray(content.value.steps) ? content.value.steps : current.value?.steps ?? []);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [fresh, values] = await Promise.all([api.getTestCase(props.testCase.id), api.listTestCaseVersions(props.testCase.id)]);
    record.value = fresh;
    versions.value = values.sort((a, b) => b.version - a.version);
    versionId.value = pending.value?.versionId ?? props.initialVersionId ?? versions.value[0]?.id ?? "";
    if (props.initialVersionId && !values.some(value => value.id === props.initialVersionId)) throw new Error("所选固定版本不存在，请重新读取用例");
    resolveOperation();
  } catch (err) { error.value = err instanceof Error ? err.message : "用例版本加载失败"; }
  finally { loading.value = false; }
}
watch(current, version => {
  editor.value = JSON.stringify(content.value, null, 2);
  notes.value = version?.execution_notes ?? "";
});
async function review(action?: ReviewOperation["action"]) {
  if (reviewing.value || !current.value || !props.canWrite) return;
  reviewing.value = true;
  error.value = "";
  rejected.value = false;
  if (!pending.value && action) {
    pending.value = { action, versionId: current.value.id, requestId: crypto.randomUUID(),
      expectedActive: record.value.active_version_id ?? null };
    persistOperation();
  }
  try {
    record.value = await api.getTestCase(props.testCase.id);
    if (resolveOperation()) { emit("saved"); return; }
    const operation = pending.value;
    if (!operation) return;
    if (operation.action === "submitted") await api.submitTestCaseReview(props.testCase.id, operation.versionId, operation.requestId);
    else await api.activateTestCase(props.testCase.id, operation.versionId, {
      request_id: operation.requestId, expected_active_version_id: operation.expectedActive,
    });
    record.value = await api.getTestCase(props.testCase.id);
    if (!resolveOperation()) throw new Error("未找到该请求的版本操作记录，请重查原请求");
    emit("saved");
  } catch (err) {
    rejected.value = err instanceof HttpError && [404, 409, 422].includes(err.status);
    error.value = err instanceof Error ? err.message : "版本评审操作失败，请重查原请求";
  } finally { reviewing.value = false; }
}
function clearRejectedOperation() {
  if (!rejected.value) return;
  pending.value = null;
  rejected.value = false;
  persistOperation();
}
async function save() {
  const version = current.value;
  if (!version || !props.canWrite) return;
  saving.value = true;
  error.value = "";
  try {
    const body = JSON.parse(editor.value);
    if (!body || Array.isArray(body) || typeof body !== "object") throw new Error("用例内容必须是 JSON 对象");
    const created = await api.createTestCaseVersion(props.testCase.id, {
      content_json: body, execution_notes: notes.value, generation_metadata: {
        ...version.generation_metadata, manual_edit: { source_version_id: version.id },
      },
      preconditions: [], steps: [], assertions: [], test_data: {}, cleanup: [],
      source_refs: version.source_refs, model_key: version.model_key, prompt_version: version.prompt_version,
      skill_versions: version.skill_versions,
    });
    versions.value.unshift(created);
    versionId.value = created.id;
    tab.value = "tree";
    emit("saved");
    try { record.value = await api.getTestCase(props.testCase.id); }
    catch (err) { error.value = `新草稿已保存，状态刷新失败：${err instanceof Error ? err.message : "请重新打开用例"}`; }
  } catch (err) { error.value = err instanceof Error ? err.message : "新版本保存失败"; }
  finally { saving.value = false; }
}
onMounted(() => { restoreOperation(); return load(); });
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="emit('close')">
    <section class="case-dialog" role="dialog" aria-label="测试用例内容">
      <header><div><h2>{{ testCase.title }}</h2><small>{{ testCase.case_key }}</small></div><button @click="emit('close')">关闭</button></header>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="storageWarning" role="status">{{ storageWarning }}</p>
      <p v-if="pending" role="status">版本请求 {{ pending.requestId }} 尚待核对。<button :disabled="reviewing" @click="review()">重查并重试原请求</button><button v-if="rejected" @click="clearRejectedOperation">清除已拒绝请求</button></p>
      <p v-if="loading">正在加载完整用例版本…</p>
      <template v-else-if="current">
        <label>版本<select v-model="versionId" :disabled="reviewing"><option v-for="version in versions" :key="version.id" :value="version.id">v{{ version.version }} · {{ labels[reviewStatus(version)] }}{{ record.active_version_id === version.id ? "（已启用）" : "" }}</option></select></label>
        <p>当前版本：{{ labels[reviewStatus(current)] }}{{ record.active_version_id === current.id ? " · 当前启用" : "" }}<span v-if="!record.version_reviews?.[current.id] || record.version_reviews[current.id].origin === 'legacy_state'"> · 历史状态推导</span></p>
        <nav v-if="canWrite && record.lifecycle_status !== 'archived'">
          <button v-if="reviewStatus(current) === 'draft'" :disabled="reviewing || !!pending || saving" @click="review('submitted')">提交当前版本评审</button>
          <button v-if="reviewStatus(current) !== 'draft'" :disabled="reviewing || !!pending || saving || (record.active_version_id === current.id && record.lifecycle_status === 'active' && reviewStatus(current) === 'approved')" @click="review('activated')">启用当前版本</button>
        </nav>
        <p v-if="current.execution_notes" class="notes">{{ current.execution_notes }}</p>
        <nav><button @click="tab = 'steps'">步骤</button><button @click="tab = 'tree'">JSON 树</button><button @click="tab = 'raw'">JSON 原文</button><button :disabled="!canWrite || record.lifecycle_status === 'archived' || reviewing" @click="tab = 'edit'">编辑新版本</button></nav>
        <div v-if="tab === 'steps'"><JsonContentTree v-for="(step, index) in steps" :key="index" :label="`步骤 ${index + 1}`" :value="step" /><p v-if="!steps.length">当前结构没有通用步骤数组，请使用 JSON 树查看完整内容。</p></div>
        <JsonContentTree v-else-if="tab === 'tree'" label="用例" :value="content" />
        <pre v-else-if="tab === 'raw'">{{ JSON.stringify(content, null, 2) }}</pre>
        <div v-else><p>保存将追加草稿版本，保留原版本与已冻结的运行依据。</p><label>用例自身说明<textarea v-model="notes" rows="3"></textarea></label><label>完整 JSON<textarea v-model="editor" rows="18" class="json-editor"></textarea></label><button :disabled="saving" @click="save">{{ saving ? "保存中…" : "保存新草稿版本" }}</button></div>
        <details><summary>来源及生成依据</summary><JsonContentTree label="来源" :value="current.source_refs" /><JsonContentTree label="生成记录" :value="current.generation_metadata ?? {}" /><small>SHA256 {{ current.content_hash }}</small></details>
        <details><summary>版本评审记录</summary><JsonContentTree label="评审" :value="record.version_reviews?.[current.id] ?? { status: reviewStatus(current), origin: 'legacy_state' }" /></details>
      </template>
      <p v-else-if="!loading">没有可查看的用例版本。</p>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
</style>
