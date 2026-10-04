<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import type { ApiDocRecord, ApiDocVersionResult, GitDocumentImportRequest, GitDocumentPolicy,
  GitDocumentResolution, GitDocumentSettings, GitDocumentSettingsRecord } from "../../../types";
import * as service from "../../../services/documentGit.api";
import { HttpError } from "../../../services/http";
import { api } from "../../../services/api";

const props = defineProps<{ projectId: string; canWrite: boolean; document?: ApiDocRecord }>();
const emit = defineEmits<{ updated: [document: ApiDocRecord] }>();
const settings = ref<GitDocumentSettingsRecord | null>(null);
const form = ref<GitDocumentSettings | null>(null);
const repositoryId = ref("");
const reference = ref("HEAD");
const path = ref("");
const resolved = ref<GitDocumentResolution | null>(null);
const pending = ref<GitDocumentImportRequest | null>(null);
const result = ref<ApiDocVersionResult | null>(null);
const busy = ref(false);
const error = ref("");
const cacheError = ref("");
let epoch = 0;
const key = () => `qa-agent:document-git:${props.projectId}:${props.document?.id || "new"}`;
const policyFields: Array<{ key: keyof GitDocumentPolicy; label: string; integer?: boolean }> = [
  { key: "timeout_seconds", label: "Git 读取超时（秒）" }, { key: "max_cpu_seconds", label: "Git CPU 上限（秒）" },
  { key: "max_memory_bytes", label: "Git 内存上限（字节）", integer: true }, { key: "max_blob_bytes", label: "Git 文件上限（字节）", integer: true },
  { key: "max_output_bytes", label: "Git 输出上限（字节）", integer: true },
];
const message = (err: unknown) => err instanceof Error ? err.message : "Git 文档操作失败";
function guard() { const generation = epoch; return () => generation === epoch; }

async function accept(value: ApiDocVersionResult, original: GitDocumentImportRequest, current: () => boolean) {
  const origin = value.source_origin;
  if (value.document.project_id !== props.projectId || value.version.document_id !== value.document.id
    || value.document.current_version_id !== value.version.id || !origin
    || (original.document_id && value.document.id !== original.document_id)
    || origin.repository_id !== original.repository_id || origin.commit !== original.commit || origin.blob !== original.blob
    || origin.path !== original.path || origin.config_revision !== original.config_revision
    || (value.created_new && original.document_id && value.version.parent_version_id !== original.expected_parent_version_id)) {
    throw new Error("返回的提交不属于当前项目、固定来源或原目标版本");
  }
  result.value = value;
  const document = await api.getApiDoc(value.document.id);
  if (!current()) return;
  if (document.project_id !== props.projectId || document.id !== value.document.id) throw new Error("当前文档归属已变化");
  emit("updated", document);
}

async function lookup() {
  if (!pending.value || busy.value) return;
  const original = { ...pending.value }; const current = guard();
  busy.value = true; error.value = "";
  try {
    const value = await service.getDocumentGitSubmission(props.projectId, original.request_id, original.document_id || undefined);
    if (current()) await accept(value, original, current);
  } catch (err) {
    if (current()) error.value = err instanceof HttpError && err.status === 404
      ? "尚未找到原提交；可使用同一编号重试。" : message(err);
  } finally { if (current()) busy.value = false; }
}

async function load() {
  const current = guard();
  try {
    const value = await service.getDocumentGitSettings(props.projectId);
    if (!current()) return;
    if (value.scope_key !== props.projectId) throw new Error("Git 设置不属于当前项目");
    settings.value = value; form.value = JSON.parse(JSON.stringify(value.payload));
    repositoryId.value = value.payload.repositories.find(item => item.enabled)?.id || "";
  } catch (err) { if (current()) error.value = message(err); }
}

async function save() {
  if (!form.value || busy.value || !props.canWrite) return;
  const current = guard(); const payload = JSON.parse(JSON.stringify(form.value)) as GitDocumentSettings;
  busy.value = true; error.value = "";
  try {
    const value = await service.saveDocumentGitSettings(props.projectId, payload);
    if (!current()) return;
    if (value.scope_key !== props.projectId) throw new Error("Git 设置不属于当前项目");
    settings.value = value; form.value = JSON.parse(JSON.stringify(value.payload)); resolved.value = null;
    repositoryId.value = value.payload.repositories.find(item => item.enabled)?.id || "";
  } catch (err) { if (current()) error.value = message(err); }
  finally { if (current()) busy.value = false; }
}

async function resolve() {
  if (busy.value || !props.canWrite || pending.value) return;
  const current = guard(); busy.value = true; error.value = ""; resolved.value = null;
  try {
    const value = await service.resolveDocumentGitSource(props.projectId,
      { repository_id: repositoryId.value, reference: reference.value, path: path.value });
    if (!current()) return;
    if (value.project_id !== props.projectId || value.origin.repository_id !== repositoryId.value || value.origin.path !== path.value) {
      throw new Error("解析结果不属于当前项目或所选来源");
    }
    resolved.value = value;
  } catch (err) { if (current()) error.value = message(err); }
  finally { if (current()) busy.value = false; }
}

async function submit() {
  if (busy.value || result.value || (!pending.value && (!resolved.value || !props.canWrite))) return;
  const current = guard(); busy.value = true; error.value = "";
  try {
    if (!pending.value) {
      const origin = resolved.value!.origin;
      pending.value = { request_id: crypto.randomUUID(), repository_id: origin.repository_id,
        config_revision: origin.config_revision, commit: origin.commit, blob: origin.blob, path: origin.path,
        ...(props.document ? { document_id: props.document.id, expected_parent_version_id: props.document.current_version_id } : {}) };
      try { sessionStorage.setItem(key(), JSON.stringify({ projectId: props.projectId, payload: pending.value })); }
      catch (err) { cacheError.value = `原请求仅保留在当前页面：${message(err)}。离开后请凭编号查询。`; }
    }
    const original = { ...pending.value };
    const value = await service.importDocumentGitSource(props.projectId, original);
    if (current()) await accept(value, original, current);
  } catch (err) { if (current()) error.value = `${message(err)}；保留原请求，请先查询原提交。`; }
  finally { if (current()) busy.value = false; }
}

