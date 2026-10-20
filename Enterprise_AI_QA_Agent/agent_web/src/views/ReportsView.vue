<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../services/api";
import { t } from "../services/i18n";
import { formatServerDateTime } from "../utils/datetime";
import { renderMarkdown } from "../utils/reportMarkdown";
import { primaryReportArtifact, reportExecutionStatus, reportGrade } from "../utils/reportPresentation";
import type {
  ReportListPage,
  SessionSummary,
  ToolArtifactRecord,
  VerificationResult,
  WorkerDispatchRecord,
} from "../types";
import { workerDispatchKey } from "../features/flow/workers";

interface ReportEntry {
  session: SessionSummary;
  artifacts: ToolArtifactRecord[];
  verifications: VerificationResult[];
  workerDispatches: WorkerDispatchRecord[];
  reportMeta: Record<string, unknown>;
  reportSessionId: string;
  reportArtifacts: ToolArtifactRecord[];
}

interface ReportsCachePayload {
  cachedAt: string;
  selectedSessionId: string;
  reports: ReportEntry[];
  hasMore: boolean;
  nextOffset: number;
}

const REPORTS_CACHE_KEY = "enterprise-ai-qa:reports-cache-v2";
const REPORTS_CACHE_TTL_MS = 2 * 60 * 1000;
const REPORTS_PAGE_SIZE = 10;

let reportsMemoryCache: ReportsCachePayload | null = null;

const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const selectedSessionId = ref("");
const reports = ref<ReportEntry[]>([]);
const hasMore = ref(false);
const nextOffset = ref(0);

const selectedReport = computed(() => {
  if (!reports.value.length) {
    return null;
  }
  return (
    reports.value.find((item) => item.session.id === selectedSessionId.value) ??
    reports.value[0]
  );
});

const selectedReportBody = computed(() => {
  const report = selectedReport.value;
  if (!report) {
    return "";
  }
  const artifacts = [...report.reportArtifacts, ...report.artifacts];
  const primaryArtifact = primaryReportArtifact(artifacts, report.reportMeta);
  const inlineContent = artifactInlineContent(primaryArtifact);
  if (inlineContent) {
    return inlineContent;
  }
  return String(report.reportMeta.summary || "").trim();
});

