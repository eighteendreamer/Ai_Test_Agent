<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { ApiDocRecord, ApiDocVersionRecord, DocumentParsingSettingsRecord, DocumentParseTaskRecord,
  DocumentParseTaskRequest, DocumentParseSnapshot as SnapshotRecord, DocumentParseSnapshotView, DocumentParseFailureView, DocumentParserCapability } from "../../../types";
import DocumentParsingSettings from "./DocumentParsingSettings.vue";
import DocumentParseSnapshot from "./DocumentParseSnapshot.vue";
import DocumentGenerationPanel from "./DocumentGenerationPanel.vue";

const props = defineProps<{ document: ApiDocRecord; canWrite: boolean }>();
const versions = ref<ApiDocVersionRecord[]>([]);
const versionId = ref("");
const settings = ref<DocumentParsingSettingsRecord | null>(null);
const capabilities = ref<DocumentParserCapability[] | null>(null);
const tasks = ref<DocumentParseTaskRecord[]>([]);
const snapshots = ref<SnapshotRecord[]>([]);
const activeTask = ref<DocumentParseTaskRecord | null>(null);
const view = ref<DocumentParseSnapshotView | null>(null);
const failure = ref<DocumentParseFailureView | null>(null);
const error = ref("");
const storageWarning = ref("");
const busy = ref(false);
const followTask = ref(false);
const loading = ref(false);
const moreVersions = ref(false);
const moreTasks = ref(false);
const moreSnapshots = ref(false);
type Submission = { payload: DocumentParseTaskRequest; task_id?: string; status?: DocumentParseTaskRecord["status"] };
const pending = ref<Submission | null>(null);
let epoch = 0;
let contentRevision = 0;
let catalogRevision = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
const labels = { queued: "等待解析", running: "正在解析", completed: "解析任务已完成", failed: "解析任务失败" };
const selectedVersion = computed(() => versions.value.find(version => version.id === versionId.value));
const selectedParser = computed(() => {
  const options = settings.value?.payload;
  const filename = selectedVersion.value?.snapshot.original_asset?.filename || "";
  const extension = filename.includes(".") ? `.${filename.split(".").pop()!.toLowerCase()}` : "";
  const parserId = options?.options.parser_id === "auto" ? options.parser_by_extension[extension] : options?.options.parser_id;
  return capabilities.value?.find(parser => parser.parser_id === parserId && parser.available && parser.formats.includes(extension));
});
const pendingUnfinished = computed(() => pending.value && !["completed", "failed"].includes(pending.value.status || ""));
const key = () => `qa-agent:document-parse:${props.document.project_id}:${props.document.id}`;
const context = () => ({ epoch, projectId: props.document.project_id!, documentId: props.document.id });
type Context = ReturnType<typeof context>;
const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.document.project_id && ctx.documentId === props.document.id;
function showError(err: unknown) { error.value = err instanceof Error ? err.message : "文档解析操作失败"; }
function persist() {
  try { sessionStorage.setItem(key(), JSON.stringify(pending.value)); storageWarning.value = ""; }
  catch { storageWarning.value = "浏览器无法保存解析请求编号，请保留下方编号，或从解析任务列表找回。"; }
}
function stopPolling() { if (timer) clearTimeout(timer); timer = undefined; }

async function openSnapshot(snapshotId: string, expectedVersion?: string, fromTask = false) {
  if (!fromTask) followTask.value = false;
  const ctx = context(); const revision = ++contentRevision;
  view.value = null; failure.value = null; error.value = "";
  try {
    const result = await api.readDocumentParseSnapshot(ctx.projectId, ctx.documentId, snapshotId);
    if (!current(ctx) || revision !== contentRevision) return;
    if (result.snapshot.id !== snapshotId || result.snapshot.seed.project_id !== ctx.projectId
      || result.snapshot.seed.document_id !== ctx.documentId || (expectedVersion && result.snapshot.seed.version_id !== expectedVersion)) {
      throw new Error("解析快照与当前文档或固定版本不一致");
    }
    view.value = result;
  } catch (err) { if (current(ctx) && revision === contentRevision) showError(err); }
}

