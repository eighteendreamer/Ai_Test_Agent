<script setup lang="ts">
import { ref, watch } from "vue";
import type { RequirementMatrix } from "../../../services/requirements.types";
import type { TestCaseVersionRecord } from "../../../types";
import JsonContentTree from "../../projects/JsonContentTree.vue";
import TestCaseContentDialog from "../../projects/TestCaseContentDialog.vue";
import { useRequirementCoverage } from "./useRequirementCoverage";

const props = defineProps<{ matrix: RequirementMatrix; canWrite: boolean; disabled: boolean }>();
const emit = defineEmits<{ matrix: [value: RequirementMatrix]; busy: [value: boolean] }>();
const { history, hasMore, choice, cases, caseMore, query, caseId, record, versions, versionId, selected,
  rows, note, pending, confirmedFailure, busy, error, message, storageWarning, canEdit, ready,
  refreshBasis, moreHistory, selectHistory, loadCases, readCase, readAssociation, addVersion, removeVersion,
  recover, submit, resetFailed } = useRequirementCoverage(props, value => emit("matrix", value));
const reviewOpen = ref(false);
const labels = { draft: "草稿", pending_review: "待评审", approved: "已确认", unavailable: "历史评审状态未知" };
const content = (version: TestCaseVersionRecord) => version.content_json ?? { preconditions: version.preconditions,
  steps: version.steps, assertions: version.assertions, test_data: version.test_data, cleanup: version.cleanup };
watch(busy, value => emit("busy", value));
watch(() => [props.matrix.requirement.project_id, props.matrix.version.id, canEdit.value], () => { reviewOpen.value = false; });
async function reviewed() { await readCase(); }
</script>

