<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../services/api";
import { t } from "../services/i18n";
import type { TaskPoolEntry, TaskPoolKind, TaskPoolState } from "../types";
import { formatServerDateTime } from "../utils/datetime";

const router = useRouter();
const loading = ref(false);
const error = ref("");
const activeTab = ref<TaskPoolState>("all");
const kind = ref<TaskPoolKind>("all");
const search = ref("");
const query = ref("");
const rows = ref<TaskPoolEntry[]>([]);
const offset = ref(0);
const hasMore = ref(false);
const pageSize = 24;
let generation = 0;
let disposed = false;
let searchTimer: ReturnType<typeof setTimeout> | undefined;
let polling: ReturnType<typeof setInterval> | undefined;
const tabs = computed(() => [
  { id: "all" as TaskPoolState, label: t("taskpool.tab_all") },
  { id: "active" as TaskPoolState, label: t("taskpool.tab_running") },
  { id: "completed" as TaskPoolState, label: t("taskpool.tab_completed") },
  { id: "failed" as TaskPoolState, label: t("taskpool.tab_failed") },
]);
function data(row: TaskPoolEntry) { return row.kind === "session" ? row.session : row.task; }
function title(row: TaskPoolEntry) {
  return row.kind === "session" ? row.session.title : row.kind === "document_difference"
    ? row.document_title || t("taskpool.kind_difference") : t("taskpool.kind_planning");
}
function parent(row: TaskPoolEntry) { return row.kind === "session" ? row.session.parent_session_id : ""; }
function active(row: TaskPoolEntry) { return ["queued", "running", "waiting_approval"].includes(data(row).status); }
function statusLabel(row: TaskPoolEntry): string {
  const status = data(row).status;
  if (row.kind === "case_planning" && status === "completed") return t("taskpool.status_planned");
  if (row.kind === "document_difference" && status === "completed") return t("taskpool.status_compared");
  const keys: Record<string, string> = { queued: "queued", waiting_approval: "waiting_approval", completed: "completed",
    failed: "failed", running: "running", interrupted: "interrupted", idle: "idle" };
  return t(`taskpool.status_${keys[status] || "idle"}`);
}
function taskKind(row: TaskPoolEntry): string {
  if (row.kind === "case_planning") return t("taskpool.kind_planning");
  if (row.kind === "document_difference") return t("taskpool.kind_difference");
  return t(row.session.session_mode === "background_task" ? "taskpool.kind_child" : "taskpool.kind_parent");
}
function modeLabel(row: TaskPoolEntry): string {
  if (row.kind === "document_difference") return t(`taskpool.difference_phase_${row.task.phase}`);
  if (row.kind === "case_planning") {
    const phases = ["retrieval", "ready", "invoking", "response", "query", "finished"];
    return phases.includes(row.task.phase) ? t(`taskpool.phase_${row.task.phase}`) : row.task.phase;
  }
  if (row.session.mode_key === "code_review") return t("taskpool.mode_code_review");
  if (row.session.session_mode === "background_task") return t("taskpool.mode_background_child");
  return row.session.mode_key;
}
function workerStats(row: TaskPoolEntry): string {
  if (row.kind === "document_difference") return `${t("taskpool.attempt_count")} ${row.task.attempt_no}`;
  if (row.kind === "case_planning") return `${t("taskpool.round_count")} ${row.task.round_count}`;
  const workers = row.session.worker_dispatches;
  if (!workers.length) return t("taskpool.no_dispatch_records");
  const running = workers.filter(item => ["running", "waiting_approval"].includes(String(item.status))).length;
  const failed = workers.filter(item => item.status === "failed").length;
  const completed = workers.filter(item => item.status === "completed").length;
  return `${t("taskpool.status_completed")} ${completed} / ${t("taskpool.status_running")} ${running} / ${t("taskpool.status_failed")} ${failed}`;
}
function openTask(row: TaskPoolEntry) {
  if (row.kind === "case_planning") {
    void router.push({ name: "project-detail", params: { projectId: row.task.project_id }, query: { planning_task_id: row.task.id } });
  } else if (row.kind === "document_difference") {
    void router.push({ name: "document-difference", params: {
      projectId: row.task.project_id, documentId: row.task.document_id, taskId: row.task.id,
    } });
  } else {
    void router.push("/reports");
  }
}
function actionLabel(row: TaskPoolEntry) {
  return t(row.kind === "session" ? "taskpool.open_report"
    : row.kind === "document_difference" ? "taskpool.open_difference" : "taskpool.open_planning");
}
async function loadTasks() {
  const request = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const page = await api.listTaskPoolEntries(pageSize, offset.value, { kind: kind.value, state: activeTab.value, query: query.value });
    if (disposed || request !== generation) return;
    rows.value = page.items;
    hasMore.value = page.has_more;
  } catch (err) {
    if (!disposed && request === generation) error.value = err instanceof Error ? err.message : t("taskpool.load_failed");
  } finally {
    if (!disposed && request === generation) loading.value = false;
  }
}
watch(search, value => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { query.value = value.trim(); }, 250);
});
watch([kind, activeTab, query], () => { offset.value = 0; void loadTasks(); });
function changePage(direction: number) { offset.value = Math.max(0, offset.value + direction * pageSize); void loadTasks(); }
onMounted(() => {
  void loadTasks();
  polling = setInterval(() => { if (!loading.value && document.visibilityState !== "hidden") void loadTasks(); }, 5000);
});
onUnmounted(() => { disposed = true; generation++; if (searchTimer) clearTimeout(searchTimer); if (polling) clearInterval(polling); });
</script>

