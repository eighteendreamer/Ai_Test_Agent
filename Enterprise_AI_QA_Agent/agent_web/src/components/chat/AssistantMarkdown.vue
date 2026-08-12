<script setup lang="ts">
import { computed, ref } from "vue";

import { renderAssistantMarkdown } from "../../utils/markdown";

const props = defineProps<{
  content: string;
  streaming?: boolean;
}>();

const rootRef = ref<HTMLElement | null>(null);

const streamingRef = computed(() => Boolean(props.streaming));
const html = computed(() => renderAssistantMarkdown(props.content));

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
    ref="rootRef"
    class="assistant-markdown"
    :class="{ 'assistant-markdown-streaming': streamingRef }"
    @click="handleClick"
    v-html="html"
  />
</template>
