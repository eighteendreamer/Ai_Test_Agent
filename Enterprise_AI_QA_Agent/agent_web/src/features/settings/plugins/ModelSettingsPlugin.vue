<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { NSelect, type SelectOption } from "naive-ui";

import { api } from "../../../services/api";
import type {
  ModelCapabilities,
  ModelCapabilitiesOverride,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
} from "../../../types";

type MessageTone = "success" | "error";
type CapabilityKey = keyof ModelCapabilities;

interface CapabilityOption {
  key: CapabilityKey;
  label: string;
  hint: string;
}

const loading = ref(false);
const saving = ref(false);
const showEditorModal = ref(false);
const editingModelName = ref<string | null>(null);
const busyActionKey = ref("");
const modelConfigs = ref<ModelConfigPublic[]>([]);
const messageVisible = ref(false);
const messageText = ref("");
const messageTone = ref<MessageTone>("success");
let messageTimer: ReturnType<typeof setTimeout> | null = null;

const capabilityOptions: CapabilityOption[] = [
  { key: "tool_calling", label: "工具调用", hint: "允许模型发起工具调用" },
  { key: "vision", label: "视觉识别", hint: "支持图片理解与视觉输入" },
  { key: "reasoning", label: "推理模式", hint: "支持更长链路的推理输出" },
  { key: "multi_image", label: "多图输入", hint: "同一轮可输入多张图片" },
  { key: "file_input", label: "文件输入", hint: "支持文件或附件类输入" },
  { key: "pdf_input", label: "PDF 输入", hint: "支持 PDF 文档输入" },
  { key: "json_mode", label: "JSON 模式", hint: "支持结构化 JSON 输出" },
  { key: "streaming", label: "流式响应", hint: "支持流式输出消息" },
  { key: "parallel_tool_calls", label: "并行工具", hint: "支持并行工具调用" },
  { key: "image_url_input", label: "图片 URL", hint: "支持图片链接输入" },
  { key: "image_base64_input", label: "Base64 图片", hint: "支持 Base64 图片输入" },
];

const baseCapabilities: ModelCapabilities = {
  text_input: true,
  text_output: true,
  tool_calling: true,
  vision: false,
  multi_image: false,
  file_input: false,
  pdf_input: false,
  reasoning: true,
  json_mode: false,
  streaming: true,
  parallel_tool_calls: false,
  image_url_input: true,
  image_base64_input: true,
};

const modelDraft = reactive<ModelConfigUpdateRequest>({
  model_name: "",
  provider: "",
  base_url: "",
  api_key: null,
  is_active: false,
  use_provider_defaults: true,
  capability_overrides: {},
});

const capabilityDraft = reactive<ModelCapabilities>({ ...baseCapabilities });

const isEditing = computed(() => Boolean(editingModelName.value));
const providerOptions = computed<SelectOption[]>(() => {
  const seen = new Set<string>();

  return modelConfigs.value
    .map((item) => item.provider.trim())
    .filter((provider) => {
      if (!provider) {
        return false;
      }
      const normalized = provider.toLowerCase();
      if (seen.has(normalized)) {
        return false;
      }
      seen.add(normalized);
      return true;
    })
    .sort((left, right) => left.localeCompare(right))
    .map((provider) => ({
      label: provider,
      value: provider,
    }));
});

onMounted(() => {
  void loadSettings();
});

onBeforeUnmount(() => {
  if (messageTimer) {
    clearTimeout(messageTimer);
    messageTimer = null;
  }
});

watch(
  () => [showEditorModal.value, modelDraft.provider, modelDraft.use_provider_defaults] as const,
  ([visible, provider, useProviderDefaults]) => {
    if (!visible || !useProviderDefaults) {
      return;
    }
    applyCapabilityDraft(inferProviderCapabilities(provider));
  },
);

async function loadSettings() {
  loading.value = true;
  try {
    modelConfigs.value = await api.listModelConfigs();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : "加载模型设置失败。");
  } finally {
    loading.value = false;
  }
}

