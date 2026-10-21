<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, reactive, ref, watch } from "vue";

import DropdownSelect from "../../../components/common/DropdownSelect.vue";
import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type {
  ModelApplication,
  ModelCapabilitiesOverride,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
  ModelRequestProfile,
  OAuthModelItem,
  OAuthProviderProfile,
  OAuthStatusResponse,
} from "../../../types";

type MessageTone = "success" | "error";
type DropdownSelectOption = { label: string; value: string; disabled?: boolean };

const TRANSPORT_OPTIONS: DropdownSelectOption[] = [
  { label: "OpenAI Chat Completions", value: "openai_chat_completions" },
  { label: "OpenAI Responses", value: "openai_responses" },
  { label: "Anthropic Messages", value: "anthropic_messages" },
  { label: "Google Gemini Generate Content", value: "google_gemini_generate_content" },
];

const loading = ref(false);
const saving = ref(false);
const requestProfileText = ref("");
const capabilityOverrideText = ref("");
const showEditorModal = ref(false);
const editingModelName = ref<string | null>(null);
const busyActionKey = ref("");
const modelConfigs = ref<ModelConfigPublic[]>([]);
const messageVisible = ref(false);
const messageText = ref("");
const messageTone = ref<MessageTone>("success");
let messageTimer: ReturnType<typeof setTimeout> | null = null;

// OAuth provider presets
const oauthProviders = ref<OAuthProviderProfile[]>([]);
const oauthFlowState = ref<string | null>(null);
const oauthFlowStatus = ref<OAuthStatusResponse | null>(null);
const oauthPolling = ref(false);
let oauthPollTimer: ReturnType<typeof setInterval> | null = null;
let pageLoadController: AbortController | null = null;

// OAuth model discovery
const oauthAvailableModels = ref<OAuthModelItem[]>([]);
const oauthModelsFetching = ref(false);
const oauthSelectedModelId = ref<string | null>(null);
// Azure AD and providers that need a custom resource base URL
const oauthCustomBaseUrl = ref("");

const applicationOptions: Array<{ value: ModelApplication; label: string; hint: string }> = [
  {
    value: "task_execution",
    label: t("modelSettings.application_task_execution"),
    hint: t("modelSettings.application_task_execution_hint"),
  },
  {
    value: "embedding_retrieval",
    label: t("modelSettings.application_embedding_retrieval"),
    hint: t("modelSettings.application_embedding_retrieval_hint"),
  },
];

const modelDraft = reactive<ModelConfigUpdateRequest>({
  model_name: "",
  provider: "",
  transport: "openai_chat_completions",
  base_url: "",
  api_key: null,
  is_active: false,
  use_provider_defaults: true,
  capability_overrides: {},
  auth_type: "api_key",
  oauth_provider: null,
  oauth_refresh_token: null,
  context_window: null,
  max_output_tokens: null,
  applications: ["task_execution"],
});

const isEditing = computed(() => Boolean(editingModelName.value));
const isOAuth = computed(() => modelDraft.auth_type === "oauth2");
const appliesToTaskExecution = computed(() =>
  modelDraft.applications.includes("task_execution"),
);
const selectedApplication = computed<ModelApplication>({
  get: () => modelDraft.applications[0] ?? "task_execution",
  set: (value) => {
    modelDraft.applications = [value];
  },
});

const selectedOAuthProfile = computed<OAuthProviderProfile | null>(() => {
  if (!modelDraft.oauth_provider) return null;
  return oauthProviders.value.find((p) => p.key === modelDraft.oauth_provider) ?? null;
});

const oauthProviderOptions = computed<DropdownSelectOption[]>(() =>
  oauthProviders.value.map((p) => ({
    label: p.is_enabled ? p.display_name : `${p.display_name}（${t("modelSettings.coming_soon")}）`,
    value: p.key,
    disabled: !p.is_enabled,
  })),
);

const oauthModelOptions = computed<DropdownSelectOption[]>(() =>
  oauthAvailableModels.value.map((m) => ({ label: `${m.id}  (${m.name})`, value: m.id })),
);

const oauthAuthSuccess = computed(
  () => oauthFlowStatus.value?.status === "completed" || Boolean(modelDraft.oauth_refresh_token),
);

const oauthNeedsBaseUrl = computed(() => selectedOAuthProfile.value?.requires_base_url ?? false);

const oauthHasModelListing = computed(() => selectedOAuthProfile.value?.has_model_listing ?? false);

const oauthSupportsManualModelName = computed(
  () => selectedOAuthProfile.value?.supports_manual_model_name ?? true,
);

/* ── */
const showOAuthConfigNameField = computed(
  () =>
    oauthSupportsManualModelName.value &&
    (!oauthHasModelListing.value || oauthAvailableModels.value.length === 0),
);

onActivated(() => {
  pageLoadController?.abort();
  pageLoadController = new AbortController();
  void loadSettings(pageLoadController.signal);
  void loadOAuthProviders(pageLoadController.signal);
});

onDeactivated(stopPageActivity);

