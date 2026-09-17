<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../services/api";
import type { ProjectExecutionSummary, ResourceOccupancyResponse, ResourceQuotaRecord } from "../../types";
import { formatServerDateTime } from "../../utils/datetime";
import { resourceLabels, waitingReasonLabel } from "./executionLabels";

const props = defineProps<{ projectId: string }>();
const summary = ref<ProjectExecutionSummary | null>(null);
const occupancy = ref<ResourceOccupancyResponse | null>(null);
const quotas = ref<ResourceQuotaRecord[] | null>(null);
const errors = ref<string[]>([]);
const loading = ref(false);
let revision = 0;
let controller: AbortController | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
const configuredPoll = Number(import.meta.env.VITE_PROJECT_MONITOR_POLL_MS ?? 5000);
const pollMs = configuredPoll === 0 ? 0 : Math.max(1000, Number.isFinite(configuredPoll) ? configuredPoll : 5000);
const configuredTimeout = Number(import.meta.env.VITE_PROJECT_MONITOR_TIMEOUT_MS ?? 10000);
const timeoutMs = Math.max(1000, Number.isFinite(configuredTimeout) ? configuredTimeout : 10000);

const cards = computed(() => {
  const s = summary.value?.stats;
  if (!s) return [];
  return [
    ["执行条目", s.total], ["排队中", s.queued], ["已领取", s.claimed], ["执行中", s.running],
    ["等待资源", s.waiting_resource], ["等待审批", s.waiting_approval], ["通过", s.passed],
    ["失败 / 错误", s.failed + s.error], ["阻塞", s.blocked], ["跳过 / 取消", s.skipped + s.cancelled],
  ];
});
const waits = computed(() => {
  const grouped = new Map<string, number>();
  for (const [reason, count] of Object.entries(summary.value?.waiting_reasons ?? {})) {
    const label = waitingReasonLabel(reason);
    grouped.set(label, (grouped.get(label) ?? 0) + count);
  }
  return [...grouped.entries()];
});
const resourceRows = computed(() => Object.entries(resourceLabels).map(([type, label]) => ({
  type, label,
  active: occupancy.value?.by_type[type] ?? 0,
  cleanup: occupancy.value?.cleanup_pending.filter(item => item.resource_type === type).length ?? 0,
  occupied: occupancy.value?.quota_usage[`project_resource_type:${props.projectId}:${type}`] ?? 0,
  limit: quotas.value?.find(item => item.resource_type === type && type !== "agent")?.limit,
})));
const projectLimit = computed(() => quotas.value?.find(item => item.resource_type === "agent")?.limit);

function limitLabel(limit?: number) {
  return limit ? String(limit) : "未单独设限";
}

function schedule() {
  if (pollMs > 0) timer = setTimeout(() => {
    if (document.hidden) schedule();
    else void refresh();
  }, pollMs);
}

async function refresh() {
  clearTimeout(timer);
  controller?.abort();
  const current = ++revision;
  const projectId = props.projectId;
  if (!projectId) return;
  const activeController = new AbortController();
  controller = activeController;
  const timeout = setTimeout(() => activeController.abort(), timeoutMs);
  loading.value = true;
  try {
    const [execution, resources, limits] = await Promise.allSettled([
      api.getProjectExecutionSummary(projectId, activeController.signal),
      api.getResourceOccupancy(projectId, activeController.signal),
      api.listResourceQuotas(projectId, activeController.signal),
    ]);
    if (current !== revision) return;
    errors.value = [];
    if (execution.status === "fulfilled") summary.value = execution.value;
    else errors.value.push("执行统计刷新失败");
    if (resources.status === "fulfilled") occupancy.value = resources.value;
    else errors.value.push("资源占用暂不可用");
    if (limits.status === "fulfilled") quotas.value = limits.value;
    else errors.value.push("项目配额刷新失败");
  } finally {
    clearTimeout(timeout);
    if (current === revision) {
      loading.value = false;
      schedule();
    }
  }
}

function stop() {
  ++revision;
  clearTimeout(timer);
  controller?.abort();
}

watch(() => props.projectId, () => {
  stop();
  summary.value = null;
  occupancy.value = null;
  quotas.value = null;
  errors.value = [];
  void refresh();
}, { immediate: true });
onBeforeUnmount(stop);
</script>

<template>
  <section class="execution-dashboard" aria-label="项目执行与资源">
    <header>
      <div><h3>执行与资源</h3><small>统计本项目全部历史执行条目，不受列表分页和筛选影响。</small></div>
      <button type="button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新状态' }}</button>
    </header>
    <p v-if="errors.length" role="alert">{{ errors.join('；') }}。已有数值为上次成功快照，请勿视为实时状态。</p>
    <div v-if="summary" class="execution-counts">
      <article v-for="[label, count] in cards" :key="label"><span>{{ label }}</span><strong>{{ count }}</strong></article>
    </div>
    <p v-else>{{ loading ? '正在加载执行统计…' : '执行统计不可用' }}</p>
    <div v-if="waits.length" class="waiting-reasons" aria-label="资源等待原因">
      <span v-for="[label, count] in waits" :key="label">{{ label }}：{{ count }}</span>
    </div>
    <small v-if="summary">执行统计更新于 {{ formatServerDateTime(summary.sampled_at) }}</small>
    <template v-if="occupancy">
      <p>本项目资源槽位占用 {{ occupancy.quota_usage[`project:${projectId}`] ?? 0 }}
        / {{ quotas === null ? '配额未知' : limitLabel(projectLimit) }}，
        其中活动资源 {{ occupancy.active_count }}，待清理 {{ occupancy.cleanup_pending_count }}。</p>
      <div class="table-scroll">
        <table aria-label="项目资源占用">
          <thead><tr><th>资源</th><th>活动</th><th>待清理</th><th>占用槽位</th><th>项目类型配额</th></tr></thead>
          <tbody><tr v-for="row in resourceRows" :key="row.type">
            <td>{{ row.label }}</td><td>{{ row.active }}</td><td>{{ row.cleanup }}</td><td>{{ row.occupied }}</td>
            <td>{{ quotas === null ? '未知' : limitLabel(row.limit) }}</td>
          </tr></tbody>
        </table>
      </div>
      <small>待清理资源仍占用配额；其他占用可能正在等待回收扫描。实际领取同时受全局、项目、运行和资源类型配额约束。</small>
    </template>
    <p v-else>{{ loading ? '正在加载资源占用…' : '资源占用不可用' }}</p>
  </section>
</template>

<style scoped>
.execution-dashboard { padding: 18px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 12px; margin-bottom: 20px; }
header { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
h3 { margin: 0 0 6px; }
small, .execution-counts span { color: var(--text-secondary, #64748b); }
button { padding: 7px 12px; cursor: pointer; border: 1px solid var(--border-color, #e2e8f0); border-radius: 6px; background: transparent; color: inherit; white-space: nowrap; }
button:disabled { cursor: wait; opacity: .6; }
.execution-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 12px; margin: 18px 0; }
article { display: flex; flex-direction: column; gap: 6px; }
article strong { font-size: 22px; }
.waiting-reasons { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
[role="alert"] { color: var(--warning-color, #b45309); }
.table-scroll { overflow-x: auto; margin-bottom: 10px; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 9px 12px; border-bottom: 1px solid var(--border-color, #e2e8f0); white-space: nowrap; }
@media (max-width: 600px) { header { align-items: flex-start; } }
</style>
