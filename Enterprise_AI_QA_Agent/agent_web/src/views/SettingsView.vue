<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "../services/api";
import type {
  EmailConfigPublic,
  EmailConfigUpdateRequest,
  ModelConfigPublic,
  ModelConfigUpdateRequest,
} from "../types";

type SettingsTab = "model" | "email";
type EmailProvider = "aliyun" | "cybermail";

const activeTab = ref<SettingsTab>("model");
const activeEmailProvider = ref<EmailProvider>("aliyun");
const selectedModelKey = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const success = ref("");

const modelConfigs = ref<ModelConfigPublic[]>([]);
const emailConfigs = ref<EmailConfigPublic[]>([]);

const modelDraft = reactive<ModelConfigUpdateRequest>({
  model_name: "",
  provider: "deepseek",
  base_url: "",
  api_key: null,
  is_active: true,
});

const aliyunDraft = reactive<EmailConfigUpdateRequest>({
  provider: "aliyun",
  enabled: false,
  is_default: false,
  from_email: "",
  from_name: "",
  reply_to: "",
  access_key_id: "",
  access_key_secret: "",
  account_name: "",
  region: "cn-hangzhou",
  use_tls: true,
});

const cybermailDraft = reactive<EmailConfigUpdateRequest>({
  provider: "cybermail",
  enabled: false,
  is_default: false,
  from_email: "",
  from_name: "",
  reply_to: "",
  smtp_host: "",
  smtp_port: 465,
  smtp_username: "",
  smtp_password: "",
  use_tls: true,
});

const activeModel = computed(
  () => modelConfigs.value.find((item) => item.is_active) ?? modelConfigs.value[0] ?? null,
);

const currentEmailConfig = computed(() =>
  emailConfigs.value.find((item) => item.provider === activeEmailProvider.value) ?? null,
);

onMounted(() => {
  void loadSettings();
});

async function loadSettings() {
  loading.value = true;
  clearNotice();
  try {
    const [models, emails] = await Promise.all([api.listModelConfigs(), api.listEmailConfigs()]);
    modelConfigs.value = models;
    emailConfigs.value = emails;

    const initialModel = models.find((item) => item.is_active) ?? models[0] ?? null;
    if (initialModel) {
      applyModelDraft(initialModel);
    } else {
      resetModelDraft();
    }

    hydrateEmailDrafts(emails);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load settings.";
  } finally {
    loading.value = false;
  }
}

function clearNotice() {
  error.value = "";
  success.value = "";
}

function resetModelDraft() {
  selectedModelKey.value = "";
  modelDraft.model_name = "";
  modelDraft.provider = "deepseek";
  modelDraft.base_url = "";
  modelDraft.api_key = null;
  modelDraft.is_active = modelConfigs.value.length === 0;
}

function applyModelDraft(item: ModelConfigPublic) {
  selectedModelKey.value = item.key;
  modelDraft.model_name = item.name;
  modelDraft.provider = item.provider;
  modelDraft.base_url = item.api_base_url;
  modelDraft.api_key = null;
  modelDraft.is_active = item.is_active;
}

function pickModel(item: ModelConfigPublic) {
  clearNotice();
  applyModelDraft(item);
}

function createModel() {
  clearNotice();
  resetModelDraft();
}

function hydrateEmailDrafts(items: EmailConfigPublic[]) {
  const aliyun = items.find((item) => item.provider === "aliyun");
  if (aliyun) {
    aliyunDraft.enabled = aliyun.enabled;
    aliyunDraft.is_default = aliyun.is_default;
    aliyunDraft.from_email = aliyun.from_email;
    aliyunDraft.from_name = aliyun.from_name;
    aliyunDraft.reply_to = aliyun.reply_to;
    aliyunDraft.access_key_id = aliyun.access_key_id ?? "";
    aliyunDraft.access_key_secret = "";
    aliyunDraft.account_name = aliyun.account_name ?? "";
    aliyunDraft.region = aliyun.region ?? "cn-hangzhou";
  }

  const cybermail = items.find((item) => item.provider === "cybermail");
  if (cybermail) {
    cybermailDraft.enabled = cybermail.enabled;
    cybermailDraft.is_default = cybermail.is_default;
    cybermailDraft.from_email = cybermail.from_email;
    cybermailDraft.from_name = cybermail.from_name;
    cybermailDraft.reply_to = cybermail.reply_to;
    cybermailDraft.smtp_host = cybermail.smtp_host ?? "";
    cybermailDraft.smtp_port = cybermail.smtp_port ?? 465;
    cybermailDraft.smtp_username = cybermail.smtp_username ?? "";
    cybermailDraft.smtp_password = "";
    cybermailDraft.use_tls = cybermail.use_tls;
  }
}