const selectedReportBodyHtml = computed(() => {
  const body = selectedReportBody.value.trim();
  if (!body) {
    return "";
  }
  return renderMarkdown(body);
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function apiTasksFor(entry: ReportEntry | null): Record<string, unknown>[] {
  if (!entry || entry.session.mode_key !== "api_testing") return [];
  const tasks = entry.reportMeta.api_tasks;
  return Array.isArray(tasks) ? tasks.filter(isRecord) : [];
}

const selectedApiTasks = computed(() => apiTasksFor(selectedReport.value));
const selectedIsApiReport = computed(() => selectedReport.value?.session.mode_key === "api_testing");

function formatApiPayload(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

const selectedWorkerSummary = computed(() => {
  const report = selectedReport.value;
  if (!report) {
    return { running: 0, completed: 0, failed: 0, total: 0 };
  }
  const summary = {
    running: 0,
    completed: 0,
    failed: 0,
    total: report.workerDispatches.length,
  };
  for (const worker of report.workerDispatches) {
    const status = String(worker.status || "").trim();
    if (status === "failed") {
      summary.failed += 1;
    } else if (status === "running" || status === "waiting_approval") {
      summary.running += 1;
    } else {
      summary.completed += 1;
    }
  }
  return summary;
});

function artifactInlineContent(artifact?: ToolArtifactRecord | null): string {
  if (!artifact) {
    return "";
  }
  const metadata = artifact.metadata || {};
  const inlineText = metadata.__content_text;
  return typeof inlineText === "string" ? inlineText.trim() : "";
}

function gradeForSession(entry: ReportEntry): string {
  return t(`reports.grade_${reportGrade(entry.session, entry.reportMeta, entry.verifications)}`);
}

function gradeTone(grade: string): string {
  if (grade === t("reports.grade_poor")) return "badge-red";
  if (grade === t("reports.grade_attention")) return "badge-yellow";
  if (grade === t("reports.grade_in_progress")) return "badge-blue";
  return "badge-green";
}

function statusLabel(status: string): string {
  if (status === "partial") return t("reports.status_partial");
  if (status === "waiting_approval") return t("taskpool.status_waiting_approval");
  if (status === "completed") return t("taskpool.status_completed");
  if (status === "failed") return t("taskpool.status_failed");
  if (status === "running") return t("taskpool.status_running");
  if (status === "interrupted") return t("taskpool.status_interrupted");
  return t("taskpool.status_idle");
}

function artifactLabel(artifact: ToolArtifactRecord): string {
  return artifact.label || artifact.artifact_type || t("reports.artifact");
}

function formatDateTime(value: string): string {
  return formatServerDateTime(value, value);
}

function normalizeSelectedSessionId(nextReports: ReportEntry[], preferredId = ""): string {
  const preferred = String(preferredId || "").trim();
  if (!nextReports.length) {
    return "";
  }
  if (preferred && nextReports.some((item) => item.session.id === preferred)) {
    return preferred;
  }
  return nextReports[0]?.session.id || "";
}

function isReportsCacheFresh(payload: ReportsCachePayload | null): boolean {
  if (!payload) {
    return false;
  }
  const cachedAt = Date.parse(String(payload.cachedAt || ""));
  if (Number.isNaN(cachedAt)) {
    return false;
  }
  return Date.now() - cachedAt <= REPORTS_CACHE_TTL_MS;
}

function readReportsCache(): ReportsCachePayload | null {
  if (reportsMemoryCache) {
    return reportsMemoryCache;
  }
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(REPORTS_CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as ReportsCachePayload;
    if (!parsed || !Array.isArray(parsed.reports)) {
      return null;
    }
    reportsMemoryCache = parsed;
    return parsed;
  } catch {
    return null;
  }
}

function writeReportsCache(nextReports: ReportEntry[], nextSelectedSessionId: string) {
  const payload: ReportsCachePayload = {
    cachedAt: new Date().toISOString(),
    selectedSessionId: normalizeSelectedSessionId(nextReports, nextSelectedSessionId),
    reports: nextReports,
    hasMore: hasMore.value,
    nextOffset: nextOffset.value,
  };
  reportsMemoryCache = payload;
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(REPORTS_CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Ignore storage quota / availability errors and keep memory cache only.
  }
}

function hydrateReportsFromCache(payload: ReportsCachePayload) {
  reports.value = payload.reports;
  selectedSessionId.value = normalizeSelectedSessionId(payload.reports, payload.selectedSessionId);
  hasMore.value = Boolean(payload.hasMore);
  nextOffset.value = Number(payload.nextOffset || payload.reports.length || 0);
}

async function fetchReportsPage(offset: number, limit = REPORTS_PAGE_SIZE) {
  const page: ReportListPage = await api.listReportsPage(limit, offset);
  const entries: ReportEntry[] = page.items.map((item) => ({
    session: item.session,
    artifacts: item.artifacts,
    verifications: item.verifications,
    workerDispatches: item.worker_dispatches,
    reportMeta: item.report_meta,
    reportSessionId: String(item.report_session_id || ""),
    reportArtifacts: item.report_artifacts,
  }));
  return {
    entries,
    hasMore: page.has_more,
    nextOffset: offset + page.items.length,
  };
}

async function loadReports(forceRefresh = false) {
  const cached = forceRefresh ? null : readReportsCache();
  if (cached) {
    hydrateReportsFromCache(cached);
    if (isReportsCacheFresh(cached)) {
      return;
    }
  }

  loading.value = true;
  error.value = "";
  try {
    const result = await fetchReportsPage(0);
    reports.value = result.entries;
    hasMore.value = result.hasMore;
    nextOffset.value = result.nextOffset;
    selectedSessionId.value = normalizeSelectedSessionId(result.entries, selectedSessionId.value);
    writeReportsCache(result.entries, selectedSessionId.value);
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : t("reports.load_failed");
  } finally {
    loading.value = false;
  }
}

async function loadMoreReports() {
  if (loading.value || loadingMore.value || !hasMore.value) {
    return;
  }
  loadingMore.value = true;
  try {
    const result = await fetchReportsPage(nextOffset.value);
    const merged = [...reports.value];
    const seen = new Set(merged.map((item) => item.session.id));
    for (const entry of result.entries) {
      if (!seen.has(entry.session.id)) {
        merged.push(entry);
        seen.add(entry.session.id);
      }
    }
    reports.value = merged;
    hasMore.value = result.hasMore;
    nextOffset.value = result.nextOffset;
    selectedSessionId.value = normalizeSelectedSessionId(merged, selectedSessionId.value);
    writeReportsCache(merged, selectedSessionId.value);
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : t("reports.load_more_failed");
  } finally {
    loadingMore.value = false;
  }
}

function handleBatchListScroll(event: Event) {
  const container = event.target as HTMLElement | null;
  if (!container) {
    return;
  }
  if (container.scrollTop + container.clientHeight >= container.scrollHeight - 120) {
    void loadMoreReports();
  }
}

onMounted(() => {
  void loadReports();
});
</script>

<template>
  <section class="view-page report-page">
    <header class="page-head">
      <div class="head-content">
        <h2>{{ t("reports.title") }}</h2>
        <p class="head-desc">
          {{ t("reports.page_desc") }}
        </p>
      </div>
      <div class="head-actions">
        <button class="primary-btn" :disabled="loading" @click="loadReports(true)">
          <i class="fa-solid fa-rotate-right"></i>
          {{ t("reports.refresh") }}
        </button>
      </div>
    </header>

    <div v-if="error && !reports.length" class="empty-state error-state">
      <strong>{{ t("reports.load_error_title") }}</strong>
      <p>{{ error }}</p>
    </div>

    <div v-else-if="loading && !reports.length" class="empty-state">
      <strong>{{ t("reports.loading") }}</strong>
      <p>{{ t("reports.loading_desc") }}</p>
    </div>

    <div v-else-if="!reports.length" class="empty-state">
      <strong>{{ t("reports.no_reports") }}</strong>
      <p>{{ t("reports.no_reports_desc") }}</p>
    </div>

    <div v-else class="report-layout">
      <aside class="report-sidebar">
        <div class="sidebar-header">
          <h3>{{ t("reports.review_batches") }}</h3>
        </div>
        <div class="batch-list" @scroll="handleBatchListScroll">
          <article
            v-for="entry in reports"
            :key="entry.session.id"
            class="batch-item"
            :class="{ active: entry.session.id === selectedSessionId }"
            @click="selectedSessionId = entry.session.id"
          >
            <div class="batch-item-header">
              <span class="batch-id">#{{ entry.session.id.slice(0, 8) }}</span>
              <span class="badge" :class="gradeTone(gradeForSession(entry))">
                {{ gradeForSession(entry) }}
              </span>
            </div>
            <p class="batch-title">{{ entry.session.title }}</p>
            <p class="batch-meta mono">
              {{ entry.session.mode_key }} · {{ formatDateTime(entry.session.updated_at) }}
            </p>
            <div class="batch-stats">
              <span class="stat pass">
                <i class="fa-solid fa-layer-group"></i>
                {{ entry.workerDispatches.length }} {{ t("reports.tasks_unit") }}
              </span>
              <span class="stat fail">
                <i class="fa-solid fa-shield-halved"></i>
                {{ entry.verifications.length }} {{ t("reports.verifications_unit") }}
              </span>
            </div>
          </article>

          <div v-if="loadingMore" class="batch-list-footer">
            <i class="fa-solid fa-spinner fa-spin"></i>
            <span>{{ t("reports.loading_more") }}</span>
          </div>
          <div v-else-if="!hasMore" class="batch-list-footer muted">
            <span>{{ t("reports.all_loaded") }}</span>
          </div>
        </div>
      </aside>

      <main v-if="selectedReport" class="report-detail">
        <div class="detail-header">
          <div class="detail-title">
            <div class="title-row">
              <h3>{{ selectedReport.session.title }}</h3>
              <span class="grade-box">{{ gradeForSession(selectedReport) }}</span>
            </div>
            <p class="detail-desc">
              <i class="fa-solid fa-robot"></i>
              {{ statusLabel(reportExecutionStatus(selectedReport.session, selectedReport.reportMeta)) }} ·
              {{ formatDateTime(selectedReport.session.updated_at) }}
            </p>
          </div>
        </div>

        <div class="report-kpis">
          <article class="kpi-card">
            <span class="kpi-label">{{ selectedIsApiReport ? "API 测试任务" : t("reports.kpi_review_tasks") }}</span>
            <strong>{{ selectedIsApiReport ? selectedApiTasks.length : selectedWorkerSummary.total }}</strong>
            <p v-if="selectedIsApiReport">
              已完成 {{ Number(selectedReport.reportMeta.completed_tasks || 0) }} /
              失败 {{ Number(selectedReport.reportMeta.failed_tasks || 0) }} /
              跳过 {{ Number(selectedReport.reportMeta.skipped_tasks || 0) }}
            </p>
            <p v-else>
              {{ t("taskpool.status_completed") }} {{ selectedWorkerSummary.completed }} /
              {{ t("taskpool.status_running") }} {{ selectedWorkerSummary.running }} /
              {{ t("taskpool.status_failed") }} {{ selectedWorkerSummary.failed }}
            </p>
          </article>
          <article class="kpi-card">
            <span class="kpi-label">{{ t("reports.kpi_artifacts") }}</span>
            <strong>{{ selectedReport.reportArtifacts.length + selectedReport.artifacts.length }}</strong>
            <p>{{ t("reports.kpi_artifacts_desc") }}</p>
          </article>
          <article class="kpi-card">
            <span class="kpi-label">{{ t("reports.kpi_report_session") }}</span>
            <strong>{{ selectedReport.reportSessionId ? t("reports.session_ready") : t("reports.session_pending") }}</strong>
            <p>
              {{
                selectedReport.reportSessionId
                  ? `#${selectedReport.reportSessionId.slice(0, 8)}`
                  : t("reports.session_not_complete")
              }}
            </p>
          </article>
        </div>

        <div class="detail-content">
          <section class="content-section">
            <div class="section-header">
              <h4>{{ t("reports.section_summary") }}</h4>
              <span class="subtitle mono">{{ selectedReport.session.mode_key }}</span>
            </div>
            <div class="summary-card">
              <div
                v-if="selectedReportBodyHtml"
                class="summary-body conversation-entry-markdown"
                v-html="selectedReportBodyHtml"
              ></div>
              <p v-else class="summary-body muted">
                {{ t("reports.no_report_body") }}
              </p>
              <table v-if="selectedIsApiReport" class="api-report-summary" data-testid="api-report-summary">
                <tbody>
                  <tr><th>总任务数</th><td>{{ Number(selectedReport.reportMeta.total_tasks || 0) }}</td></tr>
                  <tr><th>通过</th><td>{{ Number(selectedReport.reportMeta.completed_tasks || 0) }}</td></tr>
                  <tr><th>失败</th><td>{{ Number(selectedReport.reportMeta.failed_tasks || 0) }}</td></tr>
                  <tr><th>跳过</th><td>{{ Number(selectedReport.reportMeta.skipped_tasks || 0) }}</td></tr>
                  <tr><th>断言通过</th><td>{{ Number(selectedReport.reportMeta.passed_checks || 0) }}</td></tr>
                  <tr><th>断言失败</th><td>{{ Number(selectedReport.reportMeta.failed_checks || 0) }}</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section v-if="selectedIsApiReport" class="content-section">
            <div class="section-header">
              <h4>API 任务结果</h4>
              <span class="subtitle mono">请求、响应、断言与查证状态</span>
            </div>
            <div class="api-task-report-list">
              <article v-for="(task, index) in selectedApiTasks" :key="String(task.task_id || index)" class="api-task-report-card">
                <div class="api-task-report-head">
                  <strong>{{ String(task.method || "") }} {{ String(task.path || task.full_url || "") }}</strong>
                  <span class="worker-status" :class="`worker-status-${String(task.status || '')}`">
                    {{ String(task.status || "") }}
                  </span>
                </div>
                <p class="worker-meta mono">
                  HTTP {{ task.response_status ?? "—" }} · {{ String(task.request_outcome || "") }} ·
                  断言 {{ task.checks_passed ?? 0 }}/{{ task.checks_failed ?? 0 }}
                </p>
                <p v-if="task.reconciliation_for" class="worker-meta">
                  查证 {{ String(task.reconciliation_for) }} · {{ String(task.reconciliation_status || "none") }}
                </p>
                <details>
                  <summary>请求</summary>
                  <pre class="api-payload-preview">{{ formatApiPayload(task.request) }}</pre>
                </details>
                <details>
                  <summary>响应</summary>
                  <pre class="api-payload-preview">{{ formatApiPayload(task.response) }}</pre>
                </details>
                <details>
                  <summary>断言</summary>
                  <pre class="api-payload-preview">{{ formatApiPayload(task.checks) }}</pre>
                </details>
              </article>
            </div>
          </section>

          <section class="content-section">
            <div class="section-header">
              <h4>{{ t("reports.section_review_tasks") }}</h4>
              <span class="subtitle mono">{{ t("reports.review_debate_sessions") }}</span>
            </div>
            <div class="worker-grid">
              <article
                v-for="(worker, index) in selectedReport.workerDispatches"
                :key="workerDispatchKey(worker, index)"
                class="worker-card"
              >
                <div class="worker-card-head">
                  <strong>{{ worker.agent_key }}</strong>
                  <span class="worker-status" :class="`worker-status-${worker.status}`">
                    {{ statusLabel(worker.status) }}
                  </span>
                </div>
                <p>{{ worker.description }}</p>
                <p class="worker-meta mono">
                  task={{ worker.task_id.slice(0, 8) }}
                  <span v-if="worker.child_session_id">
                    · session={{ worker.child_session_id.slice(0, 8) }}
                  </span>
                </p>
              </article>
            </div>
          </section>

          <section class="content-section">
            <div class="section-header">
              <h4>{{ t("reports.section_artifacts") }}</h4>
              <span class="subtitle mono">{{ t("reports.stored_outputs") }}</span>
            </div>
            <div class="artifact-list">
              <article
                v-for="artifact in [...selectedReport.reportArtifacts, ...selectedReport.artifacts]"
                :key="artifact.id"
                class="artifact-card"
              >
                <div class="artifact-head">
                  <strong>{{ artifactLabel(artifact) }}</strong>
                  <span class="artifact-type mono">{{ artifact.artifact_type }}</span>
                </div>
                <p class="artifact-path mono">
                  <span v-if="selectedReport.reportMeta.tool_job_id">
                    {{ t(artifact.tool_job_id === selectedReport.reportMeta.tool_job_id
                      ? "reports.current_execution" : "reports.historical_execution") }} ·
                  </span>
                  {{ t("reports.execution_id") }} {{ artifact.tool_job_id.slice(0, 8) }} · {{ formatDateTime(artifact.created_at) }}
                </p>
                <p class="artifact-path mono">{{ artifact.path }}</p>
                <a :href="`/api/v1/sessions/${encodeURIComponent(artifact.session_id)}/artifacts/${encodeURIComponent(artifact.id)}/content`" download>
                  {{ t("reports.download") }}
                </a>
                <pre v-if="artifactInlineContent(artifact)" class="artifact-preview">{{
                  artifactInlineContent(artifact)
                }}</pre>
              </article>
            </div>
          </section>

          <section class="content-section">
            <div class="section-header">
              <h4>{{ t("reports.section_verification") }}</h4>
              <span class="subtitle mono">{{ t("reports.session_verifications") }}</span>
            </div>
            <div v-if="selectedReport.verifications.length" class="verification-list">
              <article
                v-for="verification in selectedReport.verifications"
                :key="verification.id"
                class="verification-card"
              >
                <div class="verification-head">
                  <strong>{{ verification.verifier }}</strong>
                  <span class="worker-status" :class="`worker-status-${verification.status}`">
                    {{ verification.status }}
                  </span>
                </div>
                <p>{{ verification.summary }}</p>
              </article>
            </div>
            <div v-else class="summary-card">
              <p class="summary-body muted">{{ t("reports.no_verifications") }}</p>
            </div>
          </section>
        </div>
      </main>
    </div>
  </section>
</template>

<style scoped>
.report-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
  background: var(--bg);
}

.api-task-report-list {
  display: grid;
  gap: 12px;
}

.api-task-report-card {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}

.api-task-report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.api-task-report-card details {
  margin-top: 8px;
}

.api-payload-preview {
  max-height: 260px;
  overflow: auto;
  margin: 8px 0 0;
  padding: 10px;
  border-radius: 8px;
  background: var(--bg);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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

.report-layout {
  display: flex;
  flex: 1;
  gap: 16px;
  min-height: 0;
}

.report-sidebar {
  width: 320px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  padding-right: 16px;
  flex-shrink: 0;
}

.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.sidebar-header h3 {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin: 0;
}
.batch-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  padding-right: 4px;
}

.batch-item {
  padding: 16px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
  cursor: pointer;
  transition: all 0.2s ease;
}

.batch-item:hover {
  background: var(--surface-muted);
}

.batch-item.active {
  background: var(--surface);
  border-color: var(--border-strong);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.batch-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.batch-id,
.batch-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text);
}

.batch-title {
  margin: 0 0 10px 0;
}

.badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
}