function showMessage(tone: MessageTone, text: string) {
  messageTone.value = tone;
  messageText.value = text;
  messageVisible.value = true;

  if (messageTimer) {
    clearTimeout(messageTimer);
  }

  messageTimer = setTimeout(() => {
    messageVisible.value = false;
    messageTimer = null;
  }, 2600);
}

function resetModelDraft() {
  modelDraft.model_name = "";
  modelDraft.provider = "";
  modelDraft.base_url = "";
  modelDraft.api_key = null;
  modelDraft.is_active = modelConfigs.value.length === 0;
  modelDraft.use_provider_defaults = true;
  modelDraft.capability_overrides = {};
  applyCapabilityDraft(inferProviderCapabilities(""));
}

function openCreateModal() {
  editingModelName.value = null;
  resetModelDraft();
  showEditorModal.value = true;
}

function openEditModal(item: ModelConfigPublic) {
  editingModelName.value = item.name;
  modelDraft.model_name = item.name;
  modelDraft.provider = item.provider;
  modelDraft.base_url = item.api_base_url;
  modelDraft.api_key = null;
  modelDraft.is_active = item.is_active;
  modelDraft.use_provider_defaults = !hasCapabilityOverrides(item.capability_overrides);
  modelDraft.capability_overrides = { ...item.capability_overrides };
  applyCapabilityDraft(item.capabilities);
  showEditorModal.value = true;
}

function closeEditorModal() {
  showEditorModal.value = false;
  editingModelName.value = null;
}

function applyCapabilityDraft(capabilities: Partial<ModelCapabilities>) {
  capabilityOptions.forEach((option) => {
    capabilityDraft[option.key] = Boolean(capabilities[option.key] ?? baseCapabilities[option.key]);
  });
}

function inferProviderCapabilities(provider: string): ModelCapabilities {
  const normalized = provider.trim().toLowerCase();
  const capabilities: ModelCapabilities = { ...baseCapabilities };
  const openAiVisionProviders = new Set([
    "openai",
    "azure-openai",
    "azure_openai",
    "qwen",
    "dashscope",
    "zhipu",
    "glm",
    "openrouter",
    "xai",
    "minimax",
    "volcengine",
    "doubao",
    "hunyuan",
    "baidu",
    "ernie",
    "deepseek",
    "moonshot",
  ]);

  if (normalized === "anthropic") {
    capabilities.vision = true;
    capabilities.image_url_input = false;
  } else if (normalized === "google" || normalized === "gemini") {
    capabilities.vision = true;
    capabilities.multi_image = true;
    capabilities.file_input = true;
    capabilities.image_url_input = false;
  } else if (openAiVisionProviders.has(normalized)) {
    capabilities.vision = true;
  }

  return capabilities;
}

function buildCapabilityOverrides(): ModelCapabilitiesOverride {
  const overrides: ModelCapabilitiesOverride = {};

  capabilityOptions.forEach((option) => {
    overrides[option.key] = capabilityDraft[option.key];
  });

  return overrides;
}

function hasCapabilityOverrides(overrides: ModelCapabilitiesOverride | undefined | null) {
  if (!overrides) {
    return false;
  }
  return Object.values(overrides).some((value) => value !== null && value !== undefined);
}

async function saveModel() {
  if (!modelDraft.model_name.trim() || !modelDraft.provider.trim() || !modelDraft.base_url.trim()) {
    showMessage("error", "模型名称、提供商和 Base URL 不能为空。");
    return;
  }

  saving.value = true;

  const payload: ModelConfigUpdateRequest = {
    model_name: modelDraft.model_name.trim(),
    provider: modelDraft.provider.trim().toLowerCase(),
    base_url: modelDraft.base_url.trim(),
    api_key: modelDraft.api_key?.trim() ? modelDraft.api_key.trim() : null,
    is_active: modelDraft.is_active,
    use_provider_defaults: modelDraft.use_provider_defaults ?? true,
    capability_overrides: modelDraft.use_provider_defaults ? {} : buildCapabilityOverrides(),
  };

  try {
    const saved = editingModelName.value
      ? await api.editModelConfig(editingModelName.value, payload)
      : await api.updateModelConfig(payload);

    showMessage("success", editingModelName.value ? `模型已更新：${saved.name}` : `模型已创建：${saved.name}`);
    closeEditorModal();
    await loadSettings();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : "保存模型配置失败。");
  } finally {
    saving.value = false;
  }
}

