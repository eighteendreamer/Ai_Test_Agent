<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { DocumentDifferenceSnapshotView, DocumentDifferenceRange, DocumentSourceLocation } from "../../../types";

const props = defineProps<{ view: DocumentDifferenceSnapshotView }>();
const kind = ref<"raw" | "normalized">("raw");
const visible = ref(20);
const error = ref("");
const changes = computed(() => props.view.result[kind.value].changes);
const operations = { insert: "新增", delete: "删除", replace: "修改" };
const sides = { old: "旧侧", new: "新侧", both: "双方" };
watch(() => props.view.snapshot.id, () => { kind.value = "raw"; visible.value = 20; error.value = ""; });
watch(kind, () => { visible.value = 20; });
function location(source: DocumentSourceLocation) {
  return source.kind === "page" ? `第 ${source.page} 页` : source.kind === "line"
    ? `第 ${source.start_line}—${source.end_line} 行` : `${source.part} · 正文/表格位置 ${source.path.join(" / ")}`;
}
function rangeLabel(value: DocumentDifferenceRange) {
  return `抽取文本字符 [${value.start_char}, ${value.end_char})`
    + (value.start_line == null ? "" : ` · 第 ${value.start_line}—${value.end_line} 行`);
}
const preview = (text: string) => text.length > 16000 ? text.slice(0, 16000) + "\n〔展示截断，完整内容可下载〕" : text;
function download() {
  error.value = "";
  try {
    const url = URL.createObjectURL(new Blob([JSON.stringify(props.view, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    try { link.href = url; link.download = `difference-${props.view.snapshot.id}.json`; link.click(); }
    finally { URL.revokeObjectURL(url); }
  } catch (err) { error.value = err instanceof Error ? err.message : "比较结果下载失败"; }
}
</script>

<template>
  <article class="document-difference-snapshot" aria-label="比较结果详情">
    <h3>固定文档差异 · {{ view.snapshot.status === 'partial' ? '输入含部分提取结果' : '比较已保存' }}</h3>
    <p>比较结果：{{ view.snapshot.id }} · SHA256：{{ view.snapshot.sha256 }}</p>
    <div class="difference-columns">
      <section v-for="side in (['old', 'new'] as const)" :key="side" :aria-label="`${sides[side]}固定来源`">
        <h4>{{ sides[side] }}</h4>
        <p>原件版本：{{ view.result[side].version_id }}</p><p>解析快照：{{ view.result[side].snapshot_id }}</p>
        <p>原件 SHA256：{{ view.result[side].original_sha256 }}</p>
        <p>解析器：{{ view.result[side].parser.parser_id }} {{ view.result[side].parser.implementation_version }}</p>
        <details><summary>解析选项与依赖版本</summary><pre>{{ JSON.stringify({ options: view.result[side].options, dependencies: view.result[side].dependency_versions }, null, 2) }}</pre></details>
      </section>
    </div>
    <p>原始文本 {{ view.snapshot.summary.raw_changes }} · 换行归一文本 {{ view.snapshot.summary.normalized_changes }} · 章节 {{ view.snapshot.summary.section_changes }} · 表格 {{ view.snapshot.summary.table_changes }}</p>
    <p v-if="view.result.originals_equal">双方原件字节相同。</p>
    <p v-if="view.result.parsing_basis_changed">双方解析依据发生变化，请结合解析器、选项和依赖版本判断。</p>
    <ul class="difference-warnings"><li v-for="(warning, index) in view.result.warnings" :key="index">
      {{ sides[warning.side] }} · {{ warning.code }} · {{ warning.message }}<span v-if="warning.source"> · {{ location(warning.source) }}</span>
    </li></ul>
    <label>文本差异视图<select v-model="kind" aria-label="文本差异视图"><option value="raw">原始抽取文本</option><option value="normalized">仅归一换行</option></select></label>
    <p>字符位置指向原始抽取文本；换行归一保留空格、缩进、数字和 Unicode。</p>
    <p v-if="!changes.length">所选文本视图没有差异；结构变化与解析能力范围请查看下方记录和警告。</p>
    <section v-for="(change, index) in changes.slice(0, visible)" :key="index" class="difference-change" :aria-label="`文本变化 ${index + 1}`">
      <h4>{{ operations[change.operation] }} {{ index + 1 }}</h4>
      <div class="difference-columns"><section v-for="side in (['old', 'new'] as const)" :key="side">
        <strong>{{ sides[side] }} · {{ rangeLabel(change[side]) }}</strong><pre>{{ preview(change[side].text) }}</pre>
        <ul><li v-for="(source, sourceIndex) in change[side].sources.slice(0, 50)" :key="sourceIndex">{{ source.block_id }} · {{ location(source.source) }}</li></ul>
        <p v-if="change[side].sources.length > 50">来源位置仅展示前 50 项，完整内容可下载。</p>
      </section></div>
      <p v-if="change.character_status === 'omitted'">字符细节超过固定资源预算，已省略；完整行差异仍保留。</p>
      <details v-else><summary>字符变化（{{ change.characters.length }}）</summary>
        <div v-for="(character, characterIndex) in change.characters.slice(0, 50)" :key="characterIndex" class="character-change">
          <strong>{{ operations[character.operation] }}</strong>
          <div v-for="side in (['old', 'new'] as const)" :key="side"><span>{{ sides[side] }} · {{ rangeLabel(character[side]) }}</span><pre>{{ preview(character[side].text) }}</pre></div>
        </div><p v-if="change.characters.length > 50">字符细节仅展示前 50 项，完整内容可下载。</p>
      </details>
    </section>
    <p v-if="visible < changes.length">已展示 {{ visible }} / {{ changes.length }} 处文本变化。</p>
    <button v-if="visible < changes.length" class="secondary-btn" @click="visible += 20">显示更多文本变化</button>
    <details v-for="structure in (['sections', 'tables'] as const)" :key="structure" class="difference-structure">
      <summary>{{ structure === 'sections' ? '章节' : '表格' }}变化（{{ view.result[structure].length }}）</summary>
      <section v-for="(change, index) in view.result[structure].slice(0, visible)" :key="index">
        <h4>{{ operations[change.operation] }}</h4><div class="difference-columns">
          <section v-for="side in (['old', 'new'] as const)" :key="side"><strong>{{ sides[side] }}</strong><pre>{{ preview(JSON.stringify(change[side], null, 2)) }}</pre>
            <div v-for="(range, rangeIndex) in change[`${side}_ranges`].slice(0, 50)" :key="rangeIndex">
              {{ rangeLabel(range) }}<ul><li v-for="(source, sourceIndex) in range.sources.slice(0, 50)" :key="sourceIndex">{{ location(source.source) }}</li></ul>
            </div>
          </section>
        </div>
      </section>
      <p v-if="view.result[structure].length > visible">已展示 {{ visible }} / {{ view.result[structure].length }} 处结构变化。</p>
      <button v-if="view.result[structure].length > visible" class="secondary-btn" @click="visible += 20">显示更多结构变化</button>
    </details>
    <details><summary>固定比较策略与实际资源预算</summary>
      <p>配置修订 {{ view.snapshot.seed.config.revision }} · 引擎 {{ view.result.implementation_version }} · Python {{ view.snapshot.seed.python_version }}</p>
      <pre>{{ JSON.stringify({ submitted: view.snapshot.seed.config.payload.policy, effective: view.snapshot.effective_policy }, null, 2) }}</pre>
    </details>
    <button class="secondary-btn" @click="download">下载完整比较结果 JSON</button><p v-if="error" role="alert">{{ error }}</p>
  </article>
</template>

<style scoped>
.document-difference-snapshot { margin-top: 20px; overflow-wrap: anywhere; }
.difference-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.difference-change, .difference-structure { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; background: var(--surface-muted); padding: 8px; }
.difference-warnings { color: var(--orange, #b45309); }
details, button, label { margin-top: 10px; }
[role="alert"] { color: var(--red); }
@media (max-width: 900px) { .difference-columns { grid-template-columns: 1fr; } }
</style>