.badge-green {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.badge-yellow {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.badge-red {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.badge-blue {
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
}

.batch-meta {
  font-size: 12px;
  color: var(--muted);
  margin: 0 0 12px 0;
}

.batch-stats {
  display: flex;
  gap: 12px;
  font-size: 12px;
  font-weight: 500;
}

.stat {
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat.pass {
  color: #16a34a;
}

.stat.fail {
  color: #dc2626;
}

.batch-list-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 0 18px;
  font-size: 12px;
  color: var(--muted);
}

.report-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding-right: 8px;
}

.detail-header {
  margin-bottom: 16px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
}

.title-row h3 {
  font-size: 20px;
  font-weight: 600;
  margin: 0;
}

.grade-box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #111827;
  color: white;
  font-weight: 700;
  font-size: 13px;
  padding: 0 10px;
}

.detail-desc {
  font-size: 13px;
  color: var(--muted);
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.report-kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.kpi-card,
.summary-card,
.worker-card,
.artifact-card,
.verification-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
}

.kpi-label {
  display: block;
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}

.kpi-card strong {
  font-size: 22px;
}

.kpi-card p,
.summary-body,
.worker-card p,
.artifact-card p,
.verification-card p {
  margin: 8px 0 0 0;
  line-height: 1.6;
  color: var(--text);
}

.summary-body {
  white-space: normal;
}

.api-report-summary {
  width: 100%;
  margin-top: 14px;
  border-collapse: collapse;
  font-size: 13px;
}

.api-report-summary th,
.api-report-summary td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
  text-align: left;
}

.api-report-summary th {
  width: 45%;
  color: var(--text-secondary, #64748b);
  font-weight: 500;
}

.muted {
  color: var(--muted);
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.section-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}

.section-header h4 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.subtitle {
  font-size: 13px;
  color: var(--muted);
}

.worker-grid,
.artifact-list,
.verification-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.worker-card-head,
.artifact-head,
.verification-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.worker-meta,
.artifact-path,
.artifact-type {
  color: var(--muted);
  font-size: 12px;
}

.artifact-preview {
  margin: 12px 0 0 0;
  padding: 12px;
  border-radius: 10px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.5;
}

.worker-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.worker-status-completed,
.worker-status-passed {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.worker-status-running,
.worker-status-waiting_approval,
.worker-status-partial {
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
}

.worker-status-failed {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
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
}

.error-state {
  color: #dc2626;
}

.mono {
  font-family: var(--font-mono, monospace);
}

@media (max-width: 1080px) {
  .report-layout {
    flex-direction: column;
  }

  .report-sidebar {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding-right: 0;
    padding-bottom: 20px;
  }

  .report-kpis {
    grid-template-columns: 1fr;
  }
}
</style>