onBeforeUnmount(() => {
  if (messageTimer) {
    clearTimeout(messageTimer);
    messageTimer = null;
  }
  stopPageActivity();
});

watch(appliesToTaskExecution, (enabled) => {
  if (!enabled) modelDraft.is_active = false;
});

function stopPageActivity() {
  pageLoadController?.abort();
  pageLoadController = null;
  stopOAuthPoll();
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function loadSettings(signal = pageLoadController?.signal) {
  loading.value = true;
  try {
    modelConfigs.value = await api.listModelConfigs(signal);
  } catch (err) {
    if (isAbortError(err)) return;
    showMessage("error", err instanceof Error ? err.message : t("modelSettings.load_failed"));
  } finally {
    if (!signal?.aborted) loading.value = false;
  }
}

async function loadOAuthProviders(signal = pageLoadController?.signal) {
  try {
    const res = await api.listOAuthProviders(signal);
    oauthProviders.value = res.providers;
  } catch (error) {
    if (isAbortError(error)) return;
    // non-critical, silently ignore
  }
}

function showMessage(tone: MessageTone, text: string) {
  messageTone.value = tone;
  messageText.value = text;
  messageVisible.value = true;

  if (messageTimer) clearTimeout(messageTimer);

  messageTimer = setTimeout(() => {
    messageVisible.value = false;
    messageTimer = null;
  }, 2600);
}

function resetModelDraft() {
  requestProfileText.value = "";
  capabilityOverrideText.value = "";
  modelDraft.model_name = "";
  modelDraft.provider = "openai";
  modelDraft.transport = "openai_chat_completions";
  modelDraft.base_url = "";
  modelDraft.api_key = null;
  modelDraft.is_active = modelConfigs.value.length === 0;
  modelDraft.use_provider_defaults = true;
  modelDraft.capability_overrides = {};
  modelDraft.auth_type = "api_key";
  modelDraft.oauth_provider = null;
  modelDraft.oauth_refresh_token = null;
  modelDraft.context_window = null;
  modelDraft.max_output_tokens = null;
  modelDraft.applications = ["task_execution"];
  resetOAuthFlow();
}

function openCreateModal() {
  editingModelName.value = null;
  resetModelDraft();
  showEditorModal.value = true;
}

function openEditModal(item: ModelConfigPublic) {
  requestProfileText.value = item.request_profile ? JSON.stringify(item.request_profile, null, 2) : "";
  const capabilityOverrides = Object.fromEntries(
    Object.entries(item.capability_overrides ?? {}).filter(([, value]) => value !== null && value !== undefined),
  ) as ModelCapabilitiesOverride;
  capabilityOverrideText.value = Object.keys(capabilityOverrides).length
    ? JSON.stringify(capabilityOverrides, null, 2)
    : "";
  editingModelName.value = item.name;
  modelDraft.model_name = item.name;
  modelDraft.provider = item.provider;
  modelDraft.transport = item.transport;
  modelDraft.base_url = item.api_base_url;
  modelDraft.api_key = null;
  modelDraft.is_active = item.is_active;
  modelDraft.use_provider_defaults = Object.keys(capabilityOverrides).length === 0;
  modelDraft.capability_overrides = capabilityOverrides;
  modelDraft.auth_type = item.auth_type ?? "api_key";
  modelDraft.oauth_provider = item.oauth_provider ?? null;
  modelDraft.oauth_refresh_token = null;
  modelDraft.context_window = item.context_window ?? null;
  modelDraft.max_output_tokens = item.max_output_tokens ?? null;
  modelDraft.applications = [...(item.applications || ["task_execution"])];
  oauthCustomBaseUrl.value = item.api_base_url;
  resetOAuthFlow();
  if (item.auth_type === "oauth2" && item.oauth_provider && item.name) {
    const profile = oauthProviders.value.find((p) => p.key === item.oauth_provider);
    if (profile?.has_model_listing) {
      oauthAvailableModels.value = [
        { id: item.name, raw_id: item.name, name: item.description?.trim() || item.name },
      ];
      oauthSelectedModelId.value = item.name;
    }
  }
  showEditorModal.value = true;
}

function closeEditorModal() {
  showEditorModal.value = false;
  editingModelName.value = null;
}

function normalizePositiveInt(value: number | null | undefined): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.floor(parsed);
}

