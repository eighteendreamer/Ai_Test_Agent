<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import type { ApiDocVersionRecord, DocumentParseSnapshot } from "../../../types";

const props = defineProps<{ side: "old" | "new"; projectId: string; documentId: string;
  versions: ApiDocVersionRecord[]; versionId: string; modelValue: DocumentParseSnapshot | null }>();
const emit = defineEmits<{ "update:versionId": [id: string]; "update:modelValue": [snapshot: DocumentParseSnapshot | null] }>();
const snapshots = ref<DocumentParseSnapshot[]>([]);
const loading = ref(false);
const more = ref(false);
const error = ref("");
const sideLabel = props.side === "old" ? "旧侧" : "新侧";
let epoch = 0;
let disposed = false;
let offset = 0;
const statusLabels = { complete: "提取完成", partial: "部分提取", unsupported: "没有可用文本" };

async function load(append = false) {
  if (loading.value || !props.versionId || !props.projectId) return;
  const { projectId, documentId, versionId } = props; const generation = epoch;
  const selected = snapshots.value.find(snapshot => snapshot.id === props.modelValue?.id && snapshot.status !== "unsupported");
  const current = () => !disposed && generation === epoch && projectId === props.projectId
    && documentId === props.documentId && versionId === props.versionId;
  loading.value = true; error.value = "";
  try {
    const page = await api.listDocumentParseSnapshots(projectId, documentId, versionId, append ? offset : 0);
    if (!current()) return;
    if (page.items.some(snapshot => snapshot.seed.project_id !== projectId || snapshot.seed.document_id !== documentId
      || snapshot.seed.version_id !== versionId)) throw new Error("解析快照列表与所选项目、文档或原件版本不一致");
    snapshots.value = append ? [...snapshots.value, ...page.items.filter(item => !snapshots.value.some(old => old.id === item.id))] : page.items;
    // Immutable historical selections may lie beyond the refreshed first page.
    if (!append && selected && !snapshots.value.some(snapshot => snapshot.id === selected.id)) snapshots.value.push(selected);
    offset = page.offset + page.items.length; more.value = page.has_more;
    if (!append) emit("update:modelValue", selected || snapshots.value.find(snapshot => snapshot.status !== "unsupported") || null);
  } catch (err) { if (current()) error.value = err instanceof Error ? err.message : "解析快照列表读取失败"; }
  finally { if (current()) loading.value = false; }
}
function selectSnapshot(event: Event) {
  const id = (event.target as HTMLSelectElement).value;
  emit("update:modelValue", snapshots.value.find(snapshot => snapshot.id === id && snapshot.status !== "unsupported") || null);
}
watch(() => [props.projectId, props.documentId, props.versionId], () => {
  ++epoch; snapshots.value = []; more.value = false; offset = 0; loading.value = false; error.value = "";
  emit("update:modelValue", null); void load();
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++epoch; });
</script>

<template>
  <section class="snapshot-selector" :aria-label="`${sideLabel}比较来源`">
    <h5>{{ sideLabel }}比较来源</h5>
    <label>{{ sideLabel }}原件版本<select :value="versionId" :aria-label="`${sideLabel}原件版本`" @change="emit('update:versionId', ($event.target as HTMLSelectElement).value)">
      <option value="">请选择原件版本</option><option v-for="version in versions" :key="version.id" :value="version.id">V{{ version.version }} · {{ version.snapshot.original_asset?.filename || version.snapshot.filename }}</option>
    </select></label>
    <label>{{ sideLabel }}解析快照<select :value="modelValue?.id || ''" :aria-label="`${sideLabel}解析快照`" :disabled="loading || !versionId" @change="selectSnapshot">
      <option value="">请选择可用解析快照</option><option v-for="snapshot in snapshots" :key="snapshot.id" :value="snapshot.id" :disabled="snapshot.status === 'unsupported'">
        {{ snapshot.parser.parser_id }} · {{ statusLabels[snapshot.status] }} · {{ snapshot.created_at }} · {{ snapshot.id }}
      </option>
    </select></label>
    <p v-if="loading">正在读取{{ sideLabel }}解析快照…</p>
    <p v-if="versionId && !loading && !modelValue">所选原件没有已选的可用解析快照，请先解析，或选择其他历史快照。</p>
    <p v-if="modelValue?.status === 'partial'">此快照只提取了部分内容；比较会保留能力范围及警告。</p>
    <ul v-if="modelValue?.warnings.length"><li v-for="(warning, index) in modelValue.warnings" :key="index">{{ warning.message }}</li></ul>
    <p v-if="error" role="alert">{{ error }}</p>
    <button class="secondary-btn" :disabled="loading || !versionId" @click="load()">刷新{{ sideLabel }}快照</button>
    <button v-if="more" class="secondary-btn" :disabled="loading" @click="load(true)">更多{{ sideLabel }}快照</button>
  </section>
</template>

<style scoped>
.snapshot-selector { min-width: 0; overflow-wrap: anywhere; }
label { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
select { min-width: 0; max-width: 100%; }
[role="alert"] { color: var(--red); }
</style>
