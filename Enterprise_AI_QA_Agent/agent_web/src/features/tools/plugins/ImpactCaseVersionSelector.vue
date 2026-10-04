<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../../types";
const props = defineProps<{ projectId: string; modelValue: string[]; disabled: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>();
const cases = ref<TestCaseRecord[]>([]);
const versions = ref<Record<string, TestCaseVersionRecord[]>>({});
const query = ref("");
const hasMore = ref(false);
const busy = ref(false);
const error = ref("");
let offset = 0;
let epoch = 0;
let disposed = false;
let versionEpoch = 0;
async function load(more = false) {
  const generation = ++epoch; const projectId = props.projectId;
  const search = query.value.trim(); busy.value = true; error.value = "";
  try {
    const page = await api.listTestCases(projectId, { query: search, limit: 20, offset: more ? offset : 0 });
    if (disposed || generation !== epoch || projectId !== props.projectId) return;
    if (page.items.some(item => item.project_id !== projectId)) throw new Error("用例列表与当前项目不一致");
    cases.value = more ? [...cases.value, ...page.items.filter(item => !cases.value.some(old => old.id === item.id))] : page.items;
    offset = page.offset + page.items.length; hasMore.value = page.has_more;
  } catch (err) { if (!disposed && generation === epoch) error.value = err instanceof Error ? err.message : "项目用例查询失败"; }
  finally { if (!disposed && generation === epoch) busy.value = false; }
}
async function readVersions(item: TestCaseRecord) {
  const generation = versionEpoch; const projectId = props.projectId; error.value = "";
  try {
    const values = await api.listTestCaseVersions(item.id);
    if (disposed || generation !== versionEpoch || projectId !== props.projectId) return;
    if (item.project_id !== projectId || values.some(value => value.case_id !== item.id)) throw new Error("用例版本与当前项目用例不一致");
    versions.value = { ...versions.value, [item.id]: values };
  } catch (err) { if (!disposed && generation === versionEpoch) error.value = err instanceof Error ? err.message : "用例版本读取失败"; }
}
function toggle(id: string, selected: boolean) {
  if (props.disabled) return;
  emit('update:modelValue', selected ? [...new Set([...props.modelValue, id])] : props.modelValue.filter(value => value !== id));
}
watch(() => props.projectId, () => { ++versionEpoch; cases.value = []; versions.value = {}; offset = 0; query.value = ""; void load(); }, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; ++versionEpoch; });
</script>

<template>
  <section aria-label="选择影响分析用例版本">
    <label>查询当前项目用例<input v-model="query" aria-label="查询影响用例" maxlength="500" :disabled="disabled" @keydown.enter.prevent="load()"></label>
    <button class="secondary-btn" :disabled="busy || disabled" @click="load()">查询用例</button>
    <p>已选 {{ modelValue.length }} 个固定版本；查询不按测试模式过滤。</p>
    <section v-for="item in cases" :key="item.id">
      <p>{{ item.case_key }} · {{ item.title }} · {{ item.lifecycle_status }}</p>
      <button class="secondary-btn" :disabled="disabled" @click="readVersions(item)">读取 {{ item.case_key }} 的完整版本</button>
      <section v-for="version in versions[item.id] || []" :key="version.id">
        <label><input type="checkbox" :checked="modelValue.includes(version.id)" :disabled="disabled"
          :aria-label="`${item.case_key} 版本 ${version.version}`" @change="toggle(version.id, ($event.target as HTMLInputElement).checked)">
          V{{ version.version }} · {{ version.id }}{{ item.active_version_id === version.id ? '〔当前启用〕' : '' }}</label>
        <details><summary>查看完整用例 V{{ version.version }}</summary><pre>{{ JSON.stringify(version, null, 2) }}</pre></details>
      </section>
    </section>
    <button v-if="hasMore" class="secondary-btn" :disabled="busy || disabled" @click="load(true)">更多项目用例</button>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
