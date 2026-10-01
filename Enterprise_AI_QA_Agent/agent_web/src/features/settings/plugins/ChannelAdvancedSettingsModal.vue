<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { NButton, NModal, useMessage } from "naive-ui";
import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type {
  ChannelAdvancedAllowlist,
  ChannelAdvancedRoute,
  ChannelAdvancedSettings,
  ChannelConfigPublic,
} from "../../../types";

const props = defineProps<{
  show: boolean;
  configs: ChannelConfigPublic[];
}>();

const emit = defineEmits<{
  (event: "update:show", value: boolean): void;
}>();

type AccessListKey = keyof Pick<
  ChannelAdvancedAllowlist,
  | "qq_users"
  | "feishu_users"
  | "weixin_users"
  | "qq_groups"
  | "feishu_groups"
  | "weixin_groups"
  | "qq_approvers"
  | "feishu_approvers"
  | "weixin_approvers"
  | "qq_admins"
  | "feishu_admins"
  | "weixin_admins"
>;
type PlatformKey = "qq" | "feishu" | "weixin";

const accessPlatforms: Array<{
  key: PlatformKey;
  labelKey: string;
  users: AccessListKey;
  groups: AccessListKey;
  approvers: AccessListKey;
  admins: AccessListKey;
}> = [
  { key: "qq", labelKey: "channels.qq", users: "qq_users", groups: "qq_groups", approvers: "qq_approvers", admins: "qq_admins" },
  { key: "feishu", labelKey: "channels.advanced_feishu_lark", users: "feishu_users", groups: "feishu_groups", approvers: "feishu_approvers", admins: "feishu_admins" },
  { key: "weixin", labelKey: "channels.weixin", users: "weixin_users", groups: "weixin_groups", approvers: "weixin_approvers", admins: "weixin_admins" },
];

const queueModeOptions = [
  { value: "steer", labelKey: "channels.advanced_queue_mode_steer" },
  { value: "followup", labelKey: "channels.advanced_queue_mode_followup" },
  { value: "collect", labelKey: "channels.advanced_queue_mode_collect" },
  { value: "interrupt", labelKey: "channels.advanced_queue_mode_interrupt" },
];

const queueDropOptions = [
  { value: "summarize", labelKey: "channels.advanced_queue_drop_summarize" },
  { value: "old", labelKey: "channels.advanced_queue_drop_old" },
  { value: "new", labelKey: "channels.advanced_queue_drop_new" },
];

const routePlatformOptions = [
  { value: "", labelKey: "channels.advanced_any" },
  { value: "qq", labelKey: "channels.qq" },
  { value: "feishu", labelKey: "channels.feishu" },
  { value: "lark", labelKey: "channels.lark" },
  { value: "weixin", labelKey: "channels.weixin" },
];

const routeChatTypeOptions = [
  { value: "", labelKey: "channels.advanced_any" },
  { value: "dm", labelKey: "channels.advanced_chat_dm" },
  { value: "group", labelKey: "channels.advanced_chat_group" },
  { value: "guild", labelKey: "channels.advanced_chat_guild" },
  { value: "direct", labelKey: "channels.advanced_chat_direct" },
  { value: "thread", labelKey: "channels.advanced_chat_thread" },
];

const approvalModeOptions = [
  { value: "", labelKey: "channels.advanced_inherit" },
  { value: "inherit", labelKey: "channels.advanced_inherit" },
  { value: "ask", labelKey: "channels.advanced_approval_ask" },
  { value: "auto", labelKey: "channels.advanced_approval_auto" },
  { value: "yolo", labelKey: "channels.advanced_approval_yolo" },
];

const toast = useMessage();
const loading = ref(false);
const saving = ref(false);
const draft = reactive<ChannelAdvancedSettings>(defaultAdvancedSettings());
const listText = reactive<Record<PlatformKey, {
  users: string;
  groups: string;
  approvers: string;
  admins: string;
  self: string;
}>>({
  qq: { users: "", groups: "", approvers: "", admins: "", self: "" },
  feishu: { users: "", groups: "", approvers: "", admins: "", self: "" },
  weixin: { users: "", groups: "", approvers: "", admins: "", self: "" },
});

