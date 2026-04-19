<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "../../../services/api";
import type { EmailConfigPublic, EmailConfigUpdateRequest } from "../../../types";

type EmailProvider = "aliyun" | "cybermail";

const activeEmailProvider = ref<EmailProvider>("aliyun");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const success = ref("");
const emailConfigs = ref<EmailConfigPublic[]>([]);

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

const currentEmailConfig = computed(
  () => emailConfigs.value.find((item) => item.provider === activeEmailProvider.value) ?? null,
);

onMounted(() => {
  void loadSettings();
});

async function loadSettings() {
  loading.value = true;
  clearNotice();
  try {
    const emails = await api.listEmailConfigs();
    emailConfigs.value = emails;
    hydrateEmailDrafts(emails);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载邮件设置失败。";
  } finally {
    loading.value = false;
  }
}

function clearNotice() {
  error.value = "";
  success.value = "";
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
    success.value = `邮件配置已保存：${saved.provider}`;
    await loadSettings();
    activeEmailProvider.value = provider;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "保存邮件配置失败。";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="settings-pane">
    <p v-if="error" class="error-text settings-page-notice">{{ error }}</p>
    <p v-if="success" class="success-text settings-page-notice">{{ success }}</p>

    <div class="settings-pane-head">
      <div>
        <h3>邮件设置</h3>
        <p>配置邮件投递服务。当前仅显示后端已经提供接口支持的邮件设置。</p>
      </div>
      <div class="settings-provider-switch">
        <button
          type="button"
          class="secondary-btn narrow"
          :class="{ active: activeEmailProvider === 'aliyun' }"
          @click="activeEmailProvider = 'aliyun'"
        >
          阿里云邮件
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

    <div class="settings-pane-block">
      <h4>邮件通道列表</h4>
      <div v-if="emailConfigs.length" class="settings-list compact">
        <div v-for="item in emailConfigs" :key="item.provider" class="settings-list-item">
          <div>
            <strong>{{ item.provider }}</strong>
            <p>{{ item.from_email || "尚未配置发件地址" }}</p>
          </div>
          <div class="settings-list-meta">
            <span class="registry-tag" :class="item.enabled ? 'success' : 'light'">
              {{ item.enabled ? "已启用" : "已停用" }}
            </span>
            <span v-if="item.is_default" class="registry-tag success">默认通道</span>
          </div>
        </div>
      </div>
      <div v-else class="settings-empty">当前还没有读取到邮件配置。</div>

      <div v-if="currentEmailConfig" class="settings-summary-line">
        <span>当前编辑：</span>
        <strong>{{ currentEmailConfig.provider }}</strong>
        <span v-if="currentEmailConfig.is_default" class="registry-tag success">默认通道</span>
      </div>
    </div>

    <div v-if="activeEmailProvider === 'aliyun'" class="settings-pane-block">
      <h4>阿里云邮件配置</h4>
      <div class="form-grid two">
        <label>
          <span>发件邮箱</span>
          <input v-model="aliyunDraft.from_email" type="email" placeholder="qa-bot@company.com" />
        </label>
        <label>
          <span>发件人名称</span>
          <input v-model="aliyunDraft.from_name" type="text" placeholder="Enterprise AI QA Agent" />
        </label>
        <label>
          <span>Reply-To</span>
          <input v-model="aliyunDraft.reply_to" type="email" placeholder="reply@company.com" />
        </label>
        <label>
          <span>账号名称</span>
          <input v-model="aliyunDraft.account_name" type="text" placeholder="控制台账号名称" />
        </label>
        <label>
          <span>Access Key ID</span>
          <input v-model="aliyunDraft.access_key_id" type="text" placeholder="AK ID" />
        </label>
        <label>
          <span>Access Key Secret</span>
          <input v-model="aliyunDraft.access_key_secret" type="password" placeholder="留空则保留当前密钥" />
        </label>
        <label>
          <span>区域</span>
          <input v-model="aliyunDraft.region" type="text" placeholder="cn-hangzhou" />
        </label>
        <label class="checkbox-row">
          <input v-model="aliyunDraft.enabled" type="checkbox" />
          <span>启用该邮件提供方</span>
        </label>
        <label class="checkbox-row">
          <input v-model="aliyunDraft.is_default" type="checkbox" />
          <span>设为默认邮件通道</span>
        </label>
      </div>

      <div class="settings-actions">
        <button class="primary-btn narrow" :disabled="loading || saving" @click="saveEmail('aliyun')">保存阿里云邮件配置</button>
      </div>
    </div>

    <div v-else class="settings-pane-block">
      <h4>CyberMail SMTP 配置</h4>
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
          <input v-model="cybermailDraft.smtp_password" type="password" placeholder="留空则保留当前密码" />
        </label>
        <label>
          <span>发件邮箱</span>
          <input v-model="cybermailDraft.from_email" type="email" placeholder="mailer@company.com" />
        </label>
        <label>
          <span>发件人名称</span>
          <input v-model="cybermailDraft.from_name" type="text" placeholder="Enterprise AI QA Agent" />
        </label>
        <label>
          <span>Reply-To</span>
          <input v-model="cybermailDraft.reply_to" type="email" placeholder="reply@company.com" />
        </label>
        <label class="checkbox-row">
          <input v-model="cybermailDraft.use_tls" type="checkbox" />
          <span>启用 TLS</span>
        </label>
        <label class="checkbox-row">
          <input v-model="cybermailDraft.enabled" type="checkbox" />
          <span>启用该邮件提供方</span>
        </label>
        <label class="checkbox-row">
          <input v-model="cybermailDraft.is_default" type="checkbox" />
          <span>设为默认邮件通道</span>
        </label>
      </div>

      <div class="settings-actions">
        <button class="primary-btn narrow" :disabled="loading || saving" @click="saveEmail('cybermail')">保存 CyberMail 配置</button>
      </div>
    </div>
  </section>
</template>