<template>
  <section class="view-page task-page">
    <header class="page-head">
      <div class="head-content"><h2>{{ t("taskpool.title") }}</h2><p class="head-desc">{{ t("taskpool.page_desc") }}</p></div>
      <div class="head-actions">
        <div class="search-box"><i class="fa-solid fa-search"></i><input v-model="search" type="search" maxlength="500" :aria-label="t('taskpool.search_placeholder')" :placeholder="t('taskpool.search_placeholder')" /></div>
        <button class="primary-btn" :disabled="loading" @click="loadTasks"><i class="fa-solid fa-rotate-right"></i>{{ t("taskpool.refresh") }}</button>
      </div>
    </header>
    <div v-if="error" class="empty-state error-state" role="alert"><strong>{{ t("taskpool.load_error_title") }}</strong><p>{{ error }}</p></div>
    <div class="task-layout" :aria-busy="loading">
      <div class="task-toolbar">
        <div class="task-tabs"><button v-for="tab in tabs" :key="tab.id" class="tab-btn" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">{{ tab.label }}</button></div>
        <div class="task-filters">
          <select v-model="kind" :aria-label="t('taskpool.col_type')"><option value="all">{{ t("taskpool.kind_all") }}</option><option value="session">{{ t("taskpool.kind_session") }}</option><option value="case_planning">{{ t("taskpool.kind_planning") }}</option><option value="document_difference">{{ t("taskpool.kind_difference") }}</option></select>
          <span class="filter-pill">{{ t("taskpool.page_number", { number: String(offset / pageSize + 1) }) }}</span>
        </div>
      </div>
      <div v-if="loading && !rows.length" class="empty-state table-empty"><strong>{{ t("taskpool.loading_sessions") }}</strong><p>{{ t("taskpool.loading_sessions_desc") }}</p></div>
      <div v-else-if="!rows.length && !error" class="empty-state table-empty"><strong>{{ t("taskpool.no_match") }}</strong><p>{{ t("taskpool.no_match_desc") }}</p></div>
      <div v-else-if="!error" class="table-container">
        <table class="data-table">
          <thead><tr><th class="col-id">{{ t("taskpool.col_session") }}</th><th class="col-name">{{ t("taskpool.col_title") }}</th><th class="col-type">{{ t("taskpool.col_type") }}</th><th class="col-model">{{ t("taskpool.col_agent_mode") }}</th><th class="col-status">{{ t("taskpool.col_status") }}</th><th class="col-stats">{{ t("taskpool.col_stats") }}</th><th class="col-actions align-right">{{ t("taskpool.col_actions") }}</th></tr></thead>
          <tbody><tr v-for="row in rows" :key="`${row.kind}:${data(row).id}`" :data-task-id="data(row).id" :class="{ 'row-active': active(row), 'row-error': data(row).status === 'failed' }">
            <td class="col-id mono strong" :title="data(row).id">{{ data(row).id.slice(0, 8) }}<div v-if="parent(row)" class="sub-meta">{{ t("taskpool.parent_session") }}={{ parent(row).slice(0, 8) }}</div></td>
            <td class="col-name"><div class="strong task-title" :title="title(row)">{{ title(row) }}</div><div class="sub-meta">{{ row.project_name || '-' }} · {{ formatServerDateTime(data(row).updated_at, data(row).updated_at) }}</div></td>
            <td class="col-type"><span class="badge badge-gray">{{ taskKind(row) }}</span></td>
            <td class="col-model"><div v-if="row.kind === 'session'" class="mono">{{ row.session.selected_agent || '-' }}</div><div class="sub-meta">{{ modeLabel(row) }}</div></td>
            <td class="col-status"><span class="status-indicator" :class="`status-${data(row).status}`"><span v-if="active(row)" class="pulse-dot"></span>{{ statusLabel(row) }}</span></td>
            <td class="col-stats"><div>{{ workerStats(row) }}</div><div v-if="row.kind !== 'session'" class="sub-meta">{{ row.task.message }}</div><div v-else class="sub-meta">{{ row.session.worker_dispatches.length }} {{ t("taskpool.dispatch_records") }}</div></td>
            <td class="col-actions align-right"><button class="action-btn" :aria-label="actionLabel(row)" :title="actionLabel(row)" @click="openTask(row)"><i class="fa-solid fa-arrow-up-right-from-square"></i></button></td>
          </tr></tbody>
        </table>
      </div>
      <nav class="task-toolbar" :aria-label="t('taskpool.pagination')"><button :disabled="loading || offset === 0" @click="changePage(-1)">{{ t("taskpool.previous") }}</button><button :disabled="loading || !hasMore" @click="changePage(1)">{{ t("taskpool.next") }}</button></nav>
    </div>
  </section>
