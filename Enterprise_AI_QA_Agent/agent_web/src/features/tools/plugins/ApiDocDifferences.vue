<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { ApiDocRecord, ApiDocVersionRecord, DocumentParseSnapshot, DocumentDifferenceSettingsRecord,
  DocumentDifferenceTaskRecord, DocumentDifferenceTaskRequest, DocumentDifferenceSnapshot } from "../../../types";
import DocumentSnapshotSelector from "./DocumentSnapshotSelector.vue";
import DocumentDifferenceSettings from "./DocumentDifferenceSettings.vue";
import DocumentDifferenceTask from "./DocumentDifferenceTask.vue";
import { assertDifferenceTask } from "./documentDifferenceIdentity";

const props = defineProps<{ document: ApiDocRecord; canWrite: boolean }>();
const versions = ref<ApiDocVersionRecord[]>([]);
const moreVersions = ref(false);
const oldVersionId = ref("");
const newVersionId = ref("");
const oldSnapshot = ref<DocumentParseSnapshot | null>(null);
const newSnapshot = ref<DocumentParseSnapshot | null>(null);
const settings = ref<DocumentDifferenceSettingsRecord | null>(null);
const configError = ref("");
const error = ref("");
const storageWarning = ref("");
const busy = ref(false);
const loading = ref(false);
const tasks = ref<DocumentDifferenceTaskRecord[]>([]);
const snapshots = ref<DocumentDifferenceSnapshot[]>([]);
const moreTasks = ref(false);
const moreSnapshots = ref(false);
const activeTaskId = ref("");
const followingPending = ref(false);
type Submission = { payload: DocumentDifferenceTaskRequest; task_id?: string; status?: DocumentDifferenceTaskRecord["status"] };
const pending = ref<Submission | null>(null);
const pendingUnfinished = computed(() => pending.value && !["completed", "failed"].includes(pending.value.status || ""));
const ready = computed(() => oldSnapshot.value && newSnapshot.value && props.canWrite && !pendingUnfinished.value);
const key = () => `qa-agent:document-difference:${props.document.project_id}:${props.document.id}`;
let epoch = 0;
let catalogRevision = 0;
let disposed = false;
let taskOffset = 0;
let snapshotOffset = 0;
let versionOffset = 0;
const context = () => ({ epoch, projectId: props.document.project_id!, documentId: props.document.id });
type Context = ReturnType<typeof context>;
const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.document.project_id && ctx.documentId === props.document.id;
const showError = (err: unknown) => { error.value = err instanceof Error ? err.message : "文档比较操作失败"; };
function persist() {
  try { sessionStorage.setItem(key(), JSON.stringify(pending.value)); storageWarning.value = ""; }
  catch { storageWarning.value = "浏览器无法保存比较请求编号，请保留下方编号，或从任务池找回。"; }
}
async function loadCatalog(kind?: "tasks" | "snapshots") {
  if (!props.document.project_id) return;
  const ctx = context(); const revision = ++catalogRevision; loading.value = true;
  const requestedTasks = kind === "tasks" ? taskOffset : 0;
  const requestedSnapshots = kind === "snapshots" ? snapshotOffset : 0;
  const results = await Promise.allSettled([
    kind === "snapshots" ? Promise.resolve(null) : api.listDocumentDifferenceTasks(ctx.projectId, ctx.documentId, undefined, requestedTasks),
    kind === "tasks" ? Promise.resolve(null) : api.listDocumentDifferenceSnapshots(ctx.projectId, ctx.documentId, undefined, requestedSnapshots),
  ]);
  if (!current(ctx) || revision !== catalogRevision) return;
  const problems: string[] = [];
  const [taskPage, snapshotPage] = results;
  const collect = (err: unknown) => problems.push(err instanceof Error ? err.message : "比较目录读取失败");
  try {
    if (taskPage.status === "fulfilled" && taskPage.value) {
      const page = taskPage.value;
      page.items.forEach(task => assertDifferenceTask(task, ctx.projectId, ctx.documentId));
      tasks.value = kind === "tasks" ? [...tasks.value, ...page.items.filter(task => !tasks.value.some(old => old.id === task.id))] : page.items;
      taskOffset = page.offset + page.items.length; moreTasks.value = page.has_more;
    }
    if (taskPage.status === "rejected") collect(taskPage.reason);
  } catch (err) { collect(err); }
  try {
    if (snapshotPage.status === "fulfilled" && snapshotPage.value) {
      const page = snapshotPage.value;
      if (page.items.some(snapshot => snapshot.seed.project_id !== ctx.projectId || snapshot.seed.document_id !== ctx.documentId)) {
        throw new Error("比较结果目录与当前项目或文档不一致");
      }
      snapshots.value = kind === "snapshots" ? [...snapshots.value, ...page.items.filter(item => !snapshots.value.some(old => old.id === item.id))] : page.items;
      snapshotOffset = page.offset + page.items.length; moreSnapshots.value = page.has_more;
    }
    if (snapshotPage.status === "rejected") collect(snapshotPage.reason);
  } catch (err) { collect(err); }
  if (problems.length) showError(new Error(problems.join("；")));
  loading.value = false;
}
function accept(task: DocumentDifferenceTaskRecord, ctx: Context, payload: DocumentDifferenceTaskRequest) {
  if (!current(ctx)) return;
  assertDifferenceTask(task, ctx.projectId, ctx.documentId, { request: payload, taskId: pending.value?.task_id });
  pending.value = { payload, task_id: task.id, status: task.status }; persist();
  activeTaskId.value = task.id; followingPending.value = true;
}
async function recover() {
  if (!props.document.project_id || !pending.value || busy.value) return;
  const ctx = context(); const payload = JSON.parse(JSON.stringify(pending.value.payload)) as DocumentDifferenceTaskRequest;
  busy.value = true; error.value = "";
  try { accept(await api.getDocumentDifferenceSubmission(ctx.projectId, ctx.documentId, payload.creation_key), ctx, payload); }
  catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}
