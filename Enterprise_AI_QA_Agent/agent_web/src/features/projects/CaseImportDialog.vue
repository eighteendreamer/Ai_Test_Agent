<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../../services/api";
import type { CaseImportBatchView, CaseImportOptions, CaseImportSettingsRecord, CaseImportSheet, CaseImportSourceRecord } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";

const props = defineProps<{ projectId: string; modes: Array<{ key: string; name: string }> }>();
const emit = defineEmits<{ close: []; imported: [] }>();
const settings = ref<CaseImportSettingsRecord | null>(null);
const source = ref<CaseImportSourceRecord | null>(null);
const sheets = ref<CaseImportSheet[]>([]);
const view = ref<CaseImportBatchView | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const candidateIndex = ref(0);
const options = ref<CaseImportOptions>({ layout: "rows", mode_key: "", header_row: 1, columns: {}, step_columns: {} });
const caseFields: Record<string, string> = { case_key: "用例编号", title: "标题", case_type: "类型", priority: "优先级", preconditions: "前置条件", steps: "步骤", expected_results: "预期结果", test_data: "测试数据 JSON", cleanup: "清理", execution_notes: "用例说明", content_json: "完整 JSON" };
const stepFields: Record<string, string> = { case_key: "用例编号", order: "步骤序号", action: "操作", expected: "步骤预期" };
const caseSheet = computed(() => sheets.value.find(sheet => sheet.name === options.value.case_sheet));
const stepSheet = computed(() => sheets.value.find(sheet => sheet.name === options.value.step_sheet));
const candidate = computed(() => view.value?.batch.preview.candidates[candidateIndex.value]);
const canCommit = computed(() => !!view.value && !busy.value && view.value.batch.status === "previewed" &&
  view.value.batch.preview.candidates.length > 0 && !view.value.conflicting_case_keys.length &&
  !view.value.batch.preview.issues.some(issue => issue.severity === "error"));
const rootUrl = computed(() => `/api/v1/projects/${encodeURIComponent(props.projectId)}/test-case-imports`);

