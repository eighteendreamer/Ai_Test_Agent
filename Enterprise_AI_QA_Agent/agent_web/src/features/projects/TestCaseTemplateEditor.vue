<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../services/api";
import type { GenerationTemplateRecord } from "../../types";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();
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
onMounted(() => load());
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="emit('close')">
    <section class="case-dialog" role="dialog" aria-label="用例生成模板">
      <header><h2>用例生成模板</h2><button @click="emit('close')">关闭</button></header>
      <p>修改只影响后续生成。已有用例保存自己的内容和说明，执行时不会读取此模板。</p>
      <p v-if="template">{{ template.is_default ? "系统默认模板" : "项目模板" }} · 修订 {{ template.revision }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="loading">正在加载模板…</p>
      <template v-else-if="template">
        <label>模板名称<input v-model="form.name"></label>
        <label>AI 写作指导<textarea v-model="form.writing_guidance" rows="6"></textarea></label>
        <label>随用例保存的执行说明<textarea v-model="form.execution_notes" rows="3"></textarea></label>
        <label>JSON 结构与示例<textarea v-model="form.example" class="json-editor" rows="14"></textarea></label>
      </template>
      <footer><button :disabled="loading || saving" @click="load(true)">载入系统默认内容</button><button :disabled="loading || saving || !template" @click="save">{{ saving ? "保存中…" : "保存项目模板" }}</button></footer>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
</style>
