<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentImpactPolicy, DocumentImpactAnalysisPolicy, DocumentImpactBatchPolicy,
  DocumentImpactInvocationPolicy, DocumentImpactReasoningPolicy, DocumentImpactSettings } from "../../../services/documentImpacts.types";

const props = defineProps<{ projectId: string; canWrite: boolean }>();
const policy = ref<DocumentImpactSettings<DocumentImpactPolicy> | null>(null);
const analysis = ref<DocumentImpactSettings<DocumentImpactAnalysisPolicy> | null>(null);
const batch = ref<DocumentImpactSettings<DocumentImpactBatchPolicy> | null>(null);
const invocation = ref<DocumentImpactSettings<DocumentImpactInvocationPolicy> | null>(null);
const reasoning = ref<DocumentImpactSettings<DocumentImpactReasoningPolicy> | null>(null);
type Kind = "policy" | "analysis" | "batch" | "invocation" | "reasoning";
const busy = ref(false);
const error = ref("");
const notice = ref("");
let epoch = 0;
let disposed = false;
const policyFields: { key: keyof DocumentImpactPolicy; label: string; max: number; min: number; step?: string }[] = [
  { key: "max_case_versions", label: "用例版本数量上限", min: 1, max: 10000 },
  { key: "max_context_chars", label: "固定依据字符上限", min: 1, max: 50000000 },
  { key: "timeout_seconds", label: "任务总超时（秒）", min: 0.001, max: 600, step: "any" },
];
const batchFields: { key: keyof DocumentImpactBatchPolicy; label: string; min: number; max: number }[] = [
  { key: "max_segment_chars", label: "单段字符上限", min: 1, max: 200000 },
  { key: "max_segments", label: "分段数量上限", min: 1, max: 100000 },
  { key: "boundary_context_chars", label: "边界上下文字符", min: 0, max: 20000 },
  { key: "max_retrieval_bytes", label: "单次补读字节上限", min: 1, max: 10000000 },
];
type NumericInvocation = Exclude<keyof DocumentImpactInvocationPolicy, "token_method" | "encoding_name" | "transport_policy">;
const invocationFields: { key: NumericInvocation; label: string; min: number; max: number; step?: string }[] = [
  { key: "max_logical_calls", label: "逻辑调用数量上限", min: 1, max: 10000 },
  { key: "max_reserved_estimated_tokens", label: "累计预留估算量上限", min: 1, max: 100000000 },
  { key: "call_timeout_seconds", label: "单次调用超时（秒）", min: 0.001, max: 600, step: "any" },
  { key: "max_response_bytes", label: "响应字节上限", min: 1, max: 100000000 },
  { key: "framing_allowance_tokens", label: "消息封装预留估算量", min: 0, max: 100000 },
];
async function load() {
  const generation = ++epoch; const projectId = props.projectId; busy.value = true; error.value = ""; notice.value = "";
  const values = await Promise.allSettled([api.getDocumentImpactSettings(projectId), api.getDocumentImpactAnalysisSettings(projectId),
    api.getDocumentImpactBatchSettings(projectId), api.getDocumentImpactInvocationSettings(projectId),
    api.getDocumentImpactReasoningSettings(projectId)]);
  if (disposed || generation !== epoch || projectId !== props.projectId) return;
  const errors: string[] = [];
  values.forEach(value => {
    if (value.status === "rejected") errors.push(value.reason instanceof Error ? value.reason.message : "影响设置读取失败");
    else if (![projectId, "global"].includes(value.value.scope_key)) errors.push("影响设置返回的项目身份不一致");
  });
  const valid = <T,>(result: PromiseSettledResult<DocumentImpactSettings<T>>) => result.status === "fulfilled"
    && [projectId, "global"].includes(result.value.scope_key) ? result.value : null;
  policy.value = valid(values[0]); analysis.value = valid(values[1]); batch.value = valid(values[2]); invocation.value = valid(values[3]);
  if (invocation.value) invocation.value.payload.transport_policy ??= "provider_defaults";
  reasoning.value = valid(values[4]);
  error.value = errors.join("；"); busy.value = false;
}
async function save(kind: Kind) {
  if (busy.value || !props.canWrite) return;
  const generation = epoch; const projectId = props.projectId;
  const current = () => !disposed && generation === epoch && projectId === props.projectId;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
    const check = <T,>(record: DocumentImpactSettings<T>) => {
      if (record.scope_key !== projectId) throw new Error("影响设置返回的项目身份不一致");
      return record;
    };
    if (kind === "policy" && policy.value) {
      const value = await api.saveDocumentImpactSettings(projectId, clone(policy.value.payload));
      if (current()) policy.value = check(value);
    } else if (kind === "analysis" && analysis.value) {
      const value = await api.saveDocumentImpactAnalysisSettings(projectId, clone(analysis.value.payload));
      if (current()) analysis.value = check(value);
    } else if (kind === "batch" && batch.value) {
      const value = await api.saveDocumentImpactBatchSettings(projectId, clone(batch.value.payload));
      if (current()) batch.value = check(value);
    } else if (kind === "invocation" && invocation.value) {
      const value = await api.saveDocumentImpactInvocationSettings(projectId, clone(invocation.value.payload));
      if (current()) invocation.value = check(value);
    } else if (kind === "reasoning" && reasoning.value) {
      const value = await api.saveDocumentImpactReasoningSettings(projectId, clone(reasoning.value.payload));
      if (current()) reasoning.value = check(value);
    } else throw new Error("请先读取对应影响设置");
    if (current()) notice.value = "项目影响设置已保存；新任务冻结提交时的实际配置，历史任务保持原依据。";
  } catch (err) { if (current()) error.value = err instanceof Error ? err.message : "影响设置保存失败"; }
  finally { if (current()) busy.value = false; }
}
function changeMethod() {
  if (invocation.value) invocation.value.payload.encoding_name = invocation.value.payload.token_method === "tiktoken_content" ? "cl100k_base" : null;
}
watch(() => props.projectId, () => {
  policy.value = null; analysis.value = null; batch.value = null; invocation.value = null; reasoning.value = null; void load();
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; });
</script>

