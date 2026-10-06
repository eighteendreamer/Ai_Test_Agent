<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import type { CasePlanItem, CasePlanRecord, CasePlanRunRequest, CasePlanningConfig, ModeDescriptor, TestRunDetail, RegressionPlanningSelection } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";
import CasePlanningTaskPanel from "./CasePlanningTaskPanel.vue";

const props = withDefaults(defineProps<{ projectId: string; candidateCaseIds: string[]; modes?: ModeDescriptor[]; initialTaskId?: string; regression?: RegressionPlanningSelection }>(), { modes: () => [] });
const emit = defineEmits<{ close: []; started: [detail: TestRunDetail] }>();
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
const regressionRequest = computed(() => props.regression ? { ...props.regression, versionOverrides: { ...regressionVersions.value } } : undefined);
function chooseRegressionVersion(caseId: string, versionId: string | null, selected: boolean) {
  if (regressionLocked.value) return;
  if (selected && versionId) regressionVersions.value[caseId] = versionId;
  else delete regressionVersions.value[caseId];
}
const testModes = computed(() => props.modes.filter(mode => mode.is_test_mode));
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
function chooseActionBinding(event: Event) {
  if (!config.value) return;
  if ((event.target as HTMLInputElement).checked) config.value.policy.action_binding_protocol = "case-action-binding-v1";
  else delete config.value.policy.action_binding_protocol;
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
  <div class="case-dialog-backdrop" @click.self="!busy && emit('close')">
    <section class="case-dialog" role="dialog" aria-modal="true" aria-label="用例执行规划">
      <header><h2>用例执行规划</h2><button :disabled="busy" @click="emit('close')">关闭</button></header>
      <p>AI 根据测试目标选择项目内用例，并补足必要前置。生成计划只保存执行依据；启动后的进度与结果以运行详情为准。</p>
      <p v-if="regression">原运行：{{ regression.parentRunId }} · {{ regression.resultIds.length ? `已选 ${regression.resultIds.length} 条失败项` : '全部可回归失败项' }}。固定目标及版本后规划必要前置，原失败结果保留。</p>
      <p v-if="regression && regressionLocked && !plan">本次回归选择已固定，正在按原任务查询；实际版本以保存的计划为准。</p>
      <fieldset v-if="regression?.versionOptions?.length && (!regressionLocked || plan?.regression)"><legend>回归用例版本</legend>
        <p>默认重测原失败版本。勾选后使用该用例当前已启用版本重新规划，未勾选项仍保留原版本；提交后本次选择固定。</p>
        <label v-for="choice in regression.versionOptions" :key="choice.caseId">
          <input type="checkbox" :checked="!!regressionVersions[choice.caseId]" :disabled="busy || regressionLocked || !choice.activeVersionId || choice.activeVersionId === choice.originalVersionId"
            @change="chooseRegressionVersion(choice.caseId, choice.activeVersionId, ($event.target as HTMLInputElement).checked)">
          {{ choice.title }} · 原版本 {{ choice.originalVersionId }} → {{ choice.activeVersionId || '暂无启用版本' }}
        </label>
      </fieldset>
      <template v-if="!regression">
        <p>{{ candidateCaseIds.length ? `以已选的 ${candidateCaseIds.length} 条用例为初始候选，必要前置仍在当前项目内查询。` : '根据目标召回当前项目内的已启用用例。' }}</p>
        <label>测试目标<textarea v-model="objective" :disabled="busy" maxlength="20000" placeholder="说明本次要验证的功能与约束" /></label>
        <label>已有运行信息（JSON）<textarea v-model="runtimeJson" :disabled="busy" placeholder='例如：{"order_id":"本次已有订单编号"}' /></label>
      </template>
      <details v-if="config"><summary>规划设置</summary><div class="policy-fields">
        <label>最多查询回合<input v-model.number="config.policy.max_rounds" :disabled="busy" type="number" min="1" max="20"></label>
        <label>每次候选数量<input v-model.number="config.policy.max_candidates" :disabled="busy" type="number" min="1" max="50"></label>
        <label>最多执行项<input v-model.number="config.policy.max_items" :disabled="busy" type="number" min="1" max="200"></label>
        <label>上下文字符上限<input v-model.number="config.policy.max_context_chars" :disabled="busy" type="number" min="10000" max="1000000"></label>
        <label>超时秒数<input v-model.number="config.policy.timeout_seconds" :disabled="busy" type="number" min="1" max="600"></label>
        <label><input data-testid="action-binding" type="checkbox" :disabled="busy"
          :checked="config.policy.action_binding_protocol === 'case-action-binding-v1'" @change="chooseActionBinding">记录工具所属执行项</label>
        <p>只影响后续计划。记录动作归属，执行中的适用性变化限制仍待接入。</p>
      </div><button :disabled="busy" @click="remember">保存项目规划设置</button></details>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <CasePlanningTaskPanel :project-id="projectId" :objective="objective" :runtime-json="runtimeJson"
        :candidate-case-ids="candidateCaseIds" :initial-task-id="initialTaskId" :regression="regressionRequest" :disabled="busy || !config" @submitted="regressionLocked = true" @selection-required="regressionLocked = false" @completed="selectPlan" />
      <template v-if="!regression"><label>已保存的计划编号<input v-model="savedId" :disabled="busy" placeholder="输入当前项目的计划编号"></label>
        <button :disabled="busy || !savedId.trim()" @click="openSaved">查看已保存计划</button>
      </template>
      <template v-if="plan">
        <h3>{{ statusLabels[plan.status] }}</h3><p>{{ plan.summary }}</p>
        <p v-if="plan.regression">回归来源：{{ plan.regression.parent_run_id }} · {{ Object.keys(plan.regression.source_results_by_case).length }} 条固定失败项。新运行结果关联原失败，准备成功不计为目标通过。</p>
        <section v-if="canLaunch" class="launch-controls" aria-label="启动已保存计划">
          <p>启动时使用这份计划中的固定用例版本和必要前置。运行保存后由 Worker 领取，保存成功不代表测试通过。</p>
          <label>执行测试模式<select v-model="modeKey" :disabled="busy || !!pendingRequest || !!plan.regression">
            <option v-if="(pendingRequest || plan.regression) && !testModes.some(mode => mode.key === modeKey)" :value="modeKey">{{ modeKey }}（已保存的选项）</option>
            <option v-for="mode in testModes" :key="mode.key" :value="mode.key">{{ mode.name }}</option>
          </select></label>
          <label>关联会话编号（可选）<input v-model="sessionId" :disabled="busy || !!pendingRequest || !!plan.regression" maxlength="160" placeholder="会话必须属于当前项目"></label>
          <p v-if="pendingRequest">启动请求编号：{{ pendingRequest.creation_key }}</p>
          <p v-if="storageWarning" role="status">{{ storageWarning }}</p>
          <template v-if="knownRunId"><p>已保存运行：{{ knownRunId }}</p><button :disabled="busy" @click="viewRun">查看运行</button><button :disabled="busy" @click="newRun">另建一次运行</button></template>
          <button v-else :disabled="busy || (!pendingRequest && !modeKey)" @click="launch">{{ pendingRequest ? '重试启动请求' : '启动测试运行' }}</button>
        </section>
        <ul v-if="plan.warnings.length"><li v-for="(warning, index) in plan.warnings" :key="index">{{ warning }}</li></ul>
        <ul v-if="plan.decision?.unresolved.length" class="gaps"><li v-for="(gap, index) in plan.decision.unresolved" :key="index">{{ gap.item_key ? `${gap.item_key}：` : '' }}{{ gap.condition }} · {{ gap.reason }}</li></ul>
        <ol class="plan-items"><li v-for="item in plan.decision?.items" :key="item.key">
          <h4>{{ kindLabels[item.kind] }} · {{ title(item) }}</h4><p>{{ item.reason }}</p>
          <p v-if="item.depends_on.length">等待前置：{{ item.depends_on.join('、') }}</p>
          <p v-if="evidence(item)?.payload.execution_notes">{{ evidence(item)?.payload.execution_notes }}</p>
          <details><summary>{{ item.kind === 'prepare' ? '准备操作与验证点' : '完整固定用例' }}</summary><JsonContentTree :value="content(item)" /></details>
          <details v-if="Object.keys(item.input_bindings).length"><summary>数据来源</summary><JsonContentTree :value="item.input_bindings" /></details>
          <p>依据：{{ item.evidence_ids.join('、') }}</p>
        </li></ol>
        <details v-if="plan.evidence.some(e => e.kind !== 'case')"><summary>查询到的项目原文与录制依据</summary><template v-for="source in plan.evidence.filter(e => e.kind !== 'case')" :key="source.id"><p>{{ source.id }}</p><JsonContentTree :value="source.payload" /></template></details>
      </template>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
.policy-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0 16px; }
.plan-items { padding-left: 24px; }
.plan-items > li { border-top: 1px solid #d8dee8; margin-top: 16px; padding-top: 8px; }
.plan-items h4 { margin: 8px 0; }
.gaps { color: #95501f; }
</style>
