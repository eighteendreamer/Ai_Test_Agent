<script setup lang="ts">
import { ref, watch } from "vue";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import RequirementMatrix from "./RequirementMatrix.vue";
import CaseGenerationPanel from "./CaseGenerationPanel.vue";
import { useRequirementReview } from "./useRequirementReview";

const props = defineProps<{ projectId: string; documentId: string; view: DocumentImpactSnapshotView }>();
const { reviews, bundles, ruleId, requirementId, matrixId, revision, decision, change, title, statement, note, points,
  pending, confirmedFailure, canWrite, loading, busy, error, storageWarning, message, selectedRule, latest, ready, cases, draftParentVersionId,
  addPoint, submit, recover, resetFailed, loadHistory } = useRequirementReview(props);
const visibleReviews = ref(20);
const generationOpen = ref(false);
watch(() => [props.projectId, props.documentId, props.view.snapshot.id], () => { visibleReviews.value = 20; generationOpen.value = false; });
</script>

<template>
  <section class="requirement-review-panel" aria-label="需求评审与追踪">
    <h4>需求评审与追踪</h4>
    <p>固定影响结果：{{ view.snapshot.id }}。评审保存需求与明确设计关联，不自动启用用例或执行测试。</p>
    <p v-if="!canWrite && !loading">当前项目不可提交新评审；历史与原请求仍可恢复。</p>
    <button type="button" class="secondary-btn" :disabled="loading || busy" @click="loadHistory()">刷新需求与评审历史</button>
    <p v-if="loading" role="status">正在读取需求与完整评审历史…</p>
    <form @submit.prevent="submit()"><fieldset :disabled="busy || loading || !canWrite || !!pending">
      <label>选择规则候选<select v-model="ruleId" aria-label="选择规则候选"><option v-for="rule in view.result.rules" :key="rule.id" :value="rule.id">{{ rule.candidate.summary }}</option></select></label>
      <section v-if="selectedRule" aria-label="所选规则依据">
        <p>{{ selectedRule.candidate.reason }}</p>
        <blockquote v-for="(citation, index) in selectedRule.candidate.document_citations" :key="index">{{ citation.quote }}</blockquote>
        <p v-if="latest">最新评审：第 {{ latest.review_number }} 次 · {{ latest.id }} · {{ latest.request.decision }}</p>
        <p v-else>尚无已读取的评审决定。</p>
        <p>依据限制：{{ view.result.basis_limitations.join('、') || '本快照未声明限制' }} · 当前环境是否生效未知</p>
      </section>
      <label>评审决定<select v-model="decision" aria-label="评审决定"><option value="accept">接受为正式需求</option><option value="reject">拒绝候选</option><option value="defer">暂缓评审</option></select></label>
      <template v-if="decision === 'accept'">
        <label>关联需求<select v-model="requirementId" aria-label="关联需求"><option value="">新建需求</option>
          <option v-for="bundle in bundles" :key="bundle.requirement.id" :value="bundle.requirement.id">{{ bundle.version.title }} · V{{ bundle.version.version }} · {{ bundle.requirement.id }}</option>
        </select></label>
        <p v-if="requirementId">本次编辑依据版本：{{ draftParentVersionId }}。</p>
        <p v-if="requirementId && bundles.find(bundle => bundle.requirement.id === requirementId)?.version.id !== draftParentVersionId">需求目录已有更新，本表单仍保留原编辑依据。请重新选择需求并核对内容。</p>
        <label>需求变化分类<select v-model="change" aria-label="需求变化分类"><option value="added">新增</option><option value="modified">修改</option><option value="removed">移除</option></select></label>
        <label>需求标题<input v-model="title" aria-label="需求标题" required maxlength="240"></label>
        <label>需求正文<textarea v-model="statement" aria-label="需求正文" required maxlength="20000" rows="4"></textarea></label>
        <section v-if="change !== 'removed'" aria-label="验证点与用例关联">
          <h5>验证点与明确用例关联</h5><p>旧版本关联不会自动复制。AI影响等级是建议；请核对具体用例是否能验证本版需求。</p>
          <section v-for="(point, index) in points" :key="index" class="point-editor">
            <label>验证点编号<input v-model="point.key" :aria-label="`验证点编号 ${index + 1}`" maxlength="160" required></label>
            <label>验证点说明<textarea v-model="point.description" :aria-label="`验证点说明 ${index + 1}`" maxlength="4000" rows="2" required></textarea></label>
            <label v-for="item in cases" :key="item.id" class="case-link"><input v-model="point.caseVersions" type="checkbox" :value="item.id" :aria-label="`验证点 ${index + 1} 关联版本 ${item.id}`">{{ item.label }}</label>
            <button type="button" class="secondary-btn" @click="points.splice(index, 1)">移除验证点 {{ index + 1 }}</button>
          </section>
          <button type="button" class="secondary-btn" :disabled="points.length >= 100" @click="addPoint">添加验证点</button>
        </section>
        <p v-else>只追加需求移除版本，保留历史用例和结果；不自动停用用例。</p>
      </template>
      <label>评审理由<textarea v-model="note" aria-label="评审理由" required maxlength="8000" rows="2"></textarea></label>
      <button type="submit" class="secondary-btn" :disabled="!ready">保存需求评审</button>
    </fieldset></form>
    <section v-if="pending" aria-label="原需求评审请求">
      <p>原评审请求编号：{{ pending.request_id }}</p>
      <p v-if="!confirmedFailure">提交结果尚未确认；查询与重试保留原编号和全部参数。</p>
      <p v-else>原请求已明确拒绝，请重新读取依据并确认后再提交新决定。</p>
      <button type="button" class="secondary-btn" :disabled="busy" @click="recover">查询原需求评审</button>
      <button v-if="!confirmedFailure" type="button" class="secondary-btn" :disabled="busy" @click="submit(true)">重试原需求评审</button>
      <button v-else type="button" class="secondary-btn" :disabled="busy" @click="resetFailed">重新读取依据并评审</button>
      <details><summary>原评审参数</summary><pre>{{ JSON.stringify(pending, null, 2) }}</pre></details>
    </section>
    <p v-if="message" role="status">{{ message }}</p><p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <details><summary>该影响结果的评审历史（{{ reviews.length }}）</summary>
      <section v-for="review in reviews.slice(-visibleReviews).reverse()" :key="review.id">
        <p>第 {{ review.review_number }} 次 · {{ review.request.decision }} · {{ review.created_at }} · {{ review.id }}</p><p>{{ review.request.note }}</p>
        <button v-if="review.requirement_version" type="button" class="secondary-btn" @click="matrixId = review.requirement_version.requirement_id">查看评审需求 {{ review.requirement_version.title }}</button>
        <details><summary>固定评审来源与决定</summary><pre>{{ JSON.stringify(review, null, 2) }}</pre></details>
      </section>
      <button v-if="visibleReviews < reviews.length" type="button" class="secondary-btn" @click="visibleReviews += 20">显示更多需求评审</button>
    </details>
    <label>查看需求矩阵<select v-model="matrixId" aria-label="查看需求矩阵"><option value="">请选择需求</option>
      <option v-for="bundle in bundles" :key="bundle.requirement.id" :value="bundle.requirement.id">{{ bundle.version.title }} · V{{ bundle.version.version }}</option>
    </select></label>
    <RequirementMatrix v-if="matrixId" :project-id="projectId" :document-id="documentId" :requirement-id="matrixId" :revision="revision" :can-write="canWrite" :disabled="busy || loading" />
    <button type="button" class="secondary-btn" @click="generationOpen = !generationOpen">{{ generationOpen ? '收起需求增量生成' : '打开需求增量生成' }}</button>
    <CaseGenerationPanel v-if="generationOpen" :project-id="projectId" :document-id="documentId" :view="view" :reviews="reviews" :can-write="canWrite" />
  </section>
</template>

<style scoped>
.requirement-review-panel { margin-top: 20px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; overflow-wrap: anywhere; }
fieldset { border: 0; padding: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.point-editor { padding: 12px; margin: 8px 0; border: 1px solid var(--border); border-radius: 8px; }
.case-link { flex-direction: row; align-items: flex-start; }
input, textarea, select { max-width: 100%; min-width: 0; }
blockquote, pre { white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote { border-left: 3px solid var(--border); padding-left: 12px; margin: 8px 0; }
pre { max-height: 300px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