const connectionOptions = computed(() => props.configs.map((item) => ({
  value: String(item.id),
  label: `${item.config_name} · ${t(`channels.${item.domain}`)}`,
})));

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      void loadAdvancedSettings();
    }
  },
);

function close() {
  if (saving.value) return;
  emit("update:show", false);
}

async function loadAdvancedSettings() {
  loading.value = true;
  try {
    replaceDraft(mergeAdvancedSettings(await api.getChannelAdvancedSettings()));
    syncListTextFromDraft();
  } catch (error) {
    toast.error(error instanceof Error ? error.message : t("channels.advanced_load_failed"));
    replaceDraft(defaultAdvancedSettings());
    syncListTextFromDraft();
  } finally {
    loading.value = false;
  }
}

async function saveAdvancedSettings() {
  saving.value = true;
  try {
    syncDraftListsFromText();
    replaceDraft(mergeAdvancedSettings(await api.updateChannelAdvancedSettings(snapshotDraft())));
    syncListTextFromDraft();
    toast.success(t("channels.advanced_save_success"));
    emit("update:show", false);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : t("channels.advanced_save_failed"));
  } finally {
    saving.value = false;
  }
}

function setAccessMode(mode: "trusted" | "everyone") {
  draft.allowlist.enabled = true;
  draft.allowlist.allow_all = mode === "everyone";
}

function addRoute() {
  draft.routes.push(defaultRoute());
}

function removeRoute(index: number) {
  draft.routes.splice(index, 1);
}

function syncListTextFromDraft() {
  for (const platform of accessPlatforms) {
    listText[platform.key].users = stringifyList(draft.allowlist[platform.users]);
    listText[platform.key].groups = stringifyList(draft.allowlist[platform.groups]);
    listText[platform.key].approvers = stringifyList(draft.allowlist[platform.approvers]);
    listText[platform.key].admins = stringifyList(draft.allowlist[platform.admins]);
    listText[platform.key].self = stringifyList(draft.self_user_ids[platform.key]);
  }
}

function syncDraftListsFromText() {
  for (const platform of accessPlatforms) {
    draft.allowlist[platform.users] = parseList(listText[platform.key].users);
    draft.allowlist[platform.groups] = parseList(listText[platform.key].groups);
    draft.allowlist[platform.approvers] = parseList(listText[platform.key].approvers);
    draft.allowlist[platform.admins] = parseList(listText[platform.key].admins);
    draft.self_user_ids[platform.key] = parseList(listText[platform.key].self);
  }
}

