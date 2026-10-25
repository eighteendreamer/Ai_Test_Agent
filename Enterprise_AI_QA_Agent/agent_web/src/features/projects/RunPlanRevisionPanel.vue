<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NSpin,
} from "naive-ui";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import CasePlanDialog from "./CasePlanDialog.vue";
import type { CasePlanRecord, ModeDescriptor, RunPlanRevisionRequest, TestRunPlanRevisionRecord } from "../../types";

const props = withDefaults(defineProps<{
  projectId: string;
  runId: string;
  expectedRevision: number;
  canRevise: boolean;
  candidateCaseIds?: string[];
  modes?: ModeDescriptor[];
}>(), { candidateCaseIds: () => [], modes: () => [] });
const emit = defineEmits<{ revised: [record: TestRunPlanRevisionRecord] }>();

const history = ref<TestRunPlanRevisionRecord[]>([]);
const historyLoading = ref(false);
const historyError = ref("");
const candidatePlanId = ref("");
const reason = ref("");
const pendingRequest = ref<RunPlanRevisionRequest | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const storageWarning = ref("");
const plannerOpen = ref(false);

const storageKey = `run-plan-revision:${props.projectId}:${props.runId}`;
const latestRevision = computed(() => history.value.reduce((latest, item) => Math.max(latest, item.revision), 0));
const stale = computed(() => latestRevision.value > props.expectedRevision);
const canSubmit = computed(() => props.canRevise && !busy.value && !historyLoading.value
  && !historyError.value && !stale.value
  && (pendingRequest.value !== null || (candidatePlanId.value.trim().length > 0 && reason.value.trim().length > 0)));