async function loadCatalog(kind?: "tasks" | "snapshots") {
  if (!props.document.project_id || !versionId.value) return;
  const ctx = context(); const selected = versionId.value; const revision = ++catalogRevision;
  loading.value = true; error.value = "";
  try {
    const [taskPage, snapshotPage] = await Promise.all([
      kind === "snapshots" ? null : api.listDocumentParseTasks(ctx.projectId, ctx.documentId, selected, kind === "tasks" ? tasks.value.length : 0),
      kind === "tasks" ? null : api.listDocumentParseSnapshots(ctx.projectId, ctx.documentId, selected, kind === "snapshots" ? snapshots.value.length : 0),
    ]);
    if (!current(ctx) || selected !== versionId.value || revision !== catalogRevision) return;
    if (taskPage) { tasks.value = kind === "tasks" ? [...tasks.value, ...taskPage.items.filter(item => !tasks.value.some(old => old.id === item.id))] : taskPage.items; moreTasks.value = taskPage.has_more; }
    if (snapshotPage) { snapshots.value = kind === "snapshots" ? [...snapshots.value, ...snapshotPage.items.filter(item => !snapshots.value.some(old => old.id === item.id))] : snapshotPage.items; moreSnapshots.value = snapshotPage.has_more; }
  } catch (err) { if (current(ctx) && revision === catalogRevision) showError(err); }
  finally { if (current(ctx) && revision === catalogRevision) loading.value = false; }
}

async function accept(value: DocumentParseTaskRecord, ctx: Context, expected: { taskId?: string; request?: DocumentParseTaskRequest }) {
  if (!current(ctx)) return;
  if (value.project_id !== ctx.projectId || value.document_id !== ctx.documentId
    || (expected.taskId && value.id !== expected.taskId)
    || (expected.request && (value.creation_key !== expected.request.creation_key || value.version_id !== expected.request.version_id))) {
    throw new Error("解析任务与当前文档或原请求身份不一致");
  }
  activeTask.value = value;
  if (pending.value?.payload.creation_key === value.creation_key) {
    pending.value.task_id = value.id; pending.value.status = value.status; persist();
  }
  if (value.status === "completed" || value.status === "failed") {
    await loadCatalog();
    if (current(ctx) && followTask.value && value.snapshot_id) await openSnapshot(value.snapshot_id, value.version_id, true);
  } else if (current(ctx)) timer = setTimeout(() => { void queryTask(value.id, true); }, 1000);
}

