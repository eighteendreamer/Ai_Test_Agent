<script setup lang="ts">
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementReview } from "../../../services/requirements.types";
import CaseGenerationSettings from "./CaseGenerationSettings.vue";
import GenerationRetryPanel from "./GenerationRetryPanel.vue";
import { useCaseGeneration } from "./useCaseGeneration";
const props = defineProps<{ projectId: string; documentId: string; view: DocumentImpactSnapshotView; reviews: RequirementReview[]; canWrite: boolean }>();
const { reviewId, targets, includeNew, modeKey, maxNew, modelKey, modes, acceptedReviews, selectedReview, removed, cases, ready,
  pending, confirmedFailure, groups, hasMore, historyBusy, report, scope, responses, versions, sourceVersions,
  busy, error, message, storageWarning, loadHistory, readGroup, submit, recover, resetFailed, refreshReport, readResponse, readVersion, download } = useCaseGeneration(props);
const statuses = { queued: "等待生成", running: "生成中", completed: "草稿或候选已保存", failed: "模块失败" };
const kinds = { update: "更新用例草稿", create: "新建用例草稿", retirement_candidate: "停用候选" };
</script>

<template>
  <section class="case-generation-panel" aria-label="需求增量生成">
    <h4>从确认需求增量生成</h4>
    <p>选择固定需求版本及原用例基线。结果保存为待评审草稿或停用候选，当前启用版本继续保留。</p>
    <p v-if="!canWrite">当前项目不可提交新生成；历史与原请求仍可读取。</p>
    <CaseGenerationSettings :project-id="projectId" :can-write="canWrite" />
    <form @submit.prevent="submit()"><fieldset :disabled="busy || !canWrite || !!pending">
      <label>生成依据需求版本<select v-model="reviewId" aria-label="生成依据需求版本"><option value="">请选择接受决定</option>
        <option v-for="review in acceptedReviews" :key="review.id" :value="review.id">{{ review.requirement_version?.title }} · V{{ review.requirement_version?.version }} · {{ review.requirement_version?.id }}</option>
      </select></label>
      <template v-if="selectedReview?.requirement_version">
        <p>{{ selectedReview.requirement_version.statement }}</p><p>固定需求：{{ selectedReview.requirement_version.id }} · 原评审：{{ selectedReview.id }}</p>
        <p>依据限制：{{ selectedReview.source.basis_limitations.join('、') || '原快照未声明限制' }} · 当前环境是否生效未知</p>
        <p v-if="removed">该需求版本已移除，只保存明确用例的停用候选，不自动停用或删除历史。</p>
        <h5>原影响快照中的用例版本</h5><p>更新保留原用例身份；选择基线不按测试模式过滤。</p>
        <section v-for="item in cases" :key="item.version_id">
          <label class="case-option"><input v-model="targets" type="checkbox" :value="item.version_id" :aria-label="`生成基线 ${item.version_id}`">
            {{ item.case.case_key }} · {{ item.case.title }} · 固定版本 {{ item.version_id }}</label>
          <button type="button" class="secondary-btn" @click="readVersion(item.case.id, item.version_id, true)">读取生成基线 {{ item.case.case_key }}</button>
          <details v-if="sourceVersions[item.version_id]"><summary>完整基线用例 JSON 与自身说明</summary><pre>{{ JSON.stringify(sourceVersions[item.version_id], null, 2) }}</pre></details>
        </section>
        <template v-if="!removed">
          <label class="case-option"><input v-model="includeNew" type="checkbox" aria-label="增量新建用例">同时为新增覆盖生成用例草稿</label>
          <template v-if="includeNew">
            <label>新用例模式元数据<select v-model="modeKey" aria-label="新用例模式元数据" required><option value="">请选择现有测试模式</option>
              <option v-for="mode in modes" :key="mode.key" :value="mode.key">{{ mode.name }}</option>
            </select></label>
            <label>新建用例数量上限<input v-model.number="maxNew" type="number" min="1" max="200" aria-label="新建用例数量上限" required></label>
          </template>
          <label>生成模型配置编号（可选）<input v-model="modelKey" aria-label="生成模型配置编号" maxlength="160" placeholder="留空使用当前生成默认模型"></label>
        </template>
      </template>
      <button type="submit" class="secondary-btn" :disabled="!ready">提交固定增量生成</button>
    </fieldset></form>
    <section v-if="pending" aria-label="原增量生成请求">
      <p>原生成请求编号：{{ pending.creation_key }}</p><p v-if="!confirmedFailure">提交结果尚未确认，保留原编号和全部参数。</p>
      <p v-else>原提交已明确拒绝，请核对依据后再提交新请求。</p>
      <button type="button" class="secondary-btn" :disabled="busy" @click="recover">查询原生成提交</button>
      <button v-if="!confirmedFailure" type="button" class="secondary-btn" :disabled="busy" @click="submit(true)">重试原生成提交</button>
      <button v-else type="button" class="secondary-btn" :disabled="busy" @click="resetFailed">核对依据并重新提交</button>
      <details><summary>原生成参数</summary><pre>{{ JSON.stringify(pending, null, 2) }}</pre></details>
    </section>
    <p v-if="busy" role="status">正在读取或提交固定生成结果…</p>
    <p v-if="message" role="status">{{ message }}</p><p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <section aria-label="文档增量生成历史">
      <h5>文档生成历史</h5><button type="button" class="secondary-btn" :disabled="historyBusy || busy" @click="loadHistory()">刷新生成历史</button>
      <section v-for="group in groups" :key="group.id"><p>{{ group.created_at }} · {{ group.id }} · {{ group.module_ids.length }} 个模块</p>
        <button type="button" class="secondary-btn" :disabled="busy" @click="readGroup(group.id)">查看生成组 {{ group.id }}</button>
      </section>
      <button v-if="hasMore" type="button" class="secondary-btn" :disabled="historyBusy || busy" @click="loadHistory(true)">更多生成历史</button>
    </section>
    <section v-if="report && scope" aria-label="固定增量生成结果">
      <h5>固定生成组 {{ report.group.id }}</h5><p>依据需求V{{ scope.requirement.version }} · {{ scope.requirement.id }} · {{ scope.requirement.statement }}</p>
      <p>固定范围SHA256：{{ scope.sha256 }} · 配置修订 {{ report.group.config.revision }}</p>
      <p>{{ scope.basis_limitations.join('、') || '原范围未声明限制' }} · 当前环境是否生效未知</p>
      <button type="button" class="secondary-btn" :disabled="busy" @click="refreshReport">刷新当前生成结果</button>
      <section v-for="task in report.modules" :key="task.id" :aria-label="`生成模块 ${task.module_id}`" class="module-result">
        <p>{{ kinds[scope.modules.find(item => item.id === task.module_id)!.kind] }} · {{ statuses[task.status] }}</p>
        <p>{{ task.message }}</p><p v-if="task.error_code">{{ task.error_code }}</p>
        <p v-if="task.checkpoint?.phase === 'invoking'">已记录调用意图，响应尚未确认；不能根据失败状态盲目重发。</p>
        <p v-if="task.checkpoint?.receipt">结果已保存，仍需评审；不表示测试通过或需求已部署。</p>
        <section v-for="version in task.checkpoint?.receipt?.versions || []" :key="version.version_id">
          <p>草稿用例 {{ version.case_id }} · V{{ version.version }} · {{ version.version_id }}</p>
          <button type="button" class="secondary-btn" :disabled="busy" @click="readVersion(version.case_id, version.version_id)">读取完整生成草稿 {{ version.version_id }}</button>
          <details v-if="versions[version.version_id]"><summary>完整生成草稿 JSON 与自身说明</summary><pre>{{ JSON.stringify(versions[version.version_id], null, 2) }}</pre></details>
        </section>
        <details v-if="task.checkpoint?.receipt?.retirement_target"><summary>原用例停用候选（待评审）</summary><pre>{{ JSON.stringify(task.checkpoint.receipt.retirement_target, null, 2) }}</pre></details>
        <details><summary>完整模块状态与提交收据</summary><pre>{{ JSON.stringify(task, null, 2) }}</pre></details>
        <button v-if="task.checkpoint?.response" type="button" class="secondary-btn" :disabled="busy" @click="readResponse(task.id)">读取原生成响应 {{ task.id }}</button>
        <template v-if="responses[task.id]"><details><summary>完整原生成响应</summary><pre>{{ JSON.stringify(responses[task.id], null, 2) }}</pre></details>
          <button type="button" class="secondary-btn" :disabled="busy" @click="download(responses[task.id], `generation-response-${task.id}.json`)">下载原生成响应 {{ task.id }}</button>
        </template>
        <GenerationRetryPanel v-if="task.status === 'failed'" :parent="task" :group="report.group" :scope="scope" :can-write="canWrite" :disabled="busy" />
      </section>
      <details><summary>完整固定生成范围与请求</summary><pre>{{ JSON.stringify(scope, null, 2) }}</pre></details>
      <button type="button" class="secondary-btn" :disabled="busy" @click="download(scope, `generation-scope-${report.group.id}.json`)">下载完整生成范围 JSON</button>
    </section>
  </section>
</template>

<style scoped>
.case-generation-panel { margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.case-option { flex-direction: row; align-items: flex-start; }
.module-result { border-top: 1px solid var(--border); padding: 12px 0; }
input, select { max-width: 100%; min-width: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
