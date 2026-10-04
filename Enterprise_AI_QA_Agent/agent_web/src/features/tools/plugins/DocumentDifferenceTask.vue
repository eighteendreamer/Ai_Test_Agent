<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentDifferenceTaskRecord, DocumentDifferenceSnapshotView, DocumentDifferenceFailureView, DocumentDifferenceTaskRequest } from "../../../types";
import DocumentDifferenceSnapshot from "./DocumentDifferenceSnapshot.vue";
import DocumentImpactPanel from "./DocumentImpactPanel.vue";
import { assertDifferenceTask } from "./documentDifferenceIdentity";

const props = defineProps<{ projectId: string; documentId: string; taskId: string; request?: DocumentDifferenceTaskRequest }>();
const emit = defineEmits<{ updated: [task: DocumentDifferenceTaskRecord] }>();
const task = ref<DocumentDifferenceTaskRecord | null>(null);
const view = ref<DocumentDifferenceSnapshotView | null>(null);
const failure = ref<DocumentDifferenceFailureView | null>(null);
const error = ref("");
const busy = ref(false);
let epoch = 0;
let disposed = false;
let timer: ReturnType<typeof setTimeout> | undefined;
const labels = { queued: "等待比较", running: "正在比较", completed: "比较结果已保存", failed: "比较任务失败" };
function stopPolling() { if (timer) clearTimeout(timer); timer = undefined; }
async function load() {
  if (busy.value) return;
  stopPolling(); const generation = epoch;
  const { projectId, documentId, taskId } = props;
  const expectedRequest = props.request;
  const current = () => !disposed && generation === epoch && projectId === props.projectId
    && documentId === props.documentId && taskId === props.taskId;
  busy.value = true; error.value = "";
  try {
    if (!projectId || !documentId || !taskId) throw new Error("缺少比较任务的项目、文档或任务身份");
    const value = await api.getDocumentDifferenceTask(projectId, documentId, taskId);
    if (!current()) return;
    assertDifferenceTask(value, projectId, documentId, { taskId, request: expectedRequest });
    task.value = value;
    emit("updated", value);
    if (value.status === "completed") {
      if (!value.snapshot_id) throw new Error("已完成比较任务缺少保存的结果身份");
      const result = await api.readDocumentDifferenceSnapshot(projectId, documentId, value.snapshot_id);
      if (!current()) return;
      const { snapshot, result: body } = result;
      const seed = snapshot.seed;
      const validSide = (side: "old" | "new") => {
        const ref = body[side]; const input = seed[`${side}_snapshot`];
        return ref.project_id === projectId && ref.document_id === documentId
          && input.seed.project_id === projectId && input.seed.document_id === documentId
          && ref.snapshot_id === value[`${side}_snapshot_id`] && ref.snapshot_id === input.id
          && ref.version_id === input.seed.version_id && ref.result_sha256 === input.sha256
          && ref.original_sha256 === input.seed.original_asset.sha256;
      };
      if (snapshot.id !== value.snapshot_id || snapshot.task_id !== taskId
        || seed.project_id !== projectId || seed.document_id !== documentId || seed.version_id !== value.version_id
        || !validSide("old") || !validSide("new")) throw new Error("比较结果与任务固定的双方快照不一致");
      view.value = result; failure.value = null;
    } else if (value.status === "failed") {
      view.value = null; failure.value = null;
      if (value.failure_artifact) {
        const diagnostic = await api.readDocumentDifferenceFailure(projectId, documentId, taskId);
        if (!current()) return;
        if (diagnostic.task_id !== taskId || diagnostic.artifact.sha256 !== value.failure_artifact.sha256) {
          throw new Error("比较失败诊断与指定任务不一致");
        }
        failure.value = diagnostic;
      }
    } else {
      view.value = null; failure.value = null;
      timer = setTimeout(() => { void load(); }, 1000);
    }
  } catch (err) {
    if (current()) { view.value = null; failure.value = null; error.value = err instanceof Error ? err.message : "比较任务读取失败"; }
  } finally { if (current()) busy.value = false; }
}
watch(() => [props.projectId, props.documentId, props.taskId,
  props.request?.creation_key, props.request?.old_snapshot_id, props.request?.new_snapshot_id], () => {
  stopPolling(); ++epoch; task.value = null; view.value = null; failure.value = null; busy.value = false; void load();
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; stopPolling(); });
</script>

<template>
  <article aria-label="比较任务详情" class="document-difference-task">
    <p>项目：{{ projectId }} · 文档：{{ documentId }}</p><p>比较任务：{{ taskId }}</p>
    <button class="secondary-btn" :disabled="busy" @click="load">刷新比较任务</button>
    <p v-if="busy" role="status">正在读取固定比较记录…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="task">
      <p class="difference-task-status" role="status">{{ labels[task.status] }} · {{ task.message }} · 第 {{ task.attempt_no }} 次领取</p>
      <p>旧侧解析快照：{{ task.old_snapshot_id }} → 新侧解析快照：{{ task.new_snapshot_id }}</p>
      <p v-if="task.error_code">失败代码：{{ task.error_code }}</p>
    </template>
    <DocumentDifferenceSnapshot v-if="view" :view="view" />
    <DocumentImpactPanel v-if="view" :project-id="projectId" :document-id="documentId" :difference-snapshot-id="view.snapshot.id" />
    <article v-if="failure" aria-label="比较失败诊断">
      <h4>比较失败诊断 · 退出码 {{ failure.exit_code ?? '未取得' }}</h4>
      <p>{{ failure.error }}</p><h5>stdout</h5><pre>{{ failure.stdout || '空输出' }}</pre>
      <h5>stderr</h5><pre>{{ failure.stderr || '空输出' }}</pre>
    </article>
  </article>
</template>

<style scoped>
.document-difference-task { overflow-wrap: anywhere; margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--text); }
pre { white-space: pre-wrap; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