function parseList(value: string | undefined): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of String(value || "").split(/,|\r?\n/)) {
    const text = item.trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

function stringifyList(value: string[] | undefined): string {
  return Array.isArray(value) ? value.join("\n") : "";
}

function defaultRoute(): ChannelAdvancedRoute {
  return {
    connection_id: "",
    platform: "",
    chat_type: "",
    chat_id: "",
    user_id: "",
    thread_id: "",
    model: "",
    tool_approval_mode: "",
    workspace_root: "",
  };
}

function defaultAdvancedSettings(): ChannelAdvancedSettings {
  return {
    allowlist: {
      enabled: true,
      allow_all: false,
      qq_users: [],
      feishu_users: [],
      weixin_users: [],
      qq_groups: [],
      feishu_groups: [],
      weixin_groups: [],
      qq_approvers: [],
      feishu_approvers: [],
      weixin_approvers: [],
      qq_admins: [],
      feishu_admins: [],
      weixin_admins: [],
    },
    max_steps: 25,
    debounce_ms: 1500,
    queue_mode: "steer",
    queue_cap: 20,
    queue_drop: "summarize",
    ignore_self_messages: true,
    self_user_ids: {
      qq: [],
      feishu: [],
      weixin: [],
    },
    pairing: {
      enabled: true,
      request_ttl_minutes: 60,
      max_pending_per_platform: 3,
    },
    routes: [],
  };
}

function mergeAdvancedSettings(value: Partial<ChannelAdvancedSettings> | null | undefined): ChannelAdvancedSettings {
  const base = defaultAdvancedSettings();
  const source = value || {};
  return {
    ...base,
    ...source,
    allowlist: { ...base.allowlist, ...(source.allowlist || {}) },
    self_user_ids: { ...base.self_user_ids, ...(source.self_user_ids || {}) },
    pairing: { ...base.pairing, ...(source.pairing || {}) },
    routes: Array.isArray(source.routes) ? source.routes.map((route) => ({ ...defaultRoute(), ...route })) : [],
  };
}

function replaceDraft(next: ChannelAdvancedSettings) {
  const normalized = mergeAdvancedSettings(next);
  draft.allowlist = normalized.allowlist;
  draft.max_steps = normalized.max_steps;
  draft.debounce_ms = normalized.debounce_ms;
  draft.queue_mode = normalized.queue_mode;
  draft.queue_cap = normalized.queue_cap;
  draft.queue_drop = normalized.queue_drop;
  draft.ignore_self_messages = normalized.ignore_self_messages;
  draft.self_user_ids = normalized.self_user_ids;
  draft.pairing = normalized.pairing;
  draft.routes = normalized.routes;
}

function snapshotDraft(): ChannelAdvancedSettings {
  return JSON.parse(JSON.stringify(draft)) as ChannelAdvancedSettings;
}
</script>

<template>
  <NModal
    :show="show"
    class="channel-advanced-modal-shell"
    style="width: min(1120px, calc(100vw - 48px)); max-width: 1120px;"
    :mask-closable="!saving"
    preset="card"
    :bordered="false"
    :segmented="{ content: true, footer: true }"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <template #header>
      <header class="channel-advanced-modal__head">
        <div>
          <span>{{ t("channels.advanced_eyebrow") }}</span>
          <h3>{{ t("channels.advanced_title") }}</h3>
          <p>{{ t("channels.advanced_desc") }}</p>
        </div>
      </header>
    </template>

      <div class="channel-advanced-modal__body" :aria-busy="loading">
        <section class="channel-advanced-panel">
          <div class="channel-advanced-panel__title">
            <div>
              <strong>{{ t("channels.advanced_access_title") }}</strong>
              <small>{{ t("channels.advanced_access_hint") }}</small>
            </div>
          </div>

          <div class="channel-advanced-choice-grid">
            <button
              type="button"
              class="channel-advanced-choice"
              :class="{ active: !draft.allowlist.allow_all }"
              @click="setAccessMode('trusted')"
            >
              <strong>{{ t("channels.advanced_access_trusted") }}</strong>
              <span>{{ t("channels.advanced_access_trusted_hint") }}</span>
            </button>
            <button
              type="button"
              class="channel-advanced-choice"
              :class="{ active: draft.allowlist.allow_all }"
              @click="setAccessMode('everyone')"
            >
              <strong>{{ t("channels.advanced_access_everyone") }}</strong>
              <span>{{ t("channels.advanced_access_everyone_hint") }}</span>
            </button>
          </div>

          <label class="channel-advanced-switch">
            <input v-model="draft.pairing.enabled" type="checkbox" />
            <span>
              <strong>{{ t("channels.advanced_pairing_gate") }}</strong>
              <small>{{ t("channels.advanced_pairing_gate_hint") }}</small>
            </span>
          </label>

          <div v-if="draft.allowlist.allow_all" class="channel-advanced-warning">
            {{ t("channels.advanced_allow_all_warn") }}
          </div>

          <div v-else class="channel-advanced-platforms">
            <article v-for="platform in accessPlatforms" :key="platform.key" class="channel-advanced-platform">
              <strong>{{ t(platform.labelKey) }}</strong>
              <label>
                <span>{{ t("channels.advanced_users") }}</span>
                <textarea v-model="listText[platform.key].users" rows="3" :placeholder="t('channels.advanced_list_ph')"></textarea>
              </label>
              <label>
                <span>{{ t("channels.advanced_groups") }}</span>
                <textarea v-model="listText[platform.key].groups" rows="3" :placeholder="t('channels.advanced_list_ph')"></textarea>
              </label>
            </article>
          </div>

          <details class="channel-advanced-subpanel">
            <summary>
              <span>
                <strong>{{ t("channels.advanced_roles_title") }}</strong>
                <small>{{ t("channels.advanced_roles_hint") }}</small>
              </span>
              <i class="fa-solid fa-chevron-down"></i>
            </summary>
            <div class="channel-advanced-platforms">
              <article v-for="platform in accessPlatforms" :key="platform.key" class="channel-advanced-platform">
                <strong>{{ t(platform.labelKey) }}</strong>
                <label>
                  <span>{{ t("channels.advanced_approvers") }}</span>
                  <textarea v-model="listText[platform.key].approvers" rows="3" :placeholder="t('channels.advanced_list_ph')"></textarea>
                </label>
                <label>
                  <span>{{ t("channels.advanced_admins") }}</span>
                  <textarea v-model="listText[platform.key].admins" rows="3" :placeholder="t('channels.advanced_list_ph')"></textarea>
                </label>
              </article>
            </div>
          </details>
        </section>

        <section class="channel-advanced-panel">
          <div class="channel-advanced-panel__title">
            <div>
              <strong>{{ t("channels.advanced_gateway_title") }}</strong>
              <small>{{ t("channels.advanced_gateway_hint") }}</small>
            </div>
          </div>

          <div class="channel-advanced-field-grid">
            <label>
              <span>{{ t("channels.advanced_max_steps") }}</span>
              <input v-model.number="draft.max_steps" type="number" min="0" />
            </label>
            <label>
              <span>{{ t("channels.advanced_debounce_ms") }}</span>
              <input v-model.number="draft.debounce_ms" type="number" min="0" />
            </label>
            <label>
              <span>{{ t("channels.advanced_queue_cap") }}</span>
              <input v-model.number="draft.queue_cap" type="number" min="0" />
            </label>
            <label>
              <span>{{ t("channels.advanced_queue_mode") }}</span>
              <select v-model="draft.queue_mode">
                <option v-for="option in queueModeOptions" :key="option.value" :value="option.value">
                  {{ t(option.labelKey) }}
                </option>
              </select>
            </label>
            <label>
              <span>{{ t("channels.advanced_queue_drop") }}</span>
              <select v-model="draft.queue_drop">
                <option v-for="option in queueDropOptions" :key="option.value" :value="option.value">
                  {{ t(option.labelKey) }}
                </option>
              </select>
            </label>
          </div>

          <label class="channel-advanced-switch">
            <input v-model="draft.ignore_self_messages" type="checkbox" />
            <span>
              <strong>{{ t("channels.advanced_ignore_self") }}</strong>
              <small>{{ t("channels.advanced_ignore_self_hint") }}</small>
            </span>
          </label>

          <div class="channel-advanced-platforms">
            <article v-for="platform in accessPlatforms" :key="platform.key" class="channel-advanced-platform">
              <strong>{{ t(platform.labelKey) }}</strong>
              <label>
                <span>{{ t("channels.advanced_self_user_ids") }}</span>
                <textarea v-model="listText[platform.key].self" rows="3" :placeholder="t('channels.advanced_list_ph')"></textarea>
              </label>
            </article>
          </div>

          <div class="channel-advanced-field-grid channel-advanced-field-grid--pairing">
            <label>
              <span>{{ t("channels.advanced_pairing_ttl") }}</span>
              <input v-model.number="draft.pairing.request_ttl_minutes" type="number" min="0" />
            </label>
            <label>
              <span>{{ t("channels.advanced_pairing_pending") }}</span>
              <input v-model.number="draft.pairing.max_pending_per_platform" type="number" min="0" />
            </label>
          </div>
        </section>

        <section class="channel-advanced-panel">
          <div class="channel-advanced-panel__title channel-advanced-panel__title--with-action">
            <div>
              <strong>{{ t("channels.advanced_routes_title") }}</strong>
              <small>{{ t("channels.advanced_routes_hint") }}</small>
            </div>
            <NButton size="small" secondary type="primary" @click="addRoute">
              {{ t("channels.advanced_add_route") }}
            </NButton>
          </div>

          <div v-if="draft.routes.length === 0" class="channel-advanced-empty">
            {{ t("channels.advanced_routes_empty") }}
          </div>

          <article v-for="(route, index) in draft.routes" :key="index" class="channel-advanced-route">
            <div class="channel-advanced-route__head">
              <strong>{{ t("channels.advanced_route_title", { n: index + 1 }) }}</strong>
              <button type="button" @click="removeRoute(index)">
                <i class="fa-solid fa-trash-can"></i>
                {{ t("common.delete") }}
              </button>
            </div>
            <div class="channel-advanced-field-grid">
              <label>
                <span>{{ t("channels.advanced_route_connection") }}</span>
                <select v-model="route.connection_id">
                  <option value="">{{ t("channels.advanced_any") }}</option>
                  <option v-for="option in connectionOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <label>
                <span>{{ t("channels.advanced_route_platform") }}</span>
                <select v-model="route.platform">
                  <option v-for="option in routePlatformOptions" :key="option.value" :value="option.value">
                    {{ t(option.labelKey) }}
                  </option>
                </select>
              </label>
              <label>
                <span>{{ t("channels.advanced_route_chat_type") }}</span>
                <select v-model="route.chat_type">
                  <option v-for="option in routeChatTypeOptions" :key="option.value" :value="option.value">
                    {{ t(option.labelKey) }}
                  </option>
                </select>
              </label>
              <label>
                <span>{{ t("channels.advanced_route_chat_id") }}</span>
                <input v-model="route.chat_id" type="text" />
              </label>
              <label>
                <span>{{ t("channels.advanced_route_user_id") }}</span>
                <input v-model="route.user_id" type="text" />
              </label>
              <label>
                <span>{{ t("channels.advanced_route_thread_id") }}</span>
                <input v-model="route.thread_id" type="text" />
              </label>
              <label>
                <span>{{ t("channels.advanced_route_workspace") }}</span>
                <input v-model="route.workspace_root" type="text" :placeholder="t('channels.advanced_route_workspace_ph')" />
              </label>
              <label>
                <span>{{ t("channels.advanced_route_model") }}</span>
                <input v-model="route.model" type="text" :placeholder="t('channels.advanced_route_model_ph')" />
              </label>
              <label>
                <span>{{ t("channels.advanced_route_approval") }}</span>
                <select v-model="route.tool_approval_mode">
                  <option v-for="option in approvalModeOptions" :key="option.value" :value="option.value">
                    {{ t(option.labelKey) }}
                  </option>
                </select>
              </label>
            </div>
          </article>
        </section>
      </div>

      <template #footer>
      <footer class="channel-advanced-modal__foot">
        <NButton quaternary :disabled="saving" @click="close">{{ t("common.cancel") }}</NButton>
        <NButton type="primary" :loading="saving" :disabled="loading" @click="saveAdvancedSettings">
          {{ t("channels.advanced_save") }}
        </NButton>
      </footer>
      </template>
  </NModal>
</template>

<style scoped>
.channel-advanced-modal-shell {
  width: min(1120px, calc(100vw - 36px));
}

.channel-advanced-modal__head,
.channel-advanced-modal__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.channel-advanced-modal__head span {
  color: #2563eb;
  font-size: 12px;
  font-weight: 800;
}

.channel-advanced-modal__head h3 {
  margin: 4px 0 6px;
  color: var(--text, #0f172a);
  font-size: 20px;
}

.channel-advanced-modal__head p {
  max-width: 720px;
  margin: 0;
  color: var(--muted, #64748b);
  line-height: 1.55;
}

.channel-advanced-route__head button {
  border: 0;
  background: transparent;
  color: var(--muted, #64748b);
  cursor: pointer;
}

.channel-advanced-modal__body {
  display: flex;
  max-height: min(74vh, 760px);
  flex-direction: column;
  gap: 16px;
  overflow: auto;
  background: color-mix(in srgb, var(--surface-muted, #f8fafc) 68%, transparent);
}

.channel-advanced-modal__foot {
  justify-content: flex-end;
}

.channel-advanced-panel,
.channel-advanced-subpanel,
.channel-advanced-route {
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  background: var(--surface, #ffffff);
  padding: 16px;
}

.channel-advanced-panel__title,
.channel-advanced-panel__title--with-action,
.channel-advanced-route__head,
.channel-advanced-subpanel summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.channel-advanced-panel__title {
  margin-bottom: 14px;
}

.channel-advanced-panel__title strong,
.channel-advanced-subpanel summary strong,
.channel-advanced-route__head strong,
.channel-advanced-platform > strong,
.channel-advanced-choice strong,
.channel-advanced-switch strong {
  color: var(--text, #0f172a);
}

.channel-advanced-panel__title small,
.channel-advanced-subpanel summary small,
.channel-advanced-choice span,
.channel-advanced-switch small {
  display: block;
  margin-top: 4px;
  color: var(--muted, #64748b);
  line-height: 1.5;
}

.channel-advanced-choice-grid,
.channel-advanced-platforms,
.channel-advanced-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.channel-advanced-platforms {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 14px;
}

.channel-advanced-choice,
.channel-advanced-platform,
.channel-advanced-switch,
.channel-advanced-empty {
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  background: var(--surface-muted, #f8fafc);
  padding: 13px;
}

.channel-advanced-choice {
  text-align: left;
  cursor: pointer;
}

.channel-advanced-choice.active {
  border-color: rgba(37, 99, 235, 0.55);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.11);
}

.channel-advanced-switch {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.channel-advanced-switch input {
  margin-top: 4px;
}

.channel-advanced-warning {
  margin-top: 14px;
  border: 1px solid rgba(245, 158, 11, 0.28);
  border-radius: 10px;
  background: rgba(245, 158, 11, 0.08);
  color: #92400e;
  padding: 12px 13px;
}

.channel-advanced-platform {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
}

.channel-advanced-field-grid label,
.channel-advanced-platform label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
}

.channel-advanced-field-grid span,
.channel-advanced-platform label span {
  color: var(--muted, #64748b);
  font-size: 12px;
  font-weight: 800;
}

.channel-advanced-field-grid input,
.channel-advanced-field-grid select,
.channel-advanced-platform textarea {
  width: 100%;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--surface, #ffffff);
  color: var(--text, #0f172a);
  font: inherit;
  outline: none;
  padding: 10px 11px;
  pointer-events: auto;
  user-select: text;
  -webkit-user-select: text;
}

.channel-advanced-platform textarea {
  min-height: 78px;
  resize: vertical;
}

.channel-advanced-field-grid input:focus,
.channel-advanced-field-grid select:focus,
.channel-advanced-platform textarea:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.channel-advanced-subpanel {
  margin-top: 14px;
  padding: 0;
}

.channel-advanced-subpanel summary {
  cursor: pointer;
  list-style: none;
  padding: 14px 16px;
}

.channel-advanced-subpanel summary::-webkit-details-marker {
  display: none;
}

.channel-advanced-subpanel[open] summary {
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.channel-advanced-subpanel[open] summary i {
  transform: rotate(180deg);
}

.channel-advanced-subpanel .channel-advanced-platforms {
  padding: 0 16px 16px;
}

.channel-advanced-field-grid--pairing {
  margin-top: 14px;
}

.channel-advanced-empty {
  color: var(--muted, #64748b);
  line-height: 1.6;
}

.channel-advanced-route {
  margin-top: 12px;
}

.channel-advanced-route__head {
  align-items: center;
  margin-bottom: 12px;
}

.channel-advanced-route__head button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #dc2626;
  font-weight: 700;
}

@media (max-width: 900px) {
  .channel-advanced-choice-grid,
  .channel-advanced-platforms,
  .channel-advanced-field-grid {
    grid-template-columns: 1fr;
  }
}
</style>