async function saveModel() {
  if (!modelDraft.model_name.trim() || !modelDraft.provider.trim() || !modelDraft.base_url.trim()) {
    error.value = "Model name, provider, and base URL are required.";
    return;
  }

  saving.value = true;
  clearNotice();
  try {
    const payload: ModelConfigUpdateRequest = {
      model_name: modelDraft.model_name.trim(),
      provider: modelDraft.provider.trim().toLowerCase(),
      base_url: modelDraft.base_url.trim(),
      api_key: modelDraft.api_key?.trim() ? modelDraft.api_key.trim() : null,
      is_active: modelDraft.is_active,
    };
    const saved = await api.updateModelConfig(payload);
    success.value = `Saved model config: ${saved.name}`;
    await loadSettings();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to save model config.";
  } finally {
    saving.value = false;
  }
}

async function saveEmail(provider: EmailProvider) {
  saving.value = true;
  clearNotice();
  try {
    const draft = provider === "aliyun" ? aliyunDraft : cybermailDraft;
    const payload: EmailConfigUpdateRequest = {
      ...draft,
      access_key_id: draft.access_key_id?.trim() || null,
      access_key_secret: draft.access_key_secret?.trim() || null,
      account_name: draft.account_name?.trim() || null,
      region: draft.region?.trim() || null,
      smtp_host: draft.smtp_host?.trim() || null,
      smtp_port: draft.smtp_port ?? null,
      smtp_username: draft.smtp_username?.trim() || null,
      smtp_password: draft.smtp_password?.trim() || null,
    };
    const saved = await api.updateEmailConfig(payload);
    success.value = `Saved email config: ${saved.provider}`;
    await loadSettings();
    activeEmailProvider.value = provider;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to save email config.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="view-page settings-page">
    <div class="settings-tabs">
      <button :class="{ active: activeTab === 'model' }" @click="activeTab = 'model'">Model Config</button>
      <button :class="{ active: activeTab === 'email' }" @click="activeTab = 'email'">Email Delivery</button>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-if="success" class="success-text">{{ success }}</p>

    <div v-if="activeTab === 'model'" class="settings-content-wrap">
      <section class="settings-block">
        <div class="settings-block-head">
          <div>
            <h3>Active Model</h3>
            <p class="settings-muted">Click a model card to edit or switch the active model.</p>
          </div>
          <button class="secondary-btn narrow" :disabled="loading || saving" @click="createModel">New Model</button>
        </div>

        <div class="settings-model-grid">
          <button
            v-for="item in modelConfigs"
            :key="item.key"
            type="button"
            class="settings-model-card"
            :class="{ active: selectedModelKey === item.key }"
            @click="pickModel(item)"
          >
            <div v-if="item.is_active" class="settings-model-card__active-strip"></div>
            <div class="settings-model-card__top">
              <div class="settings-model-card__title">
                <strong>{{ item.name }}</strong>
                <span class="settings-model-card__provider">{{ item.provider }}</span>
              </div>
              <span class="registry-tag" :class="item.is_active ? 'success' : 'light'">
                {{ item.is_active ? "active" : "inactive" }}
              </span>
            </div>
            <div class="settings-model-card__body">
              <p class="settings-model-card__url">{{ item.api_base_url }}</p>
              <div class="settings-model-card__meta">
                <span class="registry-tag light">provider</span>
                <span class="registry-tag light">{{ item.provider }}</span>
                <span v-if="item.has_secret" class="registry-tag light">secret saved</span>
              </div>
            </div>
          </button>
        </div>
      </section>

      <section class="settings-block">
        <h3>{{ selectedModelKey ? "Edit Model" : "Create Model" }}</h3>
        <div class="form-grid two">
          <label>
            <span>Model Name</span>
            <input v-model="modelDraft.model_name" type="text" placeholder="deepseek-chat / gpt-5.4" />
          </label>
          <label>
            <span>Provider</span>
            <input v-model="modelDraft.provider" type="text" placeholder="deepseek / openai / qwen" />
          </label>
          <label class="full">
            <span>Base URL</span>
            <input v-model="modelDraft.base_url" type="text" placeholder="https://api.deepseek.com/v1" />
          </label>
          <label class="full">
            <span>API Key</span>
            <input v-model="modelDraft.api_key" type="password" placeholder="Leave blank to keep the current secret" />
            <small>Requests use the unified compatibility layer by provider.</small>
          </label>
          <label class="checkbox-row full">
            <input v-model="modelDraft.is_active" type="checkbox" />
            <span>Set as active after save</span>
          </label>
        </div>

        <div class="settings-actions">
          <button class="primary-btn narrow" :disabled="loading || saving" @click="saveModel">Save Model</button>
        </div>
      </section>
    </div>

    <div v-else class="settings-content-wrap">
      <section class="settings-block">
        <div class="settings-block-head">
          <div>
            <h3>Email Channels</h3>
            <p class="settings-muted">Providers: Aliyun Mail and CyberMail SMTP.</p>
          </div>
          <div class="settings-provider-switch">
            <button
              type="button"
              class="secondary-btn narrow"
              :class="{ active: activeEmailProvider === 'aliyun' }"
              @click="activeEmailProvider = 'aliyun'"
            >
              Aliyun
            </button>
            <button
              type="button"
              class="secondary-btn narrow"
              :class="{ active: activeEmailProvider === 'cybermail' }"
              @click="activeEmailProvider = 'cybermail'"
            >
              CyberMail
            </button>
          </div>
        </div>

        <div v-if="emailConfigs.length" class="settings-list compact">
          <div v-for="item in emailConfigs" :key="item.provider" class="settings-list-item">
            <div>
              <strong>{{ item.provider }}</strong>
              <p>{{ item.from_email || "No sender configured" }}</p>
            </div>
            <div class="settings-list-meta">
              <span class="registry-tag" :class="item.enabled ? 'success' : 'light'">
                {{ item.enabled ? "enabled" : "disabled" }}
              </span>
              <span v-if="item.is_default" class="registry-tag success">default</span>
            </div>
          </div>
        </div>
        <div v-else class="settings-empty">
          No email config returned. Check /api/v1/settings/email.
        </div>

        <div v-if="currentEmailConfig" class="settings-summary-line">
          <span>Editing:</span>
          <strong>{{ currentEmailConfig.provider }}</strong>
          <span v-if="currentEmailConfig.is_default" class="registry-tag success">default channel</span>
        </div>
      </section>

      <section v-if="activeEmailProvider === 'aliyun'" class="settings-block">
        <h3>Aliyun Mail Config</h3>
        <div class="form-grid two">
          <label>
            <span>Sender Email</span>
            <input v-model="aliyunDraft.from_email" type="email" placeholder="qa-bot@company.com" />
          </label>
          <label>
            <span>Sender Name</span>
            <input v-model="aliyunDraft.from_name" type="text" placeholder="Enterprise AI QA Agent" />
          </label>
          <label>
            <span>Reply-To</span>
            <input v-model="aliyunDraft.reply_to" type="email" placeholder="reply@company.com" />
          </label>
          <label>
            <span>Account Name</span>
            <input v-model="aliyunDraft.account_name" type="text" placeholder="Console account name" />
          </label>
          <label>
            <span>Access Key ID</span>
            <input v-model="aliyunDraft.access_key_id" type="text" placeholder="AK ID" />
          </label>
          <label>
            <span>Access Key Secret</span>
            <input v-model="aliyunDraft.access_key_secret" type="password" placeholder="Leave blank to keep current" />
          </label>
          <label>
            <span>Region</span>
            <input v-model="aliyunDraft.region" type="text" placeholder="cn-hangzhou" />
          </label>
          <label class="checkbox-row">
            <input v-model="aliyunDraft.enabled" type="checkbox" />
            <span>Enable provider</span>
          </label>
          <label class="checkbox-row">
            <input v-model="aliyunDraft.is_default" type="checkbox" />
            <span>Make default channel</span>
          </label>
        </div>

        <div class="settings-actions">
          <button class="primary-btn narrow" :disabled="loading || saving" @click="saveEmail('aliyun')">Save Aliyun</button>
        </div>
      </section>

      <section v-else class="settings-block">
        <h3>CyberMail Config</h3>
        <div class="form-grid two">
          <label>
            <span>SMTP Host</span>
            <input v-model="cybermailDraft.smtp_host" type="text" placeholder="smtp.cybermail.jp" />
          </label>
          <label>
            <span>SMTP Port</span>
            <input v-model.number="cybermailDraft.smtp_port" type="number" placeholder="465" />
          </label>
          <label>
            <span>SMTP Username</span>
            <input v-model="cybermailDraft.smtp_username" type="text" placeholder="mailer@company.com" />
          </label>
          <label>
            <span>SMTP Password</span>
            <input v-model="cybermailDraft.smtp_password" type="password" placeholder="Leave blank to keep current" />
          </label>
          <label>
            <span>Sender Email</span>
            <input v-model="cybermailDraft.from_email" type="email" placeholder="mailer@company.com" />
          </label>
          <label>
            <span>Sender Name</span>
            <input v-model="cybermailDraft.from_name" type="text" placeholder="Enterprise AI QA Agent" />
          </label>
          <label>
            <span>Reply-To</span>
            <input v-model="cybermailDraft.reply_to" type="email" placeholder="reply@company.com" />
          </label>
          <label class="checkbox-row">
            <input v-model="cybermailDraft.use_tls" type="checkbox" />
            <span>Enable TLS</span>
          </label>
          <label class="checkbox-row">
            <input v-model="cybermailDraft.enabled" type="checkbox" />
            <span>Enable provider</span>
          </label>
          <label class="checkbox-row">
            <input v-model="cybermailDraft.is_default" type="checkbox" />
            <span>Make default channel</span>
          </label>
        </div>

        <div class="settings-actions">
          <button class="primary-btn narrow" :disabled="loading || saving" @click="saveEmail('cybermail')">Save CyberMail</button>
        </div>
      </section>
    </div>
  </section>
</template>
