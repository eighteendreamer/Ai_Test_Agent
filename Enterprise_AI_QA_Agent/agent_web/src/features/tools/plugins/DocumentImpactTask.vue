<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentImpactTaskRecord, DocumentImpactTaskRequest, DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import { assertImpactTask, assertImpactSnapshot } from "./documentImpactIdentity";
import DocumentImpactSnapshot from "./DocumentImpactSnapshot.vue";

const props = defineProps<{ projectId: string; documentId: string; taskId: string; request?: DocumentImpactTaskRequest }>();
const emit = defineEmits<{ updated: [task: DocumentImpactTaskRecord] }>();
const task = ref<DocumentImpactTaskRecord | null>(null);
const view = ref<DocumentImpactSnapshotView | null>(null);
const error = ref("");
const busy = ref(false);
let epoch = 0;
let disposed = false;
let timer: ReturnType<typeof setTimeout> | undefined;
const labels = { queued: "等待影响分析", running: "正在分析影响", completed: "影响结果已保存", failed: "影响任务失败" };
function stop() { if (timer) clearTimeout(timer); timer = undefined; }
async function load() {
  if (busy.value) return;
  stop(); const generation = epoch;
  const { projectId, documentId, taskId } = props;
  const expected = props.request;
  const current = () => !disposed && generation === epoch && projectId === props.projectId && documentId === props.documentId && taskId === props.taskId;
  busy.value = true; error.value = "";
  try {
    if (!projectId || !documentId || !taskId) throw new Error("缺少影响任务的项目、文档或任务身份");
    const value = await api.getDocumentImpactTask(projectId, documentId, taskId);
    if (!current()) return;
    assertImpactTask(value, projectId, documentId, { taskId, request: expected });
    task.value = value; emit("updated", value);
    if (value.status === "completed") {
      if (!value.snapshot_id) throw new Error("已完成影响任务缺少结果身份");
      const result = await api.readDocumentImpactSnapshot(projectId, documentId, value.snapshot_id);
      if (!current()) return;
      assertImpactSnapshot(result, projectId, documentId, value); view.value = result;
    } else {
      view.value = null;
      if (value.status !== "failed") timer = setTimeout(() => { void load(); }, 1000);
    }
  } catch (err) {
    if (current()) { view.value = null; error.value = err instanceof Error ? err.message : "影响任务读取失败"; }
  } finally { if (current()) busy.value = false; }
}
watch(() => [props.projectId, props.documentId, props.taskId, props.request?.creation_key, props.request?.difference_snapshot_id], () => {
  ++epoch; stop(); busy.value = false; task.value = null; view.value = null; void load();
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; stop(); });
</script>

<template>
  <article aria-label="影响任务详情" class="document-impact-task">
    <p>项目：{{ projectId }} · 文档：{{ documentId }}</p><p>影响任务：{{ taskId }}</p>
    <button class="secondary-btn" :disabled="busy" @click="load">刷新影响任务</button>
    <p v-if="busy" role="status">正在读取固定影响记录…</p><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="task">
      <p class="impact-task-status" role="status">{{ labels[task.status] }} · {{ task.message }} · 第 {{ task.attempt_no }} 次领取</p>
      <p>固定比较结果：{{ task.difference_snapshot_id }} · 原件版本：{{ task.version_id }}</p>
      <p v-if="task.error_code">失败代码：{{ task.error_code }}</p>
      <p v-if="task.status === 'failed'">原任务和已保存的证据保留。新分析需另建请求，不能重置本任务。</p>
    </template>
    <DocumentImpactSnapshot v-if="view" :view="view" :project-id="projectId" :document-id="documentId" />
  </article>
</template>

<style scoped>
.document-impact-task { overflow-wrap: anywhere; margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--text); }
[role="alert"] { color: var(--red); }
</style>
