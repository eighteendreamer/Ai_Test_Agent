<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";

const props = defineProps<{ view: DocumentImpactSnapshotView }>();
const visible = ref(20);
const error = ref("");
const units = computed(() => props.view.result.units.slice(0, visible.value));
const levels = { none: "无影响", minor: "轻微影响", major: "重大影响候选", uncertain: "影响不确定" };
const kinds = { added: "新增候选", modified: "修改候选", removed: "删除候选", uncertain: "变化不确定" };
const statuses = { analyzed: "已分析", evidence_insufficient: "依据不足", rejected: "响应未通过核验", ineligible: "不满足分析条件",
  unknown: "调用结果未知", unprocessed: "尚未处理", incomplete: "分析未完整" };
watch(() => props.view.snapshot.id, () => { visible.value = 20; error.value = ""; });
function download() {
  error.value = "";
  try {
    const url = URL.createObjectURL(new Blob([JSON.stringify(props.view, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    try { link.href = url; link.download = `impact-${props.view.snapshot.id}.json`; link.click(); }
    finally { URL.revokeObjectURL(url); }
  } catch (err) { error.value = err instanceof Error ? err.message : "影响结果下载失败"; }
}
</script>

<template>
  <article aria-label="影响结果详情" class="document-impact-snapshot">
    <h3>固定影响结果 · {{ view.result.status === 'partial' ? '部分结果' : '声明范围已分析' }}</h3>
    <p>结果：{{ view.snapshot.id }} · SHA256：{{ view.snapshot.sha256 }}</p>
    <p>语义结论待评审 · 当前环境是否生效未知</p>
    <p>已分析 {{ view.snapshot.analyzed_count }} / {{ view.snapshot.unit_count }} 个单元 · {{ view.snapshot.rule_count }} 条规则候选</p>
    <p>固定输入：{{ view.result.input_sha256 }} · 固定范围：{{ view.result.manifest_sha256 }}</p>
    <section v-if="view.result.basis_limitations.length" aria-label="影响依据限制">
      <h4>依据限制</h4><ul><li v-for="code in view.result.basis_limitations" :key="code">{{ code }}</li></ul>
    </section>
    <section v-if="view.result.overlaps.length" aria-label="规则候选冲突">
      <h4>需要评审的潜在重叠或冲突</h4><p v-for="(overlap, index) in view.result.overlaps" :key="index">{{ overlap.rule_ids.join('、') }}</p>
    </section>
    <h4>文档规则变化候选</h4><p v-if="!view.result.rules.length">没有保存规则候选；请同时核对未完成单元及依据限制。</p>
    <section v-for="rule in view.result.rules.slice(0, visible)" :key="rule.id" class="impact-rule" :aria-label="`规则候选 ${rule.id}`">
      <h5>{{ kinds[rule.candidate.kind] }} · {{ rule.candidate.summary }}</h5>
      <p>{{ rule.candidate.reason }}</p><p>候选编号：{{ rule.id }} · 来源 {{ rule.origins.length }} 处</p>
      <p>适用性建议：{{ { unknown: '未知', current: '当前环境', future: '未来版本' }[rule.candidate.applicability_suggestion] }}（待核实）</p>
      <ul><li v-for="(citation, index) in rule.candidate.document_citations" :key="index">
        <p>原文来源 {{ citation.source_id }} · 字符 [{{ citation.start_char }}, {{ citation.end_char }}) · {{ citation.text_sha256 }}</p>
        <blockquote>{{ citation.quote }}</blockquote>
      </li></ul>
      <ul><li v-for="(uncertainty, index) in rule.candidate.uncertainties" :key="index">{{ uncertainty }}</li></ul>
      <details><summary>候选原始记录与来源</summary><pre>{{ JSON.stringify(rule, null, 2) }}</pre></details>
    </section>
    <h4>用例判断与完整处理范围</h4>
    <section v-for="unit in units" :key="`${unit.unit.kind}:${unit.unit.id}`" class="impact-unit" :aria-label="`影响单元 ${unit.unit.id}`">
      <h5>{{ { case: '用例', segment: '文档分段', difference: '差异' }[unit.unit.kind] }} · {{ statuses[unit.status] }}</h5>
      <p>{{ unit.unit.id }}</p><p v-if="unit.reason_codes.length">原因：{{ unit.reason_codes.join('、') }}</p>
      <ul><li v-for="(item, index) in unit.unresolved" :key="index">{{ item }}</li></ul>
      <section v-for="candidate in unit.candidates" :key="candidate.key">
        <p>规则 {{ candidate.key }} · {{ candidate.summary }}</p>
        <section v-for="impact in candidate.case_impacts" :key="impact.version_id" class="impact-case">
          <h5>{{ levels[impact.level] }} · 用例版本 {{ impact.version_id }}</h5><p>{{ impact.reason }}</p>
          <ul><li v-for="(point, index) in impact.verification_points" :key="index">验证点：{{ point }}</li></ul>
          <details open><summary>固定用例字段引用</summary><section v-for="(citation, index) in impact.citations" :key="index">
            <p>{{ citation.root }} · 路径 {{ JSON.stringify(citation.path) }} · 版本 {{ citation.version_id }}</p>
            <pre>{{ JSON.stringify(citation.value, null, 2) }}</pre>
          </section></details>
        </section>
      </section>
      <details><summary>本单元调用与核验记录</summary><pre>{{ JSON.stringify({ calls: unit.calls, final_check: unit.final_check }, null, 2) }}</pre></details>
    </section>
    <button v-if="visible < Math.max(view.result.units.length, view.result.rules.length)" class="secondary-btn" @click="visible += 20">显示更多影响记录</button>
    <details><summary>冻结依据、配置与模型声明</summary><pre>{{ JSON.stringify(view.snapshot.seed, null, 2) }}</pre></details>
    <button class="secondary-btn" @click="download">下载完整影响结果 JSON</button><p v-if="error" role="alert">{{ error }}</p>
  </article>
</template>

<style scoped>
.document-impact-snapshot { margin-top: 16px; overflow-wrap: anywhere; }
.impact-rule, .impact-unit { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
.impact-case { padding: 12px; background: var(--surface-muted); margin: 8px 0; border-radius: 8px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
blockquote { white-space: pre-wrap; margin: 8px 0; padding-left: 12px; border-left: 3px solid var(--border); }
[role="alert"] { color: var(--red); }
</style>
