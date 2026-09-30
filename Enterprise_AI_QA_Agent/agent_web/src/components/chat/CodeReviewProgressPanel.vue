<script setup lang="ts">
import { computed, ref } from "vue";

import AssistantMarkdown from "./AssistantMarkdown.vue";
import { t } from "../../services/i18n";
import { api } from "../../services/api";
import { useSessionStore } from "../../stores/session";
import type { ChatMessage, SessionDetail, ToolApprovalRequest, WorkerDispatchRecord } from "../../types";
import { formatServerDateTime } from "../../utils/datetime";
import { toolOutputToMarkdown } from "../../utils/toolOutput";

const sessionStore = useSessionStore();

const workerStats = computed(() => sessionStore.codeReviewWorkerStats);
const reportMeta = computed(() => sessionStore.codeReviewReportMeta);
const pendingCompletionWorker = computed(() => sessionStore.pendingCompletionWorkerMeta);
const debateProgress = computed(() => sessionStore.codeReviewDebateProgressMeta);

type GovernanceRecord = Record<string, unknown>;

const governanceEvidence = computed(() => {
  const metadata = sessionStore.session?.metadata || {};
  const fromMetadata = readGovernanceRecord(metadata.code_review_governance) || readGovernanceRecord(metadata.governance);
  if (fromMetadata) {
    return fromMetadata;
  }

  const graphState = sessionStore.latestSnapshotGraphState;
  const fromGraphState = readGovernanceRecord(graphState.governance);
  if (fromGraphState) {
    return fromGraphState;
  }

  for (const result of sessionStore.latestToolResults.slice().reverse()) {
    const output = result.output || {};
    const fromOrchestrator = readGovernanceRecord(output.governance);
    if (fromOrchestrator) {
      return fromOrchestrator;
    }
    if (result.tool_key === "code-governance-runner") {
      const direct = readGovernanceRecord(output);
      if (direct) {
        return direct;
      }
    }
  }
  return null;
});

const governanceDecision = computed(() => readRecord(governanceEvidence.value?.decision));
const governanceRiskScore = computed(() => readRecord(governanceEvidence.value?.risk_score));
const governanceMetrics = computed(() => readRecord(governanceEvidence.value?.metrics));
const governanceFindings = computed(() => readArray(governanceEvidence.value?.findings).slice(0, 6));
const governanceScannerRuns = computed(() => readArray(governanceEvidence.value?.scanner_runs));

const selectedTask = ref<WorkerDispatchRecord | null>(null);
const selectedTaskSession = ref<SessionDetail | null>(null);
const isLoadingTaskDetail = ref(false);
const taskDetailError = ref("");

const workerRows = computed(() => {
  return sessionStore.workerDispatches
    .slice()
    .sort((left, right) => {
      const leftCompletion = Number(Boolean(left.is_completion_worker));
      const rightCompletion = Number(Boolean(right.is_completion_worker));
      if (leftCompletion !== rightCompletion) {
        return leftCompletion - rightCompletion;
      }
      const leftRound = Number(left.debate_round_index || 0);
      const rightRound = Number(right.debate_round_index || 0);
      return leftRound - rightRound;
    });
});

const selectedTaskRecentMessages = computed(() => {
  if (!selectedTaskSession.value) {
    return [];
  }
  return selectedTaskSession.value.messages
    .filter((message) => String(message.content || "").trim())
    .slice(-6)
    .reverse();
});

function statusTone(status: string) {
  if (status === "completed") return "success";
  if (status === "running" || status === "pending") return "running";
  if (status === "waiting_approval") return "warning";
  if (status === "failed" || status === "denied") return "danger";
  return "neutral";
}

function readRecord(value: unknown): GovernanceRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as GovernanceRecord;
}