function actionKey(modelName: string, action: string) {
  return `${modelName}:${action}`;
}

async function withAction(modelName: string, action: string, runner: () => Promise<void>) {
  busyActionKey.value = actionKey(modelName, action);
  try {
    await runner();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : "模型操作失败。");
  } finally {
    busyActionKey.value = "";
  }
}

async function testConnection(item: ModelConfigPublic) {
  await withAction(item.name, "test", async () => {
    const result = await api.testModelConfigConnection(item.name);
    if (result.ok) {
      if (result.preview) {
        showMessage("success", `连接测试成功：${item.name}，响应：${result.preview}`);
      } else if (result.latency_ms) {
        showMessage("success", `连接测试成功：${item.name}，延迟 ${result.latency_ms}ms`);
      } else {
        showMessage("success", `连接测试成功：${item.name}`);
      }
      return;
    }

    showMessage("error", `连接测试失败：${item.name}`);
  });
}

async function activateModel(item: ModelConfigPublic) {
  await withAction(item.name, "activate", async () => {
    await api.activateModelConfig(item.name);
    showMessage("success", `已激活模型：${item.name}`);
    await loadSettings();
  });
}

async function deleteModel(item: ModelConfigPublic) {
  const confirmed = window.confirm(`确定删除模型“${item.name}”吗？`);
  if (!confirmed) {
    return;
  }

  await withAction(item.name, "delete", async () => {
    await api.deleteModelConfig(item.name);
    showMessage("success", `已删除模型：${item.name}`);
    await loadSettings();
  });
}
</script>