function reset() {
  if (busy.value || (!result.value && pending.value)) return;
  try { sessionStorage.removeItem(key()); }
  catch (err) { cacheError.value = message(err); return; }
  pending.value = null; result.value = null; resolved.value = null; error.value = "";
}
watch(() => [repositoryId.value, reference.value, path.value], () => { resolved.value = null; });
watch(() => [props.projectId, props.document?.id], () => {
  ++epoch; settings.value = null; form.value = null; pending.value = null; result.value = null;
  resolved.value = null; busy.value = false; error.value = ""; cacheError.value = "";
  try {
    const raw = sessionStorage.getItem(key());
    if (raw) {
      const cached = JSON.parse(raw);
      const value = cached.payload as GitDocumentImportRequest;
      if (cached.projectId !== props.projectId || !value?.request_id || !value.repository_id
        || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value.commit) || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value.blob)
        || !value.path || !Number.isInteger(value.config_revision) || value.config_revision < 1
        || (value.document_id || null) !== (props.document?.id || null)
        || (value.document_id && !value.expected_parent_version_id)) throw new Error("原请求缓存身份不完整");
      pending.value = value; void lookup();
    }
  } catch (err) { cacheError.value = message(err); }
  void load();
}, { immediate: true });
onBeforeUnmount(() => { ++epoch; });
</script>

<template>
  <section class="document-git-import" aria-label="Git 文档导入">
    <h4>{{ document ? '从 Git 导入该文档的新版本' : '从 Git 新建文档' }}</h4>
    <p>选择已登记仓库的文件版本。导入后可在文档详情解析和比较。</p>
    <p v-if="document?.source_origin">当前版本 Git 来源：{{ document.source_origin.repository_name }} · {{ document.source_origin.path }}<br>
      commit：{{ document.source_origin.commit }} · blob：{{ document.source_origin.blob }}</p>
    <details v-if="form" class="git-source-settings"><summary>项目 Git 仓库设置 · 修订 {{ settings?.revision }}</summary>
      <form @submit.prevent="save"><fieldset :disabled="busy || !canWrite">
        <div v-for="(source, index) in form.repositories" :key="index" class="git-repository-row">
          <label>仓库编号<input v-model="source.id" aria-label="仓库编号" required></label>
          <label>仓库名称<input v-model="source.name" aria-label="仓库名称" required></label>
          <label>服务器本地仓库路径<input v-model="source.root_path" aria-label="服务器本地仓库路径" required></label>
          <label><input v-model="source.enabled" type="checkbox">启用</label>
          <button v-if="canWrite" type="button" @click="form.repositories.splice(index, 1)">移除仓库</button>
        </div>
        <button v-if="canWrite" type="button" @click="form.repositories.push({ id: '', name: '', root_path: '', enabled: true })">添加仓库</button>
        <div class="git-policy-grid"><label v-for="field in policyFields" :key="field.key">{{ field.label }}
          <input v-model.number="form.policy[field.key]" :aria-label="field.label" type="number" :min="field.integer ? 1 : 0.000001" :step="field.integer ? 1 : 'any'" required>
        </label></div><button v-if="canWrite" type="submit">保存项目 Git 设置</button>
      </fieldset></form>
    </details>
    <form v-if="canWrite" @submit.prevent="resolve"><fieldset :disabled="busy || !!pending">
      <label>Git 仓库<select v-model="repositoryId" aria-label="Git 仓库" required>
        <option value="">选择仓库</option><option v-for="source in settings?.payload.repositories.filter(item => item.enabled)" :key="source.id" :value="source.id">{{ source.name }}</option>
      </select></label>
      <label>Git 引用<input v-model="reference" aria-label="Git 引用" required></label>
      <label>仓库内文件路径<input v-model="path" aria-label="仓库内文件路径" required></label>
      <button type="submit">解析固定来源</button>
    </fieldset></form>
    <div v-if="resolved" class="git-fixed-source"><strong>固定 commit：</strong>{{ resolved.origin.commit }}<br>
      <strong>固定 blob：</strong>{{ resolved.origin.blob }} · {{ resolved.origin.size_bytes }} 字节
    </div>
    <button v-if="resolved && !pending && canWrite" :disabled="busy || (!!document && !document.current_version_id)" @click="submit">{{ document ? '导入为当前文档的新版本' : '导入为新文档' }}</button>
    <div v-if="pending"><p>原请求编号：{{ pending.request_id }} · {{ pending.path }} · {{ pending.commit }}</p>
      <button :disabled="busy" @click="lookup">查询原 Git 提交</button>
      <button v-if="!result" :disabled="busy" @click="submit">重试原 Git 提交</button>
    </div>
    <p v-if="result" role="status">{{ result.created_new ? '已保存文档版本' : '原件内容未变化，保留当前版本' }} V{{ result.version.version }}</p>
    <button v-if="result && canWrite" :disabled="busy" @click="reset">另建一次 Git 导入</button>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="cacheError" role="alert">{{ cacheError }}</p>
  </section>
</template>

<style scoped>
.document-git-import { margin: 16px 0; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; display: grid; gap: 8px; }
label { display: flex; flex-direction: column; gap: 4px; }
.git-repository-row, .git-policy-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 8px 0; }
.git-fixed-source { margin: 12px 0; }
[role="alert"] { color: var(--red); }
</style>
