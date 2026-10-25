<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NForm,
  NFormItem,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NUpload,
} from "naive-ui";
import type { UploadFileInfo } from "naive-ui";
import { api } from "../../services/api";
import type { CaseImportBatchView, CaseImportOptions, CaseImportSettingsRecord, CaseImportSheet, CaseImportSourceRecord } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";

const props = defineProps<{ projectId: string; modes: Array<{ key: string; name: string }> }>();
const emit = defineEmits<{ close: []; imported: [] }>();
const show = ref(true);
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
const layoutOptions = computed(() => [
  { label: "每行一条用例", value: "rows" },
  { label: "用例与步骤分表", value: "split" },
  ...(source.value && /\.json$/i.test(source.value.filename) ? [{ label: "JSON", value: "json" }] : []),
]);
const modeOptions = computed(() => props.modes.map(mode => ({ label: mode.name, value: mode.key })));
const headerOptions = computed(() => (caseSheet.value?.headers.filter(Boolean) || []).map(header => ({ label: header, value: header })));
const stepHeaderOptions = computed(() => (stepSheet.value?.headers.filter(Boolean) || []).map(header => ({ label: header, value: header })));
const candidateOptions = computed(() => (view.value?.batch.preview.candidates || []).map((item, index) => ({
  label: `${item.draft.case_key} · ${item.draft.title} · 第 ${item.row} 行`,
  value: index,
})));

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
async function processFile(file: File | null) {
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
  finally { busy.value = false; }
}
function onFileChange({ file }: { file: Required<UploadFileInfo> }) {
  if (file.file) void processFile(file.file);
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
function close() {
  if (busy.value) return;
  show.value = false;
  emit("close");
}
onMounted(load);
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    title="导入测试用例"
    style="width: min(960px, 96vw)"
    :mask-closable="!busy"
    :close-on-esc="!busy"
    @update:show="!$event && close()"
  >
    <p class="dialog-hint">导入后保存为草稿，评审启用后用于测试。重复提交同一预览不会重复创建；编号冲突不会覆盖旧用例。</p>
    <div class="dialog-actions-left">
      <NButton size="small" tag="a" :href="`${rootUrl}/templates/rows`" download>下载每行用例模板</NButton>
      <NButton size="small" tag="a" :href="`${rootUrl}/templates/split`" download>下载用例与步骤分表模板</NButton>
    </div>

    <NAlert v-if="error" type="error" :bordered="false" style="margin: 12px 0">{{ error }}</NAlert>
    <NAlert v-if="notice" type="success" :bordered="false" style="margin: 12px 0">{{ notice }}</NAlert>

    <div class="dialog-actions-left" style="margin: 12px 0">
      <NUpload
        :show-file-list="false"
        :default-upload="false"
        accept=".xlsx,.json"
        :disabled="busy || !settings"
        @change="onFileChange"
      >
        <NButton :disabled="busy || !settings">选择 Excel / JSON 文件</NButton>
      </NUpload>
      <span v-if="busy" class="dialog-inline-loading"><NSpin size="small" /> 正在处理，请稍候…</span>
    </div>

    <template v-if="source">
      <div class="source-line">
        <span>{{ source.filename }} · {{ source.size_bytes }} 字节</span>
        <NButton size="tiny" text tag="a" :href="`${rootUrl}/sources/${source.id}/original`" download>下载原件</NButton>
      </div>
      <NForm label-placement="top">
        <NFormItem label="导入布局">
          <NSelect
            v-model:value="options.layout"
            :options="layoutOptions"
            :disabled="busy || /\.json$/i.test(source.filename)"
            style="width: 220px"
          />
        </NFormItem>
        <NFormItem label="测试模式">
          <NSelect v-model:value="options.mode_key" :options="modeOptions" :disabled="busy" style="width: 220px" />
        </NFormItem>
      </NForm>
      <template v-if="options.layout !== 'json'">
        <div class="dialog-actions-left">
          <NFormItem label="表头行" :show-feedback="false" label-placement="left">
            <NInputNumber v-model:value="options.header_row" :min="1" :max="50" :disabled="busy" style="width: 110px" />
          </NFormItem>
          <NButton size="small" :disabled="busy" @click="inspect">读取表头</NButton>
        </div>
        <NForm label-placement="top">
          <NFormItem label="用例工作表">
            <NSelect
              :value="options.case_sheet ?? null"
              :options="sheets.map(sheet => ({ label: sheet.name, value: sheet.name }))"
              :disabled="busy"
              style="width: 240px"
              @update:value="(value: string) => { options.case_sheet = value; }"
            />
          </NFormItem>
        </NForm>
        <NCollapse style="margin-bottom: 12px">
          <NCollapseItem title="用例列映射" name="case-columns">
            <div class="import-mapping">
              <NFormItem v-for="(label, key) in caseFields" :key="key" :label="label" :show-feedback="false" label-placement="top">
                <NSelect
                  :value="options.columns[key] ?? ''"
                  :options="[{ label: '不映射', value: '' }, ...headerOptions]"
                  :disabled="busy"
                  @update:value="(value: string) => { options.columns[key] = value; }"
                />
              </NFormItem>
            </div>
          </NCollapseItem>
        </NCollapse>
        <template v-if="options.layout === 'split'">
          <NForm label-placement="top">
            <NFormItem label="步骤工作表">
              <NSelect
                :value="options.step_sheet ?? null"
                :options="sheets.map(sheet => ({ label: sheet.name, value: sheet.name }))"
                :disabled="busy"
                style="width: 240px"
                @update:value="(value: string) => { options.step_sheet = value; }"
              />
            </NFormItem>
          </NForm>
          <NCollapse style="margin-bottom: 12px">
            <NCollapseItem title="步骤列映射" name="step-columns">
              <div class="import-mapping">
                <NFormItem v-for="(label, key) in stepFields" :key="key" :label="label" :show-feedback="false" label-placement="top">
                  <NSelect
                    :value="options.step_columns[key] ?? ''"
                    :options="[{ label: '不映射', value: '' }, ...stepHeaderOptions]"
                    :disabled="busy"
                    @update:value="(value: string) => { options.step_columns[key] = value; }"
                  />
                </NFormItem>
              </div>
            </NCollapseItem>
          </NCollapse>
        </template>
      </template>
      <div class="dialog-actions-left">
        <NButton size="small" type="primary" :disabled="busy || !options.mode_key" @click="preview">生成导入预览</NButton>
        <NButton size="small" :disabled="busy" @click="remember">记住项目导入格式</NButton>
      </div>
    </template>

    <template v-if="view">
      <NAlert type="info" :bordered="false" style="margin: 16px 0 12px">
        预览：{{ view.batch.preview.candidates.length }} 条有效候选 · 批次 {{ view.batch.id }} ·
        {{ view.batch.status === 'committed' ? '已提交' : '等待提交' }}
      </NAlert>
      <NAlert v-if="view.conflicting_case_keys.length" type="error" :bordered="false" style="margin-bottom: 12px">
        编号已存在：{{ view.conflicting_case_keys.join('、') }}。请调整文件或映射后重新预览。
      </NAlert>
      <NAlert v-if="view.batch.preview.issues.length" type="warning" :bordered="false" style="margin-bottom: 12px">
        <ul class="issue-list">
          <li v-for="(issue, index) in view.batch.preview.issues" :key="index">
            {{ issue.severity === 'error' ? '错误' : '提示' }} · {{ issue.sheet || 'JSON' }}
            {{ issue.row ? `第 ${issue.row} 行` : '' }} · {{ issue.message }}
          </li>
        </ul>
      </NAlert>
      <NForm v-if="view.batch.preview.candidates.length" label-placement="top">
        <NFormItem label="查看用例">
          <NSelect
            :value="candidateIndex"
            :options="candidateOptions"
            style="width: 420px"
            @update:value="(value: number) => { candidateIndex = value; }"
          />
        </NFormItem>
      </NForm>
      <JsonContentTree v-if="candidate" label="完整用例 JSON" :value="candidate.draft.content_json" />
      <NEmpty v-else description="暂无候选用例" size="small" />
      <div class="dialog-actions-left" style="margin-top: 12px">
        <NButton size="small" type="primary" :disabled="!canCommit" @click="commit">提交草稿</NButton>
        <NButton size="small" @click="downloadReport">下载预览与错误报告</NButton>
      </div>
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
.import-mapping { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 16px; }
.source-line { display: flex; align-items: center; gap: 12px; margin: 12px 0; color: var(--text-secondary, #64748b); }
.dialog-inline-loading { display: inline-flex; align-items: center; gap: 8px; color: var(--text-secondary, #64748b); }
.issue-list { margin: 0; padding-left: 18px; }
</style>
