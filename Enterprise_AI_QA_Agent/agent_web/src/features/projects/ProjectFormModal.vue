<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NModal,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";

import { api } from "../../services/api";
import type { ProjectRecord } from "../../types";

const props = defineProps<{
  show: boolean;
  project?: ProjectRecord | null;
}>();
const emit = defineEmits<{
  "update:show": [value: boolean];
  saved: [project: ProjectRecord];
}>();

const toast = useMessage();
const formRef = ref<FormInst | null>(null);
const saving = ref(false);
const error = ref("");
const form = reactive({
  project_key: "",
  name: "",
  description: "",
  base_url: "",
  graph_scope_key: "",
});

const editing = computed(() => Boolean(props.project?.id));
const title = computed(() => (editing.value ? "编辑项目" : "新建测试项目"));

watch(
  () => [props.show, props.project?.id] as const,
  () => {
    if (!props.show) return;
    error.value = "";
    form.project_key = props.project?.project_key ?? "";
    form.name = props.project?.name ?? "";
    form.description = props.project?.description ?? "";
    form.base_url = props.project?.base_url ?? "";
    form.graph_scope_key = props.project?.graph_scope_key ?? "";
    formRef.value?.restoreValidation();
  },
  { immediate: true },
);

const rules: FormRules = {
  project_key: [
    { required: true, message: "请输入项目标识", trigger: ["blur", "input"] },
    {
      pattern: /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/,
      message: "只能使用字母、数字、下划线和连字符，且不能以符号开头",
      trigger: ["blur", "input"],
    },
    { max: 80, message: "项目标识不能超过 80 个字符", trigger: ["blur", "input"] },
  ],
  name: [
    { required: true, message: "请输入项目名称", trigger: ["blur", "input"] },
    { max: 160, message: "项目名称不能超过 160 个字符", trigger: ["blur", "input"] },
  ],
  description: [{ max: 2000, message: "项目说明不能超过 2000 个字符", trigger: ["blur", "input"] }],
  base_url: [
    { max: 2048, message: "Base URL 不能超过 2048 个字符", trigger: ["blur", "input"] },
    {
      validator: (_rule, value: string) => {
        if (!value) return true;
        try {
          const url = new URL(value);
          return url.protocol === "http:" || url.protocol === "https:";
        } catch {
          return false;
        }
      },
      message: "请输入 http(s) 开头的完整 URL",
      trigger: ["blur", "input"],
    },
  ],
  graph_scope_key: [{ max: 160, message: "图谱 Scope 不能超过 160 个字符", trigger: ["blur", "input"] }],
};

async function submit() {
  error.value = "";
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  saving.value = true;
  try {
    const common = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      base_url: form.base_url.trim() || null,
      graph_scope_key: form.graph_scope_key.trim() || null,
    };
    const saved = editing.value && props.project
      ? await api.updateProject(props.project.id, common)
      : await api.createProject({ project_key: form.project_key.trim(), ...common });
    emit("saved", saved);
    emit("update:show", false);
    toast.success(editing.value ? "项目已更新" : "项目已创建");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "项目保存失败";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    style="width: min(560px, 92vw)"
    :mask-closable="!saving"
    :close-on-esc="!saving"
    @update:show="emit('update:show', $event)"
  >
    <NAlert v-if="error" type="error" :bordered="false" style="margin-bottom: 14px">
      {{ error }}
    </NAlert>
    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top">
      <NFormItem label="项目标识" path="project_key">
        <NInput
          v-model:value="form.project_key"
          :disabled="editing"
          placeholder="orders-api"
          maxlength="80"
          show-count
        />
      </NFormItem>
      <NFormItem label="项目名称" path="name">
        <NInput v-model:value="form.name" placeholder="订单服务" maxlength="160" show-count />
      </NFormItem>
      <NFormItem label="项目说明" path="description">
        <NInput
          v-model:value="form.description"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          maxlength="2000"
          show-count
          placeholder="可选，描述项目用途与范围"
        />
      </NFormItem>
      <NFormItem label="Base URL" path="base_url">
        <NInput v-model:value="form.base_url" placeholder="https://api.example.com" />
      </NFormItem>
      <NFormItem label="知识图谱 Scope" path="graph_scope_key">
        <NInput v-model:value="form.graph_scope_key" placeholder="可选，映射现有 project_scope" />
      </NFormItem>
    </NForm>
    <template #footer>
      <div class="project-form-footer">
        <NButton :disabled="saving" @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="saving" @click="submit">保存</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.project-form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