</template>

<style scoped>
.task-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
  background: var(--bg);
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

.head-content h2 {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 2px 0;
  letter-spacing: -0.02em;
}

.head-desc {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
}

.search-box i {
  position: absolute;
  left: 12px;
  color: var(--muted);
  font-size: 14px;
}

.search-box input {
  height: 36px;
  padding: 0 16px 0 36px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  width: 280px;
}

.task-layout {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: var(--shadow-soft);
}

.task-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-soft);
}

.task-tabs {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--surface-muted);
  padding: 5px;
  border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
  border-radius: 12px;
  box-sizing: border-box;
}

.tab-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 100px;
  height: 32px;
  box-sizing: border-box;
  border: 1px solid transparent;
  background: transparent;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  border-radius: 8px;
  cursor: pointer;
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease,
    box-shadow 0.16s ease;
}

.tab-btn:hover {
  color: var(--text);
  background: color-mix(in srgb, var(--surface) 55%, transparent);
}

.tab-btn.active {
  background: var(--surface);
  color: var(--text);
  border-color: color-mix(in srgb, var(--border-strong) 45%, transparent);
  box-shadow: inset 0 -1px 0 color-mix(in srgb, var(--border) 72%, transparent);
}

.tab-btn:focus-visible {
  outline: none;
  box-shadow:
    inset 0 -1px 0 color-mix(in srgb, var(--border) 72%, transparent),
    0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}

.task-filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--surface);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
}

.table-container {
  flex: 1;
  overflow: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.data-table th {
  position: sticky;
  top: 0;
  background: var(--surface);
  padding: 14px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
  z-index: 10;
}

.data-table td {
  padding: 16px;
  font-size: 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  vertical-align: middle;
}

.data-table tbody tr:hover {
  background: var(--surface-soft);
}

.data-table tbody tr.row-active {
  background: rgba(59, 130, 246, 0.02);
}

.data-table tbody tr.row-error {
  background: rgba(239, 68, 68, 0.02);
}

.col-id {
  width: 140px;
}

.col-name {
  min-width: 280px;
}

.col-type,
.col-model,
.col-status,
.col-stats {
  width: 180px;
}

.col-actions {
  width: 80px;
}

.align-right {
  text-align: right;
}

.mono {
  font-family: var(--font-mono, monospace);
}

.strong {
  font-weight: 600;
}

.task-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  max-width: 420px;
  line-height: 1.4;
}

.sub-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
}

.badge-gray {
  background: var(--surface-muted);
  color: var(--muted);
  border: 1px solid var(--border);
}

.status-indicator {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
}

.status-running,
.status-waiting_approval {
  color: #2563eb;
}

.status-completed {
  color: #16a34a;
}

.status-failed {
  color: #dc2626;
}

.status-idle,
.status-interrupted {
  color: #6b7280;
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3b82f6;
  position: relative;
}

.pulse-dot::after {
  content: "";
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 2px solid #3b82f6;
  animation: pulse 1.5s infinite cubic-bezier(0.4, 0, 0.2, 1);
  opacity: 0;
}

@keyframes pulse {
  0% {
    transform: scale(0.5);
    opacity: 1;
  }
  100% {
    transform: scale(1.5);
    opacity: 0;
  }
}

.action-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.action-btn:hover {
  background: var(--surface-muted);
  color: var(--text);
}

.empty-state {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 240px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 16px;
  background: var(--surface);
  color: var(--muted);
  margin: 8px;
}

.table-empty {
  margin: 20px;
}

.error-state {
  color: #dc2626;
}

@media (max-width: 960px) {
  .page-head,
  .task-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }

  .search-box input {
    width: 100%;
  }

  .task-tabs {
    overflow-x: auto;
  }

  .tab-btn {
    min-width: 112px;
  }
}
</style>
