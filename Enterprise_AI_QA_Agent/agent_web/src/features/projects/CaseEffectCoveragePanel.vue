<script setup lang="ts">
import { computed } from "vue";
import JsonContentTree from "./JsonContentTree.vue";

const props = defineProps<{ coverage: unknown; resultLabel: string }>();
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
const data = computed(() => {
  const value = record(props.coverage);
  return value && ["case-effect-coverage-v1", "case-effect-coverage-v2"].includes(String(value.version)) ? value : null;
});
const rows = computed(() => Object.entries(record(data.value?.cleanups) || {})
  .map(([key, value]) => ({ key, value: record(value) || {} })));
const consumptionVerified = computed(() => data.value?.consumption_completion_verified === true);
const statusLabels: Record<string, string> = { passed: "声明对象覆盖已核对", failed: "清理证据未满足", blocked: "覆盖范围未确认" };
function count(value: unknown) { return typeof value === "number" ? value : "未取得"; }
</script>

<template>
  <section v-if="data" class="effect-coverage" aria-label="原对象清理覆盖">
    <h3>原对象清理覆盖 · {{ resultLabel }}</h3>
    <p>{{ consumptionVerified ? "固定消费阶段关闭回执已核验；这不代表 RunItem 已完成。" : "当前覆盖结果没有完整的消费阶段关闭证明。" }}</p>
    <p v-if="data.complete_inventory_available === false" role="status">完整清单未取得（证据预算不足），不能按零对象处理。</p>
    <p v-if="!rows.length">本条目没有所属的清理范围。</p>
    <div v-for="row in rows" :key="row.key">
      <strong>{{ row.key }} · {{ statusLabels[String(row.value.status)] || '状态未取得' }}</strong>
      <p>已知对象 {{ count(row.value.known_object_count) }} · 已覆盖 {{ count(row.value.covered_object_count) }}</p>
      <details><summary>来源、未覆盖对象及未知范围</summary><JsonContentTree :value="row.value" /></details>
    </div>
  </section>
</template>

<style scoped>
.effect-coverage { border: 1px solid var(--border); padding: 12px; margin-top: 12px; }
p { color: var(--muted); }
summary { cursor: pointer; }
</style>
