<script setup lang="ts">
import { computed } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();

const pendingApprovals = computed(() => sessionStore.pendingApprovals);

function approvalArgumentPreview(metadata: Record<string, unknown>) {
  const argumentsPayload = metadata.arguments;
  if (!argumentsPayload || typeof argumentsPayload !== "object") {
    return "";
  }

  try {
    return JSON.stringify(argumentsPayload, null, 2);
  } catch {
    return "";
  }
}

function readMetaValue(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  if (value === undefined || value === null || value === "") {
    return "";
  }
  return String(value);
}

async function handleDecision(approvalId: string, decision: "approved" | "denied") {
  const reason =
    decision === "approved"
      ? "用户在工作台中批准了本次执行。"
      : "用户在工作台中拒绝了本次执行。";
  await sessionStore.resolveApproval(approvalId, decision, reason);
}
</script>

<template>
  <section v-if="pendingApprovals.length" class="approval-panel">
    <div class="approval-panel-head">
      <div>
        <strong>待审批操作</strong>
        <p>这里列出了受保护的执行步骤。批准后继续执行，拒绝则停止当前受限步骤。</p>
      </div>
      <span class="approval-panel-count">{{ pendingApprovals.length }}</span>
    </div>

    <article
      v-for="approval in pendingApprovals"
      :key="approval.id"
      class="approval-card"
    >
      <div class="approval-card-meta">
        <div>
          <h3>{{ approval.tool_name }}</h3>
          <p>{{ approval.reason }}</p>
        </div>
        <span class="approval-card-status">待处理</span>
      </div>

      <dl class="approval-card-grid">
        <div>
          <dt>工具</dt>
          <dd>{{ approval.tool_key }}</dd>
        </div>
        <div>
          <dt>申请时间</dt>
          <dd>{{ new Date(approval.created_at).toLocaleString("zh-CN") }}</dd>
        </div>
        <div v-if="approval.metadata?.selected_agent_key">
          <dt>Agent</dt>
          <dd>{{ String(approval.metadata.selected_agent_key) }}</dd>
        </div>
        <div v-if="approval.metadata?.selected_model_key">
          <dt>模型</dt>
          <dd>{{ String(approval.metadata.selected_model_key) }}</dd>
        </div>
        <div v-if="readMetaValue(approval.metadata, 'permission_behavior')">
          <dt>权限行为</dt>
          <dd>{{ readMetaValue(approval.metadata, "permission_behavior") }}</dd>
        </div>
        <div v-if="readMetaValue(approval.metadata, 'permission_source')">
          <dt>策略来源</dt>
          <dd>{{ readMetaValue(approval.metadata, "permission_source") }}</dd>
        </div>
        <div v-if="readMetaValue(approval.metadata, 'permission_level')">
          <dt>权限级别</dt>
          <dd>{{ readMetaValue(approval.metadata, "permission_level") }}</dd>
        </div>
        <div v-if="readMetaValue(approval.metadata, 'category')">
          <dt>类别</dt>
          <dd>{{ readMetaValue(approval.metadata, "category") }}</dd>
        </div>
      </dl>

      <p
        v-if="readMetaValue(approval.metadata, 'permission_reason')"
        class="approval-card-reason"
      >
        {{ readMetaValue(approval.metadata, "permission_reason") }}
      </p>

      <pre
        v-if="approvalArgumentPreview(approval.metadata)"
        class="approval-card-args"
      >{{ approvalArgumentPreview(approval.metadata) }}</pre>

      <div class="approval-card-actions">
        <button
          class="secondary-btn"
          type="button"
          :disabled="sessionStore.isResolvingApproval(approval.id)"
          @click="handleDecision(approval.id, 'denied')"
        >
          拒绝
        </button>
        <button
          class="primary-btn narrow"
          type="button"
          :disabled="sessionStore.isResolvingApproval(approval.id)"
          @click="handleDecision(approval.id, 'approved')"
        >
          <i
            v-if="sessionStore.isResolvingApproval(approval.id)"
            class="fa-solid fa-spinner fa-spin"
          ></i>
          批准并继续
        </button>
      </div>
    </article>
  </section>
</template>
