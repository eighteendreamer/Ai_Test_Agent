<script setup lang="ts">
import { ref } from "vue";

import { useSessionStore } from "../../stores/session";

defineProps<{
  docked?: boolean;
}>();

const sessionStore = useSessionStore();
const draft = ref("");

async function handleSubmit() {
  if (!draft.value.trim()) {
    return;
  }
  const content = draft.value;
  draft.value = "";
  await sessionStore.sendMessage(content);
}
</script>

<template>
  <div class="home-composer" :class="{ 'home-composer-docked': docked }">
    <textarea
      v-model="draft"
      class="home-textarea"
      :placeholder="docked ? '给御策天检发送消息' : '例如：帮我测试后台管理系统的登录功能，需要覆盖各种异常输入边界情况...'"
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
          :disabled="sessionStore.isSending || !sessionStore.session"
          @click="handleSubmit"
          title="Send instruction"
          type="button"
        >
          <i class="fa-solid" :class="sessionStore.isSending ? 'fa-spinner fa-spin' : 'fa-arrow-up'"></i>
        </button>
      </div>
    </div>
  </div>
</template>