async function saveModel() {
  if (isOAuth.value && oauthHasModelListing.value && oauthAvailableModels.value.length > 0) {
    if (!oauthSelectedModelId.value?.trim()) {
      showMessage("error", t("modelSettings.select_model_first"));
      return;
    }
    modelDraft.model_name = oauthSelectedModelId.value.trim();
  }

  if (!modelDraft.model_name.trim()) {
    showMessage("error", t("modelSettings.name_required"));
    return;
  }

  if (modelDraft.applications.length !== 1) {
    showMessage("error", t("modelSettings.application_required"));
    return;
  }

  if (isOAuth.value) {
    if (!modelDraft.oauth_provider) {
      showMessage("error", t("modelSettings.oauth_provider_required"));
      return;
    }
    if (oauthNeedsBaseUrl.value && !oauthCustomBaseUrl.value.trim()) {
      showMessage("error", t("modelSettings.base_url_required"));
      return;
    }
  } else {
    if (!modelDraft.provider.trim() || !modelDraft.transport?.trim() || !modelDraft.base_url.trim()) {
      showMessage("error", t("modelSettings.provider_transport_url_required"));
      return;
    }
  }

  let request_profile: ModelRequestProfile | null = null;
  try {
    if (requestProfileText.value.trim()) {
      const parsed: unknown = JSON.parse(requestProfileText.value);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Expected an object");
      request_profile = parsed as ModelRequestProfile;
    }
  } catch {
    showMessage("error", t("modelSettings.request_profile_invalid"));
    return;
  }

  let capability_overrides: ModelCapabilitiesOverride = {};
  try {
    if (capabilityOverrideText.value.trim()) {
      const parsed: unknown = JSON.parse(capabilityOverrideText.value);
      const allowed = new Set<keyof ModelCapabilitiesOverride>([
        "text_input", "text_output", "tool_calling", "vision", "multi_image", "file_input",
        "pdf_input", "reasoning", "json_mode", "streaming", "parallel_tool_calls",
        "image_url_input", "image_base64_input",
      ]);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Expected an object");
      for (const [key, value] of Object.entries(parsed)) {
        if (!allowed.has(key as keyof ModelCapabilitiesOverride) ||
          (value !== null && typeof value !== "boolean")) throw new Error("Invalid capability override");
      }
      capability_overrides = parsed as ModelCapabilitiesOverride;
    }
  } catch {
    showMessage("error", t("modelSettings.capability_override_invalid"));
    return;
  }
  saving.value = true;

  // For OAuth mode, auto-fill provider/transport/base_url from the profile
  let provider = modelDraft.provider.trim().toLowerCase();
  let transport = modelDraft.transport ?? "openai_chat_completions";
  let base_url = modelDraft.base_url.trim();

  if (isOAuth.value && selectedOAuthProfile.value) {
    const profile = selectedOAuthProfile.value;
    provider = profile.key;
    transport = profile.default_transport || "openai_chat_completions";
    base_url = oauthNeedsBaseUrl.value
      ? oauthCustomBaseUrl.value.trim()
      : (profile.api_base_url || "");
  }

  const payload: ModelConfigUpdateRequest = {
    request_profile,
    model_name: modelDraft.model_name.trim(),
    provider,
    transport,
    base_url,
    api_key: isOAuth.value ? null : (modelDraft.api_key?.trim() || null),
    is_active: modelDraft.is_active,
    use_provider_defaults: !Object.values(capability_overrides).some((value) => value !== null && value !== undefined),
    capability_overrides,
    auth_type: modelDraft.auth_type,
    oauth_provider: isOAuth.value ? (modelDraft.oauth_provider || null) : null,
    oauth_refresh_token: isOAuth.value ? (modelDraft.oauth_refresh_token?.trim() || null) : null,
    context_window: normalizePositiveInt(modelDraft.context_window),
    max_output_tokens: normalizePositiveInt(modelDraft.max_output_tokens),
    applications: [...modelDraft.applications],
  };

  try {
    const saved = editingModelName.value
      ? await api.editModelConfig(editingModelName.value, payload)
      : await api.updateModelConfig(payload);

    showMessage("success", editingModelName.value ? `${t("modelSettings.model_updated")}: ${saved.name}` : `${t("modelSettings.model_created")}: ${saved.name}`);
    closeEditorModal();
    await loadSettings();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : t("modelSettings.save_failed"));
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
    showMessage("error", err instanceof Error ? err.message : t("modelSettings.action_failed"));
  } finally {
    busyActionKey.value = "";
  }
}

async function testConnection(item: ModelConfigPublic) {
  await withAction(item.name, "test", async () => {
    const result = await api.testModelConfigConnection(item.name);
    if (result.ok) {
      if (result.preview) {
        showMessage("success", `${t("modelSettings.test_success")}: ${item.name}, ${t("modelSettings.response")}: ${result.preview}`);
      } else if (result.latency_ms) {
        showMessage("success", `${t("modelSettings.test_success")}: ${item.name}, ${result.latency_ms}ms`);
      } else {
        showMessage("success", `${t("modelSettings.test_success")}: ${item.name}`);
      }
      return;
    }
    showMessage("error", `${t("modelSettings.test_failed")}: ${item.name}`);
  });
}

async function activateModel(item: ModelConfigPublic) {
  await withAction(item.name, "activate", async () => {
    await api.activateModelConfig(item.name);
    showMessage("success", `${t("modelSettings.activated")}: ${item.name}`);
    await loadSettings();
  });
}

// ── OAuth provider selection ──

