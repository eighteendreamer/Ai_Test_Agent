<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { getGenerationSettings, saveGenerationSettings } from "../../../services/caseGeneration.api";
import type { GenerationTaskPolicy, GenerationTaskSettings } from "../../../services/caseGeneration.types";
const props = defineProps<{ projectId: string; canWrite: boolean }>();
const record = ref<GenerationTaskSettings | null>(null); const busy = ref(false); const error = ref(""); const notice = ref("");
let epoch = 0; let disposed = false;
const fields: { key: Exclude<keyof GenerationTaskPolicy, "transport_policy">; label: string; min: number; max: number; step?: string }[] = [
  { key: "timeout_seconds", label: "生成任务总超时（秒）", min: 0.001, max: 86400, step: "any" },
  { key: "call_timeout_seconds", label: "生成单次调用超时（秒）", min: 0.001, max: 600, step: "any" },
  { key: "max_input_bytes", label: "生成完整输入字节上限", min: 1, max: 10000000 },
  { key: "max_response_bytes", label: "生成原响应字节上限", min: 1, max: 100000000 },
  { key: "framing_allowance_tokens", label: "生成消息封装预留估算量", min: 0, max: 100000 },
];
async function load() {
  const ticket = ++epoch; const projectId = props.projectId; busy.value = true; error.value = ""; notice.value = ""; record.value = null;
  try {
    const value = await getGenerationSettings(projectId);
    if (disposed || ticket !== epoch) return;
    if (![projectId, "global"].includes(value.scope_key)) throw new Error("生成设置与当前项目不一致");
    record.value = value;
  } catch (err) { if (!disposed && ticket === epoch) error.value = err instanceof Error ? err.message : "生成设置读取失败"; }
  finally { if (!disposed && ticket === epoch) busy.value = false; }
}
async function save() {
  if (!record.value || busy.value || !props.canWrite) return;
  const ticket = epoch; const projectId = props.projectId; busy.value = true; error.value = ""; notice.value = "";
  try {
    const payload = JSON.parse(JSON.stringify(record.value.payload)) as GenerationTaskPolicy;
    const value = await saveGenerationSettings(projectId, payload);
    if (disposed || ticket !== epoch) return;
    if (value.scope_key !== projectId) throw new Error("保存的生成设置与当前项目不一致");
    record.value = value; notice.value = "项目生成设置已保存，仅影响后续新提交。";
  } catch (err) { if (!disposed && ticket === epoch) error.value = err instanceof Error ? err.message : "生成设置保存失败"; }
  finally { if (!disposed && ticket === epoch) busy.value = false; }
}
watch(() => props.projectId, () => { void load(); }, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; });
</script>

<template>
  <details aria-label="项目增量生成设置"><summary>项目增量生成设置</summary>
    <p>修改只影响新提交；原组继续使用固定策略。输入容量使用字节和估算量，不表示精确Token计量。</p>
    <form v-if="record" @submit.prevent="save"><fieldset :disabled="busy || !canWrite">
      <label v-for="field in fields" :key="field.key">{{ field.label }}<input v-model.number="record.payload[field.key]" type="number"
        :aria-label="field.label" :min="field.min" :max="field.max" :step="field.step || '1'" required></label>
      <label>生成请求发送策略<select v-model="record.payload.transport_policy" aria-label="生成请求发送策略">
        <option value="provider_defaults">使用提供商默认策略</option><option value="single_attempt">单次发送（SDK不重试）</option>
      </select></label>
      <p>配置来源：{{ record.scope_key }} · 修订 {{ record.revision }}</p><button type="submit" class="secondary-btn">保存生成设置</button>
    </fieldset></form>
    <p v-if="busy" role="status">正在读取或保存生成设置…</p><p v-if="notice" role="status">{{ notice }}</p><p v-if="error" role="alert">{{ error }}</p>
  </details>
</template>

<style scoped>
fieldset { border: 0; padding: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
input, select { max-width: 100%; min-width: 0; }
[role="alert"] { color: var(--red); }
</style>
