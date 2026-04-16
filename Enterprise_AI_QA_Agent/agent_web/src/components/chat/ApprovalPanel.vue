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

async function handleDecision(approvalId: string, decision: "approved" | "denied") {
  const reason =
    decision === "approved"
      ? "User approved execution in the web console."
      : "User denied execution in the web console.";
  await sessionStore.resolveApproval(approvalId, decision, reason);
}
</script>

<template>
  <section v-if="pendingApprovals.length" class="approval-panel">
    <div class="approval-panel-head">
      <div>
        <strong>Approval Required</strong>
        <p>Protected operations are listed here. Approve to continue, or deny to stop the gated step.</p>
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
        <span class="approval-card-status">Pending</span>
      </div>

      <dl class="approval-card-grid">
        <div>
          <dt>Tool</dt>
          <dd>{{ approval.tool_key }}</dd>
        </div>
        <div>
          <dt>Requested At</dt>
          <dd>{{ new Date(approval.created_at).toLocaleString("zh-CN") }}</dd>
        </div>
        <div v-if="approval.metadata?.selected_agent_key">
          <dt>Agent</dt>
          <dd>{{ String(approval.metadata.selected_agent_key) }}</dd>
        </div>
        <div v-if="approval.metadata?.selected_model_key">
          <dt>Model</dt>
          <dd>{{ String(approval.metadata.selected_model_key) }}</dd>
        </div>
      </dl>

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
          Deny
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
          Approve And Continue
        </button>
      </div>
    </article>
  </section>
</template>