function onOAuthProviderChange(key: string | null) {
  if (!key) {
    oauthAvailableModels.value = [];
    oauthSelectedModelId.value = null;
    return;
  }
  const profile = oauthProviders.value.find((item) => item.key === key);
  if (profile && !profile.is_enabled) {
    showMessage("error", `${profile.display_name} ${t("modelSettings.not_available")}`);
    modelDraft.oauth_provider = null;
    oauthAvailableModels.value = [];
    oauthSelectedModelId.value = null;
    return;
  }
  // Clear model list when provider changes
  oauthAvailableModels.value = [];
  oauthSelectedModelId.value = null;
  // Reset auth state for new provider
  resetOAuthFlow();
}

// ── OAuth Authorization Code flow ──

function resetOAuthFlow() {
  stopOAuthPoll();
  oauthFlowState.value = null;
  oauthFlowStatus.value = null;
  oauthPolling.value = false;
  oauthAvailableModels.value = [];
  oauthSelectedModelId.value = null;
}

function stopOAuthPoll() {
  if (oauthPollTimer !== null) {
    clearInterval(oauthPollTimer);
    oauthPollTimer = null;
  }
}

function buildOAuthRedirectUri(provider: string) {
  const origin = `${window.location.protocol}//${window.location.hostname}:8000`;
  const normalized = provider.trim().toLowerCase();
  if (normalized === "github") return `${origin}/api/v1/oauth/github/callback`;
  if (normalized === "google") return `${origin}/api/v1/oauth/google/callback`;
  if (normalized === "azure_ad") return `${origin}/api/v1/oauth/azure/callback`;
  return `${origin}/api/v1/oauth/callback`;
}

async function launchOAuth() {
  if (!modelDraft.oauth_provider) {
    showMessage("error", t("modelSettings.select_oauth_provider"));
    return;
  }
  if (selectedOAuthProfile.value && !selectedOAuthProfile.value.is_enabled) {
    showMessage("error", `${selectedOAuthProfile.value.display_name} ${t("modelSettings.oauth_not_available")}`);
    return;
  }
  const provider = modelDraft.oauth_provider;
  const redirectUri = buildOAuthRedirectUri(provider);

  try {
    const res = await api.startOAuthFlow({ provider, redirect_uri: redirectUri });

    oauthFlowState.value = res.state;
    oauthFlowStatus.value = { state: res.state, status: "pending" };
    oauthPolling.value = true;

    window.open(res.authorization_url, "_blank", "noopener,noreferrer");

    stopOAuthPoll();
    oauthPollTimer = setInterval(async () => {
      try {
        const status = await api.getOAuthStatus(res.state);
        oauthFlowStatus.value = status;
        if (status.status === "completed") {
          stopOAuthPoll();
          oauthPolling.value = false;
          if (status.refresh_token) {
            modelDraft.oauth_refresh_token = status.refresh_token;
          }
          showMessage("success", t("modelSettings.oauth_success"));
        } else if (status.status === "failed") {
          stopOAuthPoll();
          oauthPolling.value = false;
          showMessage("error", `${t("modelSettings.oauth_failed")}: ${status.error || t("modelSettings.unknown_error")}`);
        }
      } catch {
        // network hiccup 鈥?keep polling
      }
    }, 2000);
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : t("modelSettings.oauth_launch_failed"));
  }
}

// ── OAuth model discovery ──

async function fetchOAuthModels() {
  if (!modelDraft.oauth_provider) {
    showMessage("error", t("modelSettings.select_oauth_provider"));
    return;
  }
  if (selectedOAuthProfile.value && !selectedOAuthProfile.value.is_enabled) {
    showMessage("error", `${selectedOAuthProfile.value.display_name} ${t("modelSettings.model_fetch_not_available")}`);
    return;
  }
  oauthModelsFetching.value = true;
  try {
    const res = await api.listOAuthModels(
      modelDraft.oauth_provider,
      oauthFlowState.value ?? null,
      oauthNeedsBaseUrl.value ? oauthCustomBaseUrl.value.trim() || null : null,
    );
    oauthAvailableModels.value = res.models;
    if (res.models.length === 0) {
      showMessage("error", t("modelSettings.no_models_found"));
    }
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : t("modelSettings.fetch_models_failed"));
  } finally {
    oauthModelsFetching.value = false;
  }
}

function onOAuthModelSelect(modelId: string | null) {
  oauthSelectedModelId.value = modelId;
  if (modelId) {
    modelDraft.model_name = modelId;
  }
}

