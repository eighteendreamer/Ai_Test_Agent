<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentImpactTaskRecord, DocumentImpactTaskRequest, DocumentImpactSnapshot } from "../../../services/documentImpacts.types";
import { assertImpactTask } from "./documentImpactIdentity";
import DocumentImpactTask from "./DocumentImpactTask.vue";
import DocumentImpactSettings from "./DocumentImpactSettings.vue";
import ImpactCaseVersionSelector from "./ImpactCaseVersionSelector.vue";

const props = defineProps<{ projectId: string; documentId: string; differenceSnapshotId: string }>();
const canWrite = ref(false);
const range = ref<"project_current" | "explicit">("project_current");
const selectedVersions = ref<string[]>([]);
const modelKey = ref("");
const environment = ref("");
const release = ref("");
const tasks = ref<DocumentImpactTaskRecord[]>([]);
const snapshots = ref<DocumentImpactSnapshot[]>([]);
const moreTasks = ref(false);
const moreSnapshots = ref(false);
const activeTaskId = ref("");
const following = ref(false);
const error = ref("");
const storageWarning = ref("");
const busy = ref(false);
const loading = ref(false);
type Submission = { payload: DocumentImpactTaskRequest; task_id?: string; status?: DocumentImpactTaskRecord["status"] };
const pending = ref<Submission | null>(null);
const unfinished = computed(() => pending.value && !["completed", "failed"].includes(pending.value.status || ""));
const ready = computed(() => canWrite.value && props.differenceSnapshotId && !unfinished.value
  && (range.value === "project_current" || selectedVersions.value.length > 0));
const key = () => `qa-agent:document-impact:${props.projectId}:${props.documentId}`;
let epoch = 0;
let catalogEpoch = 0;
let disposed = false;
let taskOffset = 0;
let snapshotOffset = 0;
const context = () => ({ epoch, projectId: props.projectId, documentId: props.documentId });
type Context = ReturnType<typeof context>;
const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.projectId && ctx.documentId === props.documentId;
const showError = (err: unknown) => { error.value = err instanceof Error ? err.message : "影响分析操作失败"; };
function persist() {
  try { sessionStorage.setItem(key(), JSON.stringify(pending.value)); storageWarning.value = ""; }
  catch { storageWarning.value = "无法保存影响请求编号，请保留下方编号，或从任务池找回。"; }
}
async function loadCatalog(kind?: "tasks" | "snapshots") {
  const ctx = context(); const generation = ++catalogEpoch; loading.value = true;
  const values = await Promise.allSettled([
    kind === "snapshots" ? Promise.resolve(null) : api.listDocumentImpactTasks(ctx.projectId, ctx.documentId, undefined, kind === "tasks" ? taskOffset : 0),
    kind === "tasks" ? Promise.resolve(null) : api.listDocumentImpactSnapshots(ctx.projectId, ctx.documentId, undefined, kind === "snapshots" ? snapshotOffset : 0),
  ]);
  if (!current(ctx) || generation !== catalogEpoch) return;
  const problems: string[] = [];
  const collect = (err: unknown) => problems.push(err instanceof Error ? err.message : "影响历史读取失败");
  try {
    if (values[0].status === "fulfilled" && values[0].value) {
      const page = values[0].value; page.items.forEach(task => assertImpactTask(task, ctx.projectId, ctx.documentId));
      tasks.value = kind === "tasks" ? [...tasks.value, ...page.items.filter(task => !tasks.value.some(old => old.id === task.id))] : page.items;
      taskOffset = page.offset + page.items.length; moreTasks.value = page.has_more;
    } else if (values[0].status === "rejected") collect(values[0].reason);
  } catch (err) { collect(err); }
  try {
    if (values[1].status === "fulfilled" && values[1].value) {
      const page = values[1].value;
      if (page.items.some(snapshot => snapshot.seed.project_id !== ctx.projectId || snapshot.seed.document_id !== ctx.documentId)) throw new Error("影响结果目录与当前项目或文档不一致");
      snapshots.value = kind === "snapshots" ? [...snapshots.value, ...page.items.filter(snapshot => !snapshots.value.some(old => old.id === snapshot.id))] : page.items;
      snapshotOffset = page.offset + page.items.length; moreSnapshots.value = page.has_more;
    } else if (values[1].status === "rejected") collect(values[1].reason);
  } catch (err) { collect(err); }
  if (problems.length) showError(new Error(problems.join("；")));
  loading.value = false;
}
function accept(value: DocumentImpactTaskRecord, ctx: Context, payload: DocumentImpactTaskRequest) {
  if (!current(ctx)) return;
  assertImpactTask(value, ctx.projectId, ctx.documentId, { taskId: pending.value?.task_id, request: payload });
  pending.value = { payload, task_id: value.id, status: value.status }; persist();
  activeTaskId.value = value.id; following.value = true;
}
async function recover() {
  if (!pending.value || busy.value) return;
  const ctx = context(); const payload = JSON.parse(JSON.stringify(pending.value.payload)) as DocumentImpactTaskRequest;
  busy.value = true; error.value = "";
  try { accept(await api.getDocumentImpactSubmission(ctx.projectId, ctx.documentId, payload.creation_key), ctx, payload); }
  catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}
