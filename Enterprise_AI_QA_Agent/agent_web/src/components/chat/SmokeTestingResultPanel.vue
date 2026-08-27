<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { useSessionStore } from "../../stores/session";

const sessionStore = useSessionStore();
const selectedCasesByPlan = ref<Record<string, string[]>>({});

const results = computed(() => {
  const primary = sessionStore.verificationMeta?.verification_results;
  const all = Array.isArray(primary) && primary.length > 0
    ? primary
    : (sessionStore.session?.verification_results ?? []);
  return all
    .filter((item) => String(item.metadata?.tool_key || "") === "smoke-suite-runner" || item.verifier === "冒烟测试结果")
    .slice()
    .reverse()
    .slice(0, 6);
});

const plans = computed(() => {
  return sessionStore.recentToolJobs
    .filter((job) => job.tool_key === "smoke-suite-runner")
    .map((job) => {
      const plan = job.output_payload?.plan;
      if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
        return null;
      }
      const normalized = plan as Record<string, unknown>;
      const cases = Array.isArray(normalized.cases)
        ? normalized.cases.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
        : [];
      return {
        jobId: job.id,
        planId: String(normalized.plan_id || ""),
        version: Number(normalized.version || 1),
        title: String(normalized.title || "冒烟测试方案"),
        status: String(normalized.status || ""),
        targetUrl: String(normalized.target_url || ""),
        projectScope: String(normalized.project_scope || ""),
        planUri: String(job.output_payload?.plan_uri || ""),
        cases,
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item?.planId))
    .slice(0, 3);
});

watch(
  plans,
  (items) => {
    const next = { ...selectedCasesByPlan.value };
    for (const plan of items) {
      if (next[plan.planId]) {
        continue;
      }
      next[plan.planId] = plan.cases
        .filter((item) => item.selected !== false && item.execution_eligible !== false)
        .map((item) => String(item.case_id || ""))
        .filter(Boolean);
    }
    selectedCasesByPlan.value = next;
  },
  { immediate: true },
);

function tone(status: string) {
  if (status === "passed") return "online";
  if (status === "partial" || status === "not_run") return "degraded";
  return "offline";
}

function statusLabel(status: string) {
  if (status === "passed") return "准入通过";
  if (status === "failed") return "准入失败";
  if (status === "partial") return "部分通过";
  if (status === "not_run") return "待确认";
  return status;
}

function valueText(value: unknown) {
  const text = String(value ?? "").trim();
  return text || "-";
}

function caseId(item: Record<string, unknown>) {
  return String(item.case_id || "");
}

function isSelected(planId: string, id: string) {
  return (selectedCasesByPlan.value[planId] || []).includes(id);
}

function toggleCase(planId: string, id: string) {
  const current = selectedCasesByPlan.value[planId] || [];
  selectedCasesByPlan.value = {
    ...selectedCasesByPlan.value,
    [planId]: current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id],
  };
}

async function confirmSelected(planId: string) {
  const selected = selectedCasesByPlan.value[planId] || [];
  if (!selected.length) {
    return;
  }
  await sessionStore.sendMessage(
    `确认执行冒烟测试方案 ${planId}，只执行以下勾选用例：${selected.join(", ")}`,
    [],
    {
      context: {
        smoke_action: "execute_approved_plan",
        plan_id: planId,
        selected_case_ids: selected,
      },
    },
  );
}
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel verification-panel">
    <div class="observability-head">
      <div>
        <strong>冒烟测试结果</strong>
        <p>展示冒烟方案、所选用例执行情况、准入结论和 RustFS 资产链接。</p>
      </div>
      <span class="registry-tag light">{{ results.length }} 条结果</span>
    </div>

    <div v-if="results.length" class="tool-activity-list">
      <article v-for="item in results" :key="item.id" class="tool-activity-item">
        <div class="tool-activity-head">
          <div>
            <strong>{{ statusLabel(item.status) }}</strong>
            <p>{{ item.summary }}</p>
          </div>
          <span :class="['runtime-status-square-dot', `is-${tone(item.status)}`]"></span>
        </div>
        <dl class="tool-activity-grid">
          <div>
            <dt>方案</dt>
            <dd>{{ valueText(item.metadata?.plan_id) }} v{{ valueText(item.metadata?.plan_version) }}</dd>
          </div>
          <div>
            <dt>总用例</dt>
            <dd>{{ valueText(item.metadata?.total_cases) }}</dd>
          </div>
          <div>
            <dt>执行</dt>
            <dd>{{ valueText(item.metadata?.selected_case_count) }}</dd>
          </div>
          <div>
            <dt>通过</dt>
            <dd>{{ item.passed_count }}</dd>
          </div>
          <div>
            <dt>失败</dt>
            <dd>{{ item.failed_count }}</dd>
          </div>
          <div>
            <dt>阻塞</dt>
            <dd>{{ valueText(item.metadata?.blocked_count) }}</dd>
          </div>
        </dl>
        <div class="tool-activity-output" v-if="item.metadata?.approved_plan_uri || item.metadata?.report_uri">
          <p v-if="item.metadata?.approved_plan_uri">
            <strong>冻结方案：</strong>{{ item.metadata.approved_plan_uri }}
          </p>
          <p v-if="item.metadata?.report_uri">
            <strong>执行报告：</strong>{{ item.metadata.report_uri }}
          </p>
        </div>
      </article>
    </div>

    <div v-else class="settings-empty">暂无冒烟测试结果。生成方案或执行冒烟测试后会显示在这里。</div>

    <div v-if="plans.length" class="tool-activity-list smoke-plan-list">
      <article v-for="plan in plans" :key="`${plan.planId}-${plan.version}`" class="tool-activity-item">
        <div class="tool-activity-head">
          <div>
            <strong>{{ plan.title }} v{{ plan.version }}</strong>
            <p>{{ plan.projectScope }} · {{ plan.targetUrl || "未提供目标地址" }}</p>
          </div>
          <span class="registry-tag light">{{ plan.status || "待确认" }}</span>
        </div>
        <div class="smoke-case-picker">
          <label v-for="item in plan.cases" :key="caseId(item)" class="smoke-case-row">
            <input
              type="checkbox"
              :checked="isSelected(plan.planId, caseId(item))"
              :disabled="item.execution_eligible === false"
              @change="toggleCase(plan.planId, caseId(item))"
            >
            <span>
              <strong>{{ String(item.title || "未命名用例") }}</strong>
              <small>
                {{ String(item.case_type || "-") }} · {{ String(item.risk_level || "-") }}
                <template v-if="item.requires_approval"> · 需确认</template>
                <template v-if="item.execution_eligible === false"> · 暂不可执行</template>
              </small>
            </span>
          </label>
        </div>
        <div class="smoke-plan-actions">
          <button
            type="button"
            class="home-tool-btn"
            :disabled="!(selectedCasesByPlan[plan.planId] || []).length || sessionStore.isBusy"
            @click="confirmSelected(plan.planId)"
          >
            确认执行所选用例
          </button>
          <span v-if="plan.planUri" class="registry-tag light">{{ plan.planUri }}</span>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.smoke-plan-list {
  margin-top: 12px;
}

.smoke-case-picker {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.smoke-case-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 8px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.72);
}

.smoke-case-row span,
.smoke-case-row strong,
.smoke-case-row small {
  min-width: 0;
}

.smoke-case-row strong,
.smoke-case-row small {
  display: block;
  overflow-wrap: anywhere;
}

.smoke-case-row small {
  margin-top: 2px;
  color: #64748b;
}

.smoke-plan-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}
</style>