async function deleteModel(item: ModelConfigPublic) {
  const confirmed = window.confirm(`${t("modelSettings.confirm_delete")} "${item.name}"?`);
  if (!confirmed) return;

  await withAction(item.name, "delete", async () => {
    await api.deleteModelConfig(item.name);
    showMessage("success", `${t("modelSettings.deleted")}: ${item.name}`);
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
        <h3>{{ t("modelSettings.title") }}</h3>
        <p>{{ t("modelSettings.desc") }}</p>
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
              {{ t("modelSettings.current_use") }}
            </span>
          </div>

          <div class="settings-model-card__name">
            <strong>{{ item.name }}</strong>
          </div>

          <div class="settings-model-card__provider-row">
            <i class="fa-solid fa-arrows-rotate"></i>
            <span>{{ item.transport }}</span>
          </div>

          <div class="settings-model-card__provider-row">
            <i class="fa-solid fa-bullseye"></i>
            <span>
              {{ t("modelSettings.applications") }}:
              {{ item.applications.map((value) => value === "embedding_retrieval" ? t("modelSettings.application_embedding_retrieval") : t("modelSettings.application_task_execution")).join(" · ") }}
            </span>
          </div>

          <div class="settings-model-card__stats settings-model-card__stats-plain">
            <div class="settings-model-card__stat">
              <span v-if="item.auth_type === 'oauth2'">
                <i class="fa-solid fa-shield-halved"></i>
                OAuth{{ item.oauth_provider ? ` · ${item.oauth_provider}` : " 2.0" }}
              </span>
              <span v-else>
                <i class="fa-solid fa-key"></i> API Key
              </span>
              <strong v-if="item.auth_type === 'oauth2'">
                {{ item.has_oauth_refresh_token ? t("modelSettings.token_obtained") : t("modelSettings.not_configured") }}
              </strong>
              <strong v-else>{{ item.has_secret ? t("modelSettings.configured") : t("modelSettings.not_configured") }}</strong>
            </div>
            <div class="settings-model-card__stat settings-model-card__stat-full">
              <span>{{ t("modelSettings.endpoint_address") }}</span>
              <strong>{{ item.api_base_url }}</strong>
            </div>
          </div>

          <div class="settings-model-card__spacer"></div>

          <div class="settings-model-card__actions">
            <button
              v-if="item.applications.includes('task_execution')"
              type="button"
              class="settings-model-card__action settings-model-card__action-test"
              :disabled="busyActionKey === actionKey(item.name, 'test')"
              :title="t('modelSettings.test_connection')"
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
              :title="t('modelSettings.activate_model')"
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
              :title="t('modelSettings.edit_model_btn')"
              @click="openEditModal(item)"
            >
              <i class="fa-solid fa-pen-to-square"></i>
            </button>
            <button
              type="button"
              class="settings-model-card__action settings-model-card__action-danger"
              :disabled="busyActionKey === actionKey(item.name, 'delete')"
              :title="t('modelSettings.delete_model_btn')"
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
            <strong>{{ t("modelSettings.add_model") }}</strong>
          </div>
        </button>
      </div>
    </div>

    <!-- editor modal -->
    <div v-if="showEditorModal" class="settings-modal-overlay" @click.self="closeEditorModal">
      <section
        class="settings-modal-card settings-modal-card-clean settings-model-editor-modal"
        role="dialog"
        aria-modal="true"
        :aria-label="isEditing ? t('modelSettings.edit_model') : t('modelSettings.add_model')"
      >
        <div class="settings-modal-head">
          <div>
            <h4>{{ isEditing ? t("modelSettings.edit_model") : t("modelSettings.add_model") }}</h4>
            <p>{{ isEditing ? t("modelSettings.edit_model_desc") : t("modelSettings.add_model_desc") }}</p>
          </div>
          <button type="button" class="settings-modal-close" :title="t('modelSettings.cancel')" @click="closeEditorModal">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="settings-model-editor-body">
          <div class="form-grid two settings-model-editor-form">
          <div class="full settings-auth-type-row">
            <span class="settings-auth-type-label">{{ t("modelSettings.auth_type") }}</span>
            <div class="settings-auth-type-btns">
              <button
                type="button"
                class="settings-auth-type-btn"
                :class="{ active: modelDraft.auth_type === 'api_key' }"
                @click="modelDraft.auth_type = 'api_key'"
              >
                <i class="fa-solid fa-key"></i> API Key
              </button>
              <button
                type="button"
                class="settings-auth-type-btn"
                :class="{ active: modelDraft.auth_type === 'oauth2' }"
                @click="modelDraft.auth_type = 'oauth2'"
              >
                <i class="fa-solid fa-shield-halved"></i> OAuth 2.0
              </button>
            </div>
          </div>

          <!-- ── API Key mode ── -->
          <template v-if="!isOAuth">
            <label>
              <span>{{ t("modelSettings.model_name") }}</span>
              <input v-model="modelDraft.model_name" type="text" placeholder="claude-opus-4-7 / gpt-5.4" />
            </label>

            <label>
              <span>{{ t("modelSettings.provider") }}</span>
              <input v-model="modelDraft.provider" type="text" :placeholder="t('modelSettings.provider_ph')" />
            </label>

            <label class="settings-provider-field">
              <span>{{ t("modelSettings.transport") }}</span>
              <DropdownSelect
                :model-value="modelDraft.transport ?? null"
                @update:model-value="(value) => { modelDraft.transport = value === null ? null : String(value); }"
                button-class="settings-provider-select"
                menu-class="settings-provider-select-menu"
                :options="TRANSPORT_OPTIONS"
                :placeholder="t('modelSettings.transport_ph')"
              />
            </label>

            <label class="full">
              <span>{{ t("modelSettings.base_url") }}</span>
              <input v-model="modelDraft.base_url" type="text" placeholder="https://api.deepseek.com/v1" />
            </label>

            <label class="full">
              <span>{{ t("modelSettings.api_key") }}</span>
              <input v-model="modelDraft.api_key" type="password" :placeholder="t('modelSettings.api_key_keep')" />
              <small>{{ t("modelSettings.api_key_hidden_hint") }}</small>
            </label>
          </template>

          <!-- ── OAuth 2.0 mode ── -->
          <template v-else>
            <!-- Step 1: Provider -->
            <label class="full settings-provider-field">
              <span>{{ t("modelSettings.oauth_provider") }}</span>
              <DropdownSelect
                :model-value="modelDraft.oauth_provider ?? null"
                button-class="settings-provider-select"
                menu-class="settings-provider-select-menu"
                :options="oauthProviderOptions"
                :placeholder="t('modelSettings.oauth_provider_ph')"
                clearable
                @update:model-value="(value) => { modelDraft.oauth_provider = value ? String(value) : null; onOAuthProviderChange(modelDraft.oauth_provider); }"
              />
              <small v-if="selectedOAuthProfile?.notes" class="oauth-provider-note">
                {{ selectedOAuthProfile.notes }}
              </small>
              <small v-if="selectedOAuthProfile && !selectedOAuthProfile.is_enabled" class="oauth-provider-note">
                {{ t("modelSettings.oauth_provider_disabled_hint") }}
              </small>
            </label>

            <label v-if="oauthNeedsBaseUrl" class="full">
              <span>{{ t("modelSettings.oauth_base_url") }}</span>
              <input
                v-model="oauthCustomBaseUrl"
                type="text"
                placeholder="https://your-resource.openai.azure.com"
              />
              <small>{{ t("modelSettings.oauth_base_url_hint") }}</small>
            </label>

            <div v-if="modelDraft.oauth_provider" class="full oauth-launch-section">
              <div class="oauth-step-label">
                <span class="oauth-step-num">1</span>
                <span>{{ t("modelSettings.oauth_browser_auth") }}</span>
                <i v-if="oauthAuthSuccess" class="fa-solid fa-check oauth-step-done"></i>
              </div>

              <div v-if="modelDraft.oauth_refresh_token" class="oauth-token-status oauth-token-ok">
                <i class="fa-solid fa-circle-check"></i>
                {{ t("modelSettings.oauth_refresh_token_ready") }}
                <button type="button" class="oauth-token-clear" @click="modelDraft.oauth_refresh_token = null; resetOAuthFlow()">
                  {{ t("modelSettings.clear") }}
                </button>
              </div>

              <div
                v-else-if="oauthFlowStatus"
                class="oauth-token-status"
                :class="{
                  'oauth-token-pending': oauthFlowStatus.status === 'pending',
                  'oauth-token-ok': oauthFlowStatus.status === 'completed',
                  'oauth-token-error': oauthFlowStatus.status === 'failed',
                }"
              >
                <i
                  class="fa-solid"
                  :class="{
                    'fa-spinner fa-spin': oauthFlowStatus.status === 'pending',
                    'fa-circle-check': oauthFlowStatus.status === 'completed',
                    'fa-circle-xmark': oauthFlowStatus.status === 'failed',
                  }"
                ></i>
                <span v-if="oauthFlowStatus.status === 'pending'">{{ t("modelSettings.oauth_waiting") }}</span>
                <span v-else-if="oauthFlowStatus.status === 'completed'">{{ t("modelSettings.oauth_auth_success") }}</span>
                <span v-else>{{ t("modelSettings.oauth_auth_failed") }}: {{ oauthFlowStatus.error }}</span>
                <button v-if="oauthFlowStatus.status !== 'pending'" type="button" class="oauth-token-clear" @click="resetOAuthFlow()">
                  {{ t("modelSettings.reset") }}
                </button>
              </div>

              <button
                type="button"
                class="oauth-launch-btn"
                :disabled="oauthPolling || !modelDraft.oauth_provider || (selectedOAuthProfile ? !selectedOAuthProfile.is_enabled : false)"
                @click="launchOAuth"
              >
                <i class="fa-solid" :class="oauthPolling ? 'fa-spinner fa-spin' : 'fa-arrow-up-right-from-square'"></i>
                {{ oauthPolling ? t("modelSettings.oauth_launch_waiting") : (oauthAuthSuccess ? t("modelSettings.oauth_launch_again") : t("modelSettings.oauth_launch")) }}
              </button>

              <p class="oauth-env-hint">
                <i class="fa-solid fa-circle-info"></i>
                {{ t("modelSettings.oauth_env_hint") }} <code>.env</code>.
              </p>
            </div>

            <div v-if="modelDraft.oauth_provider" class="full oauth-models-section">
              <div class="oauth-step-label">
                <span class="oauth-step-num">2</span>
                <span>{{ t("modelSettings.oauth_select_model") }}</span>
                <i v-if="oauthSelectedModelId" class="fa-solid fa-check oauth-step-done"></i>
              </div>

              <div class="oauth-models-row">
                <button
                  type="button"
                  class="oauth-fetch-btn"
                  :disabled="oauthModelsFetching || !modelDraft.oauth_provider || (selectedOAuthProfile ? !selectedOAuthProfile.is_enabled : false)"
                  @click="fetchOAuthModels"
                >
                  <i class="fa-solid" :class="oauthModelsFetching ? 'fa-spinner fa-spin' : 'fa-magnifying-glass'"></i>
                  {{ oauthModelsFetching ? t("modelSettings.oauth_fetching") : t("modelSettings.oauth_fetch_models") }}
                </button>

                <DropdownSelect
                  v-if="oauthAvailableModels.length > 0"
                  :model-value="oauthSelectedModelId"
                  button-class="settings-provider-select oauth-model-select"
                  menu-class="settings-provider-select-menu"
                  :options="oauthModelOptions"
                  :placeholder="t('modelSettings.oauth_fetch_available_models')"
                  clearable
                  @update:model-value="(value) => onOAuthModelSelect(value ? String(value) : null)"
                />
              </div>

              <p v-if="!oauthHasModelListing && !oauthAvailableModels.length" class="oauth-no-listing-hint">
                {{ t("modelSettings.oauth_provider_no_listing") }}
              </p>
            </div>

            <label v-if="showOAuthConfigNameField" class="full">
              <span>{{ t("modelSettings.config_name") }}</span>
              <input
                v-model="modelDraft.model_name"
                type="text"
                placeholder="gpt-4o / gemini-2.0-flash"
              />
              <small>{{ t("modelSettings.config_name_hint") }}</small>
            </label>
          </template>

          <div class="full settings-model-editor-divider" aria-hidden="true"></div>

          <label>
            <span>{{ t("modelSettings.context_window") }}</span>
            <input
              v-model.number="modelDraft.context_window"
              type="number"
              min="1024"
              step="1024"
              placeholder="128000"
            />
            <small>{{ t("modelSettings.context_window_hint") }}</small>
          </label>

          <label>
            <span>{{ t("modelSettings.max_output_tokens") }}</span>
            <input
              v-model.number="modelDraft.max_output_tokens"
              type="number"
              min="1"
              placeholder="8192"
            />
            <small>{{ t("modelSettings.max_output_tokens_hint") }}</small>
          </label>

          <label class="full">
            <span>{{ t("modelSettings.request_profile") }}</span>
            <textarea v-model="requestProfileText" rows="7" spellcheck="false"
              :placeholder='JSON.stringify({ version: 1, revision: 1, output_formats: ["json_object"], chat_token_parameter: "max_tokens" }, null, 2)' />
            <small>{{ t("modelSettings.request_profile_hint") }}</small>
          </label>

          <label class="full">
            <span>{{ t("modelSettings.capability_override") }}</span>
            <textarea v-model="capabilityOverrideText" rows="5" spellcheck="false"
              :placeholder='JSON.stringify({ vision: false, parallel_tool_calls: false }, null, 2)' />
            <small>{{ t("modelSettings.capability_override_desc") }}</small>
          </label>

          <div class="full settings-application-section">
            <span class="settings-application-section__title">{{ t("modelSettings.applications") }}</span>
            <div class="settings-application-options">
              <label
                v-for="option in applicationOptions"
                :key="option.value"
                class="checkbox-row settings-application-option"
              >
                <input
                  v-model="selectedApplication"
                  type="radio"
                  name="model-application"
                  :value="option.value"
                />
                <span>
                  <strong>{{ option.label }}</strong>
                  <small>{{ option.hint }}</small>
                </span>
              </label>
            </div>
          </div>

          <label v-if="appliesToTaskExecution" class="checkbox-row full settings-model-default-row">
            <input v-model="modelDraft.is_active" type="checkbox" />
            <span>{{ t("modelSettings.set_as_default") }}</span>
          </label>
          </div>
        </div>

        <div class="settings-modal-actions">
          <button type="button" class="secondary-btn narrow" :disabled="saving" @click="closeEditorModal">{{ t("modelSettings.cancel") }}</button>
          <button type="button" class="primary-btn narrow" :disabled="loading || saving" @click="saveModel">
            {{ isEditing ? t("modelSettings.save_changes") : t("modelSettings.save_model") }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.settings-model-editor-modal {
  width: min(700px, calc(100vw - 40px));
  max-height: min(820px, calc(100vh - 40px));
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border-radius: 10px;
}

.settings-model-editor-modal .settings-modal-head {
  flex: 0 0 auto;
  margin: 0;
  padding: 22px 24px 18px;
  border-bottom: 1px solid var(--border);
}

.settings-model-editor-modal .settings-modal-head h4 {
  font-size: 17px;
  line-height: 1.3;
}

.settings-model-editor-modal .settings-modal-head p {
  margin-top: 2px;
  font-size: 12px;
}

.settings-model-editor-modal .settings-modal-close {
  flex: 0 0 auto;
  font-size: 16px;
}

.settings-model-editor-body {
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 18px 24px 20px;
  scrollbar-width: thin;
  scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
}

.settings-model-editor-body::-webkit-scrollbar {
  width: 7px;
}

.settings-model-editor-body::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.32);
}