function newIdempotencyKey() {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID() : `run-revision-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function removePending() {
  pendingRequest.value = null;
  try {
    window.sessionStorage.removeItem(storageKey);
    storageWarning.value = "";
  } catch {
    storageWarning.value = "浏览器无法清除本地请求记录；请刷新历史确认修订状态。";
  }
}

function savePending(request: RunPlanRevisionRequest) {
  pendingRequest.value = request;
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify(request));
    storageWarning.value = "";
  } catch {
    storageWarning.value = "浏览器无法保存幂等请求。若提交结果不确定，请先刷新修订历史再决定是否重试。";
  }
}

function restorePending() {
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return;
    const value = JSON.parse(raw) as Partial<RunPlanRevisionRequest>;
    if (!Number.isInteger(value.expected_revision) || typeof value.idempotency_key !== "string"
      || !value.idempotency_key || typeof value.plan_id !== "string" || !value.plan_id
      || typeof value.reason !== "string" || !value.reason) {
      throw new Error("保存的修订请求格式无效");
    }
    pendingRequest.value = {
      expected_revision: value.expected_revision!,
      idempotency_key: value.idempotency_key,
      plan_id: value.plan_id,
      reason: value.reason,
    };
    candidatePlanId.value = value.plan_id;
    reason.value = value.reason;
    notice.value = "已恢复待确认的修订请求；重试会复用相同幂等键。";
  } catch {
    storageWarning.value = "无法读取保存的修订请求。请先刷新历史确认是否已提交，再创建新请求。";
  }
}

async function loadHistory() {
  historyLoading.value = true;
  historyError.value = "";
  try {
    history.value = await api.listTestRunPlanRevisions(props.projectId, props.runId);
    const confirmed = pendingRequest.value
      ? history.value.find(item => item.idempotency_key === pendingRequest.value?.idempotency_key)
      : undefined;
    if (confirmed) {
      removePending();
      candidatePlanId.value = "";
      reason.value = "";
      notice.value = `修订 ${confirmed.revision} 已在历史记录中确认。`;
      emit("revised", confirmed);
    }
  } catch (err) {
    historyError.value = err instanceof Error ? err.message : "修订历史加载失败";
  } finally {
    historyLoading.value = false;
  }
}

function showError(err: unknown) {
  error.value = err instanceof Error ? err.message : "Run 计划修订失败";
}

function selectCandidatePlan(plan: CasePlanRecord) {
  if (plan.project_id !== props.projectId || plan.id.trim().length === 0) {
    error.value = "候选计划不属于当前项目或缺少计划编号";
    return;
  }
  candidatePlanId.value = plan.id;
  reason.value = "";
  error.value = "";
  notice.value = `已选择候选计划 ${plan.id}。请填写修订原因后提交。`;
  plannerOpen.value = false;
}

async function submitRevision() {
  if (!canSubmit.value) return;
  error.value = "";
  notice.value = "";
  if (!pendingRequest.value) {
    savePending({
      expected_revision: props.expectedRevision,
      idempotency_key: newIdempotencyKey(),
      plan_id: candidatePlanId.value.trim(),
      reason: reason.value.trim(),
    });
  }
  const request = pendingRequest.value;
  if (!request) return;

  busy.value = true;
  try {
    const record = await api.reviseTestRunPlan(props.projectId, props.runId, request);
    removePending();
    candidatePlanId.value = "";
    reason.value = "";
    notice.value = `已提交修订 ${record.revision}；运行条目和历史记录已更新。`;
    history.value = [...history.value.filter(item => item.revision !== record.revision), record]
      .sort((left, right) => left.revision - right.revision);
    emit("revised", record);
  } catch (err) {
    if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) {
      removePending();
      notice.value = "服务器明确拒绝了本次修订。可检查计划编号或刷新运行后重新提交。";
    } else {
      notice.value = "提交结果尚未确认。重试会复用相同请求和幂等键；也可先刷新历史核对。";
    }
    showError(err);
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  restorePending();
  void loadHistory();
});
</script>

<template>

<section class="run-plan-revision" aria-label="运行计划修订">
    <header class="revision-heading">
      <div>
        <h3>执行计划修订</h3>
        <p class="dialog-hint">当前修订号 {{ expectedRevision }}。先在当前项目生成并评审候选计划，再提交；已领取或已执行条目会保留原快照。</p>
      </div>
      <NButton size="small" :loading="historyLoading" @click="loadHistory">刷新历史</NButton>
    </header>

    <NAlert v-if="!canRevise" type="info" :bordered="false" style="margin: 12px 0">此运行当前不支持修订。</NAlert>
    <NAlert v-else-if="stale" type="warning" :bordered="false" style="margin: 12px 0">
      已发现更新的修订记录（{{ latestRevision }}）。请刷新运行详情后再提交，避免使用过期修订号。
    </NAlert>

    <NForm v-if="canRevise" label-placement="top" class="revision-form">
      <NFormItem label="候选计划编号">
        <NInput
          v-model:value="candidatePlanId"
          maxlength="160"
          :disabled="busy || !!pendingRequest"
          placeholder="粘贴当前项目已保存的候选计划编号"
        />
      </NFormItem>
      <div class="dialog-actions-left">
        <NButton
          data-testid="open-candidate-planner"
          size="small"
          :disabled="busy || !!pendingRequest || historyLoading || stale"
          @click="plannerOpen = true"
        >
          生成并审阅候选计划
        </NButton>
        <small class="dialog-hint">可使用上方流程生成并审阅候选计划，也可粘贴已保存的同项目计划编号。</small>
      </div>
      <NFormItem label="修订原因">
        <NInput
          v-model:value="reason"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 8 }"
          maxlength="2000"
          show-count
          :disabled="busy || !!pendingRequest"
          placeholder="说明为什么需要调整当前运行的未执行部分"
        />
      </NFormItem>
      <NAlert v-if="pendingRequest" type="warning" :bordered="false">
        待确认请求：{{ pendingRequest.idempotency_key }}。输入已锁定以保持重试内容不变。
      </NAlert>
      <NAlert v-if="storageWarning" type="warning" :bordered="false">{{ storageWarning }}</NAlert>
      <NAlert v-if="error" type="error" :bordered="false">{{ error }}</NAlert>
      <NAlert v-if="notice" type="success" :bordered="false">{{ notice }}</NAlert>
      <div class="dialog-actions-left">
        <NButton type="primary" :loading="busy" :disabled="!canSubmit" @click="submitRevision">
          {{ busy ? "提交中…" : pendingRequest ? "重试原修订" : "提交计划修订" }}
        </NButton>
        <NButton v-if="pendingRequest" :disabled="busy" @click="loadHistory">先核对修订历史</NButton>
      </div>
    </NForm>

    <NAlert v-if="historyError" type="error" :bordered="false" style="margin-top: 12px">{{ historyError }}</NAlert>
    <div v-else-if="historyLoading && !history.length" class="dialog-loading">
      <NSpin size="small" /> 正在读取修订历史…
    </div>
    <NEmpty v-else-if="!history.length" description="暂无计划修订记录。" size="small" />
    <ol v-else class="revision-history">
      <li v-for="revision in history" :key="revision.revision">
        <strong>修订 {{ revision.revision }} · {{ revision.created_at }}</strong>
        <p>{{ revision.reason }}</p>
        <small>候选计划 {{ revision.candidate_plan_id }} · 新增 {{ revision.added_item_ids.length }} · 更新 {{ revision.updated_item_ids.length }} · 停用 {{ revision.cancelled_item_ids.length }}</small>
        <NCollapse v-if="revision.item_changes.length" style="margin-top: 8px">
          <NCollapseItem :title="`查看条目变化（${revision.item_changes.length}）`" :name="`changes-${revision.revision}`" display-directive="show">
            <ul class="revision-changes">
              <li v-for="change in revision.item_changes" :key="`${revision.revision}:${change.run_item_id}`">
                {{ change.change }} · RunItem {{ change.run_item_id }} · {{ change.after.status }} · 用例 {{ change.after.case_id }} / {{ change.after.case_version_id }}
              </li>
            </ul>
          </NCollapseItem>
        </NCollapse>
      </li>
    </ol>

    <CasePlanDialog v-if="plannerOpen" :key="`${projectId}:${runId}:revision-candidate`"
      :project-id="projectId" :candidate-case-ids="candidateCaseIds" :modes="modes" candidate-only
      @candidate="selectCandidatePlan" @close="plannerOpen = false" />
  </section>
</template>

<style scoped>
.run-plan-revision { border-top: 1px solid var(--border, #d8dee8); margin-top: 20px; padding-top: 16px; }
.revision-heading, .revision-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.revision-heading h3 { margin: 0; }
.revision-heading p, .revision-muted, .revision-form small { color: var(--muted, #667085); }
.revision-form { display: grid; gap: 8px; margin: 12px 0 20px; }
.revision-form label { display: grid; gap: 6px; font-weight: 600; }
.revision-form input, .revision-form textarea { width: 100%; box-sizing: border-box; }
.revision-form textarea { min-height: 76px; resize: vertical; }
.revision-warning { color: #8a4b08; }
.revision-error { color: #b42318; }
.revision-history { display: grid; gap: 12px; padding-left: 22px; }
.revision-history > li { border-top: 1px solid var(--border, #d8dee8); padding-top: 10px; }
.revision-history p { margin: 6px 0; }
.revision-history ul { display: grid; gap: 6px; }
@media (max-width: 640px) { .revision-heading { align-items: flex-start; flex-direction: column; } }
</style>
