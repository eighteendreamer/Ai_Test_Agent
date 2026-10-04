<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { DocumentParsingSettings, DocumentParsingSettingsRecord } from "../../../types";

const props = defineProps<{ projectId: string; record: DocumentParsingSettingsRecord; canWrite: boolean }>();
const emit = defineEmits<{ saved: [record: DocumentParsingSettingsRecord] }>();
const form = ref<DocumentParsingSettings>(JSON.parse(JSON.stringify(props.record.payload)));
const saving = ref(false);
const error = ref("");
const notice = ref("");
let disposed = false;
const memoryMiB = computed({ get: () => form.value.policy.max_memory_bytes / 1048576,
  set: (value: number) => { form.value.policy.max_memory_bytes = Math.round(value * 1048576); } });
const limitFields: Array<{ key: keyof DocumentParsingSettings["policy"]["limits"]; label: string }> = [
  { key: "max_bytes", label: "文件大小上限（字节）" }, { key: "max_expanded_bytes", label: "解压大小上限（字节）" },
  { key: "max_zip_entries", label: "压缩条目上限" }, { key: "max_pages", label: "PDF 页数上限" },
  { key: "max_blocks", label: "文本块数量上限" }, { key: "max_text_chars", label: "文本字符上限" },
  { key: "max_table_cells", label: "表格单元格上限" },
];
watch(() => props.record, value => { form.value = JSON.parse(JSON.stringify(value.payload)); });

async function save() {
  if (saving.value || !props.canWrite) return;
  const projectId = props.projectId;
  saving.value = true; error.value = ""; notice.value = "";
  try {
    const saved = await api.saveDocumentParsingSettings(projectId, JSON.parse(JSON.stringify(form.value)));
    if (!disposed && projectId === props.projectId) {
      emit("saved", saved);
      notice.value = "项目解析设置已保存，后续新任务使用新配置；旧任务和快照保持原配置。";
    }
  } catch (err) {
    if (!disposed) error.value = err instanceof Error ? err.message : "解析设置保存失败";
  } finally { saving.value = false; }
}
onBeforeUnmount(() => { disposed = true; });
</script>

<template>
  <details class="document-parse-settings">
    <summary>项目解析设置</summary>
    <p>当前配置：{{ record.scope_key === 'global' ? '全局默认' : '项目覆盖' }} · 修订 {{ record.revision }}</p>
    <form @submit.prevent="save">
      <fieldset :disabled="saving || !canWrite">
        <div class="settings-grid">
          <label>文本编码<select v-model="form.options.encoding" aria-label="文本编码">
            <option value="utf-8-sig">UTF-8（含 BOM）</option><option value="utf-16">UTF-16</option><option value="gb18030">GB18030</option>
          </select></label>
          <label>PDF 文本提取<select v-model="form.options.pdf_extraction_mode" aria-label="PDF 文本提取">
            <option value="plain">普通文本</option><option value="layout">保留布局</option>
          </select></label>
          <label>超时（秒）<input v-model.number="form.policy.timeout_seconds" aria-label="解析超时（秒）" type="number" min="0.1" max="300" step="0.1" required></label>
          <label>CPU 时间上限（秒）<input v-model.number="form.policy.max_cpu_seconds" type="number" min="0.1" max="300" step="0.1" required></label>
          <label>内存上限（MiB）<input v-model.number="memoryMiB" type="number" min="1" required></label>
        </div>
        <details><summary>高级资源限制</summary><div class="settings-grid">
          <label v-for="field in limitFields" :key="field.key">{{ field.label }}
            <input v-model.number="form.policy.limits[field.key]" type="number" min="1" step="1" required>
          </label>
          <label>结果大小上限（字节）<input v-model.number="form.policy.max_output_bytes" type="number" min="1" step="1" required></label>
        </div></details>
        <button v-if="canWrite" class="secondary-btn" type="submit">{{ saving ? '保存中…' : '保存项目解析设置' }}</button>
      </fieldset>
    </form>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </details>
</template>

<style scoped>
.document-parse-settings { margin: 12px 0; }
fieldset { border: 0; padding: 0; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 12px 0; }
label { display: flex; flex-direction: column; gap: 4px; }
[role="alert"] { color: var(--red); }
</style>