async function submit(retry = false) {
  if (!props.document.project_id || !props.canWrite || busy.value || (retry ? !pendingUnfinished.value : !ready.value)) return;
  const ctx = context(); busy.value = true; error.value = "";
  try {
    if (!retry) {
      const creationKey = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `difference-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pending.value = { payload: { creation_key: creationKey, old_snapshot_id: oldSnapshot.value!.id, new_snapshot_id: newSnapshot.value!.id } };
      activeTaskId.value = ""; followingPending.value = false; persist();
    }
    const payload = JSON.parse(JSON.stringify(pending.value!.payload)) as DocumentDifferenceTaskRequest;
    accept(await api.submitDocumentDifferenceTask(ctx.projectId, ctx.documentId, payload), ctx, payload);
    if (current(ctx)) await loadCatalog();
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}
function updateTask(task: DocumentDifferenceTaskRecord) {
  if (!followingPending.value || !pending.value || activeTaskId.value !== pending.value.task_id) return;
  try {
    assertDifferenceTask(task, props.document.project_id!, props.document.id, { request: pending.value.payload, taskId: pending.value.task_id });
    const changed = pending.value.status !== task.status;
    pending.value.status = task.status; persist();
    if (changed && ["completed", "failed"].includes(task.status)) void loadCatalog();
  } catch (err) { showError(err); }
}
function openHistory(taskId: string) { activeTaskId.value = taskId; followingPending.value = false; }
async function loadMoreVersions() {
  if (loading.value) return;
  const ctx = context(); loading.value = true;
  try {
    const page = await api.listApiDocVersions(ctx.documentId, versionOffset);
    if (!current(ctx)) return;
    if (page.some(version => version.document_id !== ctx.documentId)) throw new Error("原件版本列表与当前文档不一致");
    versions.value.push(...page.filter(version => !versions.value.some(old => old.id === version.id)));
    versionOffset += page.length; moreVersions.value = page.length === 20;
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) loading.value = false; }
}
async function initialize() {
  ++epoch; ++catalogRevision; busy.value = false; loading.value = false;
  versions.value = []; tasks.value = []; snapshots.value = []; settings.value = null; oldVersionId.value = ""; newVersionId.value = "";
  oldSnapshot.value = null; newSnapshot.value = null; activeTaskId.value = ""; followingPending.value = false; pending.value = null;
  moreVersions.value = false; moreTasks.value = false; moreSnapshots.value = false; error.value = ""; configError.value = ""; storageWarning.value = "";
  versionOffset = 0; taskOffset = 0; snapshotOffset = 0;
  if (!props.document.project_id) return;
  const ctx = context(); loading.value = true;
  try {
    const raw = sessionStorage.getItem(key());
    if (raw && JSON.parse(raw)) {
      const stored = JSON.parse(raw) as Submission; const payload = stored.payload;
      if (!payload || typeof payload.creation_key !== "string" || !payload.creation_key || payload.creation_key.length > 160
        || typeof payload.old_snapshot_id !== "string" || !payload.old_snapshot_id
        || typeof payload.new_snapshot_id !== "string" || !payload.new_snapshot_id
        || (stored.task_id !== undefined && typeof stored.task_id !== "string")) throw new Error("保存的比较请求身份无效，请从任务池找回");
      // A cached terminal label cannot unlock a new submission without server evidence.
      pending.value = { payload: { creation_key: payload.creation_key, old_snapshot_id: payload.old_snapshot_id, new_snapshot_id: payload.new_snapshot_id }, task_id: stored.task_id };
    }
  } catch (err) { storageWarning.value = err instanceof Error ? err.message : "比较请求读取失败，请从任务池找回"; }
  const [configResult, versionsResult] = await Promise.allSettled([
    api.getDocumentDifferenceSettings(ctx.projectId), api.listApiDocVersions(ctx.documentId),
  ]);
  if (!current(ctx)) return;
  if (configResult.status === "fulfilled") settings.value = configResult.value;
  else configError.value = configResult.reason instanceof Error ? configResult.reason.message : "当前比较设置读取失败，历史仍可查看";
  if (versionsResult.status === "fulfilled") {
    const page = versionsResult.value;
    if (page.some(version => version.document_id !== ctx.documentId)) showError(new Error("原件版本列表与当前文档不一致"));
    else {
      versions.value = page; versionOffset = page.length; moreVersions.value = page.length === 20;
      const newest = page.find(version => version.id === props.document.current_version_id);
      newVersionId.value = newest?.id || "";
      oldVersionId.value = page.find(version => version.version === (newest?.version || 0) - 1)?.id || "";
    }
  } else showError(versionsResult.reason);
  await loadCatalog();
  if (current(ctx) && pending.value) await recover();
  if (current(ctx)) loading.value = false;
}
watch(() => [props.document.id, props.document.project_id, props.document.current_version_id], initialize, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; ++catalogRevision; });
</script>

<template>
  <section class="document-differences" aria-label="文档版本比较"><h4>文档版本比较</h4>
    <p v-if="!document.project_id">请先将文档绑定到正式项目。</p>
    <template v-else>
      <p>默认推荐当前原件与紧邻前一版本。可选择其他历史，或同一原件的不同解析快照；上传和解析不会自动提交比较。</p>
      <div class="comparison-inputs">
        <DocumentSnapshotSelector side="old" :project-id="document.project_id" :document-id="document.id" :versions="versions" v-model:version-id="oldVersionId" v-model="oldSnapshot" />
        <DocumentSnapshotSelector side="new" :project-id="document.project_id" :document-id="document.id" :versions="versions" v-model:version-id="newVersionId" v-model="newSnapshot" />
      </div>
      <button v-if="moreVersions" class="secondary-btn" :disabled="loading" @click="loadMoreVersions">更多比较原件版本</button>
      <p v-if="oldSnapshot && newSnapshot && oldSnapshot.id === newSnapshot.id">双方选中同一解析快照，比较将核对相同输入。</p>
      <button v-if="canWrite" class="primary-btn" :disabled="busy || !ready" @click="submit()">提交文档比较</button>
      <button class="secondary-btn" :disabled="busy || loading" @click="initialize">刷新比较记录</button>
      <DocumentDifferenceSettings v-if="settings" :project-id="document.project_id" :record="settings" :can-write="canWrite" @saved="settings = $event" />
      <p v-if="configError" role="alert">{{ configError }}；历史记录可继续查看，提交时由服务端保存实际策略。</p>
      <div v-if="pending" class="comparison-submission">
        <p>比较请求编号：{{ pending.payload.creation_key }}</p>
        <p>固定旧侧快照 {{ pending.payload.old_snapshot_id }} → 固定新侧快照 {{ pending.payload.new_snapshot_id }}</p>
        <button class="secondary-btn" :disabled="busy" @click="recover">查询上次比较提交</button>
        <button v-if="canWrite && pendingUnfinished && !pending.task_id" class="secondary-btn" :disabled="busy" @click="submit(true)">重试原比较提交</button>
        <p v-if="!pending.status">提交结果尚未确认；查询与重试保留原编号和双方快照，不受上方选择或当前策略变化影响。</p>
      </div>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
      <details><summary>比较任务历史（{{ tasks.length }}{{ moreTasks ? '+' : '' }}）</summary>
        <div v-for="task in tasks" :key="task.id" class="comparison-row"><span>{{ task.status }} · {{ task.created_at }} · {{ task.creation_key }}</span>
          <button class="secondary-btn" @click="openHistory(task.id)">查看历史比较任务 {{ task.id }}</button></div>
        <button v-if="moreTasks" class="secondary-btn" :disabled="loading" @click="loadCatalog('tasks')">更多比较任务</button>
      </details>
      <details><summary>已保存比较结果（{{ snapshots.length }}{{ moreSnapshots ? '+' : '' }}）</summary>
        <div v-for="snapshot in snapshots" :key="snapshot.id" class="comparison-row"><span>{{ snapshot.status === 'partial' ? '部分提取输入' : '已保存' }} · {{ snapshot.created_at }} · {{ snapshot.id }}</span>
          <button class="secondary-btn" @click="openHistory(snapshot.task_id)">查看历史比较结果 {{ snapshot.id }}</button></div>
        <button v-if="moreSnapshots" class="secondary-btn" :disabled="loading" @click="loadCatalog('snapshots')">更多比较结果</button>
      </details>
      <DocumentDifferenceTask v-if="activeTaskId" :key="`${document.project_id}:${document.id}:${activeTaskId}`"
        :project-id="document.project_id" :document-id="document.id" :task-id="activeTaskId"
        :request="followingPending ? pending?.payload : undefined" @updated="updateTask" />
    </template>
  </section>
</template>

<style scoped>
.document-differences { margin: 20px 0; padding: 16px; border: 1px solid var(--border); border-radius: 12px; overflow-wrap: anywhere; }
.comparison-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.comparison-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 10px 0; }
[role="alert"] { color: var(--red); }
@media (max-width: 900px) { .comparison-inputs { grid-template-columns: 1fr; } }
</style>
