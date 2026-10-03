<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../services/api";
import type { CasePlanItem, CasePlanRecord, CasePlanningConfig } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";

const props = defineProps<{ projectId: string; candidateCaseIds: string[] }>();
const emit = defineEmits<{ close: [] }>();
const objective = ref("");
const runtimeJson = ref("{}");
const config = ref<CasePlanningConfig | null>(null);
const plan = ref<CasePlanRecord | null>(null);
const savedId = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");
const statusLabels: Record<CasePlanRecord["status"], string> = { planned: "计划已生成", no_match: "未找到目标用例", blocked: "仍有执行缺口", retrieval_failed: "用例检索失败", planning_failed: "规划失败" };
const kindLabels = { target: "目标用例", dependency: "必要前置用例", prepare: "运行内准备" };

function showError(err: unknown) { error.value = err instanceof Error ? err.message : "用例规划操作失败"; }
function evidence(item: CasePlanItem) { return plan.value?.evidence.find(e => e.kind === "case" && e.version === item.case_version_id); }
function title(item: CasePlanItem) { const value = evidence(item)?.payload.title; return typeof value === "string" ? value : item.key; }
function content(item: CasePlanItem) { return item.kind === "prepare" ? item.instructions : evidence(item)?.payload.content; }
async function loadPolicy() {
  busy.value = true;
  try { config.value = await api.getCasePlanningPolicy(props.projectId); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function generate() {
  if (!objective.value.trim() || busy.value) return;
  error.value = ""; notice.value = ""; plan.value = null;
  let context: unknown;
  try {
    context = JSON.parse(runtimeJson.value);
    if (!context || typeof context !== "object" || Array.isArray(context)) throw new Error("已有运行信息需要是 JSON 对象");
  } catch (err) { showError(err); return; }
  busy.value = true;
  try {
    plan.value = await api.createCasePlan(props.projectId, { objective: objective.value.trim(),
      candidate_case_ids: [...props.candidateCaseIds], runtime_context: context as Record<string, unknown> });
    savedId.value = plan.value.id;
  } catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function remember() {
  if (!config.value || busy.value) return;
  busy.value = true; error.value = "";
  try { config.value = await api.saveCasePlanningPolicy(props.projectId, config.value.policy); notice.value = "项目规划设置已保存"; }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function openSaved() {
  if (!savedId.value.trim() || busy.value) return;
  busy.value = true; error.value = ""; plan.value = null;
  try { plan.value = await api.getCasePlan(props.projectId, savedId.value.trim()); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
onMounted(loadPolicy);
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="!busy && emit('close')">
    <section class="case-dialog" role="dialog" aria-modal="true" aria-label="用例执行规划">
      <header><h2>用例执行规划</h2><button :disabled="busy" @click="emit('close')">关闭</button></header>
      <p>AI 根据测试目标选择项目内用例，并补足必要前置。本次生成并保存计划，测试尚未开始。</p>
      <p>{{ candidateCaseIds.length ? `以已选的 ${candidateCaseIds.length} 条用例为初始候选，必要前置仍在当前项目内查询。` : '根据目标召回当前项目内的已启用用例。' }}</p>
      <label>测试目标<textarea v-model="objective" :disabled="busy" maxlength="20000" placeholder="说明本次要验证的功能与约束" /></label>
      <label>已有运行信息（JSON）<textarea v-model="runtimeJson" :disabled="busy" placeholder='例如：{"order_id":"本次已有订单编号"}' /></label>
      <details v-if="config"><summary>规划设置</summary><div class="policy-fields">
        <label>最多查询回合<input v-model.number="config.policy.max_rounds" :disabled="busy" type="number" min="1" max="20"></label>
        <label>每次候选数量<input v-model.number="config.policy.max_candidates" :disabled="busy" type="number" min="1" max="50"></label>
        <label>最多执行项<input v-model.number="config.policy.max_items" :disabled="busy" type="number" min="1" max="200"></label>
        <label>上下文字符上限<input v-model.number="config.policy.max_context_chars" :disabled="busy" type="number" min="10000" max="1000000"></label>
        <label>超时秒数<input v-model.number="config.policy.timeout_seconds" :disabled="busy" type="number" min="1" max="600"></label>
      </div><button :disabled="busy" @click="remember">保存项目规划设置</button></details>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <footer><button :disabled="busy || !config || !objective.trim()" @click="generate">{{ busy ? '处理中…' : '生成执行计划' }}</button></footer>
      <label>已保存的计划编号<input v-model="savedId" :disabled="busy" placeholder="输入当前项目的计划编号"></label>
      <button :disabled="busy || !savedId.trim()" @click="openSaved">查看已保存计划</button>
      <template v-if="plan">
        <h3>{{ statusLabels[plan.status] }}</h3><p>{{ plan.summary }}</p>
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