.settings-model-editor-form {
  gap: 16px 18px;
}

.settings-model-editor-form label {
  gap: 5px;
}

.settings-model-editor-form label > span,
.settings-application-section__title {
  font-size: 13px;
  font-weight: 600;
}

.settings-model-editor-form label small {
  font-size: 11px;
  line-height: 1.45;
}

.settings-model-editor-divider {
  height: 1px;
  margin: 2px 0 0;
  background: var(--border);
}

.settings-model-editor-modal .settings-modal-actions {
  flex: 0 0 auto;
  margin: 0;
  padding: 15px 24px;
  border-top: 1px solid var(--border);
  background: var(--surface);
}

.settings-auth-type-row {
  display: grid;
  grid-template-columns: minmax(90px, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--surface-soft);
  font-size: 13px;
  color: var(--text);
  font-weight: 600;
}

.settings-auth-type-btns {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
}

.settings-auth-type-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 5px 12px;
  border-radius: 5px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}

.settings-auth-type-btn:hover {
  background: var(--surface-soft);
  color: var(--text);
}

.settings-auth-type-btn.active {
  background: var(--text);
  color: var(--surface);
  font-weight: 600;
}

/* ── OAuth provider styles ── */
.oauth-provider-note {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
  line-height: 1.5;
}

/* ── ── */
.oauth-step-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.oauth-step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--text);
  color: var(--bg);
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.oauth-step-done {
  color: var(--text);
  font-size: 14px;
}

