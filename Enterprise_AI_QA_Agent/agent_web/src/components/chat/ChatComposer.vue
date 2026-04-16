<script setup lang="ts">
import type { KeyboardEvent } from "vue";
import { computed, ref } from "vue";

import { useSessionStore } from "../../stores/session";

const props = defineProps<{
  docked?: boolean;
}>();

const sessionStore = useSessionStore();
const draft = ref("");

const dockedPlaceholder =
  "给御策天检发送消息，按 Enter 快速发送，Shift+Enter 换行";
const heroPlaceholder =
  "例如：帮我测试后台管理系统的登录功能，需要覆盖各种异常输入边界情况...";
const busyTitle = "正在处理当前任务";
const idleTitle = "发送指令";
const placeholder = computed(() => (props.docked ? dockedPlaceholder : heroPlaceholder));
const buttonTitle = computed(() => (sessionStore.isBusy ? busyTitle : idleTitle));

async function handleSubmit() {
  if (sessionStore.isBusy || !sessionStore.session || !draft.value.trim()) {
    return;
  }

  const content = draft.value;
  draft.value = "";
  await sessionStore.sendMessage(content);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }

  event.preventDefault();
  void handleSubmit();
}
</script>

<template>
  <div class="home-composer" :class="{ 'home-composer-docked': docked }">
    <textarea
      v-model="draft"
      class="home-textarea"
      :placeholder="placeholder"
      @keydown="handleKeydown"
    />

    <div class="home-composer-footer">
      <div class="home-toolbar">
        <button class="home-tool-btn" type="button">
          <i class="fa-solid fa-paperclip"></i>
          Attachments
        </button>
        <button class="home-tool-btn" type="button">
          <i class="fa-solid fa-sitemap"></i>
          {{ sessionStore.activeAgent?.name ?? "Coordinator" }}
        </button>
      </div>

      <div class="home-send-group">
        <button
          class="home-send-btn"
          :disabled="sessionStore.isBusy || !sessionStore.session"
          @click="handleSubmit"
          :title="buttonTitle"
          type="button"
        >
          <i class="fa-solid" :class="sessionStore.isBusy ? 'fa-spinner fa-spin' : 'fa-arrow-up'"></i>
        </button>
      </div>
    </div>
  </div>
</template>
