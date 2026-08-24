<script setup lang="ts">
import { computed } from "vue";

import { t } from "../../services/i18n";
import type { ToolApprovalRequest } from "../../types";
import { useSessionStore } from "../../stores/session";
import { formatServerTime } from "../../utils/datetime";

const sessionStore = useSessionStore();

const pendingApprovals = computed(() => sessionStore.pendingApprovals);
const primaryApproval = computed(() => pendingApprovals.value[0] ?? null);
const pendingCount = computed(() => pendingApprovals.value.length);

/** ui_recording 审批特化卡（P0-10）：项目 / 入口 URL / 三源缺口 / 驱动。 */
const isUiRecordingApproval = computed(
  () => primaryApproval.value?.metadata?.approval_type === "ui_recording",
);
const recordingRequest = computed<Record<string, unknown>>(() => {
  const payload = primaryApproval.value?.metadata?.recording_request;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return {};
  }
  return payload as Record<string, unknown>;
});
const knowledgeGateReasons = computed(() => {
  const gate = primaryApproval.value?.metadata?.knowledge_gate;
  if (!gate || typeof gate !== "object") {
    return [] as string[];
  }
  const sources = (gate as Record<string, unknown>).sources;
  if (!sources || typeof sources !== "object") {
    return [String((gate as Record<string, unknown>).reason || "")].filter(Boolean);
  }
  const labels: Array<[string, string]> = [
    ["graph", t("approvalPanel.recording_source_graph")],
    ["cases", t("approvalPanel.recording_source_cases")],
    ["memory", t("approvalPanel.recording_source_memory")],
  ];
  const lines: string[] = [];
  for (const [key, label] of labels) {
    const source = (sources as Record<string, unknown>)[key];
    if (!source || typeof source !== "object") {
      continue;
    }
    const record = source as Record<string, unknown>;
    if (record.sufficient === true) {
      continue;
    }
    const counts: string[] = [];
    for (const field of ["page_count", "element_count", "active_case_count", "hit_count", "total_docs"]) {
      if (typeof record[field] === "number") {
        counts.push(`${field}=${record[field]}`);
      }
    }
    lines.push(`${label}${counts.length ? ` (${counts.join(", ")})` : ""}`);
  }
  return lines;
});
const recordingDriverLabel = computed(() => {
  const driver = recordingRequest.value.driver;
  if (!driver || typeof driver !== "object") {
    return "embedded";
  }
  return String((driver as Record<string, unknown>).kind || "embedded");
});

function readArguments(metadata: Record<string, unknown>) {
  const argumentsPayload = metadata.arguments;
  if (!argumentsPayload || typeof argumentsPayload !== "object" || Array.isArray(argumentsPayload)) {
    return {};
  }
  return argumentsPayload as Record<string, unknown>;
}

function formatAgentLabel(value: unknown) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return "Coordinator";
  }

  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function approvalKindLabel(approval: ToolApprovalRequest) {
  switch (approval.tool_key) {
    case "cli-executor":
      return t("approvalPanel.kind_cli");
    case "browser-automation":
    case "browser-control":
    case "dom-inspector":
      return t("approvalPanel.kind_browser");
    case "api-caller":
    case "api-tester":
      return t("approvalPanel.kind_api");
    case "mail-confirm":
    case "mail-download-attachment":
      return t("approvalPanel.kind_message");
    case "ui-automation-runner":
      return t("approvalPanel.kind_recording");
    case "filesystem":
    case "file-artifact-manager":
      return t("approvalPanel.kind_file");
    default:
      return approval.tool_name || approval.tool_key;
  }
}

function approvalMetaLine(approval: ToolApprovalRequest) {
  const agentLabel = formatAgentLabel(approval.metadata?.selected_agent_key);
  const timeLabel = formatServerTime(approval.created_at);
  return `${agentLabel} · ${timeLabel}`;
}

function approvalPreviewSource(approval: ToolApprovalRequest) {
  const args = readArguments(approval.metadata);
  const lines: string[] = [];

  const pushTextLines = (value: unknown) => {
    if (typeof value !== "string") {
      return;
    }
    for (const line of value.split(/\r?\n/)) {
      const normalized = line.trim();
      if (normalized) {
        lines.push(normalized);
      }
    }
  };

  pushTextLines(args.command);

  if (lines.length === 0 && Array.isArray(args.commands)) {
    for (const item of args.commands) {
      pushTextLines(item);
    }
  }

  if (lines.length === 0 && typeof args.subject === "string") {
    lines.push(`${t("approvalPanel.subject")}${args.subject}`);
  }

  if (lines.length === 0 && typeof args.url === "string") {
    lines.push(`${t("approvalPanel.visit")} ${args.url}`);
  }

  if (lines.length === 0 && typeof args.target_url === "string") {
    lines.push(`${t("approvalPanel.visit")} ${args.target_url}`);
  }

  if (lines.length === 0 && typeof args.path === "string") {
    lines.push(`${t("approvalPanel.path")}${args.path}`);
  }

  if (lines.length === 0 && typeof args.query === "string") {
    lines.push(`${t("approvalPanel.query")}${args.query}`);
  }

  if (lines.length === 0 && Array.isArray(args.to) && args.to.length > 0) {
    lines.push(`${t("approvalPanel.recipients")}${args.to.join(", ")}`);
  }

  if (lines.length === 0) {
    lines.push(approvalKindLabel(approval));
  }

  return lines;
}

