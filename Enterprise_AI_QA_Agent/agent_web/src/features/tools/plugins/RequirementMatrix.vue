<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { listRequirementVersions, readRequirementMatrix } from "../../../services/requirements.api";
import type { RequirementMatrix, RequirementVersion } from "../../../services/requirements.types";
import { assertRequirement, assertVersions } from "./requirementProduct";

const props = defineProps<{ projectId: string; documentId: string; requirementId: string; revision: number }>();
const versions = ref<RequirementVersion[]>([]);
const versionId = ref(""); const matrix = ref<RequirementMatrix | null>(null);
const busy = ref(false); const error = ref("");
let epoch = 0; let disposed = false;
const labels = { uncovered: "未覆盖", design_covered: "已有设计覆盖", needs_review: "关联用例待复核" };
async function load(reset = false) {
  const generation = ++epoch;
  const { projectId, documentId, requirementId } = props;
  const current = () => !disposed && generation === epoch;
  busy.value = true; error.value = ""; matrix.value = null;
  if (reset) { versions.value = []; versionId.value = ""; }
  try {
    if (reset) {
      const history = await listRequirementVersions(projectId, documentId, requirementId);
      if (!current()) return;
      assertVersions(history, requirementId); versions.value = history;
    }
    const selected = versionId.value || undefined;
    const value = await readRequirementMatrix(projectId, documentId, requirementId, selected);
    if (!current()) return;
    assertRequirement(value, projectId, documentId);
    if (value.requirement.id !== requirementId || value.version.id !== (selected || value.requirement.current_version_id)
      || !versions.value.some(version => version.id === value.version.id)) throw new Error("矩阵与指定需求版本不一致，请刷新历史");
    matrix.value = value; versionId.value = value.version.id;
  } catch (err) { if (current()) error.value = err instanceof Error ? err.message : "需求矩阵读取失败"; }
  finally { if (current()) busy.value = false; }
}
watch(() => [props.projectId, props.documentId, props.requirementId, props.revision], () => { void load(true); }, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; });
</script>

<template>
  <section aria-label="需求追踪矩阵" class="requirement-matrix">
    <h4>需求版本与设计追踪</h4>
    <label>需求历史版本<select v-model="versionId" aria-label="需求历史版本" :disabled="busy" @change="load()">
      <option v-for="version in versions" :key="version.id" :value="version.id">V{{ version.version }} · {{ version.title }}</option>
    </select></label>
    <button type="button" class="secondary-btn" :disabled="busy" @click="load(true)">刷新需求版本与矩阵</button>
    <p v-if="busy" role="status">正在读取固定需求版本…</p><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="matrix">
      <h5>V{{ matrix.version.version }} · {{ matrix.version.title }}</h5><p class="statement">{{ matrix.version.statement }}</p>
      <p v-if="matrix.version.id !== matrix.requirement.current_version_id">正在查看历史版本，当前版本为 V{{ matrix.requirement.latest_version }}。</p>
      <p v-if="matrix.removed">该需求版本已移除；历史用例和结果继续保留。</p>
      <p>执行结果尚未关联 · 当前环境是否生效未知</p>
      <section v-for="item in matrix.points" :key="item.point.id" class="matrix-point" :aria-label="`验证点 ${item.point.key}`">
        <h5>{{ item.point.key }} · {{ labels[item.design_status] }}</h5><p>{{ item.point.description }}</p>
        <p v-for="link in item.case_links" :key="link.case_version_id">明确覆盖：用例 {{ link.case_id }} · 版本 {{ link.case_version_id }}</p>
        <details v-if="item.previous_case_links.length"><summary>历史关联（需要按本版需求复核）</summary>
          <p v-for="link in item.previous_case_links" :key="link.case_version_id">用例 {{ link.case_id }} · 版本 {{ link.case_version_id }}</p>
        </details>
      </section>
      <details><summary>需求身份、固定版本与完整矩阵依据</summary><pre>{{ JSON.stringify(matrix, null, 2) }}</pre></details>
    </template>
  </section>
</template>

<style scoped>
.requirement-matrix { margin-top: 16px; overflow-wrap: anywhere; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.matrix-point { border-top: 1px solid var(--border); padding: 8px 0; }
.statement, pre { white-space: pre-wrap; overflow-wrap: anywhere; }
pre { max-height: 300px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
