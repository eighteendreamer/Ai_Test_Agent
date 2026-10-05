<script setup lang="ts">
import type { GenerationGroup, GenerationScope, GenerationTask } from "../../../services/caseGeneration.types";
import { useGenerationRetries } from "./useGenerationRetries";
const props = defineProps<{ parent: GenerationTask; group: GenerationGroup; scope: GenerationScope; canWrite: boolean; disabled: boolean }>();
const { items, hasMore, pending, confirmedFailure, expanded, responses, versions, busy, error, message, storageWarning,
  availability, ready, loadHistory, recover, submit, resetFailed, refresh, readResponse, readVersion, download, toggle } = useGenerationRetries(props);
const statuses = { queued: "等待重试", running: "重试处理中", completed: "重试草稿或候选已保存", failed: "重试任务失败" };
</script>

<template>
  <section class="generation-retry-panel" :aria-label="`模块重试 ${parent.id}`">
    <h5>失败模块的新任务</h5><p>原失败任务：{{ parent.id }}。原记录与固定范围保留，新任务结果单独查看。</p>
    <p>{{ availability.reason }}</p><p v-if="!canWrite">当前项目不可创建新重试；原请求与历史仍可读取。</p>
    <button v-if="availability.strategy" type="button" class="secondary-btn" :disabled="!ready" @click="submit()">为失败模块创建新任务 {{ parent.id }}</button>
    <section v-if="pending" aria-label="原模块重试请求">
      <p>原重试请求编号：{{ pending.creation_key }}</p><p v-if="!confirmedFailure">提交结果尚未确认，保留原父任务与编号。</p>
      <p v-else>原重试提交已明确拒绝，请核对失败原因后再提交。</p>
      <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="recover">查询原模块重试提交</button>
      <button v-if="!confirmedFailure" type="button" class="secondary-btn" :disabled="busy || disabled" @click="submit(true)">重试原模块提交</button>
      <button v-else type="button" class="secondary-btn" :disabled="busy || disabled" @click="resetFailed">结束已拒绝重试编号</button>
      <details><summary>原重试参数</summary><pre>{{ JSON.stringify(pending, null, 2) }}</pre></details>
    </section>
    <p v-if="busy" role="status">正在读取或提交模块重试…</p><p v-if="message" role="status">{{ message }}</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
    <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="loadHistory()">刷新模块重试历史 {{ parent.id }}</button>
    <section v-for="item in items" :key="item.retry.id" :aria-label="`重试任务 ${item.task.id}`" class="retry-result">
      <p>{{ statuses[item.task.status] }} · {{ item.task.id }}</p><p>{{ item.task.message }} · {{ item.task.error_code }}</p>
      <p>策略：{{ item.retry.strategy === 'saved_response' ? '复用原持久响应' : '执行原固定请求' }} · 配置修订 {{ item.retry.config.revision }}</p>
      <p v-if="item.task.checkpoint?.receipt">结果仍需评审，不表示测试通过或需求已部署。</p>
      <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="refresh(item)">刷新重试任务 {{ item.task.id }}</button>
      <section v-for="reference in item.task.checkpoint?.receipt?.versions || []" :key="reference.version_id">
        <p>重试草稿 {{ reference.case_id }} · V{{ reference.version }} · {{ reference.version_id }}</p>
        <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="readVersion(item, reference.version_id)">读取完整重试草稿 {{ reference.version_id }}</button>
        <template v-if="versions[reference.version_id]"><details><summary>完整重试草稿 JSON 与自身说明</summary><pre>{{ JSON.stringify(versions[reference.version_id], null, 2) }}</pre></details>
          <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="download(versions[reference.version_id], `generation-retry-draft-${reference.version_id}.json`)">下载重试草稿 {{ reference.version_id }}</button>
        </template>
      </section>
      <details v-if="item.task.checkpoint?.receipt?.retirement_target"><summary>重试停用候选（待评审）</summary><pre>{{ JSON.stringify(item.task.checkpoint.receipt.retirement_target, null, 2) }}</pre></details>
      <details><summary>完整重试关系、状态与收据</summary><pre>{{ JSON.stringify(item, null, 2) }}</pre></details>
      <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="download(item, `generation-retry-${item.retry.id}.json`)">下载重试关系与结果 {{ item.task.id }}</button>
      <button v-if="item.task.checkpoint?.response" type="button" class="secondary-btn" :disabled="busy || disabled" @click="readResponse(item)">读取重试原响应 {{ item.task.id }}</button>
      <template v-if="responses[item.task.id]"><details><summary>完整重试原响应</summary><pre>{{ JSON.stringify(responses[item.task.id], null, 2) }}</pre></details>
        <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="download(responses[item.task.id], `generation-retry-response-${item.task.id}.json`)">下载重试原响应 {{ item.task.id }}</button>
      </template>
      <template v-if="item.task.status === 'failed'">
        <button type="button" class="secondary-btn" :disabled="busy || disabled" @click="toggle(item.task.id)">{{ expanded.includes(item.task.id) ? '收起' : '查看' }}失败子任务的重试 {{ item.task.id }}</button>
        <GenerationRetryPanel v-if="expanded.includes(item.task.id)" :parent="item.task" :group="group" :scope="scope" :can-write="canWrite" :disabled="disabled || busy" />
      </template>
    </section>
    <button v-if="hasMore" type="button" class="secondary-btn" :disabled="busy || disabled" @click="loadHistory(true)">更多模块重试 {{ parent.id }}</button>
  </section>
</template>

<style scoped>
.generation-retry-panel { border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-top: 12px; overflow-wrap: anywhere; }
.retry-result { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
