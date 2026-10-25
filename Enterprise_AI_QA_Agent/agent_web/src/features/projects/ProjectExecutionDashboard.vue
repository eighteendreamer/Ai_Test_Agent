<script setup lang="ts">
import { computed, h, onBeforeUnmount, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NGi,
  NGrid,
  NSpin,
  NStatistic,
  NTag,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
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

interface ResourceRow {
  type: string;
  label: string;
  active: number;
  cleanup: number;
  occupied: number;
  limit?: number;
}

const cards = computed(() => {
  const s = summary.value?.stats;
  if (!s) return [];
  return [
    ["执行条目", s.total], ["排队中", s.queued], ["已领取", s.claimed], ["执行中", s.running],
    ["等待资源", s.waiting_resource], ["等待审批", s.waiting_approval], ["通过", s.passed],
    ["失败 / 错误", s.failed + s.error], ["阻塞", s.blocked], ["跳过 / 取消", s.skipped + s.cancelled],
  ] as Array<[string, number]>;
});
const waits = computed(() => {
  const grouped = new Map<string, number>();
  for (const [reason, count] of Object.entries(summary.value?.waiting_reasons ?? {})) {
    const label = waitingReasonLabel(reason);
    grouped.set(label, (grouped.get(label) ?? 0) + count);
  }
  return [...grouped.entries()];
});
const resourceRows = computed<ResourceRow[]>(() => Object.entries(resourceLabels).map(([type, label]) => ({
  type, label,
  active: occupancy.value?.by_type[type] ?? 0,
  cleanup: occupancy.value?.cleanup_pending.filter(item => item.resource_type === type).length ?? 0,
  occupied: occupancy.value?.quota_usage[`project_resource_type:${props.projectId}:${type}`] ?? 0,
  limit: quotas.value?.find(item => item.resource_type === type)?.limit,
})));

function limitLabel(limit?: number) {
  return limit ? String(limit) : "未单独设限";
}

const resourceColumns = computed<DataTableColumns<ResourceRow>>(() => [
  { title: "资源", key: "label", minWidth: 140 },
  { title: "活动", key: "active", width: 100 },
  { title: "待清理", key: "cleanup", width: 100 },
  { title: "占用槽位", key: "occupied", width: 120 },
  {
    title: "项目类型配额",
    key: "limit",
    width: 150,
    render: (row) => quotas.value === null ? "未知" : limitLabel(row.limit),
  },
]);

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
      <NButton size="small" :loading="loading" @click="refresh">刷新状态</NButton>
    </header>

    <NAlert v-if="errors.length" type="warning" :bordered="false" style="margin-top: 12px">
      {{ errors.join('；') }}。已有数值为上次成功快照，请勿视为实时状态。
    </NAlert>

    <div v-if="summary" class="execution-counts">
      <NGrid :cols="5" :x-gap="12" :y-gap="12">
        <NGi v-for="[label, count] in cards" :key="label">
          <NStatistic :label="label" :value="count" />
        </NGi>
      </NGrid>
    </div>
    <div v-else-if="loading" class="dashboard-loading"><NSpin size="small" /> 正在加载执行统计…</div>
    <NEmpty v-else description="执行统计不可用" size="small" />

    <div v-if="waits.length" class="waiting-reasons" aria-label="资源等待原因">
      <NTag v-for="[label, count] in waits" :key="label" size="small" :bordered="false">
        {{ label }}：{{ count }}
      </NTag>
    </div>
    <small v-if="summary">执行统计更新于 {{ formatServerDateTime(summary.sampled_at) }}</small>

    <template v-if="occupancy">
      <p class="dashboard-copy">
        本项目资源槽位占用 {{ occupancy.quota_usage[`project:${projectId}`] ?? 0 }}
        ，活动资源 {{ occupancy.active_count - (occupancy.by_type.agent ?? 0) }}，待清理 {{ occupancy.cleanup_pending_count }}。
        Agent 任务占用独立计数，见下表。
      </p>
      <NDataTable
        :columns="resourceColumns"
        :data="resourceRows"
        :row-key="(row: ResourceRow) => row.type"
        size="small"
        :bordered="false"
      >
        <template #empty><NEmpty description="暂无资源占用数据" size="small" /></template>
      </NDataTable>
      <small>待清理资源仍占用配额；其他占用可能正在等待回收扫描。实际领取同时受全局、项目、运行和资源类型配额约束。</small>
    </template>
    <div v-else-if="loading" class="dashboard-loading"><NSpin size="small" /> 正在加载资源占用…</div>
    <NEmpty v-else description="资源占用不可用" size="small" />
  </section>
</template>

<style scoped>
.execution-dashboard { padding: 18px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 12px; margin-bottom: 20px; }
header { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
h3 { margin: 0 0 6px; }
small { color: var(--text-secondary, #64748b); }
.execution-counts { margin: 18px 0; }
.waiting-reasons { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.dashboard-loading { display: flex; align-items: center; gap: 8px; padding: 12px 0; color: var(--text-secondary, #64748b); }
.dashboard-copy { margin: 16px 0 8px; }
@media (max-width: 600px) { header { align-items: flex-start; } }
</style>
