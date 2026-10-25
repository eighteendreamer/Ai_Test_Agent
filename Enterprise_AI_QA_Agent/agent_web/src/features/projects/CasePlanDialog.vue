<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NTag,
} from "naive-ui";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import type { CasePlanItem, CasePlanRecord, CasePlanRunRequest, CasePlanningConfig, ModeDescriptor, TestRunDetail, RegressionPlanningSelection } from "../../types";
import type { BusinessEnvironmentSelection } from "../../services/businessPlanning.types";
import JsonContentTree from "./JsonContentTree.vue";
import CasePlanningTaskPanel from "./CasePlanningTaskPanel.vue";
import BusinessEnvironmentSnapshotPicker from "./BusinessEnvironmentSnapshotPicker.vue";

const props = withDefaults(defineProps<{ projectId: string; candidateCaseIds: string[]; modes?: ModeDescriptor[]; initialTaskId?: string; regression?: RegressionPlanningSelection; candidateOnly?: boolean }>(), { modes: () => [], candidateOnly: false });
const emit = defineEmits<{ close: []; started: [detail: TestRunDetail]; candidate: [plan: CasePlanRecord] }>();
const show = ref(true);
function close() {
  show.value = false;
  emit("close");
}
const objective = ref("");
const runtimeJson = ref("{}");
const config = ref<CasePlanningConfig | null>(null);
const plan = ref<CasePlanRecord | null>(null);
const savedId = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");
const modeKey = ref("");
const sessionId = ref("");
const pendingRequest = ref<CasePlanRunRequest | null>(null);
const knownRunId = ref("");
const storageWarning = ref("");
const regressionVersions = ref<Record<string, string>>({ ...props.regression?.versionOverrides });
const regressionLocked = ref(false);
const environmentSelection = ref<BusinessEnvironmentSelection | null>(null);
const environmentPickerOpen = ref(false);
const regressionRequest = computed(() => props.regression ? { ...props.regression, versionOverrides: { ...regressionVersions.value } } : undefined);
function chooseRegressionVersion(caseId: string, versionId: string | null, selected: boolean) {
  if (regressionLocked.value) return;
  if (selected && versionId) regressionVersions.value[caseId] = versionId;
  else delete regressionVersions.value[caseId];
}
const testModes = computed(() => props.modes.filter(mode => mode.is_test_mode));
const testModeOptions = computed(() => testModes.value.map(mode => ({ label: mode.name, value: mode.key })));
const canLaunch = computed(() => plan.value && ["planned", "blocked"].includes(plan.value.status)
  && plan.value.decision?.items.some(item => item.kind === "target"));
watch(testModes, modes => {
  if (plan.value?.regression) { modeKey.value = plan.value.regression.mode_key; return; }
  if (!pendingRequest.value && !modes.some(mode => mode.key === modeKey.value)) modeKey.value = modes[0]?.key || "";
}, { immediate: true });
const statusLabels: Record<CasePlanRecord["status"], string> = { planned: "计划已生成", no_match: "未找到目标用例", blocked: "仍有执行缺口", retrieval_failed: "用例检索失败", planning_failed: "规划失败" };
const kindLabels = { target: "目标用例", dependency: "必要前置用例", prepare: "运行内准备" };

