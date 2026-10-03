<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ value: unknown; label?: string }>();
const structured = computed(() => props.value !== null && typeof props.value === "object");
const entries = computed(() => structured.value ? Object.entries(props.value as object) : []);
const kind = computed(() => Array.isArray(props.value) ? "数组" : "对象");
const scalar = computed(() => JSON.stringify(props.value) ?? String(props.value));
</script>

<template>
  <details v-if="structured" open class="json-node">
    <summary>{{ label }} <small>{{ kind }} · {{ entries.length }}</small></summary>
    <div class="json-children">
      <JsonContentTree v-for="[key, item] in entries" :key="key" :label="key" :value="item" />
      <span v-if="!entries.length">{{ Array.isArray(value) ? "[]" : "{}" }}</span>
    </div>
  </details>
  <div v-else class="json-leaf"><strong v-if="label !== undefined">{{ label }}:</strong> {{ scalar }}</div>
</template>

<style scoped>
.json-node, .json-leaf { margin: 5px 0; overflow-wrap: anywhere; }
.json-children { padding-left: 18px; border-left: 1px solid var(--border); }
summary { cursor: pointer; }
small { color: var(--muted); }
.json-leaf { white-space: pre-wrap; }
</style>