<template>
  <section aria-label="用例覆盖确认" class="coverage-panel">
    <h5>V{{ matrix.version.version }} · 用例覆盖确认</h5>
    <p>设计覆盖不表示测试已通过；确认不会自动启用用例。编辑以最新覆盖为基线，历史选择只用于查看。</p>
    <p v-if="!canEdit">当前项目不可提交新覆盖；原请求和固定历史仍可查询。</p>
    <label>覆盖确认历史<select v-model="choice" aria-label="覆盖确认历史" :disabled="busy || disabled" @change="selectHistory">
      <option value="latest">最新确认</option><option value="initial">原始需求关联</option>
      <option v-for="item in history" :key="item.id" :value="item.id">第 {{ item.revision }} 次 · {{ item.created_at }} · {{ item.id }}</option>
    </select></label>
    <button type="button" class="secondary-btn" :disabled="busy || disabled || !!pending" @click="refreshBasis">重新读取最新覆盖与编辑依据</button>
    <button v-if="hasMore" type="button" class="secondary-btn" :disabled="busy || disabled" @click="moreHistory">读取更多覆盖确认</button>
    <details v-if="matrix.coverage_review"><summary>本次查看的固定覆盖确认</summary>
      <p>第 {{ matrix.coverage_review.revision }} 次 · {{ matrix.coverage_review.id }}</p>
      <p v-for="link in matrix.coverage_review.case_links" :key="link.case_version_id">确认时版本 {{ link.case_version_id }}：{{ labels[link.review_status] }}</p>
      <pre>{{ JSON.stringify(matrix.coverage_review, null, 2) }}</pre>
    </details>

    <fieldset :disabled="busy || disabled">
      <legend>当前项目用例与完整版本</legend>
      <label>查询项目用例<input v-model="query" aria-label="查询项目用例" maxlength="500"></label>
      <button type="button" class="secondary-btn" @click="loadCases()">查询当前项目用例</button>
      <button v-if="caseMore" type="button" class="secondary-btn" @click="loadCases(true)">读取更多项目用例</button>
      <label>覆盖候选用例<select v-model="caseId" aria-label="覆盖候选用例" @change="readCase">
        <option value="">请选择用例</option><option v-for="item in cases" :key="item.id" :value="item.id">{{ item.case_key }} · {{ item.title }} · {{ item.lifecycle_status }}</option>
      </select></label>
      <template v-if="record">
        <label>覆盖候选完整版本<select v-model="versionId" aria-label="覆盖候选完整版本">
          <option v-for="version in versions" :key="version.id" :value="version.id">V{{ version.version }} · {{ labels[record.version_reviews?.[version.id]?.status ?? 'unavailable'] }} · {{ version.id }}</option>
        </select></label>
        <template v-if="selected">
          <p>版本 {{ selected.id }} · SHA256 {{ selected.content_hash }}</p>
          <p class="notes">{{ selected.execution_notes }}</p>
          <details open><summary>候选完整 JSON 与自身说明</summary><JsonContentTree label="用例" :value="content(selected)" /></details>
          <details><summary>候选完整版本和来源</summary><pre>{{ JSON.stringify(selected, null, 2) }}</pre></details>
          <button type="button" class="secondary-btn" :disabled="!canEdit || !!pending || matrix.removed" @click="addVersion">加入所选固定版本</button>
          <button type="button" class="secondary-btn" :disabled="!canEdit || !!pending" @click="reviewOpen = true">打开所选用例评审</button>
        </template>
      </template>
    </fieldset>

    <form @submit.prevent="submit()"><fieldset :disabled="busy || disabled">
      <legend>确认完整关联集合</legend>
      <p v-if="!rows.length">当前关联集合为空。填写说明并保存将明确撤销已有覆盖。</p>
      <section v-for="(row, index) in rows" :key="row.versionId" class="coverage-row" :aria-label="`覆盖关联 ${row.versionId}`">
        <p>用例 {{ row.record?.case_key ?? row.caseId }} · 固定版本 {{ row.versionId }}</p>
        <button type="button" class="secondary-btn" @click="readAssociation(row)">读取完整关联用例 {{ row.versionId }}</button>
        <template v-if="row.version">
          <p>实际读取 SHA256 {{ row.contentHash }} · {{ labels[row.record?.version_reviews?.[row.versionId]?.status ?? 'unavailable'] }}</p>
          <p class="notes">{{ row.version.execution_notes }}</p>
          <details><summary>关联完整 JSON、版本及来源</summary><JsonContentTree label="用例" :value="content(row.version)" /><pre>{{ JSON.stringify(row.version, null, 2) }}</pre></details>
        </template><p v-else>请读取完整固定版本后确认其覆盖点。</p>
        <label v-for="point in matrix.version.points" :key="point.id" class="point-choice"><input v-model="row.points" type="checkbox" :value="point.key" :disabled="!canEdit || !!pending" :aria-label="`覆盖 ${row.versionId} 验证点 ${point.key}`">{{ point.key }} · {{ point.description }}</label>
        <label>生成任务来源（可选）<input v-model="row.taskId" :aria-label="`覆盖 ${row.versionId} 生成任务`" :disabled="!canEdit || !!pending" maxlength="36" placeholder="明确关联原生成任务编号"></label>
        <button type="button" class="secondary-btn" :disabled="!canEdit || !!pending" @click="removeVersion(index)">移除覆盖版本 {{ row.versionId }}</button>
      </section>
      <label>覆盖确认说明<textarea v-model="note" aria-label="覆盖确认说明" :disabled="!canEdit || !!pending" maxlength="8000" rows="3" required></textarea></label>
      <button type="submit" class="secondary-btn" :disabled="!ready">保存用例覆盖确认</button>
    </fieldset></form>

    <section v-if="pending" aria-label="原覆盖确认请求">
      <p>原覆盖请求编号：{{ pending.request_id }}</p>
      <p v-if="!confirmedFailure">提交结果尚未确认；回查和显式重试保留原编号及全部参数。</p>
      <p v-else>原请求已明确拒绝，请重新读取最新依据后再作决定。</p>
      <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="recover">查询原覆盖确认</button>
      <button v-if="!confirmedFailure" type="button" class="secondary-btn" :disabled="busy || disabled" @click="submit(true)">重试原覆盖确认</button>
      <button v-else type="button" class="secondary-btn" :disabled="busy || disabled" @click="resetFailed">重新读取依据并确认覆盖</button>
      <details open><summary>原覆盖确认参数</summary><pre>{{ JSON.stringify(pending, null, 2) }}</pre></details>
    </section>
    <p v-if="busy" role="status">正在读取或保存固定覆盖…</p>
    <p v-if="message" role="status">{{ message }}</p><p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <TestCaseContentDialog v-if="reviewOpen && record" :key="record.id" :test-case="record" :initial-version-id="versionId"
      :can-write="canEdit && !disabled" @close="reviewOpen = false" @saved="reviewed" />
  </section>
</template>

<style scoped>
.coverage-panel { margin: 12px 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; overflow-wrap: anywhere; }
fieldset { min-width: 0; padding: 8px 0; border: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.point-choice { flex-direction: row; align-items: flex-start; }
.coverage-row { margin: 8px 0; padding: 10px; border: 1px solid var(--border); border-radius: 8px; }
input, textarea, select { min-width: 0; max-width: 100%; }
pre, .notes { white-space: pre-wrap; overflow-wrap: anywhere; }
pre { max-height: 300px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