/* ── ── */
.oauth-launch-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 8px;
}

/* ── ── */
.oauth-models-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.oauth-models-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.oauth-model-select {
  flex: 1;
  min-width: 200px;
}

.oauth-no-listing-hint {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
}

/* ── ── */
.oauth-launch-btn,
.oauth-fetch-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s, border-color 0.15s;
  white-space: nowrap;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text);
  align-self: flex-start;
}

.oauth-launch-btn:hover {
  border-color: var(--text);
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

.oauth-launch-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── ── */
.oauth-token-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

/* ── */
.oauth-token-pending {
  background: color-mix(in srgb, var(--muted) 8%, transparent);
  color: var(--muted);
  border: 1px solid var(--border);
}

/* Success: dark emphasis */
.oauth-token-ok {
  background: color-mix(in srgb, var(--text) 6%, transparent);
  color: var(--text);
  border: 1px solid var(--border-strong);
}

/* Error: red with note */
.oauth-token-error {
  background: color-mix(in srgb, var(--red) 10%, transparent);
  color: var(--red);
  border: 1px solid color-mix(in srgb, var(--red) 30%, transparent);
}

.oauth-token-clear {
  margin-left: auto;
  background: transparent;
  border: none;
  color: inherit;
  font-size: 12px;
  text-decoration: underline;
  cursor: pointer;
  opacity: 0.6;
  padding: 0;
}

.oauth-token-clear:hover {
  opacity: 1;
}

/* ── ── */
.oauth-env-hint {
  font-size: 12px;
  color: var(--muted);
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin: 0;
}

.oauth-env-hint code {
  background: var(--surface-muted);
  padding: 1px 4px;
  border-radius: 4px;
  font-family: monospace;
  color: var(--text);
}

.settings-application-section {
  display: grid;
  gap: 7px;
}

.settings-application-section__title {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.settings-application-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.settings-application-option {
  align-items: flex-start;
  min-width: 0;
  padding: 10px 12px;
  border: 0;
  border-radius: 0;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.settings-application-option + .settings-application-option {
  border-left: 1px solid var(--border);
}

.settings-application-option:hover {
  background: var(--surface-soft);
}

.settings-application-option span {
  display: grid;
  gap: 2px;
}

.settings-application-option small {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
}

.settings-model-default-row {
  width: fit-content;
  color: var(--muted);
  cursor: pointer;
}

.settings-model-default-row span {
  font-size: 12px !important;
  font-weight: 500 !important;
}

@media (max-width: 680px) {
  .settings-modal-overlay {
    padding: 12px;
  }

  .settings-model-editor-modal {
    width: calc(100vw - 24px);
    max-height: calc(100vh - 24px);
  }

  .settings-model-editor-modal .settings-modal-head {
    padding: 18px 18px 15px;
  }

  .settings-model-editor-body {
    padding: 16px 18px 18px;
  }

  .settings-model-editor-form {
    grid-template-columns: 1fr;
  }

  .settings-model-editor-form .full {
    grid-column: auto;
  }

  .settings-auth-type-row {
    grid-template-columns: 1fr;
  }

  .settings-auth-type-btns {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .settings-auth-type-btn {
    justify-content: center;
  }

  .settings-application-options {
    grid-template-columns: 1fr;
  }

  .settings-application-option + .settings-application-option {
    border-left: 0;
    border-top: 1px solid var(--border);
  }

  .settings-model-editor-modal .settings-modal-actions {
    padding: 13px 18px;
  }
}
</style>