function readArray(value: unknown): GovernanceRecord[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is GovernanceRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

function readGovernanceRecord(value: unknown): GovernanceRecord | null {
  const record = readRecord(value);
  const hasGovernanceShape = Boolean(record.decision || record.risk_score || record.findings || record.approval_decision);
  return hasGovernanceShape ? record : null;
}

function governanceStatus() {
  return String(governanceDecision.value.status || governanceEvidence.value?.approval_decision || "pending");
}

function governanceTone(status: string) {
  if (status === "pass") return "success";
  if (status === "warning") return "warning";
  if (status === "blocked") return "danger";
  return "neutral";
}

function severityTone(severity: unknown) {
  const value = String(severity || "").toLowerCase();
  if (value === "critical" || value === "high") return "danger";
  if (value === "medium") return "warning";
  if (value === "low") return "neutral";
  return "light";
}

function governanceDecisionLabel(status: string) {
  if (status === "pass") return t("reviewProgress.governance_pass");
  if (status === "warning") return t("reviewProgress.governance_warning");
  if (status === "blocked") return t("reviewProgress.governance_blocked");
  return t("reviewProgress.pending");
}

function metricNumber(value: unknown) {
  const numberValue = Number(value || 0);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

function findingLocation(item: GovernanceRecord) {
  const filePath = String(item.file_path || "").trim();
  const line = item.line ? `:${item.line}` : "";
  return filePath ? `${filePath}${line}` : "n/a";
}

function statusLabel(status: string) {
  if (status === "completed") return t("reviewProgress.completed");
  if (status === "running") return t("reviewProgress.running");
  if (status === "waiting_approval") return t("reviewProgress.waiting_approval");
  if (status === "pending" || status === "queued" || status === "launching") return t("reviewProgress.pending");
  if (status === "failed") return t("reviewProgress.failed");
  if (status === "denied") return t("reviewProgress.denied");
  return status || t("reviewProgress.unknown");
}

function stageLabel(stage?: string, roundIndex = 0, totalRoundCount = 0) {
  if (stage === "independent_findings") {
    return t("reviewProgress.stage_findings");
  }
  if (stage === "cross_review") {
    return t("reviewProgress.stage_cross_review", { round: String(roundIndex || 2) });
  }
  if (stage === "summary_resolution") {
    const resolvedRound = roundIndex || totalRoundCount || 0;
    return resolvedRound ? t("reviewProgress.stage_resolution_round", { round: String(resolvedRound) }) : t("reviewProgress.stage_resolution");
  }
  if (stage === "completed") {
    return t("reviewProgress.stage_completed");
  }
  return t("reviewProgress.pending");
}

function roleLabel(item: WorkerDispatchRecord) {
  if (item.is_completion_worker) return t("reviewProgress.role_summary");
  if (item.dispatch_role === "debate_followup") return t("reviewProgress.role_cross");
  return t("reviewProgress.role_findings");
}

function shortId(value: string | undefined) {
  const normalized = String(value || "").trim();
  if (!normalized) return t("reviewProgress.not_assigned");
  return normalized.length <= 12 ? normalized : `${normalized.slice(0, 8)}...`;
}

function formatTime(value: string | undefined) {
  const normalized = String(value || "").trim();
  if (!normalized) return t("reviewProgress.not_updated");
  return formatServerDateTime(normalized, normalized);
}

function approvalHeadline(approval: ToolApprovalRequest) {
  return `${approval.tool_name} / ${statusLabel(approval.status)}`;
}

function taskRoundLabel(item: WorkerDispatchRecord) {
  return stageLabel(item.debate_stage, item.debate_round_index || 0, item.debate_total_round_count || 0);
}

function messageRoleLabel(role: ChatMessage["role"]) {
  if (role === "assistant") return "Agent";
  if (role === "tool") return "Tool";
  if (role === "user") return "Input";
  if (role === "event") return "Event";
  return role;
}

async function openTaskDetail(item: WorkerDispatchRecord) {
  selectedTask.value = item;
  selectedTaskSession.value = null;
  taskDetailError.value = "";

  const childSessionId = String(item.child_session_id || "").trim();
  if (!childSessionId) {
    return;
  }

  isLoadingTaskDetail.value = true;
  try {
    selectedTaskSession.value = await api.getSession(childSessionId);
  } catch (error) {
    taskDetailError.value =
      error instanceof Error ? error.message : t("reviewProgress.load_detail_failed");
  } finally {
    isLoadingTaskDetail.value = false;
  }
}

function closeTaskDetail() {
  selectedTask.value = null;
  selectedTaskSession.value = null;
  taskDetailError.value = "";
  isLoadingTaskDetail.value = false;
}
</script>

<template>
  <section v-if="sessionStore.session" class="review-progress-panel">
    <div class="review-progress-head">
      <div>
        <strong>{{ t("reviewProgress.title") }}</strong>
        <p>{{ t("reviewProgress.desc") }}</p>
      </div>
      <span class="registry-tag light">{{ sessionStore.session.mode_key }}</span>
    </div>

    <div class="review-progress-stage">
      <span class="registry-tag running">
        {{ debateProgress?.stage_label || stageLabel(debateProgress?.stage, debateProgress?.current_round_index || 0, debateProgress?.total_round_count || 0) }}
      </span>
      <span>{{ t("reviewProgress.stage_status") }}: {{ statusLabel(debateProgress?.status || "pending") }}</span>
      <span v-if="debateProgress?.current_round_index">
        {{ t("reviewProgress.current_round") }}: {{ debateProgress.current_round_index }} / {{ debateProgress?.total_round_count || "?" }}
      </span>
      <span>{{ t("reviewProgress.last_update") }}: {{ formatTime(debateProgress?.updated_at) }}</span>
      <span v-if="debateProgress?.peer_review_count">{{ t("reviewProgress.peer_samples") }}: {{ debateProgress.peer_review_count }}</span>
    </div>

    <section class="review-progress-card review-governance-card">
      <div class="review-progress-section-head">
        <strong>{{ t("reviewProgress.governance_gate") }}</strong>
        <span
          class="registry-tag"
          :class="governanceTone(governanceStatus())"
        >
          {{ governanceDecisionLabel(governanceStatus()) }}
        </span>
      </div>
      <template v-if="governanceEvidence">
        <div class="review-governance-score-row">
          <article class="review-governance-score">
            <span>{{ t("reviewProgress.governance_score") }}</span>
            <strong>{{ metricNumber(governanceRiskScore.score) }}</strong>
            <small>{{ governanceRiskScore.level || "LOW" }}</small>
          </article>
          <div class="review-governance-metrics">
            <span>{{ t("reviewProgress.governance_changed_files") }}: {{ metricNumber(governanceMetrics.changed_file_count) }}</span>
            <span>{{ t("reviewProgress.governance_findings") }}: {{ metricNumber(governanceMetrics.finding_count) }}</span>
            <span>{{ t("reviewProgress.governance_blockers") }}: {{ metricNumber(governanceDecision.blocking_findings && Array.isArray(governanceDecision.blocking_findings) ? governanceDecision.blocking_findings.length : 0) }}</span>
            <span>{{ t("reviewProgress.governance_scanners") }}: {{ governanceScannerRuns.length }}</span>
            <span>{{ t("reviewProgress.governance_graph_nodes") }}: {{ metricNumber(governanceMetrics.code_graph_node_count) }}</span>
            <span>{{ t("reviewProgress.governance_graph_edges") }}: {{ metricNumber(governanceMetrics.code_graph_edge_count) }}</span>
            <span>{{ t("reviewProgress.governance_graph_impacted") }}: {{ metricNumber(governanceMetrics.code_graph_impacted_node_count) }}</span>
          </div>
        </div>
        <p v-if="governanceDecision.reason" class="review-progress-reason">{{ governanceDecision.reason }}</p>

        <div v-if="governanceScannerRuns.length" class="review-governance-scanners">
          <span
            v-for="scanner in governanceScannerRuns"
            :key="String(scanner.scanner || scanner.command || scanner.status)"
            class="registry-tag"
            :class="statusTone(String(scanner.status || 'pending'))"
          >
            {{ scanner.scanner }} · {{ scanner.status }} · {{ metricNumber(scanner.finding_count) }}
          </span>
        </div>

        <div v-if="governanceFindings.length" class="review-progress-list review-governance-findings">
          <article
            v-for="finding in governanceFindings"
            :key="String(finding.id || finding.title || findingLocation(finding))"
            class="review-progress-item review-governance-finding"
          >
            <div class="review-progress-item-head">
              <strong>{{ finding.title }}</strong>
              <span class="registry-tag" :class="severityTone(finding.severity)">{{ finding.severity }}</span>
            </div>
            <div class="review-progress-item-meta">
              <span>{{ finding.category }}</span>
              <span>{{ finding.source }}</span>
              <span>{{ findingLocation(finding) }}</span>
            </div>
            <p class="review-progress-reason">{{ finding.summary }}</p>
          </article>
        </div>
        <div v-else class="settings-empty">{{ t("reviewProgress.governance_no_findings") }}</div>
      </template>
      <div v-else class="settings-empty">{{ t("reviewProgress.governance_pending") }}</div>
    </section>

    <div class="review-progress-stats">
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_reviewers") }}</span>
        <strong>{{ workerStats.reviewer_count }}</strong>
      </article>
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_running") }}</span>
        <strong>{{ workerStats.running_count }}</strong>
      </article>
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_approval") }}</span>
        <strong>{{ workerStats.waiting_approval_count + sessionStore.pendingApprovals.length }}</strong>
      </article>
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_completed") }}</span>
        <strong>{{ workerStats.completed_count }}</strong>
      </article>
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_failed") }}</span>
        <strong>{{ workerStats.failed_count }}</strong>
      </article>
      <article class="review-progress-stat">
        <span>{{ t("reviewProgress.stat_report") }}</span>
        <strong>{{ statusLabel(reportMeta?.status || "pending") }}</strong>
      </article>
    </div>

    <div class="review-progress-grid">
      <section class="review-progress-card review-progress-card--tasks">
        <div class="review-progress-section-head">
          <strong>{{ t("reviewProgress.review_tasks") }}</strong>
          <span>{{ workerRows.length }} {{ t("reviewProgress.items_unit") }}</span>
        </div>
        <div v-if="workerRows.length" class="review-progress-list review-progress-scroll">
          <button
            v-for="item in workerRows"
            :key="item.task_id"
            type="button"
            class="review-progress-item review-progress-item-button"
            @click="openTaskDetail(item)"
          >
            <div class="review-progress-item-head">
              <div class="review-progress-item-title">
                <strong>{{ item.description }}</strong>
                <span class="registry-tag light">{{ roleLabel(item) }}</span>
                <span v-if="item.debate_stage" class="registry-tag neutral">
                  {{ taskRoundLabel(item) }}
                </span>
              </div>
              <span class="registry-tag" :class="statusTone(item.status)">{{ statusLabel(item.status) }}</span>
            </div>
            <div class="review-progress-item-meta">
              <span>Agent: {{ item.agent_key }}</span>
              <span>Task: {{ shortId(item.task_id) }}</span>
              <span>Session: {{ shortId(item.child_session_id) }}</span>
              <span v-if="item.debate_round_index">{{ t("reviewProgress.round") }}: {{ item.debate_round_index }}</span>
                <span v-if="item.source_round_index">{{ t("reviewProgress.source_round") }}: {{ item.source_round_index }}</span>
                <span v-if="item.model_key">Model: {{ item.model_key }}</span>
                <span v-if="item.completed_at">{{ t("reviewProgress.completed_at") }}: {{ formatTime(item.completed_at) }}</span>
                <span class="review-progress-item-action">{{ t("reviewProgress.click_detail") }}</span>
              </div>
          </button>
        </div>
        <div v-else class="settings-empty">{{ t("reviewProgress.no_tasks") }}</div>
      </section>

      <section class="review-progress-card review-progress-card--approvals">
        <div class="review-progress-section-head">
          <strong>{{ t("reviewProgress.approval_requests") }}</strong>
          <span>{{ sessionStore.pendingApprovals.length }} {{ t("reviewProgress.entries_unit") }}</span>
        </div>
        <div v-if="sessionStore.pendingApprovals.length" class="review-progress-list review-progress-scroll review-progress-scroll--compact">
          <article
            v-for="approval in sessionStore.pendingApprovals"
            :key="approval.id"
            class="review-progress-item"
          >
            <div class="review-progress-item-head">
              <strong>{{ approvalHeadline(approval) }}</strong>
              <span class="registry-tag warning">{{ t("reviewProgress.manual_process") }}</span>
            </div>
            <div class="review-progress-item-meta">
              <span>Tool: {{ approval.tool_key }}</span>
              <span>{{ t("reviewProgress.created") }}: {{ formatTime(approval.created_at) }}</span>
            </div>
            <p class="review-progress-reason">{{ approval.reason }}</p>
          </article>
        </div>
        <div v-else class="settings-empty">{{ t("reviewProgress.no_approvals") }}</div>
      </section>
    </div>

    <section class="review-progress-card">
      <div class="review-progress-section-head">
        <strong>{{ t("reviewProgress.summary_report") }}</strong>
        <span>{{ statusLabel(reportMeta?.status || "pending") }}</span>
      </div>
      <div class="review-progress-report-meta">
        <span>{{ t("reviewProgress.report_agent") }}: {{ reportMeta?.agent_key || "code-review-synthesizer" }}</span>
        <span>{{ t("reviewProgress.report_session") }}: {{ shortId(reportMeta?.report_session_id) }}</span>
        <span>{{ t("reviewProgress.last_update") }}: {{ formatTime(reportMeta?.updated_at) }}</span>
        <span v-if="reportMeta?.completed_at">{{ t("reviewProgress.completed_at") }}: {{ formatTime(reportMeta.completed_at) }}</span>
      </div>
      <p v-if="reportMeta?.description" class="review-progress-reason">{{ reportMeta.description }}</p>
      <p v-if="reportMeta?.summary" class="review-progress-reason">{{ reportMeta.summary }}</p>
      <p
        v-else-if="pendingCompletionWorker?.description && !workerStats.completion_dispatch"
        class="review-progress-reason"
      >
        {{ t("reviewProgress.completion_queued") }}{{ pendingCompletionWorker.description }}
      </p>
      <div v-else class="settings-empty">{{ t("reviewProgress.no_report") }}</div>
    </section>

    <teleport to="body">
      <div v-if="selectedTask" class="review-task-modal-backdrop" @click.self="closeTaskDetail">
        <section class="review-task-modal">
          <div class="review-task-modal-head">
            <div>
              <strong>{{ selectedTask.description }}</strong>
              <p>
                {{ roleLabel(selectedTask) }} · {{ taskRoundLabel(selectedTask) }} ·
                {{ statusLabel(selectedTask.status) }}
              </p>
            </div>
            <button type="button" class="review-task-close" @click="closeTaskDetail">{{ t("common.close") }}</button>
          </div>

          <div class="review-task-modal-body">
            <div class="review-task-modal-meta">
              <span>Agent: {{ selectedTask.agent_key }}</span>
              <span>Task ID: {{ selectedTask.task_id }}</span>
              <span>{{ t("reviewProgress.child_session") }}: {{ selectedTask.child_session_id || t("reviewProgress.not_created") }}</span>
              <span v-if="selectedTask.debate_round_index">{{ t("reviewProgress.round") }}: {{ selectedTask.debate_round_index }}</span>
              <span v-if="selectedTask.source_round_index">{{ t("reviewProgress.source_round") }}: {{ selectedTask.source_round_index }}</span>
              <span v-if="selectedTask.model_key">Model: {{ selectedTask.model_key }}</span>
              <span v-if="selectedTask.completed_at">{{ t("reviewProgress.completed_at") }}: {{ formatTime(selectedTask.completed_at) }}</span>
            </div>

            <div v-if="isLoadingTaskDetail" class="settings-empty">{{ t("reviewProgress.loading_detail") }}</div>
            <div v-else-if="taskDetailError" class="review-task-error">{{ taskDetailError }}</div>
            <template v-else-if="selectedTaskSession">
              <div class="review-task-session-stats">
                <span>{{ t("reviewProgress.session_status") }}: {{ statusLabel(selectedTaskSession.status) }}</span>
                <span>{{ t("reviewProgress.message_count") }}: {{ selectedTaskSession.messages.length }}</span>
                <span>{{ t("reviewProgress.event_count") }}: {{ selectedTaskSession.event_count }}</span>
                <span>{{ t("reviewProgress.snapshot_count") }}: {{ selectedTaskSession.snapshot_count }}</span>
                <span>{{ t("reviewProgress.last_update") }}: {{ formatTime(selectedTaskSession.updated_at) }}</span>
              </div>

              <div class="review-task-message-list">
                <article
                  v-for="message in selectedTaskRecentMessages"
                  :key="message.id"
                  class="review-task-message"
                >
                  <details v-if="message.role === 'tool'" class="review-task-message-details">
                    <summary style="cursor: pointer; outline: none;">
                      <div class="review-task-message-head" style="display: inline-flex; width: calc(100% - 20px); vertical-align: top;">
                        <strong>{{ messageRoleLabel(message.role) }} <span style="font-weight: normal; font-size: 12px; color: #6b7280; margin-left: 6px;">({{ t("reviewProgress.click_expand") }})</span></strong>
                        <span>{{ formatTime(message.created_at) }}</span>
                      </div>
                    </summary>
                    <div class="conversation-entry-markdown review-task-message-markdown">
                      <AssistantMarkdown :content="toolOutputToMarkdown(message.content)" />
                    </div>
                  </details>
                  <template v-else>
                    <div class="review-task-message-head">
                      <strong>{{ messageRoleLabel(message.role) }}</strong>
                      <span>{{ formatTime(message.created_at) }}</span>
                    </div>
                    <div
                      v-if="message.role === 'assistant'"
                      class="conversation-entry-markdown review-task-message-markdown"
                    >
                      <AssistantMarkdown :content="message.content" />
                    </div>
                    <pre v-else>{{ message.content }}</pre>
                  </template>
                </article>
              </div>
            </template>
            <div v-else class="settings-empty">{{ t("reviewProgress.no_child_session") }}</div>
          </div>
        </section>
      </div>
    </teleport>
  </section>
</template>

<style>
.review-progress-panel {
  display: grid;
  gap: 16px;
}

.review-progress-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.review-progress-head p {
  margin: 6px 0 0;
  color: var(--text-soft, #6b7280);
  font-size: 13px;
}

.review-progress-stage {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: var(--text-soft, #6b7280);
  font-size: 13px;
}

.review-progress-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
}

.review-progress-stat,
.review-progress-card,
.review-progress-item,
.review-task-modal {
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.88);
}

.review-progress-stat {
  padding: 14px 16px;
}

.review-progress-stat span {
  display: block;
  color: var(--text-soft, #6b7280);
  font-size: 12px;
}

.review-progress-stat strong {
  display: block;
  margin-top: 8px;
  font-size: 22px;
}

.review-progress-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.review-progress-card {
  padding: 16px;
  min-height: 0;
}

.review-governance-card {
  display: grid;
  gap: 12px;
}

.review-governance-score-row {
  display: grid;
  grid-template-columns: minmax(160px, 220px) minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.review-governance-score {
  display: grid;
  align-content: center;
  gap: 4px;
  padding: 14px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.76);
}

.review-governance-score span,
.review-governance-score small {
  color: var(--text-soft, #6b7280);
  font-size: 12px;
}

.review-governance-score strong {
  font-size: 30px;
  line-height: 1;
}

.review-governance-metrics,
.review-governance-scanners {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-content: center;
  color: var(--text-soft, #6b7280);
  font-size: 12px;
}

.review-governance-metrics span {
  min-width: 128px;
}

.review-governance-findings {
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

.review-governance-finding {
  background: rgba(248, 250, 252, 0.72);
}

.review-progress-card--tasks,
.review-progress-card--approvals {
  display: flex;
  flex-direction: column;
}

.review-progress-section-head,
.review-progress-item-head,
.review-progress-item-title,
.review-task-message-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.review-progress-section-head {
  margin-bottom: 12px;
}

.review-progress-section-head span {
  color: var(--text-soft, #6b7280);
  font-size: 12px;
}

.review-progress-list {
  display: grid;
  gap: 10px;
}

.review-progress-scroll {
  max-height: min(460px, 58vh);
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

.review-progress-scroll--compact {
  max-height: min(260px, 36vh);
}

.review-progress-item {
  padding: 12px;
}

.review-progress-item-button {
  appearance: none;
  width: 100%;
  text-align: left;
  cursor: pointer;
  border: 1px solid rgba(15, 23, 42, 0.08);
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.review-progress-item-button:hover {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.24);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
}

.review-progress-item-button:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.32);
  outline-offset: 2px;
  border-color: rgba(37, 99, 235, 0.24);
}

.review-progress-item-title {
  justify-content: flex-start;
  flex-wrap: wrap;
}

.review-progress-item-meta,
.review-progress-report-meta,
.review-task-modal-meta,
.review-task-session-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 8px;
  color: var(--text-soft, #6b7280);
  font-size: 12px;
}

.review-progress-item-action {
  margin-left: auto;
  color: #2563eb;
  font-weight: 600;
}

.review-progress-reason,
.review-task-message pre {
  margin: 10px 0 0;
  line-height: 1.6;
  white-space: pre-wrap;
}

.review-task-message pre {
  margin-top: 8px;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.04);
  font-family: "Consolas", "SFMono-Regular", monospace;
  font-size: 12px;
  overflow-x: auto;
}

.review-task-message-markdown {
  margin-top: 8px;
  max-height: 320px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
}

.registry-tag.running {
  background: rgba(59, 130, 246, 0.12);
  color: #2563eb;
}

.registry-tag.success {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}

.registry-tag.warning {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.registry-tag.danger {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}

.registry-tag.neutral {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.review-task-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2400;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(6px);
}

.review-task-modal {
  width: min(920px, 100%);
  max-height: min(82vh, 840px);
  display: grid;
  grid-template-rows: auto 1fr;
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.24);
}

.review-task-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
}

.review-task-modal-head p {
  margin: 6px 0 0;
  color: var(--text-soft, #6b7280);
  font-size: 13px;
}

.review-task-close {
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 999px;
  background: #fff;
  padding: 8px 14px;
  font-size: 13px;
  cursor: pointer;
}

.review-task-modal-body {
  padding: 18px 20px 20px;
  overflow-y: auto;
  display: grid;
  gap: 14px;
}

.review-task-message-list {
  display: grid;
  gap: 12px;
}

.review-task-message {
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  padding: 12px 14px;
  background: rgba(248, 250, 252, 0.88);
}

.review-task-error {
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .review-progress-grid {
    grid-template-columns: 1fr;
  }
}

:root[data-theme="dark"] .review-progress-stat,
:root[data-theme="dark"] .review-progress-card,
:root[data-theme="dark"] .review-progress-item,
:root[data-theme="dark"] .review-task-modal {
  background: var(--surface, #050505);
  border-color: var(--border, #1c1c1c);
}

:root[data-theme="dark"] .review-task-message {
  background: var(--panel, #0b0b0b);
  border-color: var(--border, #1c1c1c);
}

:root[data-theme="dark"] .review-governance-score,
:root[data-theme="dark"] .review-governance-finding {
  background: var(--panel, #0b0b0b);
  border-color: var(--border, #1c1c1c);
}

:root[data-theme="dark"] .review-progress-item-button:hover {
  border-color: var(--border-strong, #333333);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.4);
}

:root[data-theme="dark"] .review-task-message pre {
  background: var(--surface, #050505);
  color: var(--text, #f5f5f5);
}

:root[data-theme="dark"] .review-task-close {
  background: var(--surface, #050505);
  border-color: var(--border, #1c1c1c);
  color: var(--text, #f5f5f5);
}

:root[data-theme="dark"] .review-task-error {
  background: rgba(239, 68, 68, 0.12);
}

@media (max-width: 760px) {
  .review-governance-score-row {
    grid-template-columns: 1fr;
  }
}
</style>