watch(options, () => { view.value = null; notice.value = ""; }, { deep: true });
function mappedColumns(sheet: CaseImportSheet | undefined, current: Record<string, string>, fields: Record<string, string>) {
  return Object.fromEntries(Object.keys(fields).map(key => [key, current[key] !== undefined && (current[key] === "" || sheet?.headers.includes(current[key])) ? current[key] : sheet?.suggested_columns[key] || ""]));
}
watch(caseSheet, sheet => { if (sheet) options.value.columns = mappedColumns(sheet, options.value.columns, caseFields); });
watch(stepSheet, sheet => { if (sheet) options.value.step_columns = mappedColumns(sheet, options.value.step_columns, stepFields); });
function requestOptions(): CaseImportOptions {
  return { ...options.value,
    columns: { ...options.value.columns },
    step_columns: { ...options.value.step_columns },
  };
}
function showError(err: unknown) { error.value = err instanceof Error ? err.message : "导入操作失败"; }
async function load() {
  busy.value = true;
  try {
    settings.value = await api.getCaseImportSettings(props.projectId);
    options.value = settings.value.payload.options ? JSON.parse(JSON.stringify(settings.value.payload.options)) : {
      ...options.value, mode_key: props.modes[0]?.key || "",
    };
  } catch (err) { showError(err); }
  finally { busy.value = false; }
}
function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(reader.error || new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}
async function choose(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file || !settings.value) return;
  error.value = ""; notice.value = ""; view.value = null; source.value = null; sheets.value = [];
  if (!/\.(xlsx|json)$/i.test(file.name)) { error.value = "请选择 .xlsx 或 .json 文件"; return; }
  if (file.size > settings.value.payload.policy.limits.max_bytes) { error.value = "文件超过当前导入大小限制"; return; }
  busy.value = true;
  try {
    source.value = await api.uploadCaseImport(props.projectId, file.name, await readFile(file));
    sheets.value = source.value.sheets;
    options.value.layout = /\.json$/i.test(file.name) ? "json" : options.value.layout === "json" ? "rows" : options.value.layout;
    selectSheets();
  } catch (err) { showError(err); }
  finally { busy.value = false; (event.target as HTMLInputElement).value = ""; }
}
function selectSheets() {
  if (!sheets.value.some(sheet => sheet.name === options.value.case_sheet)) options.value.case_sheet = sheets.value[0]?.name ?? null;
  if (!sheets.value.some(sheet => sheet.name === options.value.step_sheet)) options.value.step_sheet = sheets.value.find(sheet => sheet.name === "步骤")?.name ?? sheets.value[1]?.name ?? null;
}
async function inspect() {
  if (!source.value) return;
  busy.value = true; error.value = "";
  try {
    sheets.value = await api.inspectCaseImportSheets(props.projectId, source.value.id, options.value.header_row);
    selectSheets();
  } catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function preview() {
  if (!source.value) return;
  busy.value = true; error.value = ""; view.value = null; candidateIndex.value = 0;
  try { view.value = await api.previewCaseImport(props.projectId, source.value.id, requestOptions()); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function remember() {
  if (!settings.value) return;
  busy.value = true; error.value = "";
  try {
    settings.value = await api.saveCaseImportSettings(props.projectId, { ...settings.value.payload, options: requestOptions() });
    notice.value = "项目导入格式已保存";
  } catch (err) { showError(err); }
  finally { busy.value = false; }
}
async function commit() {
  if (!canCommit.value || !view.value) return;
  busy.value = true; error.value = "";
  try { view.value = await api.commitCaseImport(props.projectId, view.value.batch.id); }
  catch (err) {
    showError(err);
    try { view.value = await api.getCaseImportBatch(props.projectId, view.value.batch.id); }
    catch (lookupError) { error.value += `；状态复核失败：${lookupError instanceof Error ? lookupError.message : "无法读取批次"}`; }
  } finally { busy.value = false; }
  if (view.value?.batch.status === "committed") { error.value = ""; notice.value = "导入完成，草稿等待评审"; emit("imported"); }
}
function downloadReport() {
  if (!view.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(view.value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = `case-import-${view.value.batch.id}.json`; link.click(); URL.revokeObjectURL(url);
}
onMounted(load);
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="!busy && emit('close')">
    <section class="case-dialog" role="dialog" aria-label="导入测试用例">
      <header><h2>导入测试用例</h2><button :disabled="busy" @click="emit('close')">关闭</button></header>
      <p>导入后保存为草稿，评审启用后用于测试。重复提交同一预览不会重复创建；编号冲突不会覆盖旧用例。</p>
      <nav><a :href="`${rootUrl}/templates/rows`" download>下载每行用例模板</a><a :href="`${rootUrl}/templates/split`" download>下载用例与步骤分表模板</a></nav>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <label>Excel / JSON 文件<input type="file" accept=".xlsx,.json" :disabled="busy || !settings" @change="choose"></label>
      <p v-if="busy" role="status">正在处理，请稍候…</p>
      <template v-if="source">
        <p>{{ source.filename }} · {{ source.size_bytes }} 字节 · <a :href="`${rootUrl}/sources/${source.id}/original`" download>下载原件</a></p>
        <label>导入布局<select v-model="options.layout" :disabled="busy || /\.json$/i.test(source.filename)"><option value="rows">每行一条用例</option><option value="split">用例与步骤分表</option><option v-if="/\.json$/i.test(source.filename)" value="json">JSON</option></select></label>
        <label>测试模式<select v-model="options.mode_key" :disabled="busy"><option v-for="mode in modes" :key="mode.key" :value="mode.key">{{ mode.name }}</option></select></label>
        <template v-if="options.layout !== 'json'">
          <label>表头行<input v-model.number="options.header_row" type="number" min="1" max="50" :disabled="busy"></label><button :disabled="busy" @click="inspect">读取表头</button>
          <label>用例工作表<select v-model="options.case_sheet" :disabled="busy"><option v-for="sheet in sheets" :key="sheet.name" :value="sheet.name">{{ sheet.name }}</option></select></label>
          <details open><summary>用例列映射</summary><div class="import-mapping"><label v-for="(label, key) in caseFields" :key="key">{{ label }}<select v-model="options.columns[key]" :disabled="busy"><option value="">不映射</option><option v-for="header in caseSheet?.headers.filter(Boolean)" :key="header" :value="header">{{ header }}</option></select></label></div></details>
          <template v-if="options.layout === 'split'">
            <label>步骤工作表<select v-model="options.step_sheet" :disabled="busy"><option v-for="sheet in sheets" :key="sheet.name" :value="sheet.name">{{ sheet.name }}</option></select></label>
            <details open><summary>步骤列映射</summary><div class="import-mapping"><label v-for="(label, key) in stepFields" :key="key">{{ label }}<select v-model="options.step_columns[key]" :disabled="busy"><option value="">不映射</option><option v-for="header in stepSheet?.headers.filter(Boolean)" :key="header" :value="header">{{ header }}</option></select></label></div></details>
          </template>
        </template>
        <footer><button :disabled="busy || !options.mode_key" @click="preview">生成导入预览</button><button :disabled="busy" @click="remember">记住项目导入格式</button></footer>
      </template>
      <template v-if="view">
        <h3>预览：{{ view.batch.preview.candidates.length }} 条有效候选</h3>
        <p>批次 {{ view.batch.id }} · {{ view.batch.status === 'committed' ? '已提交' : '等待提交' }}</p>
        <p v-if="view.conflicting_case_keys.length" role="alert">编号已存在：{{ view.conflicting_case_keys.join('、') }}。请调整文件或映射后重新预览。</p>
        <ul v-if="view.batch.preview.issues.length"><li v-for="(issue, index) in view.batch.preview.issues" :key="index">{{ issue.severity === 'error' ? '错误' : '提示' }} · {{ issue.sheet || 'JSON' }} {{ issue.row ? `第 ${issue.row} 行` : '' }} · {{ issue.message }}</li></ul>
        <label v-if="view.batch.preview.candidates.length">查看用例<select v-model.number="candidateIndex"><option v-for="(item, index) in view.batch.preview.candidates" :key="index" :value="index">{{ item.draft.case_key }} · {{ item.draft.title }} · 第 {{ item.row }} 行</option></select></label>
        <JsonContentTree v-if="candidate" label="完整用例 JSON" :value="candidate.draft.content_json" />
        <footer><button :disabled="!canCommit" @click="commit">提交草稿</button><button @click="downloadReport">下载预览与错误报告</button></footer>
      </template>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
.import-mapping { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 16px; }
nav { flex-wrap: wrap; }
</style>
