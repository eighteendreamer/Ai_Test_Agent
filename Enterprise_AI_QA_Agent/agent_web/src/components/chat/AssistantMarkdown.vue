<script setup lang="ts">
import { computed, ref } from "vue";

import { displayAssistantContent, renderAssistantMarkdown } from "../../utils/markdown";

const props = defineProps<{
  content: string;
  streaming?: boolean;
}>();

const rootRef = ref<HTMLElement | null>(null);

const streamingRef = computed(() => Boolean(props.streaming));
const streamingText = computed(() => displayAssistantContent(props.content));
const html = computed(() => streamingRef.value ? "" : renderAssistantMarkdown(props.content));

// Event delegation for the per-code-block copy button.
function handleClick(event: MouseEvent) {
  const target = (event.target as HTMLElement | null)?.closest?.(
    "[data-code-copy]",
  ) as HTMLButtonElement | null;
  if (!target) {
    return;
  }
  const block = target.closest(".assistant-code-block");
  const code = block?.querySelector("code");
  const text = code?.textContent ?? "";
  if (!text) {
    return;
  }
  void navigator.clipboard?.writeText(text).then(
    () => {
      const original = target.textContent;
      target.textContent = "已复制";
      target.classList.add("is-copied");
      window.setTimeout(() => {
        target.textContent = original;
        target.classList.remove("is-copied");
      }, 1400);
    },
    () => undefined,
  );
}
</script>

<template>
  <div
    v-if="streamingRef"
    ref="rootRef"
    class="assistant-markdown assistant-markdown-streaming"
  >{{ streamingText }}</div>
  <div
    v-else
    ref="rootRef"
    class="assistant-markdown"
    @click="handleClick"
    v-html="html"
  />
</template>

<style scoped>
.assistant-markdown-streaming {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
