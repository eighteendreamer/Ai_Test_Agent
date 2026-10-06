<script setup lang="ts">
import { watch } from "vue";
import type { RequirementMatrix } from "../../../services/requirements.types";
import JsonContentTree from "../../projects/JsonContentTree.vue";
import { executionCoverage } from "./requirementExecutionProduct";
import { useRequirementExecution } from "./useRequirementExecution";

const props = defineProps<{ matrix: RequirementMatrix; canWrite: boolean; disabled: boolean }>();
const emit = defineEmits<{ matrix: [value: RequirementMatrix]; busy: [value: boolean] }>();
const { history, hasMore, choice, candidates, candidateMore, resultId, full, fixedCase, rows, note, pending, confirmedFailure,
  busy, error, message, storageWarning, canEdit, ready, refreshBasis, moreHistory, selectHistory, loadCandidates, readResult,
  allowedPoints, addCheck, removeCheck, recover, submit, resetFailed } = useRequirementExecution(props, value => emit("matrix", value));
watch(busy, value => emit("busy", value));
const labels = { passed: "通过", failed: "失败", not_run: "未执行" };
</script>

<template>
  <section aria-label="正文执行核验关联" class="execution-panel">
    <h5>V{{ matrix.version.version }} · 正文执行核验关联</h5>
    <p>覆盖依据 {{ executionCoverage(matrix) }}。仅所选正文检查参与判定；准备、清理和原运行总体结果分别保留。当前环境是否生效未知。</p>
    <p v-if="!canEdit">当前项目不可提交新执行确认；原请求、完整结果和固定历史仍可查询。</p>
    <label>执行确认历史<select v-model="choice" aria-label="执行确认历史" :disabled="busy || disabled" @change="selectHistory">
      <option value="latest">所选覆盖的最新确认</option><option value="initial">不关联执行核验</option>
      <option v-for="item in history" :key="item.id" :value="item.id" :disabled="item.request.coverage_id !== executionCoverage(matrix)">
        第 {{ item.revision }} 次 · {{ item.id }}{{ item.request.coverage_id !== executionCoverage(matrix) ? ' · 其他覆盖依据' : '' }}
      </option>
    </select></label>
    <p>查看历史不会替换编辑集合；提交以重读的全局最新确认作为父记录。</p>
    <button type="button" class="secondary-btn" :disabled="busy || disabled || !!pending" @click="refreshBasis">重新读取执行确认与编辑依据</button>
    <button v-if="hasMore" type="button" class="secondary-btn" :disabled="busy || disabled" @click="moreHistory">读取更多执行确认</button>
    <details v-if="matrix.execution_review"><summary>本次查看的固定执行确认</summary><pre>{{ JSON.stringify(matrix.execution_review, null, 2) }}</pre></details>

    <fieldset :disabled="busy || disabled">
      <legend>当前固定覆盖的实际运行结果</legend>
      <button type="button" class="secondary-btn" @click="loadCandidates()">读取实际执行结果</button>
      <button v-if="candidateMore" type="button" class="secondary-btn" @click="loadCandidates(true)">读取更多执行结果</button>
      <p v-if="!candidates.length">当前覆盖版本没有实际执行结果。</p>
      <label>执行候选结果<select v-model="resultId" aria-label="执行候选结果" @change="readResult">
        <option value="">选择原运行结果</option>
        <option v-for="item in candidates" :key="item.result_id" :value="item.result_id">{{ item.status }} · {{ item.summary }} · {{ item.result_id }}</option>
      </select></label>
      <p v-for="warning in candidates.find(item => item.result_id === resultId)?.warnings || []" :key="warning">{{ warning }}</p>
      <button v-if="resultId" type="button" class="secondary-btn" @click="readResult">重读完整结果与固定用例</button>
      <template v-if="full && fixedCase">
        <p>原运行总体结果 {{ full.result.status }} · 结果 {{ full.result.id }}</p>
        <p v-if="full.result.regression_source_result_id">回归源失败结果 {{ full.result.regression_source_result_id }}</p>
        <p>固定用例版本 {{ fixedCase.id }} · SHA256 {{ fixedCase.content_hash }}</p><p class="notes">{{ fixedCase.execution_notes }}</p>
        <details open><summary>执行时完整 JSON 用例</summary><JsonContentTree label="用例" :value="fixedCase.content_json ?? fixedCase" /></details>
        <details><summary>原完整结果、工具引用与各阶段核验</summary><pre>{{ JSON.stringify(full.result, null, 2) }}</pre></details>
        <section v-for="check in full.checks" :key="`${check.verification_id}/${check.check_index}`" class="check-row">
          <p>{{ check.name }} · {{ labels[check.status] }} · {{ check.method }}</p>
          <JsonContentTree label="正文检查" :value="check.check" />
          <button type="button" class="secondary-btn" :disabled="!canEdit || !!pending || rows.length >= 200"
            @click="addCheck(check.verification_id, check.check_index)">选择正文核验 {{ check.check_index }}</button>
        </section>
      </template>
    </fieldset>

    <form @submit.prevent="submit()"><fieldset :disabled="busy || disabled || !canEdit || !!pending">
      <legend>确认完整核验关联集合</legend>
      <p v-if="!rows.length">集合为空。填写说明并保存会明确撤销已有执行关联，历史继续保留。</p>
      <section v-for="(row, index) in rows" :key="`${row.result_id}/${row.verification_id}/${row.check_index}`" class="check-row">
        <p>{{ row.check.name }} · {{ row.check.status }} · 结果 {{ row.result_id }}</p>
        <label v-for="point in allowedPoints(row)" :key="point.key" class="point-choice">
          <input v-model="row.point_keys" type="checkbox" :value="point.key" :aria-label="`核验 ${index + 1} 验证点 ${point.key}`">{{ point.key }} · {{ point.description }}
        </label>
        <details><summary>选中正文核验依据</summary><pre>{{ JSON.stringify(row, null, 2) }}</pre></details>
        <button type="button" class="secondary-btn" @click="removeCheck(index)">移除正文核验 {{ index + 1 }}</button>
      </section>
      <label>执行确认说明<textarea v-model="note" aria-label="执行确认说明" maxlength="8000" /></label>
      <button type="submit" class="primary-btn" :disabled="!ready">保存正文执行确认</button>
    </fieldset></form>
    <section v-if="pending" aria-label="原执行确认请求">
      <p>{{ confirmedFailure ? '原请求已明确拒绝；请重读后作新决定。' : '提交结果尚未确认；原编号和参数继续保留。' }}</p>
      <pre>{{ JSON.stringify(pending, null, 2) }}</pre>
      <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="recover">回查原执行确认</button>
      <button v-if="!confirmedFailure && canEdit" type="button" class="secondary-btn" :disabled="busy || disabled" @click="submit(true)">重试原执行确认</button>
      <button v-if="confirmedFailure && canEdit" type="button" class="secondary-btn" :disabled="busy || disabled" @click="resetFailed">重读依据并确认执行</button>
    </section>
    <p v-if="busy" role="status">正在读取固定执行依据…</p><p v-if="message" role="status">{{ message }}</p>
    <p v-if="storageWarning" role="alert">{{ storageWarning }}</p><p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.execution-panel { margin: 12px 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; overflow-wrap: anywhere; }
fieldset { min-width: 0; padding: 8px 0; border: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.point-choice { flex-direction: row; align-items: flex-start; }
.check-row { margin: 8px 0; padding: 10px; border: 1px solid var(--border); border-radius: 8px; }
input, textarea, select { min-width: 0; max-width: 100%; }
pre, .notes { white-space: pre-wrap; overflow-wrap: anywhere; }
pre { max-height: 300px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
