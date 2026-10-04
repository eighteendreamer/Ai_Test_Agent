<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentDifferenceSettings, DocumentDifferenceSettingsRecord, DocumentDifferencePolicy } from "../../../types";

const props = defineProps<{ projectId: string; record: DocumentDifferenceSettingsRecord; canWrite: boolean }>();
const emit = defineEmits<{ saved: [record: DocumentDifferenceSettingsRecord] }>();
const form = ref<DocumentDifferenceSettings>(JSON.parse(JSON.stringify(props.record.payload)));
const saving = ref(false);
const error = ref("");
const notice = ref("");
let epoch = 0;
let disposed = false;
const fields: Array<{ key: keyof DocumentDifferencePolicy; label: string; integer?: boolean; max?: number }> = [
  { key: "timeout_seconds", label: "比较超时（秒）", max: 300 }, { key: "max_cpu_seconds", label: "比较 CPU 上限（秒）", max: 300 },
  { key: "max_memory_bytes", label: "比较内存上限（字节）", integer: true }, { key: "max_input_bytes", label: "输入大小上限（字节）", integer: true },
  { key: "max_text_chars", label: "文本字符上限", integer: true }, { key: "max_lines", label: "文本行数上限", integer: true },
  { key: "max_structure_items", label: "结构条目上限", integer: true }, { key: "max_changes", label: "变化数量上限", integer: true },
  { key: "max_character_pair_product", label: "字符匹配预算", integer: true }, { key: "max_character_detail_chars", label: "字符细节大小上限", integer: true },
  { key: "max_output_bytes", label: "结果大小上限（字节）", integer: true },
];
watch(() => props.projectId, () => {
  ++epoch; form.value = JSON.parse(JSON.stringify(props.record.payload)); saving.value = false; error.value = ""; notice.value = "";
});
watch(() => props.record, value => {
  ++epoch; form.value = JSON.parse(JSON.stringify(value.payload)); saving.value = false;
});
async function save() {
  if (saving.value || !props.canWrite) return;
  const projectId = props.projectId; const generation = epoch;
  const current = () => !disposed && generation === epoch && projectId === props.projectId;
  const payload = JSON.parse(JSON.stringify(form.value)) as DocumentDifferenceSettings;
  saving.value = true; error.value = ""; notice.value = "";
  try {
    const value = await api.saveDocumentDifferenceSettings(projectId, payload);
    if (!current()) return;
    if (value.scope_key !== projectId) throw new Error("比较设置返回的项目身份不一致");
    emit("saved", value); notice.value = "项目比较设置已保存；后续新任务使用提交时的实际策略，旧任务保持固定依据。";
  } catch (err) { if (current()) error.value = err instanceof Error ? err.message : "比较设置保存失败"; }
  finally { if (current()) saving.value = false; }
}
onBeforeUnmount(() => { disposed = true; ++epoch; });
</script>

<template>
  <details class="document-difference-settings"><summary>项目比较设置</summary>
    <p>当前配置：{{ record.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ record.revision }}</p>
    <form @submit.prevent="save"><fieldset :disabled="saving || !canWrite">
      <div class="settings-grid"><label v-for="field in fields" :key="field.key">{{ field.label }}
        <input v-model.number="form.policy[field.key]" :aria-label="field.label" type="number" :min="field.integer ? 1 : 0" :max="field.max" :step="field.integer ? 1 : 'any'" required>
      </label></div><button v-if="canWrite" class="secondary-btn" type="submit">{{ saving ? '保存中…' : '保存项目比较设置' }}</button>
    </fieldset></form><p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </details>
</template>

<style scoped>
.document-difference-settings { margin: 12px 0; }
fieldset { border: 0; padding: 0; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 12px 0; }
label { display: flex; flex-direction: column; gap: 4px; }
[role="alert"] { color: var(--red); }
</style>
