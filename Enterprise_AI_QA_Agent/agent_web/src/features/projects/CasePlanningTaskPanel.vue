<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import type { CasePlanRecord, CasePlanningTaskRecord, CasePlanningTaskRequest } from "../../types";

const props = defineProps<{ projectId: string; objective: string; runtimeJson: string; candidateCaseIds: string[]; disabled: boolean }>();
const emit = defineEmits<{ completed: [plan: CasePlanRecord] }>();
const task = ref<CasePlanningTaskRecord | null>(null);
const recent = ref<CasePlanningTaskRecord[]>([]);
const creationKey = ref("");
const busy = ref(false);
const error = ref("");
const storageWarning = ref("");
let pending: CasePlanningTaskRequest | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
const labels = { queued: "规划已排队", running: "正在规划", completed: "规划任务已完成", failed: "规划任务失败" };

function storageKey() { return `case-planning-task:${props.projectId}`; }
function persist() {
  try {
    window.sessionStorage.setItem(storageKey(), JSON.stringify({ creationKey: creationKey.value, taskId: task.value?.id || "" }));
    storageWarning.value = "";
  } catch {
    storageWarning.value = "浏览器无法保存任务编号。请保留下方请求编号，或从项目规划任务列表找回。";
  }
}
function showError(err: unknown) { error.value = err instanceof Error ? err.message : "规划任务查询失败"; }
async function accept(value: CasePlanningTaskRecord) {
  if (value.project_id !== props.projectId) throw new Error("规划任务不属于当前项目");
  task.value = value; creationKey.value = value.creation_key; persist();
  if (value.status === "completed" || value.status === "failed") {
    if (value.plan_id) {
      const plan = await api.getCasePlan(props.projectId, value.plan_id);
      if (plan.project_id !== props.projectId || plan.id !== value.plan_id) throw new Error("任务结果计划身份不一致");
      if (!disposed) emit("completed", plan);
    }
    return;
  }
  if (!disposed) timer = setTimeout(() => { void refresh(); }, 1000);
}
async function refresh() {
  if (!creationKey.value || busy.value || disposed) return;
  if (timer) clearTimeout(timer);
  busy.value = true; error.value = "";
  try {
    const value = task.value ? await api.getCasePlanningTask(props.projectId, task.value.id)
      : await api.getCasePlanningTaskByRequest(props.projectId, creationKey.value);
    if (!disposed) await accept(value);
  } catch (err) { if (!disposed) showError(err); }
  finally { busy.value = false; }
}
async function submit() {
  if (busy.value || props.disabled || (task.value && ["queued", "running"].includes(task.value.status))) return;
  error.value = "";
  if (!pending) {
    try {
      const context: unknown = JSON.parse(props.runtimeJson);
      if (!context || typeof context !== "object" || Array.isArray(context)) throw new Error("已有运行信息需要是 JSON 对象");
      if (!props.objective.trim()) throw new Error("测试目标不能为空");
      if (!creationKey.value || task.value) {
        creationKey.value = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `planning-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      }
      pending = { creation_key: creationKey.value, objective: props.objective.trim(),
        candidate_case_ids: [...props.candidateCaseIds], runtime_context: context as Record<string, unknown> };
      task.value = null; persist();
    } catch (err) { showError(err); return; }
  }
  busy.value = true;
  try {
    const value = await api.submitCasePlanningTask(props.projectId, { ...pending });
    if (!disposed) { pending = null; await accept(value); }
  } catch (err) {
    if (!disposed) {
      showError(err);
      if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) {
        pending = null; creationKey.value = "";
        try { window.sessionStorage.removeItem(storageKey()); } catch { /* Existing visible warning remains. */ }
      }
    }
  } finally { busy.value = false; }
}
async function loadRecent() {
  busy.value = true; error.value = "";
  try { recent.value = (await api.listCasePlanningTasks(props.projectId, 10, 0)).items; }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function selectRecent(value: CasePlanningTaskRecord) {
  if (timer) clearTimeout(timer);
  busy.value = true; error.value = ""; pending = null;
  try { await accept(await api.getCasePlanningTask(props.projectId, value.id)); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
onMounted(async () => {
  try {
    const raw = window.sessionStorage.getItem(storageKey());
    if (raw) {
      const stored = JSON.parse(raw);
      if (typeof stored.creationKey !== "string" || !stored.creationKey || stored.creationKey.length > 160) throw new Error("无效任务身份");
      creationKey.value = stored.creationKey;
    }
  } catch { storageWarning.value = "无法读取保存的任务身份，请从项目规划任务列表找回。"; }
  if (creationKey.value) await refresh();
});
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); });
</script>

<template>
  <section aria-label="持久规划任务">
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <p v-if="creationKey">规划请求编号：{{ creationKey }}</p>
    <p v-if="task" role="status">{{ labels[task.status] }} · {{ task.message }} · 已调用 {{ task.round_count }} 回合</p>
    <p v-if="creationKey && !task">提交结果尚未确认。先按原请求编号查询；当前页面重试保留原请求内容。刷新后不保存敏感输入，需要重新填写相同内容才能重试。</p>
    <button :disabled="busy || disabled || !objective.trim() || (!!task && ['queued', 'running'].includes(task.status))" @click="submit">
      {{ busy ? '处理中…' : creationKey && !task ? '重试规划提交' : task ? '另建规划任务' : '生成执行计划' }}
    </button>
    <button v-if="creationKey" :disabled="busy" @click="refresh">查询规划任务</button>
    <button :disabled="busy" @click="loadRecent">项目规划任务</button>
    <ul v-if="recent.length"><li v-for="entry in recent" :key="entry.id"><button :disabled="busy" @click="selectRecent(entry)">{{ labels[entry.status] }} · {{ entry.id }}</button></li></ul>
  </section>
</template>