function showError(err: unknown) { error.value = err instanceof Error ? err.message : "用例规划操作失败"; }
function evidence(item: CasePlanItem) { return plan.value?.evidence.find(e => e.kind === "case" && e.version === item.case_version_id); }
function title(item: CasePlanItem) { const value = evidence(item)?.payload.title; return typeof value === "string" ? value : item.key; }
function content(item: CasePlanItem) { return item.kind === "prepare" ? item.instructions : evidence(item)?.payload.content; }
const operationSelections = computed(() => plan.value?.rounds.flatMap(round => {
  const decision = round.decision;
  if (!decision || typeof decision !== "object" || Array.isArray(decision)) return [];
  const selections = (decision as Record<string, unknown>).operation_selections;
  return Array.isArray(selections) ? selections : [];
}) || []);
const linkedSourceReads = computed(() => plan.value?.rounds.find(round => round.linked_source_reads)?.linked_source_reads);
function launchStorageKey() { return `case-plan-launch:${props.projectId}:${plan.value?.id}`; }
function persistLaunch() {
  try {
    if (pendingRequest.value) window.sessionStorage.setItem(launchStorageKey(), JSON.stringify({ request: pendingRequest.value, runId: knownRunId.value }));
    else window.sessionStorage.removeItem(launchStorageKey());
    storageWarning.value = "";
  } catch {
    storageWarning.value = "浏览器未能保存启动请求，请保留下方请求编号；关闭页面后先核对运行记录再重新启动。";
  }
}
function selectPlan(value: CasePlanRecord) {
  plan.value = value; savedId.value = value.id;
  environmentSelection.value = value.request?.environment ? { ...value.request.environment } : null;
  if (value.regression) {
    regressionVersions.value = { ...value.regression.version_overrides };
    regressionLocked.value = true;
  }
  pendingRequest.value = null; knownRunId.value = ""; storageWarning.value = "";
  sessionId.value = value.regression?.session_id || "";
  modeKey.value = value.regression?.mode_key || testModes.value[0]?.key || "";
  try {
    const raw = window.sessionStorage.getItem(launchStorageKey());
    if (!raw) return;
    const stored = JSON.parse(raw);
    const request = stored?.request;
    if (!request || typeof request.creation_key !== "string" || !request.creation_key || request.creation_key.length > 160
      || typeof request.mode_key !== "string" || !request.mode_key || request.mode_key.length > 80
      || (request.session_id !== undefined && (typeof request.session_id !== "string" || !request.session_id || request.session_id.length > 160))) {
      throw new Error("已保存的启动请求格式无效");
    }
    if (value.regression && (request.mode_key !== value.regression.mode_key
      || (request.session_id || null) !== value.regression.session_id)) throw new Error("已保存启动请求与固定回归模式或会话不一致");
    pendingRequest.value = { creation_key: request.creation_key, mode_key: request.mode_key,
      ...(request.session_id ? { session_id: request.session_id } : {}) };
    modeKey.value = request.mode_key; sessionId.value = request.session_id || "";
    knownRunId.value = typeof stored.runId === "string" ? stored.runId : "";
  } catch {
    storageWarning.value = "无法读取已保存的启动请求。若此前已经提交，请先从项目运行列表核对结果，避免重复创建。";
  }
}
async function launch() {
  if (!plan.value || !canLaunch.value || busy.value || knownRunId.value || (!pendingRequest.value && !modeKey.value)) return;
  error.value = ""; notice.value = ""; busy.value = true;
  if (!pendingRequest.value) {
    const creationKey = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID() : `client-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    pendingRequest.value = { creation_key: creationKey, mode_key: modeKey.value,
      ...(sessionId.value.trim() ? { session_id: sessionId.value.trim() } : {}) };
    persistLaunch();
  }
  try {
    const detail = await api.launchCasePlan(props.projectId, plan.value.id, { ...pendingRequest.value });
    knownRunId.value = detail.run.id; persistLaunch();
    notice.value = "运行记录已保存，执行进度和结果请查看运行详情。";
    emit("started", detail);
  } catch (err) {
    showError(err);
    if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) {
      pendingRequest.value = null; persistLaunch();
    } else {
      notice.value = "启动结果尚未确认。重试将保留相同请求和执行选项，不另建运行。";
    }
  } finally { busy.value = false; }
}
async function viewRun() {
  if (!knownRunId.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    const detail = await api.getTestRun(knownRunId.value);
    if (detail.run.project_id !== props.projectId || detail.run.plan_snapshot?.plan.id !== plan.value?.id) {
      throw new Error("运行记录不属于当前项目和计划");
    }
    emit("started", detail);
  }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
function newRun() {
  if (!knownRunId.value || busy.value) return;
  knownRunId.value = ""; pendingRequest.value = null; error.value = ""; notice.value = "";
  persistLaunch();
}
async function loadPolicy() {
  busy.value = true;
  try { config.value = await api.getCasePlanningPolicy(props.projectId); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function remember() {
  if (!config.value || busy.value) return;
  busy.value = true; error.value = "";
  try { config.value = await api.saveCasePlanningPolicy(props.projectId, config.value.policy); notice.value = "项目规划设置已保存"; }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
function chooseReceiptCorrection(checked: boolean) {
  if (!config.value) return;
  if (checked) config.value.policy.receipt_correction_protocol = "case-receipt-correction-v1";
  else {
    delete config.value.policy.receipt_correction_protocol;
    delete config.value.policy.receipt_correction_transport_options;
  }
}
function chooseReceiptTransport(checked: boolean) {
  if (!config.value?.policy.receipt_correction_protocol) return;
  if (checked) {
    config.value.policy.receipt_correction_transport_options = { timeout_seconds: 60, max_retries: 0 };
  } else delete config.value.policy.receipt_correction_transport_options;
}
function chooseActionBinding(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) config.value.policy.action_binding_protocol = "case-action-binding-v1";
  else {
    delete config.value.policy.action_binding_protocol;
    delete config.value.policy.action_admission_protocol;
    delete config.value.policy.operation_protocol;
    delete config.value.policy.preparation_scope_protocol;
    delete config.value.policy.effect_evidence_protocol;
    delete config.value.policy.effect_evidence_max_bytes;
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function chooseActionAdmission(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    config.value.policy.action_binding_protocol = "case-action-binding-v1";
    config.value.policy.action_admission_protocol = "case-action-admission-v1";
  } else {
    delete config.value.policy.action_admission_protocol;
    delete config.value.policy.operation_protocol;
    delete config.value.policy.preparation_scope_protocol;
    delete config.value.policy.effect_evidence_protocol;
    delete config.value.policy.effect_evidence_max_bytes;
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function chooseOperationRecovery(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    config.value.policy.action_binding_protocol = "case-action-binding-v1";
    config.value.policy.action_admission_protocol = "case-action-admission-v1";
    config.value.policy.operation_protocol = "case-operation-v1";
  } else {
    delete config.value.policy.operation_protocol;
    delete config.value.policy.preparation_scope_protocol;
    delete config.value.policy.effect_evidence_protocol;
    delete config.value.policy.effect_evidence_max_bytes;
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function choosePreparationScopes(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    config.value.policy.action_binding_protocol = "case-action-binding-v1";
    config.value.policy.action_admission_protocol = "case-action-admission-v1";
    config.value.policy.operation_protocol = "case-operation-v1";
    config.value.policy.preparation_scope_protocol = "preparation-scope-v1";
  } else {
    delete config.value.policy.preparation_scope_protocol;
    delete config.value.policy.effect_evidence_protocol;
    delete config.value.policy.effect_evidence_max_bytes;
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function chooseEffectEvidence(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    config.value.policy.action_binding_protocol = "case-action-binding-v1";
    config.value.policy.action_admission_protocol = "case-action-admission-v1";
    config.value.policy.operation_protocol = "case-operation-v1";
    config.value.policy.preparation_scope_protocol = "preparation-scope-v1";
    config.value.policy.effect_evidence_protocol = "case-effect-evidence-v1";
  } else {
    delete config.value.policy.effect_evidence_protocol;
    delete config.value.policy.effect_evidence_max_bytes;
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function chooseEffectCoverage(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    config.value.policy.action_binding_protocol = "case-action-binding-v1";
    config.value.policy.action_admission_protocol = "case-action-admission-v1";
    config.value.policy.operation_protocol = "case-operation-v1";
    config.value.policy.preparation_scope_protocol = "preparation-scope-v1";
    config.value.policy.effect_evidence_protocol = "case-effect-evidence-v1";
    config.value.policy.effect_coverage_protocol = "case-effect-coverage-v1";
  } else {
    delete config.value.policy.effect_coverage_protocol;
    delete config.value.policy.effect_coverage_max_bytes;
    delete config.value.policy.planning_stage_protocol;
  }
}
function choosePlanningStages(checked: boolean) {
  if (!config.value) return;
  downgradeFormalEffects();
  if (checked) {
    chooseEffectCoverage(true);
    config.value.policy.planning_stage_protocol = "case-planning-staged-evidence-v1";
  } else delete config.value.policy.planning_stage_protocol;
}
function downgradeFormalEffects() {
  const policy = config.value?.policy;
  if (policy) { delete policy.consumption_protocol; delete policy.effect_expected_source_protocol; delete policy.operation_prefetch_protocol; delete policy.source_identity_protocol; delete policy.linked_source_protocol; delete policy.evidence_reference_protocol; delete policy.scene_query_protocol; delete policy.scene_receipt_protocol; }
  if (!policy?.planning_stage_protocol || !["case-planning-staged-evidence-v2", "case-planning-staged-evidence-v3"].includes(policy.planning_stage_protocol)) return;
  policy.preparation_scope_protocol = "preparation-scope-v1";
  policy.effect_evidence_protocol = "case-effect-evidence-v1";
  policy.effect_coverage_protocol = "case-effect-coverage-v1";
  policy.planning_stage_protocol = "case-planning-staged-evidence-v1";
}
function chooseFormalEffects(checked: boolean) {
  if (!config.value) return;
  if (!checked) { downgradeFormalEffects(); return; }
  config.value.policy.action_binding_protocol = "case-action-binding-v1";
  config.value.policy.action_admission_protocol = "case-action-admission-v1";
  config.value.policy.operation_protocol = "case-operation-v1";
  config.value.policy.preparation_scope_protocol = "preparation-scope-v2";
  config.value.policy.effect_evidence_protocol = "case-effect-evidence-v2";
  config.value.policy.effect_coverage_protocol = "case-effect-coverage-v2";
  if (config.value.policy.planning_stage_protocol !== "case-planning-staged-evidence-v3") config.value.policy.planning_stage_protocol = "case-planning-staged-evidence-v2";
}
function chooseDocumentExpected(checked: boolean) {
  if (!config.value) return;
  if (!checked) {
    downgradeEffectTasks();
    delete config.value.policy.effect_expected_source_protocol; delete config.value.policy.operation_prefetch_protocol;
    delete config.value.policy.source_identity_protocol; delete config.value.policy.linked_source_protocol; delete config.value.policy.evidence_reference_protocol; delete config.value.policy.scene_query_protocol; delete config.value.policy.scene_receipt_protocol; return;
  }
  chooseFormalEffects(true);
  config.value.policy.effect_expected_source_protocol = "case-effect-document-expected-v1";
}
function chooseConsumptionPhase(checked: boolean) {
  if (!config.value) return;
  if (!checked) { delete config.value.policy.consumption_protocol; return; }
  chooseFormalEffects(true);
  config.value.policy.consumption_protocol = "case-consumption-phase-v1";
}
function chooseOperationPrefetch(checked: boolean) {
  if (!config.value) return;
  if (!checked) { delete config.value.policy.operation_prefetch_protocol; return; }
  chooseDocumentExpected(true);
  config.value.policy.operation_prefetch_protocol = "case-planning-declared-operation-prefetch-v1";
}
function chooseSourceIdentity(checked: boolean) {
  if (!config.value) return;
  if (!checked) {
    downgradeEffectTasks();
    delete config.value.policy.evidence_reference_protocol;
    delete config.value.policy.linked_source_protocol;
    delete config.value.policy.source_identity_protocol;
    delete config.value.policy.scene_query_protocol;
    delete config.value.policy.scene_receipt_protocol;
    if (config.value.policy.operation_prefetch_protocol === "case-planning-item-operation-prefetch-v2") {
      config.value.policy.operation_prefetch_protocol = "case-planning-declared-operation-prefetch-v1";
    }
    return;
  }
  chooseDocumentExpected(true);
  config.value.policy.source_identity_protocol = "case-planning-source-full-hash-v1";
}
function chooseItemOperations(checked: boolean) {
  if (!config.value) return;
  if (!checked) {
    if (config.value.policy.operation_prefetch_protocol === "case-planning-item-operation-prefetch-v2") {
      config.value.policy.operation_prefetch_protocol = "case-planning-declared-operation-prefetch-v1";
    }
    return;
  }
  chooseSourceIdentity(true);
  config.value.policy.operation_prefetch_protocol = "case-planning-item-operation-prefetch-v2";
}
function chooseLinkedSources(checked: boolean) {
  if (!config.value) return;
  if (!checked) {
    delete config.value.policy.linked_source_protocol;
    if (!config.value.policy.scene_query_protocol) delete config.value.policy.scene_receipt_protocol;
    return;
  }
  chooseSourceIdentity(true);
  config.value.policy.linked_source_protocol = "case-planning-linked-sources-v2";
}
function chooseEvidenceReferences(checked: boolean) {
  if (!config.value) return;
  if (!checked) { downgradeEffectTasks(); delete config.value.policy.evidence_reference_protocol; return; }
  chooseSourceIdentity(true);
  config.value.policy.evidence_reference_protocol = config.value.policy.evidence_reference_protocol ?? "case-planning-evidence-references-v1";
}
function downgradeEffectTasks() {
  if (config.value?.policy.planning_stage_protocol === "case-planning-staged-evidence-v3") config.value.policy.planning_stage_protocol = "case-planning-staged-evidence-v2";
}
function chooseEffectTasks(checked: boolean) {
  if (!config.value) return;
  if (!checked) { downgradeEffectTasks(); return; }
  chooseEvidenceReferences(true);
  config.value.policy.planning_stage_protocol = "case-planning-staged-evidence-v3";
}
function chooseSceneQueries(checked: boolean) {
  if (!config.value) return;
  if (!checked) {
    delete config.value.policy.scene_query_protocol;
    if (config.value.policy.linked_source_protocol !== "case-planning-linked-sources-v2") delete config.value.policy.scene_receipt_protocol;
    return;
  }
  chooseSourceIdentity(true);
  config.value.policy.scene_query_protocol = "case-planning-scene-query-v1";
}
function chooseSceneReceipt(checked: boolean) {
  if (!config.value) return;
  if (!checked) { delete config.value.policy.scene_receipt_protocol; return; }
  chooseSceneQueries(true);
  config.value.policy.scene_receipt_protocol = "case-scene-receipt-v1";
}
async function openSaved() {
  if (!savedId.value.trim() || busy.value) return;
  busy.value = true; error.value = ""; notice.value = ""; plan.value = null;
  try { selectPlan(await api.getCasePlan(props.projectId, savedId.value.trim())); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
onMounted(loadPolicy);
</script>

<template>

<NModal
    v-model:show="show"
    preset="card"
    title="用例执行规划"
    style="width: min(1040px, 97vw)"
    :mask-closable="!busy"
    :close-on-esc="!busy"
    @update:show="!$event && close()"
  >
    <p class="dialog-hint">AI 根据测试目标选择项目内用例，并补足必要前置。生成计划只保存执行依据；启动后的进度与结果以运行详情为准。</p>

    <NAlert v-if="regression" type="info" :bordered="false" style="margin: 12px 0">
      原运行：{{ regression.parentRunId }} ·
      {{ regression.resultIds.length ? `已选 ${regression.resultIds.length} 条失败项` : '全部可回归失败项' }}。
      固定目标及版本后规划必要前置，原失败结果保留。
    </NAlert>
    <NAlert v-if="regression && regressionLocked && !plan" type="info" :bordered="false" style="margin-bottom: 12px">
      本次回归选择已固定，正在按原任务查询；实际版本以保存的计划为准。
    </NAlert>

    <section v-if="regression?.versionOptions?.length && (!regressionLocked || plan?.regression)" class="plan-section">
      <h3>回归用例版本</h3>
      <p class="dialog-hint">默认重测原失败版本。勾选后使用该用例当前已启用版本重新规划，未勾选项仍保留原版本；提交后本次选择固定。</p>
      <div class="policy-checks">
        <NCheckbox
          v-for="choice in regression.versionOptions"
          :key="choice.caseId"
          :checked="!!regressionVersions[choice.caseId]"
          :disabled="busy || regressionLocked || !choice.activeVersionId || choice.activeVersionId === choice.originalVersionId"
          @update:checked="(checked: boolean) => chooseRegressionVersion(choice.caseId, choice.activeVersionId, checked)"
        >
          {{ choice.title }} · 原版本 {{ choice.originalVersionId }} → {{ choice.activeVersionId || '暂无启用版本' }}
        </NCheckbox>
      </div>
    </section>

    <template v-if="!regression">
      <p class="dialog-hint">
        {{ candidateCaseIds.length ? `以已选的 ${candidateCaseIds.length} 条用例为初始候选，必要前置仍在当前项目内查询。` : '根据目标召回当前项目内的已启用用例。' }}
      </p>
      <NForm label-placement="top">
        <NFormItem label="测试目标" required>
          <NInput
            v-model:value="objective"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
            maxlength="20000"
            show-count
            :disabled="busy"
            placeholder="说明本次要验证的功能与约束"
          />
        </NFormItem>
        <NFormItem label="已有运行信息（JSON）">
          <NInput
            v-model:value="runtimeJson"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            :disabled="busy"
            placeholder='例如：{"order_id":"本次已有订单编号"}'
          />
        </NFormItem>
      </NForm>
      <div class="environment-selection">
        <NButton size="small" :disabled="busy" @click="environmentPickerOpen = true">选择固定测试环境快照（可选）</NButton>
        <p v-if="environmentSelection" class="dialog-hint">
          规划请求将固定环境 {{ environmentSelection.environment_id }}、快照 {{ environmentSelection.snapshot_id }} 及其哈希。
        </p>
        <p v-else class="dialog-hint">未选择时按现有项目规划方式执行。</p>
        <BusinessEnvironmentSnapshotPicker
          v-if="environmentPickerOpen"
          v-model="environmentSelection"
          :project-id="projectId"
          @close="environmentPickerOpen = false"
        />
      </div>
    </template>

    <NCollapse v-if="config" class="policy-collapse">
      <NCollapseItem title="规划设置" name="policy">
        <NForm label-placement="top" class="policy-form">
          <div class="policy-fields">
            <NFormItem label="最多查询回合">
              <NInputNumber v-model:value="config.policy.max_rounds" :min="1" :max="20" :disabled="busy" />
            </NFormItem>
            <NFormItem label="每次候选数量">
              <NInputNumber v-model:value="config.policy.max_candidates" :min="1" :max="50" :disabled="busy" />
            </NFormItem>
            <NFormItem label="最多执行项">
              <NInputNumber v-model:value="config.policy.max_items" :min="1" :max="200" :disabled="busy" />
            </NFormItem>
            <NFormItem label="上下文字符上限">
              <NInputNumber v-model:value="config.policy.max_context_chars" :min="10000" :max="1000000" :disabled="busy" />
            </NFormItem>
            <NFormItem label="超时秒数">
              <NInputNumber v-model:value="config.policy.timeout_seconds" :min="1" :max="600" :disabled="busy" />
            </NFormItem>
          </div>

          <div class="policy-checks">
            <NCheckbox data-testid="action-binding" :checked="config.policy.action_binding_protocol === 'case-action-binding-v1'" :disabled="busy" @update:checked="chooseActionBinding">
              记录工具所属执行项
            </NCheckbox>
            <NCheckbox data-testid="action-admission" :checked="config.policy.action_admission_protocol === 'case-action-admission-v1'" :disabled="busy" @update:checked="chooseActionAdmission">
              执行前核对当前失效确认
            </NCheckbox>
            <NCheckbox data-testid="operation-recovery" :checked="config.policy.operation_protocol === 'case-operation-v1'" :disabled="busy" @update:checked="chooseOperationRecovery">
              按逻辑操作恢复结果
            </NCheckbox>
            <NCheckbox data-testid="preparation-scopes" :checked="!!config.policy.preparation_scope_protocol" :disabled="busy" @update:checked="choosePreparationScopes">
              按准备消费范围安排清理
            </NCheckbox>
            <NCheckbox data-testid="effect-evidence" :checked="!!config.policy.effect_evidence_protocol" :disabled="busy" @update:checked="chooseEffectEvidence">
              保存实际对象证据
            </NCheckbox>
            <NCheckbox data-testid="formal-effects" :checked="['case-planning-staged-evidence-v2', 'case-planning-staged-evidence-v3'].includes(config.policy.planning_stage_protocol || '')" :disabled="busy" @update:checked="chooseFormalEffects">
              支持正式用例内的产生与清理
            </NCheckbox>
            <NCheckbox data-testid="document-expected" :checked="!!config.policy.effect_expected_source_protocol" :disabled="busy" @update:checked="chooseDocumentExpected">
              允许引用固定项目文档中的预期
            </NCheckbox>
            <NCheckbox data-testid="operation-prefetch" :checked="!!config.policy.operation_prefetch_protocol" :disabled="busy" @update:checked="chooseOperationPrefetch">
              自动读取计划已明确选择的工具操作说明
            </NCheckbox>
            <NCheckbox data-testid="source-identity" :checked="!!config.policy.source_identity_protocol" :disabled="busy" @update:checked="chooseSourceIdentity">
              使用统一的文档与录制引用
            </NCheckbox>
            <NCheckbox data-testid="item-operation-selections" :checked="config.policy.operation_prefetch_protocol === 'case-planning-item-operation-prefetch-v2'" :disabled="busy" @update:checked="chooseItemOperations">
              读取正式用例及准备项已选操作的接口
            </NCheckbox>
            <NCheckbox data-testid="linked-sources" :checked="!!config.policy.linked_source_protocol" :disabled="busy" @update:checked="chooseLinkedSources">
              规划前读取用例明确关联的文档与场景
            </NCheckbox>
            <NCheckbox data-testid="scene-queries" :checked="!!config.policy.scene_query_protocol" :disabled="busy" @update:checked="chooseSceneQueries">
              允许规划时查询项目场景
            </NCheckbox>
            <NCheckbox data-testid="scene-receipts" :checked="!!config.policy.scene_receipt_protocol" :disabled="busy" @update:checked="chooseSceneReceipt">
              允许按固定场景预期核对结果
            </NCheckbox>
            <NCheckbox data-testid="evidence-references" :checked="!!config.policy.evidence_reference_protocol" :disabled="busy" @update:checked="chooseEvidenceReferences">
              由系统还原证据引用
            </NCheckbox>
            <NCheckbox data-testid="effect-tasks" :checked="config.policy.planning_stage_protocol === 'case-planning-staged-evidence-v3'" :disabled="busy" @update:checked="chooseEffectTasks">
              分开补充传值、产生与清理证据
            </NCheckbox>
            <NCheckbox data-testid="effect-coverage" :checked="!!config.policy.effect_coverage_protocol" :disabled="busy" @update:checked="chooseEffectCoverage">
              核对原对象清理覆盖
            </NCheckbox>
            <NCheckbox data-testid="consumption-phase-policy" :checked="!!config.policy.consumption_protocol" :disabled="busy" @update:checked="chooseConsumptionPhase">
              清理前核验消费阶段结束
            </NCheckbox>
            <NCheckbox data-testid="planning-stages" :checked="!!config.policy.planning_stage_protocol" :disabled="busy" @update:checked="choosePlanningStages">
              分阶段生成执行计划
            </NCheckbox>
            <NCheckbox :checked="!!config.policy.receipt_correction_protocol" :disabled="busy" @update:checked="chooseReceiptCorrection">
              回执错误时只读纠正一次
            </NCheckbox>
            <NCheckbox v-if="config.policy.receipt_correction_protocol" data-testid="receipt-transport" :checked="!!config.policy.receipt_correction_transport_options" :disabled="busy" @update:checked="chooseReceiptTransport">
              单独设置回执调用超时和重试
            </NCheckbox>
          </div>

          <p v-if="config.policy.effect_expected_source_protocol" class="dialog-hint">保留原用例内容，预期引用已读取的文档版本和具体原文。影响后续计划。</p>
          <p v-if="config.policy.linked_source_protocol" class="dialog-hint">保留来源版本和读取范围。仅有文档编号的引用标为当前关联资料；场景必须有明确版本和哈希，缺失时显示缺口。</p>
          <p v-if="config.policy.linked_source_protocol === 'case-planning-linked-documents-v1'" class="dialog-hint">当前配置只读取关联文档。取消后重新勾选并保存，可同时读取明确关联的场景。</p>
          <p v-if="config.policy.scene_query_protocol" class="dialog-hint">按缺口查找场景，再读取固定版本的完整步骤、响应和元素别名；目录不代表已执行。</p>
          <p v-if="config.policy.scene_receipt_protocol" class="dialog-hint">后续运行可引用已读场景中保存的预期，与本次真实工具记录核对；仍须验证用例要求，场景可读不代表通过。</p>
          <p v-if="config.policy.planning_stage_protocol === 'case-planning-staged-evidence-v3'" class="dialog-hint">每步保存结果并共用原回合数和超时；全部校验后才能启动测试。</p>
          <p v-if="config.policy.effect_evidence_protocol" class="dialog-hint">保存声明路径中的实际返回与验证结果；可另启用清理覆盖核对。共享消费完成仍需验证。</p>
          <p v-if="config.policy.effect_coverage_protocol" class="dialog-hint">核对固定声明范围内的所有原对象及清理证据。未覆盖或来源未知时保留缺口，不代表共享消费已完成。</p>
          <p v-if="config.policy.consumption_protocol" class="dialog-hint">消费结束后继续清理；失败结果保留。证据未知时不发送清理，旧运行保持原策略。</p>
          <p v-if="config.policy.planning_stage_protocol" class="dialog-hint">先确定用例、必要前置和顺序，再补充执行证据；两阶段共用本次回合数和超时，全部校验后才可启动。</p>
          <p class="dialog-hint">只影响后续计划。明确失效时停止尚未领取的目标与前置动作；后置步骤保留，实际清理仍需核对已创建数据。</p>
          <p v-if="config.policy.receipt_correction_protocol" class="dialog-hint">使用原预算和已保存证据，不重复测试操作。设置仅影响新计划，历史运行保留原策略。</p>

          <div v-if="config.policy.receipt_correction_transport_options" class="policy-fields">
            <NFormItem label="回执调用超时（秒）">
              <NInputNumber data-testid="receipt-timeout" v-model:value="config.policy.receipt_correction_transport_options.timeout_seconds" :min="0.1" :max="600" :step="0.1" :disabled="busy" />
            </NFormItem>
            <NFormItem label="回执发送失败后的重试次数">
              <NInputNumber data-testid="receipt-retries" v-model:value="config.policy.receipt_correction_transport_options.max_retries" :min="0" :max="10" :step="1" :disabled="busy" />
            </NFormItem>
          </div>
          <p v-if="config.policy.receipt_correction_transport_options" class="dialog-hint">只读纠正最多进行一次。发送失败是否重试由这里的设置决定；0 表示只发送一次。未单独设置时沿用模型默认调用设置。</p>
          <div v-if="config.policy.effect_evidence_protocol" class="policy-fields">
            <NFormItem label="对象证据预算（字节）">
              <NInputNumber
                data-testid="effect-evidence-budget"
                :value="config?.policy.effect_evidence_max_bytes ?? null"
                :min="1"
                :max="10000000"
                :disabled="busy"
                placeholder="512000"
                @update:value="(value: number | null) => { if (config) config.policy.effect_evidence_max_bytes = value ?? undefined; }"
              />
            </NFormItem>
          </div>
          <div v-if="config.policy.effect_coverage_protocol" class="policy-fields">
            <NFormItem label="覆盖证据预算（字节）">
              <NInputNumber
                data-testid="effect-coverage-budget"
                :value="config?.policy.effect_coverage_max_bytes ?? null"
                :min="1"
                :max="10000000"
                :disabled="busy"
                placeholder="1024000"
                @update:value="(value: number | null) => { if (config) config.policy.effect_coverage_max_bytes = value ?? undefined; }"
              />
            </NFormItem>
          </div>
        </NForm>
        <NButton size="small" :disabled="busy" @click="remember">保存项目规划设置</NButton>
      </NCollapseItem>
    </NCollapse>

    <NAlert v-if="error" type="error" :bordered="false" style="margin-top: 12px">{{ error }}</NAlert>
    <NAlert v-if="notice" type="success" :bordered="false" style="margin-top: 12px">{{ notice }}</NAlert>

    <CasePlanningTaskPanel
      :project-id="projectId"
      :objective="objective"
      :runtime-json="runtimeJson"
      :environment="environmentSelection"
      :candidate-case-ids="candidateCaseIds"
      :initial-task-id="initialTaskId"
      :regression="regressionRequest"
      :disabled="busy || !config"
      @submitted="regressionLocked = true"
      @selection-required="regressionLocked = false"
      @completed="selectPlan"
    />

    <div v-if="!regression" class="dialog-actions-left plan-saved">
      <NFormItem label="已保存的计划编号" :show-feedback="false" label-placement="left">
        <NInput v-model:value="savedId" :disabled="busy" placeholder="输入当前项目的计划编号" style="width: 320px" />
      </NFormItem>
      <NButton size="small" :disabled="busy || !savedId.trim()" @click="openSaved">查看已保存计划</NButton>
    </div>

    <template v-if="plan">
      <h3 class="plan-title">{{ statusLabels[plan.status] }}</h3>
      <p class="dialog-hint">
        {{ plan.status === "no_match" ? "未找到符合本次目标的正式用例；本次未创建测试运行。" : plan.summary }}
      </p>
      <section v-if="plan.environment" class="fixed-environment">
        <h3>计划固定的目标环境</h3>
        <p>{{ plan.environment.snapshot.configuration.payload.display_name }} · {{ plan.environment.snapshot.configuration.payload.base_url }}</p>
        <p>快照 {{ plan.environment.snapshot.id }} · 配置修订 {{ plan.environment.snapshot.configuration.revision }} · 部署状态未知</p>
      </section>
      <NAlert v-if="plan.regression" type="info" :bordered="false" style="margin-bottom: 12px">
        回归来源：{{ plan.regression.parent_run_id }} · {{ Object.keys(plan.regression.source_results_by_case).length }} 条固定失败项。
        新运行结果关联原失败，准备成功不计为目标通过。
      </NAlert>

      <section v-if="canLaunch && !candidateOnly" class="launch-controls">
        <p class="dialog-hint">启动时使用这份计划中的固定用例版本和必要前置。运行保存后由 Worker 领取，保存成功不代表测试通过。</p>
        <NForm label-placement="top">
          <NFormItem label="执行测试模式">
            <NSelect
              :value="modeKey"
              :options="[...(pendingRequest || plan.regression) && !testModes.some(mode => mode.key === modeKey) ? [{ label: `${modeKey}（已保存的选项）`, value: modeKey }] : [], ...testModeOptions]"
              :disabled="busy || !!pendingRequest || !!plan.regression"
              style="width: 260px"
              @update:value="(value: string) => { modeKey = value; }"
            />
          </NFormItem>
          <NFormItem label="关联会话编号（可选）">
            <NInput
              v-model:value="sessionId"
              :disabled="busy || !!pendingRequest || !!plan.regression"
              maxlength="160"
              placeholder="会话必须属于当前项目"
              style="width: 320px"
            />
          </NFormItem>
        </NForm>
        <p v-if="pendingRequest" class="dialog-hint">启动请求编号：{{ pendingRequest.creation_key }}</p>
        <NAlert v-if="storageWarning" type="warning" :bordered="false" style="margin-bottom: 12px">{{ storageWarning }}</NAlert>
        <div v-if="knownRunId" class="dialog-actions-left">
          <span class="dialog-hint">已保存运行：{{ knownRunId }}</span>
          <NButton size="small" :disabled="busy" @click="viewRun">查看运行</NButton>
          <NButton size="small" :disabled="busy" @click="newRun">另建一次运行</NButton>
        </div>
        <NButton v-else type="primary" :loading="busy" :disabled="busy || (!pendingRequest && !modeKey)" @click="launch">
          {{ pendingRequest ? '重试启动请求' : '启动测试运行' }}
        </NButton>
      </section>

      <section v-if="canLaunch && candidateOnly" class="launch-controls">
        <p class="dialog-hint">候选计划已固定到项目和用例版本。请检查上方执行项、依赖和缺口，再选择是否用于当前 Run 修订。</p>
        <p class="dialog-hint">候选计划编号：{{ plan.id }}</p>
        <NButton type="primary" :disabled="busy" @click="emit('candidate', plan)">将此计划用于当前 Run 修订</NButton>
      </section>

      <NAlert v-if="plan.warnings.length" type="warning" :bordered="false" style="margin-bottom: 12px">
        <ul class="issue-list"><li v-for="(warning, index) in plan.warnings" :key="index">{{ warning }}</li></ul>
      </NAlert>

      <NCollapse style="margin-bottom: 12px" :default-expanded-names="['operations', 'linked']">
        <NCollapseItem v-if="operationSelections.length" title="逻辑阶段选定的工具接口" name="operations">
          <JsonContentTree :value="operationSelections" />
        </NCollapseItem>
        <NCollapseItem v-if="linkedSourceReads" title="用例关联来源的固定读取记录" name="linked">
          <JsonContentTree :value="linkedSourceReads" />
        </NCollapseItem>
      </NCollapse>

      <NAlert v-if="plan.decision?.unresolved.length" type="warning" :bordered="false" style="margin-bottom: 12px">
        <ul class="issue-list">
          <li v-for="(gap, index) in plan.decision.unresolved" :key="index">
            {{ gap.item_key ? `${gap.item_key}：` : '' }}{{ gap.condition }} · {{ gap.reason }}
          </li>
        </ul>
      </NAlert>

      <ol class="plan-items">
        <li v-for="item in plan.decision?.items" :key="item.key">
          <h4>{{ kindLabels[item.kind] }} · {{ title(item) }}</h4>
          <p>{{ item.reason }}</p>
          <p v-if="item.depends_on.length">等待前置：{{ item.depends_on.join('、') }}</p>
          <p v-if="evidence(item)?.payload.execution_notes">{{ evidence(item)?.payload.execution_notes }}</p>
          <NCollapse :default-expanded-names="(plan.decision?.items ?? []).flatMap(item => [`content-${item.key}`, `bindings-${item.key}`, `effect-${item.key}`, `declarations-${item.key}`])">
            <NCollapseItem :title="item.kind === 'prepare' ? '准备操作与验证点' : '完整固定用例'" :name="`content-${item.key}`">
              <JsonContentTree :value="content(item)" />
            </NCollapseItem>
            <NCollapseItem v-if="Object.keys(item.input_bindings).length" title="数据来源" :name="`bindings-${item.key}`">
              <JsonContentTree :value="item.input_bindings" />
            </NCollapseItem>
            <NCollapseItem v-if="item.effect_evidence" title="对象证据声明" :name="`effect-${item.key}`">
              <JsonContentTree :value="item.effect_evidence" />
            </NCollapseItem>
            <NCollapseItem v-if="item.effect_declarations" title="产生与清理证据声明" :name="`declarations-${item.key}`">
              <JsonContentTree :value="item.effect_declarations" />
            </NCollapseItem>
          </NCollapse>
          <p>依据：{{ item.evidence_ids.join('、') }}</p>
          <p v-if="item.cleanup_sources">清理来源：{{ item.cleanup_sources.join('、') }}。这是计划范围，清理结果需实际证据核验。</p>
        </li>
      </ol>

      <NCollapse v-if="plan.evidence.some(e => e.kind !== 'case')" style="margin-top: 12px" :default-expanded-names="['evidence']">
        <NCollapseItem title="查询到的项目原文与录制依据" name="evidence">
          <template v-for="source in plan.evidence.filter(e => e.kind !== 'case')" :key="source.id">
            <p class="dialog-hint">{{ source.id }}</p>
            <JsonContentTree :value="source.payload" />
          </template>
        </NCollapseItem>
      </NCollapse>
    </template>

    <template #footer>
      <div class="dialog-actions">
        <NButton :disabled="busy" @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
@import "./caseDialogs.css";
.policy-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0 16px; }
.policy-checks { display: flex; flex-direction: column; gap: 8px; margin: 8px 0 12px; }
.policy-collapse { margin: 16px 0; }
.policy-form { margin-top: 8px; }
.plan-section { margin: 12px 0; }
.plan-title { margin-top: 16px; }
.plan-saved { margin: 16px 0; }
.issue-list { margin: 0; padding-left: 18px; }
.plan-items { padding-left: 24px; }
.plan-items > li { border-top: 1px solid #d8dee8; margin-top: 16px; padding-top: 8px; }
.plan-items h4 { margin: 8px 0; }
.gaps { color: #95501f; }
.environment-selection, .fixed-environment, .launch-controls { border: 1px solid var(--border); border-radius: 8px; margin: 12px 0; padding: 12px; }
</style>