async function queryTask(taskId?: string, poll = false) {
  if (busy.value || !props.document.project_id || (!taskId && !pending.value)) return;
  stopPolling(); const ctx = context(); const request = pending.value?.payload;
  if (!poll) { followTask.value = true; ++contentRevision; view.value = null; failure.value = null; }
  busy.value = true; error.value = "";
  try {
    const value = taskId ? await api.getDocumentParseTask(ctx.projectId, ctx.documentId, taskId)
      : await api.getDocumentParseSubmission(ctx.projectId, ctx.documentId, request!.creation_key);
    await accept(value, ctx, taskId ? { taskId } : { request });
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}

async function submit(retry = false) {
  if (busy.value || !props.canWrite || !props.document.project_id) return;
  if (retry ? !pendingUnfinished.value : (!selectedParser.value || !selectedVersion.value?.snapshot.original_asset || pendingUnfinished.value)) return;
  stopPolling(); const ctx = context(); busy.value = true; error.value = "";
  try {
    if (!retry) {
      const creationKey = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `parse-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pending.value = { payload: { creation_key: creationKey, version_id: versionId.value } };
      activeTask.value = null; view.value = null; failure.value = null; ++contentRevision; persist();
    }
    followTask.value = true;
    const payload = JSON.parse(JSON.stringify(pending.value!.payload)) as DocumentParseTaskRequest;
    await accept(await api.submitDocumentParseTask(ctx.projectId, ctx.documentId, payload), ctx, { request: payload });
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}

async function openFailure() {
  if (!activeTask.value?.failure_artifact || busy.value) return;
  const ctx = context(); const taskId = activeTask.value.id; const revision = ++contentRevision;
  followTask.value = false; busy.value = true; view.value = null; failure.value = null; error.value = "";
  try {
    const result = await api.readDocumentParseFailure(ctx.projectId, ctx.documentId, taskId);
    if (current(ctx) && revision === contentRevision) {
      if (result.task_id !== taskId) throw new Error("失败诊断任务身份不一致");
      failure.value = result;
    }
  } catch (err) { if (current(ctx) && revision === contentRevision) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}

async function changeVersion() {
  followTask.value = false; ++contentRevision; view.value = null; failure.value = null; tasks.value = []; snapshots.value = [];
  await loadCatalog();
}
async function loadMoreVersions() {
  const ctx = context(); loading.value = true;
  try {
    const items = await api.listApiDocVersions(ctx.documentId, versions.value.length);
    if (current(ctx)) { versions.value.push(...items); moreVersions.value = items.length === 20; }
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) loading.value = false; }
}
async function initialize() {
  stopPolling(); ++epoch; ++contentRevision; ++catalogRevision;
  pending.value = null; activeTask.value = null; view.value = null; failure.value = null; settings.value = null; capabilities.value = null;
  versions.value = []; tasks.value = []; snapshots.value = []; versionId.value = ""; busy.value = false; followTask.value = false;
  error.value = ""; storageWarning.value = "";
  if (!props.document.project_id) return;
  const ctx = context(); loading.value = true;
  try {
    const raw = sessionStorage.getItem(key());
    if (raw) {
      const stored = JSON.parse(raw) as Submission;
      if (!stored?.payload || typeof stored.payload.creation_key !== "string" || !stored.payload.creation_key
        || stored.payload.creation_key.length > 160 || typeof stored.payload.version_id !== "string" || !stored.payload.version_id) throw new Error("保存的解析请求身份无效");
      // A cached terminal label is not proof of the current server state.
      pending.value = { payload: stored.payload, task_id: stored.task_id };
    }
  } catch (err) { storageWarning.value = err instanceof Error ? err.message : "解析请求读取失败，请从任务列表找回。"; }
  try {
    const [configResult, versionsResult, parsersResult] = await Promise.allSettled([
      api.getDocumentParsingSettings(ctx.projectId), api.listApiDocVersions(ctx.documentId), api.getDocumentParserCapabilities(ctx.projectId)]);
    if (!current(ctx)) return;
    if (configResult.status === "fulfilled") settings.value = configResult.value;
    if (parsersResult.status === "fulfilled") capabilities.value = parsersResult.value;
    if (versionsResult.status === "fulfilled") { versions.value = versionsResult.value; moreVersions.value = versionsResult.value.length === 20; }
    const problems = [configResult, versionsResult, parsersResult].filter(result => result.status === "rejected")
      .map(result => result.reason instanceof Error ? result.reason.message : "文档解析目录读取失败");
    versionId.value = props.document.current_version_id || versions.value[0]?.id || "";
    await loadCatalog();
    if (current(ctx) && pending.value) await queryTask();
    if (current(ctx) && problems.length) error.value = [...problems, error.value].filter(Boolean).join("；");
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) loading.value = false; }
}
watch(() => `${props.document.id}:${props.document.project_id}:${props.document.current_version_id}`, initialize, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; ++contentRevision; ++catalogRevision; stopPolling(); });
</script>

<template>
  <section class="document-parsing" aria-label="文档解析">
    <h4>文档解析</h4>
    <p v-if="!document.project_id">请先将文档绑定到正式项目。</p>
    <template v-else>
      <p v-if="!document.current_version_id">请先初始化文档历史版本，再解析可追溯的原件。</p>
      <div class="parse-actions">
        <label>解析原件版本<select v-model="versionId" aria-label="解析原件版本" :disabled="loading" @change="changeVersion">
          <option v-for="version in versions" :key="version.id" :value="version.id">V{{ version.version }} · {{ version.snapshot.original_asset?.filename || version.snapshot.filename }}</option>
        </select></label>
        <button v-if="moreVersions" class="secondary-btn" :disabled="loading" @click="loadMoreVersions">更多原件版本</button>
        <button v-if="canWrite" class="primary-btn" :disabled="busy || !selectedParser || !selectedVersion?.snapshot.original_asset || !!pendingUnfinished" @click="submit()">提交解析</button>
        <button class="secondary-btn" :disabled="busy || loading" @click="initialize">刷新解析列表</button>
      </div>
      <p v-if="selectedVersion && !selectedVersion.snapshot.original_asset">此版本缺少原件，不能用派生内容冒充原件解析。</p>
      <p v-if="capabilities && settings && selectedVersion?.snapshot.original_asset && !selectedParser">当前设置没有支持此原件格式的可用解析器。已转换的 API 文档内容可在版本历史中查看。</p>
      <DocumentParsingSettings v-if="settings" :project-id="document.project_id" :record="settings" :can-write="canWrite" @saved="settings = $event" />
      <div v-if="pending" class="parse-submission">
        <p>解析请求编号：{{ pending.payload.creation_key }} · 固定原件版本 {{ pending.payload.version_id }}</p>
        <button class="secondary-btn" :disabled="busy" @click="queryTask()">查询上次解析提交</button>
        <button v-if="canWrite && pendingUnfinished && !pending.task_id" class="secondary-btn" :disabled="busy" @click="submit(true)">重试原解析提交</button>
        <p v-if="!pending.status">提交结果尚未确认，查询或重试会保留原请求和版本。</p>
      </div>
      <p v-if="activeTask" class="parse-task-status" role="status">{{ labels[activeTask.status] }} · {{ activeTask.message }} · {{ activeTask.version_id }} · 第 {{ activeTask.attempt_no }} 次领取</p>
      <button v-if="activeTask?.failure_artifact" class="secondary-btn" :disabled="busy" @click="openFailure">查看解析失败诊断</button>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
      <p v-if="loading">正在读取解析记录…</p>
      <details><summary>所选版本的解析任务（{{ tasks.length }}{{ moreTasks ? '+' : '' }}）</summary>
        <div v-for="task in tasks" :key="task.id" class="parse-row">
          <span>{{ labels[task.status] }} · {{ task.created_at }}</span><button class="secondary-btn" :disabled="busy" @click="queryTask(task.id)">查看解析任务 {{ task.id }}</button>
        </div>
        <button v-if="moreTasks" class="secondary-btn" :disabled="loading" @click="loadCatalog('tasks')">更多解析任务</button>
      </details>
      <p class="parse-default">所选原件版本默认快照：{{ snapshots[0]?.id || '暂无已保存结果' }}</p>
      <p>默认读取最近提交且已保存的结果。新任务的等待或失败状态单独显示，历史快照可继续查看。</p>
      <div v-for="snapshot in snapshots" :key="snapshot.id" class="parse-row">
        <span>{{ snapshot.parser.parser_id }} · {{ snapshot.seed.options.pdf_extraction_mode === 'layout' ? '保留布局' : '普通文本' }} · {{ snapshot.created_at }}</span>
        <button class="secondary-btn" @click="openSnapshot(snapshot.id, snapshot.seed.version_id)">查看解析快照 {{ snapshot.id }}</button>
      </div>
      <button v-if="moreSnapshots" class="secondary-btn" :disabled="loading" @click="loadCatalog('snapshots')">更多解析快照</button>
      <DocumentParseSnapshot v-if="view" :view="view" />
      <DocumentGenerationPanel v-if="view" :key="view.snapshot.id" :view="view" :can-write="canWrite" />
      <article v-if="failure" class="parse-failure" aria-label="解析失败诊断">
        <h5>解析失败诊断 · 退出码 {{ failure.exit_code ?? '未取得' }}</h5>
        <p>{{ failure.error }}</p><h6>stdout</h6><pre>{{ failure.stdout || '空输出' }}</pre><h6>stderr</h6><pre>{{ failure.stderr || '空输出' }}</pre>
      </article>
    </template>
  </section>
</template>

<style scoped>
.document-parsing { margin: 20px 0; padding: 16px; border: 1px solid var(--border); border-radius: 12px; }
.parse-actions, .parse-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.parse-row { justify-content: space-between; }
.parse-submission, .parse-task-status, .parse-default { overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