<template>
  <section class="settings-pane">
    <transition name="settings-toast">
      <div
        v-if="messageVisible"
        class="settings-message"
        :class="messageTone === 'success' ? 'is-success' : 'is-error'"
      >
        {{ messageText }}
      </div>
    </transition>

    <div class="settings-pane-head">
      <div>
        <h3>模型设置</h3>
        <p>维护当前系统可用的大模型配置，并指定默认使用的模型。</p>
      </div>
    </div>

    <div class="settings-pane-block settings-model-grid-shell">
      <div class="settings-model-grid">
        <article
          v-for="item in modelConfigs"
          :key="item.id ?? item.name"
          class="settings-model-card settings-model-card-static"
        >
          <div class="settings-model-card__header">
            <span v-if="item.is_active" class="settings-model-card__badge settings-model-card__badge-current">
              当前使用
            </span>
          </div>

          <div class="settings-model-card__name">
            <strong>{{ item.name }}</strong>
          </div>

          <div class="settings-model-card__provider-row">
            <i class="fa-solid fa-server"></i>
            <span>{{ item.provider }}</span>
          </div>

          <div class="settings-model-card__stats settings-model-card__stats-plain">
            <div class="settings-model-card__stat">
              <span>API Key</span>
              <strong>{{ item.has_secret ? "已配置" : "未配置" }}</strong>
            </div>
            <div class="settings-model-card__stat settings-model-card__stat-full">
              <span>Base URL</span>
              <strong>{{ item.api_base_url }}</strong>
            </div>
          </div>

          <div class="settings-model-card__spacer"></div>

          <div class="settings-model-card__actions">
            <button
              type="button"
              class="settings-model-card__action settings-model-card__action-test"
              :disabled="busyActionKey === actionKey(item.name, 'test')"
              title="测试连接"
              @click="testConnection(item)"
            >
              <i
                class="fa-solid"
                :class="busyActionKey === actionKey(item.name, 'test') ? 'fa-spinner fa-spin' : 'fa-plug-circle-check'"
              ></i>
            </button>
            <button
              type="button"
              class="settings-model-card__action settings-model-card__action-activate"
              :disabled="item.is_active || busyActionKey === actionKey(item.name, 'activate')"
              title="激活模型"
              @click="activateModel(item)"
            >
              <i
                class="fa-solid"
                :class="busyActionKey === actionKey(item.name, 'activate') ? 'fa-spinner fa-spin' : 'fa-circle-check'"
              ></i>
            </button>
            <button
              type="button"
              class="settings-model-card__action settings-model-card__action-edit"
              :disabled="busyActionKey === actionKey(item.name, 'edit')"
              title="编辑模型"
              @click="openEditModal(item)"
            >
              <i class="fa-solid fa-pen-to-square"></i>
            </button>
            <button
              type="button"
              class="settings-model-card__action settings-model-card__action-danger"
              :disabled="busyActionKey === actionKey(item.name, 'delete')"
              title="删除模型"
              @click="deleteModel(item)"
            >
              <i
                class="fa-solid"
                :class="busyActionKey === actionKey(item.name, 'delete') ? 'fa-spinner fa-spin' : 'fa-trash-can'"
              ></i>
            </button>
          </div>
        </article>

        <button type="button" class="settings-model-card settings-model-card-add" @click="openCreateModal">
          <div class="settings-model-card-add__icon">+</div>
          <div class="settings-model-card-add__body">
            <strong>新增模型</strong>
            <p>创建新的模型配置</p>
          </div>
        </button>
      </div>
    </div>

    <div v-if="showEditorModal" class="settings-modal-overlay" @click.self="closeEditorModal">
      <section class="settings-modal-card settings-modal-card-clean">
        <div class="settings-modal-head">
          <div>
            <h4>{{ isEditing ? "编辑模型" : "新增模型" }}</h4>
            <p>{{ isEditing ? "修改已有模型配置" : "创建新的模型配置" }}</p>
          </div>
          <button type="button" class="settings-modal-close" @click="closeEditorModal">×</button>
        </div>

        <div class="form-grid two">
          <label>
            <span>模型名称</span>
            <input v-model="modelDraft.model_name" type="text" placeholder="deepseek-chat / gpt-5.4" />
          </label>

          <label class="settings-provider-field">
            <span>提供商</span>
            <NSelect
              v-model:value="modelDraft.provider"
              class="settings-provider-select"
              filterable
              tag
              clearable
              :options="providerOptions"
              placeholder="请选择或输入 provider"
            />
          </label>

          <label class="full">
            <span>Base URL</span>
            <input v-model="modelDraft.base_url" type="text" placeholder="https://api.deepseek.com/v1" />
          </label>

          <label class="full">
            <span>API Key</span>
            <input v-model="modelDraft.api_key" type="password" placeholder="留空则保留当前密钥" />
            <small>系统不会回显已保存的密钥内容。</small>
          </label>

          <label class="checkbox-row full">
            <input v-model="modelDraft.is_active" type="checkbox" />
            <span>保存后设为当前主模型</span>
          </label>
        </div>

        <section class="settings-capability-panel">
          <div class="settings-capability-panel__head">
            <div>
              <strong>能力覆盖</strong>
              <p>默认跟随提供商能力；如果需要，可在当前模型上单独覆盖。</p>
            </div>
            <label class="checkbox-row">
              <input v-model="modelDraft.use_provider_defaults" type="checkbox" />
              <span>跟随提供商默认能力</span>
            </label>
          </div>

          <div class="settings-capability-grid">
            <label
              v-for="option in capabilityOptions"
              :key="option.key"
              class="settings-capability-option"
              :class="{ 'is-disabled': modelDraft.use_provider_defaults }"
            >
              <input
                v-model="capabilityDraft[option.key]"
                type="checkbox"
                :disabled="modelDraft.use_provider_defaults"
              />
              <div>
                <strong>{{ option.label }}</strong>
                <small>{{ option.hint }}</small>
              </div>
            </label>
          </div>
        </section>

        <div class="settings-modal-actions">
          <button type="button" class="secondary-btn narrow" :disabled="saving" @click="closeEditorModal">取消</button>
          <button type="button" class="primary-btn narrow" :disabled="loading || saving" @click="saveModel">
            {{ isEditing ? "保存修改" : "保存模型配置" }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>
