<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NSpin } from "naive-ui";
import { api } from "../../services/api";
import type { GenerationTemplateRecord } from "../../types";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const show = ref(true);
const template = ref<GenerationTemplateRecord | null>(null);
const form = ref({ name: "", writing_guidance: "", execution_notes: "", example: "" });
const loading = ref(false);
const saving = ref(false);
const error = ref("");

async function load(useDefault = false) {
  loading.value = true;
  error.value = "";
  try {
    template.value = await api.getTestCaseGenerationTemplate(props.projectId, useDefault);
    const payload = template.value.payload;
    form.value = { ...payload, example: JSON.stringify(payload.example_json, null, 2) };
  } catch (err) {
    error.value = err instanceof Error ? err.message : "模板加载失败";
  } finally { loading.value = false; }
}

async function save() {
  saving.value = true;
  error.value = "";
  try {
    const example = JSON.parse(form.value.example);
    if (!example || Array.isArray(example) || typeof example !== "object") throw new Error("示例必须是 JSON 对象");
    template.value = await api.saveTestCaseGenerationTemplate(props.projectId, {
      name: form.value.name, writing_guidance: form.value.writing_guidance,
      execution_notes: form.value.execution_notes, example_json: example,
    });
    emit("saved");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "模板保存失败";
  } finally { saving.value = false; }
}

function close() {
  show.value = false;
  emit("close");
}

onMounted(() => load());
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    title="用例生成模板"
    style="width: min(760px, 94vw)"
    :mask-closable="!saving"
    :close-on-esc="!saving"
    @update:show="!$event && emit('close')"
  >
    <p class="dialog-hint">修改只影响后续生成。已有用例保存自己的内容和说明，执行时不会读取此模板。</p>
    <p v-if="template" class="dialog-hint">
      {{ template.is_default ? "系统默认模板" : "项目模板" }} · 修订 {{ template.revision }}
    </p>
    <NAlert v-if="error" type="error" :bordered="false" style="margin-bottom: 12px">{{ error }}</NAlert>
    <div v-if="loading" class="dialog-loading"><NSpin size="small" /> 正在加载模板…</div>
    <NForm v-else-if="template" label-placement="top">
      <NFormItem label="模板名称" required>
        <NInput v-model:value="form.name" maxlength="160" show-count />
      </NFormItem>
      <NFormItem label="AI 写作指导">
        <NInput v-model:value="form.writing_guidance" type="textarea" :autosize="{ minRows: 6, maxRows: 14 }" />
      </NFormItem>
      <NFormItem label="随用例保存的执行说明">
        <NInput v-model:value="form.execution_notes" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" />
      </NFormItem>
      <NFormItem label="JSON 结构与示例">
        <NInput v-model:value="form.example" type="textarea" :autosize="{ minRows: 14, maxRows: 26 }" class="json-editor" />
      </NFormItem>
    </NForm>
    <template #footer>
      <div class="dialog-actions">
        <NButton :disabled="loading || saving" @click="load(true)">载入系统默认内容</NButton>
        <NButton :disabled="loading || saving || !template" type="primary" :loading="saving" @click="save">
          保存项目模板
        </NButton>
        <NButton :disabled="saving" @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
@import "./caseDialogs.css";

.json-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
