<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../../services/api";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../types";
import JsonContentTree from "./JsonContentTree.vue";

const props = defineProps<{ testCase: TestCaseRecord }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const versions = ref<TestCaseVersionRecord[]>([]);
const versionId = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const tab = ref<"steps" | "tree" | "raw" | "edit">("tree");
const editor = ref("");
const notes = ref("");
const current = computed(() => versions.value.find(item => item.id === versionId.value));
const content = computed(() => {
  const version = current.value;
  return version?.content_json ?? { preconditions: version?.preconditions ?? [], steps: version?.steps ?? [],
    assertions: version?.assertions ?? [], test_data: version?.test_data ?? {}, cleanup: version?.cleanup ?? [] };
});
const steps = computed(() => Array.isArray(content.value.steps) ? content.value.steps : current.value?.steps ?? []);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    versions.value = (await api.listTestCaseVersions(props.testCase.id)).sort((a, b) => b.version - a.version);
    versionId.value = versions.value[0]?.id ?? "";
  } catch (err) { error.value = err instanceof Error ? err.message : "用例版本加载失败"; }
  finally { loading.value = false; }
}
watch(current, version => {
  editor.value = JSON.stringify(content.value, null, 2);
  notes.value = version?.execution_notes ?? "";
});
async function save() {
  const version = current.value;
  if (!version) return;
  saving.value = true;
  error.value = "";
  try {
    const body = JSON.parse(editor.value);
    if (!body || Array.isArray(body) || typeof body !== "object") throw new Error("用例内容必须是 JSON 对象");
    const created = await api.createTestCaseVersion(props.testCase.id, {
      content_json: body, execution_notes: notes.value, generation_metadata: {
        ...version.generation_metadata, manual_edit: { source_version_id: version.id },
      },
      preconditions: [], steps: [], assertions: [], test_data: {}, cleanup: [],
      source_refs: version.source_refs, model_key: version.model_key, prompt_version: version.prompt_version,
      skill_versions: version.skill_versions,
    });
    versions.value.unshift(created);
    versionId.value = created.id;
    tab.value = "tree";
    emit("saved");
  } catch (err) { error.value = err instanceof Error ? err.message : "新版本保存失败"; }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="emit('close')">
    <section class="case-dialog" role="dialog" aria-label="测试用例内容">
      <header><div><h2>{{ testCase.title }}</h2><small>{{ testCase.case_key }}</small></div><button @click="emit('close')">关闭</button></header>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="loading">正在加载完整用例版本…</p>
      <template v-else-if="current">
        <label>版本<select v-model="versionId"><option v-for="version in versions" :key="version.id" :value="version.id">v{{ version.version }}{{ testCase.active_version_id === version.id ? "（已启用）" : "" }}</option></select></label>
        <p v-if="current.execution_notes" class="notes">{{ current.execution_notes }}</p>
        <nav><button @click="tab = 'steps'">步骤</button><button @click="tab = 'tree'">JSON 树</button><button @click="tab = 'raw'">JSON 原文</button><button :disabled="testCase.lifecycle_status === 'archived'" @click="tab = 'edit'">编辑新版本</button></nav>
        <div v-if="tab === 'steps'"><JsonContentTree v-for="(step, index) in steps" :key="index" :label="`步骤 ${index + 1}`" :value="step" /><p v-if="!steps.length">当前结构没有通用步骤数组，请使用 JSON 树查看完整内容。</p></div>
        <JsonContentTree v-else-if="tab === 'tree'" label="用例" :value="content" />
        <pre v-else-if="tab === 'raw'">{{ JSON.stringify(content, null, 2) }}</pre>
        <div v-else><p>保存将追加草稿版本，保留原版本与已冻结的运行依据。</p><label>用例自身说明<textarea v-model="notes" rows="3"></textarea></label><label>完整 JSON<textarea v-model="editor" rows="18" class="json-editor"></textarea></label><button :disabled="saving" @click="save">{{ saving ? "保存中…" : "保存新草稿版本" }}</button></div>
        <details><summary>来源及生成依据</summary><JsonContentTree label="来源" :value="current.source_refs" /><JsonContentTree label="生成记录" :value="current.generation_metadata ?? {}" /><small>SHA256 {{ current.content_hash }}</small></details>
      </template>
      <p v-else-if="!loading">没有可查看的用例版本。</p>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
</style>