<template>
  <details class="document-impact-settings"><summary>项目影响分析设置</summary>
    <button class="secondary-btn" :disabled="busy" @click="load">重新读取影响设置</button>
    <p>设置保存到项目配置；环境与目标发布版本仅为声明，生效情况仍需证据确认。</p>
    <form v-if="policy" aria-label="影响范围设置" @submit.prevent="save('policy')">
      <p>范围策略 · {{ policy.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ policy.revision }}</p>
      <fieldset :disabled="busy || !canWrite"><div class="settings-grid"><label v-for="field in policyFields" :key="field.key">{{ field.label }}
        <input v-model.number="policy.payload[field.key]" :aria-label="field.label" type="number" :min="field.min" :max="field.max" :step="field.step || 1" required>
      </label></div><button v-if="canWrite" class="secondary-btn" type="submit">保存影响范围设置</button></fieldset>
    </form>
    <form v-if="analysis" aria-label="影响模型指导设置" @submit.prevent="save('analysis')">
      <p>模型指导 · {{ analysis.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ analysis.revision }}</p>
      <fieldset :disabled="busy || !canWrite"><div class="settings-grid">
        <label>默认模型配置编号（留空使用系统默认）<input :value="analysis.payload.model_key || ''" aria-label="影响默认模型配置编号"
          maxlength="255" @input="analysis.payload.model_key = ($event.target as HTMLInputElement).value.trim() || null"></label>
        <label>模型输入字节上限<input v-model.number="analysis.payload.max_input_bytes" aria-label="模型输入字节上限" type="number" min="1" max="10000000" step="1" required></label>
      </div><label>分析指导<textarea v-model="analysis.payload.analysis_guidance" aria-label="影响分析指导" maxlength="20000" rows="4" /></label>
      <button v-if="canWrite" class="secondary-btn" type="submit">保存影响模型指导</button></fieldset>
    </form>
    <form v-if="batch" aria-label="影响分段设置" @submit.prevent="save('batch')">
      <p>分段与补读 · {{ batch.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ batch.revision }}</p>
      <fieldset :disabled="busy || !canWrite"><div class="settings-grid"><label v-for="field in batchFields" :key="field.key">{{ field.label }}
        <input v-model.number="batch.payload[field.key]" :aria-label="field.label" type="number" :min="field.min" :max="field.max" step="1" required>
      </label></div><button v-if="canWrite" class="secondary-btn" type="submit">保存影响分段设置</button></fieldset>
    </form>
    <form v-if="reasoning" aria-label="影响推理分组设置" @submit.prevent="save('reasoning')">
      <p>证据分组 · {{ reasoning.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ reasoning.revision }}</p>
      <fieldset :disabled="busy || !canWrite"><div class="settings-grid">
        <label>每组证据数量上限<input v-model.number="reasoning.payload.max_group_units" aria-label="每组证据数量上限" type="number" min="1" max="100" step="1" required></label>
        <label>每组完整读取字节上限<input v-model.number="reasoning.payload.max_group_read_bytes" aria-label="每组完整读取字节上限" type="number" min="1" max="10000000" step="1" required></label>
      </div><p>超限证据保留并显示原因；实际模型请求仍受调用预算约束。</p>
      <button v-if="canWrite" class="secondary-btn" type="submit">保存影响推理分组设置</button></fieldset>
    </form>
    <form v-if="invocation" aria-label="影响调用预算设置" @submit.prevent="save('invocation')">
      <p>调用预算 · {{ invocation.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ invocation.revision }}</p>
      <fieldset :disabled="busy || !canWrite"><div class="settings-grid"><label v-for="field in invocationFields" :key="field.key">{{ field.label }}
        <input v-model.number="invocation.payload[field.key]" :aria-label="field.label" type="number" :min="field.min" :max="field.max" :step="field.step || 1" required>
      </label></div><label>请求发送策略<select v-model="invocation.payload.transport_policy" aria-label="影响请求发送策略">
        <option value="provider_defaults">沿用提供商重试</option><option value="single_attempt">单次发送</option>
      </select></label><p>单次发送使用本次调用期限等待响应，关闭 SDK 重试；超时结果保留为未知。</p>
      <label>内容计量<select v-model="invocation.payload.token_method" aria-label="影响内容计量" @change="changeMethod">
        <option value="utf8_bytes_estimate">UTF-8字节估算</option><option value="tiktoken_content">指定编码的内容计数</option>
      </select></label><label v-if="invocation.payload.token_method === 'tiktoken_content'">编码<select v-model="invocation.payload.encoding_name" aria-label="影响计量编码">
        <option value="cl100k_base">cl100k_base</option><option value="o200k_base">o200k_base</option>
      </select></label><p>消息总输入仍为估算；逻辑调用数量不等于SDK或HTTP发送次数。</p>
      <button v-if="canWrite" class="secondary-btn" type="submit">保存影响调用预算</button></fieldset>
    </form>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </details>
</template>

<style scoped>
.document-impact-settings { margin: 12px 0; }
fieldset { border: 0; padding: 0; }
form { margin: 16px 0; border-top: 1px solid var(--border); padding-top: 8px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 12px 0; }
label { display: flex; flex-direction: column; gap: 4px; }
textarea { width: 100%; box-sizing: border-box; }
[role="alert"] { color: var(--red); }
</style>