async function submit(retry = false) {
  if (!canWrite.value || busy.value || (retry ? !unfinished.value || !!pending.value?.task_id : !ready.value)) return;
  const ctx = context(); busy.value = true; error.value = "";
  try {
    if (!retry) {
      const creationKey = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `impact-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pending.value = { payload: { creation_key: creationKey, difference_snapshot_id: props.differenceSnapshotId,
        case_version_ids: range.value === "explicit" ? [...selectedVersions.value] : [], model_key: modelKey.value.trim() || null,
        environment_key: environment.value.trim() || null, target_release: release.value.trim() || null } };
      activeTaskId.value = ""; following.value = false; persist();
    }
    const payload = JSON.parse(JSON.stringify(pending.value!.payload)) as DocumentImpactTaskRequest;
    accept(await api.submitDocumentImpactTask(ctx.projectId, ctx.documentId, payload), ctx, payload);
    if (current(ctx)) await loadCatalog();
  } catch (err) { if (current(ctx)) showError(err); }
  finally { if (current(ctx)) busy.value = false; }
}
function updated(value: DocumentImpactTaskRecord) {
  if (!following.value || !pending.value || value.id !== activeTaskId.value) return;
  try {
    assertImpactTask(value, props.projectId, props.documentId, { taskId: pending.value.task_id, request: pending.value.payload });
    const changed = value.status !== pending.value.status; pending.value.status = value.status; persist();
    if (changed && ["completed", "failed"].includes(value.status)) void loadCatalog();
  } catch (err) { showError(err); }
}
function openHistory(taskId: string) { activeTaskId.value = taskId; following.value = false; }
async function initialize() {
  ++epoch; ++catalogEpoch; busy.value = false; loading.value = false; canWrite.value = false;
  tasks.value = []; snapshots.value = []; moreTasks.value = false; moreSnapshots.value = false;
  pending.value = null; activeTaskId.value = ""; following.value = false; error.value = ""; storageWarning.value = "";
  range.value = "project_current"; selectedVersions.value = []; modelKey.value = ""; environment.value = ""; release.value = "";
  taskOffset = 0; snapshotOffset = 0; const ctx = context();
  try {
    const raw = sessionStorage.getItem(key());
    if (raw && JSON.parse(raw)) {
      const stored = JSON.parse(raw) as Submission; const payload = stored.payload;
      if (!payload || typeof payload.creation_key !== "string" || !payload.creation_key || payload.creation_key.length > 160
        || typeof payload.difference_snapshot_id !== "string" || !payload.difference_snapshot_id
        || (payload.case_version_ids !== undefined && (!Array.isArray(payload.case_version_ids) || payload.case_version_ids.some(id => typeof id !== "string")))
        || [payload.model_key, payload.environment_key, payload.target_release].some(value => value != null && typeof value !== "string")
        || (stored.task_id !== undefined && typeof stored.task_id !== "string")) throw new Error("保存的影响请求无效，请从任务池找回");
      // Cached terminal labels are not server evidence; restore only the original request and identity.
      pending.value = { payload: { creation_key: payload.creation_key, difference_snapshot_id: payload.difference_snapshot_id,
        case_version_ids: payload.case_version_ids, model_key: payload.model_key, environment_key: payload.environment_key,
        target_release: payload.target_release }, task_id: stored.task_id };
    }
  } catch (err) { storageWarning.value = err instanceof Error ? err.message : "影响请求读取失败，请从任务池找回"; }
  const projectRead = api.getProjectOverview(ctx.projectId).then(value => {
    if (!current(ctx)) return;
    if (value.project.id !== ctx.projectId) throw new Error("影响入口与当前项目不一致");
    canWrite.value = value.project.status === "active";
  }).catch(err => { if (current(ctx)) showError(err); });
  await Promise.all([projectRead, loadCatalog()]);
  if (current(ctx) && pending.value) await recover();
}
watch(() => [props.projectId, props.documentId, props.differenceSnapshotId], initialize, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; ++catalogEpoch; });
</script>

<template>
  <section class="document-impact-panel" aria-label="文档影响分析入口">
    <h4>分析文档变化对用例的影响</h4><p>当前固定比较结果：{{ differenceSnapshotId }}</p>
    <p>分析完整用例、当时编写来源与新旧差异；保存候选供评审，不直接改变启用版本或执行测试。</p>
    <p v-if="!canWrite">当前项目不可提交新分析；已保存的任务及结果仍可查看。</p>
    <form @submit.prevent="submit()"><fieldset :disabled="busy || !canWrite || !!unfinished">
      <label>影响分析用例范围<select v-model="range" aria-label="影响分析用例范围"><option value="project_current">项目非归档用例的启用版本与最新草稿</option><option value="explicit">选择具体用例版本</option></select></label>
      <ImpactCaseVersionSelector v-if="range === 'explicit'" v-model="selectedVersions" :project-id="projectId" :disabled="busy || !canWrite || !!unfinished" />
      <div class="impact-fields"><label>本次模型配置编号（留空使用项目设置）<input v-model="modelKey" aria-label="本次影响模型" maxlength="255"></label>
        <label>环境声明<input v-model="environment" aria-label="影响环境声明" maxlength="160"></label>
        <label>目标发布版本声明<input v-model="release" aria-label="影响发布版本声明" maxlength="160"></label>
      </div><p>环境及发布版本仅为声明；当前环境是否生效仍需核实。</p>
      <button v-if="canWrite" class="secondary-btn" type="submit" :disabled="!ready">提交固定影响分析</button>
    </fieldset></form>
    <DocumentImpactSettings :project-id="projectId" :can-write="canWrite" />
    <section v-if="pending" class="impact-submission"><p>影响请求编号：{{ pending.payload.creation_key }}</p>
      <p>本请求固定比较：{{ pending.payload.difference_snapshot_id }}</p>
      <button class="secondary-btn" :disabled="busy" @click="recover">查询上次影响提交</button>
      <button v-if="canWrite && unfinished && !pending.task_id" class="secondary-btn" :disabled="busy" @click="submit(true)">重试原影响提交</button>
      <p v-if="!pending.status">提交结果尚未确认；查询与重试保留原编号、用例范围和声明。</p>
    </section>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <details><summary>影响任务历史（{{ tasks.length }}{{ moreTasks ? '+' : '' }}）</summary>
      <p v-for="task in tasks" :key="task.id">{{ task.status }} · {{ task.created_at }} · {{ task.creation_key }}
        <button class="secondary-btn" @click="openHistory(task.id)">查看历史影响任务 {{ task.id }}</button></p>
      <button v-if="moreTasks" class="secondary-btn" :disabled="loading" @click="loadCatalog('tasks')">更多影响任务</button>
    </details>
    <details><summary>已保存影响结果（{{ snapshots.length }}{{ moreSnapshots ? '+' : '' }}）</summary>
      <p v-for="snapshot in snapshots" :key="snapshot.id">{{ snapshot.status === 'partial' ? '部分结果' : '声明范围已分析' }} · {{ snapshot.created_at }}
        <button class="secondary-btn" @click="openHistory(snapshot.task_id)">查看历史影响结果 {{ snapshot.id }}</button></p>
      <button v-if="moreSnapshots" class="secondary-btn" :disabled="loading" @click="loadCatalog('snapshots')">更多影响结果</button>
    </details>
    <DocumentImpactTask v-if="activeTaskId" :key="`${projectId}:${documentId}:${activeTaskId}`" :project-id="projectId" :document-id="documentId"
      :task-id="activeTaskId" :request="following ? pending?.payload : undefined" @updated="updated" />
  </section>
</template>

<style scoped>
.document-impact-panel { margin-top: 20px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.impact-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
[role="alert"] { color: var(--red); }
</style>