function approvalPreviewLines(approval: ToolApprovalRequest) {
  return approvalPreviewSource(approval).slice(0, 3);
}

function approvalPreviewOverflowCount(approval: ToolApprovalRequest) {
  return Math.max(0, approvalPreviewSource(approval).length - 3);
}

function approvalHint(approval: ToolApprovalRequest) {
  switch (approval.tool_key) {
    case "cli-executor":
      return t("approvalPanel.hint_cli");
    case "browser-automation":
    case "browser-control":
    case "dom-inspector":
      return t("approvalPanel.hint_browser");
    case "api-caller":
    case "api-tester":
      return t("approvalPanel.hint_api");
    case "mail-confirm":
    case "mail-download-attachment":
      return t("approvalPanel.hint_message");
    case "filesystem":
    case "file-artifact-manager":
      return t("approvalPanel.hint_file");
    default:
      return t("approvalPanel.hint_default");
  }
}

async function handleDecision(approvalId: string, decision: "approved" | "denied") {
  const reason =
    decision === "approved"
      ? t("approvalPanel.reason_approved")
      : t("approvalPanel.reason_denied");
  await sessionStore.resolveApproval(approvalId, decision, reason);
}
</script>

<template>
  <section v-if="primaryApproval" class="approval-panel">
    <article class="approval-sidecard">
      <div class="approval-sidecard-top">
        <span class="approval-sidecard-code">AUTH</span>
        <span class="approval-sidecard-dot"></span>
      </div>

      <div class="approval-sidecard-body">
        <div class="approval-sidecard-heading">
          <strong>{{ t("approvalPanel.title") }}</strong>
          <span class="approval-sidecard-badge">{{ t("approvalPanel.pending") }}</span>
        </div>
        <p class="approval-sidecard-meta">{{ approvalMetaLine(primaryApproval) }}</p>

        <div class="approval-sidecard-summary">
          <span class="approval-sidecard-label">{{ t("approvalPanel.current_action") }}</span>
          <strong>{{ approvalKindLabel(primaryApproval) }}</strong>
          <p>{{ primaryApproval.reason || t("approvalPanel.default_reason") }}</p>
        </div>

        <!-- ui_recording 特化：项目 / 入口 / 三源缺口 / 驱动（P0-10） -->
        <div v-if="isUiRecordingApproval" class="approval-recording-detail">
          <div class="approval-recording-row">
            <span class="approval-recording-key">{{ t("approvalPanel.recording_project") }}</span>
            <span class="approval-recording-value" :title="String(recordingRequest.project_id || '')">
              {{ recordingRequest.project_id || "—" }}
            </span>
          </div>
          <div class="approval-recording-row">
            <span class="approval-recording-key">{{ t("approvalPanel.recording_entry") }}</span>
            <span class="approval-recording-value" :title="String(recordingRequest.entry_url || '')">
              {{ recordingRequest.entry_url || "—" }}
            </span>
          </div>
          <div class="approval-recording-row">
            <span class="approval-recording-key">{{ t("approvalPanel.recording_driver") }}</span>
            <span class="approval-recording-value approval-recording-driver">
              {{ recordingDriverLabel }}
            </span>
          </div>
          <div v-if="knowledgeGateReasons.length > 0" class="approval-recording-gaps">
            <span class="approval-recording-key">{{ t("approvalPanel.recording_gaps") }}</span>
            <span v-for="line in knowledgeGateReasons" :key="line" class="approval-recording-gap">
              {{ line }}
            </span>
          </div>
        </div>

        <div v-if="!isUiRecordingApproval" class="approval-sidecard-preview">
          <span
            v-for="line in approvalPreviewLines(primaryApproval)"
            :key="line"
            class="approval-sidecard-command"
          >
            {{ line }}
          </span>
          <span
            v-if="approvalPreviewOverflowCount(primaryApproval) > 0"
            class="approval-sidecard-more"
          >
            +{{ approvalPreviewOverflowCount(primaryApproval) }} {{ t("approvalPanel.more_params") }}
          </span>
        </div>

        <p class="approval-sidecard-hint">
          {{ isUiRecordingApproval ? t("approvalPanel.hint_recording") : approvalHint(primaryApproval) }}
        </p>

        <p v-if="pendingCount > 1" class="approval-sidecard-tail">
          {{ t("approvalPanel.queue_hint", { count: String(pendingCount - 1) }) }}
        </p>
      </div>

      <div class="approval-sidecard-actions">
        <button
          class="secondary-btn"
          type="button"
          :disabled="sessionStore.isResolvingApproval(primaryApproval.id)"
          @click="handleDecision(primaryApproval.id, 'denied')"
        >
          {{ t("approvalPanel.deny") }}
        </button>
        <button
          class="primary-btn"
          type="button"
          :disabled="sessionStore.isResolvingApproval(primaryApproval.id)"
          @click="handleDecision(primaryApproval.id, 'approved')"
        >
          <i
            v-if="sessionStore.isResolvingApproval(primaryApproval.id)"
            class="fa-solid fa-spinner fa-spin"
          ></i>
          {{
            isUiRecordingApproval
              ? t("approvalPanel.approve_recording")
              : t("approvalPanel.approve")
          }}
        </button>
      </div>
    </article>
  </section>
</template>
