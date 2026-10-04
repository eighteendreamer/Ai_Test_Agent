<script setup lang="ts">
import { ref, watch } from "vue";
import type { DocumentParseSnapshotView, DocumentSourceLocation } from "../../../types";

const props = defineProps<{ view: DocumentParseSnapshotView }>();
const visibleBlocks = ref(50);
const error = ref("");
watch(() => props.view.snapshot.id, () => { visibleBlocks.value = 50; error.value = ""; });
const statusLabels = { complete: "文本提取完成", partial: "部分内容或结构无法提取", unsupported: "未提取到可用文本" };
function location(source: DocumentSourceLocation) {
  return source.kind === "page" ? `第 ${source.page} 页` : source.kind === "line"
    ? `第 ${source.start_line}—${source.end_line} 行` : `${source.part} · 正文/表格位置 ${source.path.join(" / ")}`;
}
function download() {
  error.value = "";
  try {
    const url = URL.createObjectURL(new Blob([JSON.stringify(props.view, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    try { link.href = url; link.download = `parse-${props.view.snapshot.id}.json`; link.click(); }
    finally { URL.revokeObjectURL(url); }
  } catch (err) { error.value = err instanceof Error ? err.message : "解析结果下载失败"; }
}
</script>

<template>
  <article class="document-parse-snapshot" aria-label="解析快照详情">
    <h5>{{ view.result.filename }} · {{ statusLabels[view.result.status] }}</h5>
    <p>快照：{{ view.snapshot.id }}</p>
    <p>解析器：{{ view.result.parser.parser_id }} {{ view.result.parser.implementation_version }} · 配置修订 {{ view.snapshot.seed.config.revision }}</p>
    <p>原件版本：{{ view.snapshot.seed.version_id }} · 原件 SHA256：{{ view.result.source_sha256 }}</p>
    <p v-if="view.result.warnings.length">解析警告（{{ view.result.warnings.length }}）</p>
    <ul class="parse-warnings"><li v-for="(warning, index) in view.result.warnings" :key="index">
      {{ warning.message }}<span v-if="warning.source"> · {{ location(warning.source) }}</span>
    </li></ul>
    <p>章节 {{ view.result.sections.length }} · 表格 {{ view.result.tables.length }} · 文本块 {{ view.result.blocks.length }}</p>
    <pre class="parse-text">{{ view.result.text || '没有可用正文，请查看解析警告。' }}</pre>
    <details class="parse-structure"><summary>结构与来源位置</summary>
      <div v-for="block in view.result.blocks.slice(0, visibleBlocks)" :key="block.id" class="parse-block">
        <strong>{{ block.kind }} · {{ location(block.source) }}</strong><pre>{{ block.text }}</pre>
      </div>
      <p v-if="visibleBlocks < view.result.blocks.length">当前展示 {{ visibleBlocks }} / {{ view.result.blocks.length }} 个文本块。完整内容可下载。</p>
      <button v-if="visibleBlocks < view.result.blocks.length" class="secondary-btn" @click="visibleBlocks += 50">显示更多文本块</button>
    </details>
    <button class="secondary-btn" @click="download">下载完整解析结果 JSON</button>
    <p v-if="error" role="alert">{{ error }}</p>
  </article>
</template>

<style scoped>
.document-parse-snapshot { margin-top: 16px; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
.parse-block { border-top: 1px solid var(--border); padding: 10px 0; }
.parse-warnings { color: var(--orange, #b45309); }
[role="alert"] { color: var(--red); }
</style>
