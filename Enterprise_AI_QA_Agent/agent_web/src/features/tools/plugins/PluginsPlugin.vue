<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useMessage } from "naive-ui";

import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type {
  IntegrationAuthType,
  IntegrationCreateRequest,
  IntegrationKind,
  IntegrationRecord,
  IntegrationTestResponse,
  ManagedMCPPromptDescriptor,
  ManagedMCPResourceDescriptor,
  ManagedMCPServerDescriptor,
  ManagedMCPToolDescriptor,
} from "../../../types";

type PluginTab = "api" | "mcp";
type ModalKind = "api" | "mcp";

const toast = useMessage();

const apiIntegrations = ref<IntegrationRecord[]>([]);
const mcpServers = ref<ManagedMCPServerDescriptor[]>([]);
const mcpToolsByServer = ref<Record<string, ManagedMCPToolDescriptor[]>>({});
const mcpResourcesByServer = ref<Record<string, ManagedMCPResourceDescriptor[]>>({});
const mcpPromptsByServer = ref<Record<string, ManagedMCPPromptDescriptor[]>>({});

const activeTab = ref<PluginTab>("api");
const loading = ref(false);
const error = ref("");
const saving = ref(false);
const testingId = ref("");
const deletingId = ref("");
const mcpTestingKey = ref("");
const mcpDeletingKey = ref("");
const mcpReconnectingKey = ref("");
const toolsLoadingKey = ref("");

const modalOpen = ref(false);
const modalKind = ref<ModalKind>("api");
const editingApiId = ref<string | null>(null);
const editingMcpKey = ref<string | null>(null);
const testResult = ref<IntegrationTestResponse | null>(null);

const apiName = ref("");
const apiEnabled = ref(true);
const apiDescription = ref("");
const apiProjectName = ref("");
const apiDocumentUrl = ref("");
const apiBaseUrl = ref("");
const apiHeadersJson = ref("{}");
const apiAuthType = ref<IntegrationAuthType>("none");
const apiAuthToken = ref("");
const apiKeyHeader = ref("X-API-Key");
const apiUsername = ref("");
const apiPassword = ref("");

const mcpEnabled = ref(true);
const mcpJsonText = ref("");

const mcpToolsModalOpen = ref(false);
const mcpToolsModalKey = ref<string | null>(null);

const mcpJsonPlaceholder = `{
  "mcpServers": {
    "remote-tools": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}`;

const mcpToolsModalTitle = computed(() => {
  const key = mcpToolsModalKey.value;
  if (!key) return t("plugins.mcp_tools");
  return mcpServers.value.find((item) => item.key === key)?.name ?? key;
});

const mcpToolsModalTools = computed(() => {
  const key = mcpToolsModalKey.value;
  return key ? mcpToolsByServer.value[key] ?? [] : [];
});

const mcpToolsModalResources = computed(() => {
  const key = mcpToolsModalKey.value;
  return key ? mcpResourcesByServer.value[key] ?? [] : [];
});

const mcpToolsModalPrompts = computed(() => {
  const key = mcpToolsModalKey.value;
  return key ? mcpPromptsByServer.value[key] ?? [] : [];
});

function resetApiForm() {
  apiName.value = "";
  apiEnabled.value = true;
  apiDescription.value = "";
  apiProjectName.value = "";
  apiDocumentUrl.value = "";
  apiBaseUrl.value = "";
  apiHeadersJson.value = "{}";
  apiAuthType.value = "none";
  apiAuthToken.value = "";
  apiKeyHeader.value = "X-API-Key";
  apiUsername.value = "";
  apiPassword.value = "";
  testResult.value = null;
}

function resetMcpForm() {
  mcpEnabled.value = true;
  mcpJsonText.value = "";
}

function openCreate(kind: IntegrationKind = "api") {
  modalKind.value = kind === "mcp" ? "mcp" : "api";
  editingApiId.value = null;
  editingMcpKey.value = null;
  resetApiForm();
  resetMcpForm();
  modalOpen.value = true;
}

function closeModal() {
  modalOpen.value = false;
  editingApiId.value = null;
  editingMcpKey.value = null;
  resetApiForm();
  resetMcpForm();
}

function parseJsonMap(label: string, raw: string): Record<string, string> {
  const trimmed = raw.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${label} ${t("plugins.must_be_json_object")}`);
    }
    return normalizeUnknownMap(parsed);
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : `${label} ${t("plugins.parse_failed")}`);
  }
}

function normalizeUnknownMap(value: Record<string, unknown> | undefined | null): Record<string, string> {
  const result: Record<string, string> = {};
  Object.entries(value || {}).forEach(([key, item]) => {
    const normalizedKey = String(key || "").trim();
    const normalizedValue = String(item ?? "").trim();
    if (normalizedKey && normalizedValue) result[normalizedKey] = normalizedValue;
  });
  return result;
}

function connectionState(server: ManagedMCPServerDescriptor) {
  const state = server.metadata?.connection_state;
  return state && typeof state === "object" ? state as Record<string, unknown> : {};
}

function credentialSummary(server: ManagedMCPServerDescriptor) {
  const summary = server.metadata?.credential_summary;
  return summary && typeof summary === "object" ? summary as Record<string, unknown> : {};
}

function stringListFromMetadata(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function mcpToolCount(server: ManagedMCPServerDescriptor): number {
  const count = connectionState(server).tool_count;
  return typeof count === "number" ? count : 0;
}

function mcpResourceCount(server: ManagedMCPServerDescriptor): number {
  const count = connectionState(server).resource_count ?? server.metadata?.resource_count;
  return typeof count === "number" ? count : 0;
}

function mcpPromptCount(server: ManagedMCPServerDescriptor): number {
  const count = connectionState(server).prompt_count ?? server.metadata?.prompt_count;
  return typeof count === "number" ? count : 0;
}

function mcpLastError(server: ManagedMCPServerDescriptor): string {
  return String(connectionState(server).last_error || "");
}

function mcpCredentialText(server: ManagedMCPServerDescriptor): string {
  const summary = credentialSummary(server);
  const headers = stringListFromMetadata(summary.headers);
  const env = stringListFromMetadata(summary.env);
  const parts: string[] = [];
  if (headers.length) parts.push(`headers: ${headers.join(", ")} = ******`);
  if (env.length) parts.push(`env: ${env.join(", ")} = ******`);
  if (summary.has_command) parts.push("command: configured");
  return parts.join(" / ");
}

function buildApiPayload(): IntegrationCreateRequest {
  const headers = parseJsonMap(t("plugins.label_headers"), apiHeadersJson.value);
  const payload: IntegrationCreateRequest = {
    name: apiName.value.trim(),
    kind: "api",
    enabled: apiEnabled.value,
    description: apiDescription.value.trim() || null,
    project_name: apiProjectName.value.trim() || null,
    document_url: apiDocumentUrl.value.trim() || null,
    headers,
    env: {},
    capabilities: [],
    metadata: {},
    base_url: apiBaseUrl.value.trim() || null,
    auth_type: apiAuthType.value,
    auth_config: {},
  };

  if (apiAuthType.value === "bearer") {
    payload.auth_config = { token: apiAuthToken.value.trim() };
  } else if (apiAuthType.value === "api_key") {
    payload.auth_config = {
      header_name: apiKeyHeader.value.trim() || "X-API-Key",
      token: apiAuthToken.value.trim(),
    };
  } else if (apiAuthType.value === "basic") {
    payload.auth_config = {
      username: apiUsername.value.trim(),
      password: apiPassword.value,
    };
  }

  if (!payload.name) throw new Error(t("plugins.name_required"));
  if (!payload.base_url && !payload.document_url) throw new Error(t("plugins.base_url_required"));
  return payload;
}

function hydrateApiForm(record: IntegrationRecord) {
  modalKind.value = "api";
  apiName.value = record.name;
  apiEnabled.value = record.enabled;
  apiDescription.value = record.description || "";
  apiProjectName.value = record.project_name || "";
  apiDocumentUrl.value = record.document_url || "";
  apiBaseUrl.value = record.base_url || "";
  apiHeadersJson.value = JSON.stringify(record.headers || {}, null, 2);
  apiAuthType.value = record.auth_type;
  apiAuthToken.value = record.auth_config.token || "";
  apiKeyHeader.value = record.auth_config.header_name || "X-API-Key";
  apiUsername.value = record.auth_config.username || "";
  apiPassword.value = record.auth_config.password || "";
}

function hydrateMcpForm(server: ManagedMCPServerDescriptor) {
  modalKind.value = "mcp";
  mcpEnabled.value = server.enabled;
  mcpJsonText.value = JSON.stringify(server.config || {}, null, 2);
}

async function loadData() {
  loading.value = true;
  error.value = "";
  try {
    const [integrations, servers] = await Promise.all([
      api.listIntegrations(),
      api.listManagedMcpServers(),
    ]);
    apiIntegrations.value = integrations;
    mcpServers.value = servers;
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("plugins.load_failed");
  } finally {
    loading.value = false;
  }
}

function openEditApi(record: IntegrationRecord) {
  editingApiId.value = record.id;
  editingMcpKey.value = null;
  hydrateApiForm(record);
  modalOpen.value = true;
}

function openEditMcp(server: ManagedMCPServerDescriptor) {
  if (server.source_kind !== "external") return;
  editingMcpKey.value = server.key;
  editingApiId.value = null;
  hydrateMcpForm(server);
  modalOpen.value = true;
}

async function saveModal() {
  saving.value = true;
  try {
    if (modalKind.value === "api") {
      const payload = buildApiPayload();
      const saved = editingApiId.value
        ? await api.updateIntegration(editingApiId.value, payload)
        : await api.createIntegration(payload);
      apiIntegrations.value = [saved, ...apiIntegrations.value.filter((item) => item.id !== saved.id)];
      toast.success(editingApiId.value ? t("plugins.updated") : t("plugins.created"), { duration: 2200 });
    } else {
      if (editingMcpKey.value) {
        const payload = parseMcpServerConfigForEdit();
        await api.updateManagedMcpServer(editingMcpKey.value, payload);
        toast.success(t("plugins.updated"), { duration: 2200 });
      } else {
        await importMcpFromJson({ closeAfterImport: false });
      }
    }
    await loadData();
    closeModal();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.save_failed"));
  } finally {
    saving.value = false;
  }
}

function parseMcpServerConfigForEdit() {
  const rawJson = mcpJsonText.value.trim();
  if (!rawJson) throw new Error(t("plugins.paste_json_first"));
  const parsed = JSON.parse(rawJson) as Record<string, unknown>;
  const servers = parsed.mcpServers;
  let name: string | undefined;
  let config: Record<string, unknown> = parsed;
  if (servers && typeof servers === "object" && !Array.isArray(servers)) {
    const entries = Object.entries(servers as Record<string, unknown>).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value));
    if (entries.length !== 1) {
      throw new Error(t("plugins.single_mcp_edit_required"));
    }
    name = entries[0][0];
    config = entries[0][1] as Record<string, unknown>;
  } else if (typeof parsed.name === "string") {
    name = parsed.name.trim();
  }
  return {
    ...(name ? { name } : {}),
    enabled: mcpEnabled.value,
    config,
  };
}

async function importMcpFromJson(options: { closeAfterImport?: boolean } = {}) {
  saving.value = true;
  try {
    const rawJson = mcpJsonText.value.trim();
    if (!rawJson) throw new Error(t("plugins.paste_json_first"));
    const payload = JSON.parse(rawJson) as Record<string, unknown>;
    const response = await api.importManagedMcpServers({ payload });
    toast.success(t("plugins.imported_count", { count: String(response.servers.length) }), { duration: 2200 });
    await loadData();
    if (options.closeAfterImport !== false) closeModal();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.json_import_failed"));
    throw err;
  } finally {
    saving.value = false;
  }
}

async function deleteApiIntegration(record: IntegrationRecord) {
  deletingId.value = record.id;
  try {
    await api.deleteIntegration(record.id);
    apiIntegrations.value = apiIntegrations.value.filter((item) => item.id !== record.id);
    toast.success(`${t("plugins.deleted")}: ${record.name}`, { duration: 2200 });
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.delete_failed"));
  } finally {
    deletingId.value = "";
  }
}

async function deleteMcpServer(server: ManagedMCPServerDescriptor) {
  if (server.source_kind !== "external") return;
  mcpDeletingKey.value = server.key;
  try {
    await api.deleteManagedMcpServer(server.key);
    mcpServers.value = mcpServers.value.filter((item) => item.key !== server.key);
    toast.success(`${t("plugins.deleted")}: ${server.name}`, { duration: 2200 });
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.delete_failed"));
  } finally {
    mcpDeletingKey.value = "";
  }
}

async function testApiIntegration(record: IntegrationRecord) {
  testingId.value = record.id;
  try {
    const result = await api.testIntegration(record.id);
    testResult.value = result;
    toast[result.ok ? "success" : "warning"](result.message, { duration: 2600 });
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.test_failed"));
  } finally {
    testingId.value = "";
  }
}

async function testMcpServer(server: ManagedMCPServerDescriptor) {
  mcpTestingKey.value = server.key;
  try {
    const result = await api.testManagedMcpServer(server.key);
    const meta = `tools ${result.tool_count ?? 0}${result.latency_ms != null ? ` / ${result.latency_ms} ms` : ""}`;
    toast[result.ok ? "success" : "warning"](`${result.message} / ${meta}`, { duration: 3200 });
    await loadData();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.managed_test_failed"));
  } finally {
    mcpTestingKey.value = "";
  }
}

async function confirmMcpStdio(server: ManagedMCPServerDescriptor) {
  try {
    await api.confirmManagedMcpStdio(server.key);
    toast.success("stdio MCP 已确认", { duration: 2400 });
    await loadData();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.save_failed"));
  }
}

async function reconnectMcpServer(server: ManagedMCPServerDescriptor) {
  mcpReconnectingKey.value = server.key;
  try {
    await api.reconnectManagedMcpServer(server.key);
    toast.success("MCP Host 已重新连接", { duration: 2400 });
    await loadData();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.managed_test_failed"));
  } finally {
    mcpReconnectingKey.value = "";
  }
}

async function openMcpToolsModal(server: ManagedMCPServerDescriptor) {
  mcpToolsModalKey.value = server.key;
  mcpToolsModalOpen.value = true;
  if (mcpToolsByServer.value[server.key] && mcpResourcesByServer.value[server.key] && mcpPromptsByServer.value[server.key]) return;
  toolsLoadingKey.value = server.key;
  try {
    const [toolsResponse, resourcesResponse, promptsResponse] = await Promise.all([
      api.listManagedMcpTools(server.key),
      api.listManagedMcpResources(server.key),
      api.listManagedMcpPrompts(server.key),
    ]);
    mcpToolsByServer.value = {
      ...mcpToolsByServer.value,
      [server.key]: toolsResponse.tools,
    };
    mcpResourcesByServer.value = {
      ...mcpResourcesByServer.value,
      [server.key]: resourcesResponse.resources,
    };
    mcpPromptsByServer.value = {
      ...mcpPromptsByServer.value,
      [server.key]: promptsResponse.prompts,
    };
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("plugins.load_tools_failed"));
    closeMcpToolsModal();
  } finally {
    toolsLoadingKey.value = "";
  }
}

function closeMcpToolsModal() {
  mcpToolsModalOpen.value = false;
  mcpToolsModalKey.value = null;
}

function handleOpenCreateEvent(event: Event) {
  const detail = (event as CustomEvent<{ kind?: IntegrationKind }>).detail;
  const kind = detail?.kind || "api";
  activeTab.value = kind === "mcp" ? "mcp" : "api";
  openCreate(kind);
}

function broadcastPluginsIntegrationPane() {
  window.dispatchEvent(new CustomEvent("qa-agent:plugins-integration-pane", { detail: { tab: activeTab.value } }));
}

watch(activeTab, broadcastPluginsIntegrationPane);

onMounted(() => {
  void loadData();
  broadcastPluginsIntegrationPane();
  window.addEventListener("qa-agent:open-integration-create", handleOpenCreateEvent as EventListener);
});

onBeforeUnmount(() => {
  window.removeEventListener("qa-agent:open-integration-create", handleOpenCreateEvent as EventListener);
});
</script>

<template>
  <div class="tools-tab-pane">


    <nav class="main-tabs">
      <button :class="{ active: activeTab === 'api' }" @click="activeTab = 'api'">
        {{ t("plugins.tab_api") }} <span class="count">{{ apiIntegrations.length }}</span>
      </button>
      <button :class="{ active: activeTab === 'mcp' }" @click="activeTab = 'mcp'">
        MCP Host <span class="count">{{ mcpServers.length }}</span>
      </button>
    </nav>

    <div v-if="loading" class="plugins-empty">
      <i class="fa-solid fa-spinner fa-spin"></i>
      <p>{{ t("plugins.loading") }}</p>
    </div>
    <div v-else-if="error" class="plugins-empty plugins-empty--error">
      <i class="fa-solid fa-circle-exclamation"></i>
      <p>{{ error }}</p>
    </div>

    <section v-else-if="activeTab === 'api'" class="panel-section">
      <div v-if="apiIntegrations.length" class="integration-grid">
        <article v-for="item in apiIntegrations" :key="item.id" class="integration-card">
          <header class="integration-card-head">
            <div class="card-title-row">
              <span class="status-dot" :class="{ active: item.enabled }"></span>
              <h5>{{ item.name }}</h5>
            </div>
            <span class="meta-badge">{{ item.enabled ? t("plugins.enabled") : t("plugins.disabled") }}</span>
          </header>
          <p class="card-desc">{{ item.description || t("plugins.no_description") }}</p>
          <div class="integration-meta">
            <div class="meta-item truncate" :title="item.base_url || ''"><i class="fa-solid fa-link"></i> {{ item.base_url || t("plugins.no_base_url") }}</div>
            <div class="meta-item truncate" :title="item.document_url || ''"><i class="fa-solid fa-book"></i> {{ item.document_url || t("plugins.no_doc_url") }}</div>
            <div class="meta-item truncate" :title="item.project_name || ''"><i class="fa-solid fa-folder"></i> {{ item.project_name || t("plugins.no_project") }}</div>
          </div>
          <div class="integration-actions">
            <button class="ghost-btn" @click="openEditApi(item)">{{ t("common.edit") }}</button>
            <button class="ghost-btn" :disabled="testingId === item.id" @click="testApiIntegration(item)">
              {{ testingId === item.id ? t("plugins.testing") : t("plugins.test_connection") }}
            </button>
            <button class="ghost-btn danger" :disabled="deletingId === item.id" @click="deleteApiIntegration(item)">
              {{ deletingId === item.id ? t("plugins.deleting") : t("common.delete") }}
            </button>
          </div>
        </article>
      </div>
      <div v-else class="plugins-empty">
        <i class="fa-solid fa-plug"></i>
        <p>{{ t("plugins.no_api") }}</p>
      </div>
    </section>

    <section v-else class="panel-section">
      <div v-if="mcpServers.length" class="integration-grid">
        <article v-for="server in mcpServers" :key="server.key" class="integration-card integration-card--mcp">
          <header class="integration-card-head">
            <div class="card-title-row">
              <span class="status-dot" :class="{ active: server.status === 'connected' }"></span>
              <h5>{{ server.name }}</h5>
            </div>
            <span class="meta-badge">{{ server.source_kind === "builtin" ? "builtin" : "external" }}</span>
          </header>
          <p class="card-desc">{{ server.summary }}</p>
          <div class="integration-meta">
            <div class="meta-group">
              <span class="meta-chip"><i class="fa-solid fa-shuffle"></i> {{ server.transport }}</span>
              <span class="meta-chip" :class="{ connected: server.status === 'connected' }"><i class="fa-solid fa-signal"></i> {{ server.status }}</span>
              <span class="meta-chip" title="Tools"><i class="fa-solid fa-screwdriver-wrench"></i> {{ mcpToolCount(server) }}</span>
              <span class="meta-chip" title="Resources"><i class="fa-solid fa-database"></i> {{ mcpResourceCount(server) }}</span>
              <span class="meta-chip" title="Prompts"><i class="fa-solid fa-message"></i> {{ mcpPromptCount(server) }}</span>
            </div>
            <div class="meta-item truncate" :title="server.project_name || ''"><i class="fa-solid fa-folder"></i> {{ server.project_name || t("plugins.no_project") }}</div>
            <div class="meta-item truncate" :title="server.endpoint_url || server.document_url || ''"><i class="fa-solid fa-link"></i> {{ server.endpoint_url || server.document_url || t("plugins.no_remote_url") }}</div>
          </div>
          <div v-if="mcpCredentialText(server)" class="credential-summary">
            <i class="fa-solid fa-key"></i> {{ mcpCredentialText(server) }}
          </div>
          <div class="tag-row">
            <span v-for="capability in server.capabilities" :key="capability">{{ capability }}</span>
            <span v-if="server.provider_name">{{ server.provider_name }}</span>
          </div>
          <div v-if="mcpLastError(server)" class="inline-error">
            {{ mcpLastError(server) }}
          </div>
          <div class="integration-actions">
            <button class="ghost-btn" :disabled="toolsLoadingKey === server.key" @click="openMcpToolsModal(server)">
              {{ toolsLoadingKey === server.key ? t("plugins.loading_tools") : t("plugins.view_tools") }}
            </button>
            <button class="ghost-btn" :disabled="mcpTestingKey === server.key" @click="testMcpServer(server)">
              {{ mcpTestingKey === server.key ? t("plugins.testing") : t("plugins.unified_test") }}
            </button>
            <button
              v-if="server.source_kind === 'external'"
              class="ghost-btn"
              :disabled="mcpReconnectingKey === server.key"
              @click="reconnectMcpServer(server)"
            >
              {{ mcpReconnectingKey === server.key ? "reconnecting" : "reconnect" }}
            </button>
            <button
              v-if="server.source_kind === 'external' && server.transport === 'stdio' && !server.metadata?.confirmed_at"
              class="ghost-btn"
              @click="confirmMcpStdio(server)"
            >
              确认 stdio
            </button>
            <button v-if="server.source_kind === 'external'" class="ghost-btn" @click="openEditMcp(server)">
              {{ t("common.edit") }}
            </button>
            <button
              v-if="server.source_kind === 'external'"
              class="ghost-btn danger"
              :disabled="mcpDeletingKey === server.key"
              @click="deleteMcpServer(server)"
            >
              {{ mcpDeletingKey === server.key ? t("plugins.deleting") : t("common.delete") }}
            </button>
          </div>
        </article>
      </div>
      <div v-else class="plugins-empty">
        <i class="fa-solid fa-network-wired"></i>
        <p>{{ t("plugins.no_managed") }}</p>
      </div>
    </section>

    <div v-if="modalOpen" class="tools-modal-backdrop" @click.self="closeModal">
      <section class="tools-modal integration-modal">
        <header class="tools-modal-head">
          <div>
            <h3>{{ modalKind === "mcp" ? "MCP Host" : "API" }}</h3>
            <p>{{ modalKind === "mcp" ? "通过 MCP Host 创建真实可连接、可被模型调用的 MCP 服务。" : t("plugins.api_form_hint") }}</p>
          </div>
          <button type="button" class="icon-btn" @click="closeModal">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div class="modal-body modal-body-scroll">
          <template v-if="modalKind === 'mcp'">
            <div class="json-import-panel">
              <label>{{ editingMcpKey ? t("plugins.mcp_config_json") : t("plugins.json_import") }}</label>
              <textarea v-model="mcpJsonText" class="mcp-json-textarea" rows="12" spellcheck="false" :placeholder="mcpJsonPlaceholder"></textarea>
              <p class="field-hint">JSON 解析在后端完成，前端只提交原始配置。</p>
            </div>

          </template>

          <template v-else>
            <div class="settings-list">
              <div class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_name") }}</label>
                </div>
                <div class="setting-control">
                  <input v-model="apiName" :placeholder="t('plugins.ph_api_name')" />
                </div>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_description") }}</label>
                </div>
                <div class="setting-control">
                  <input v-model="apiDescription" :placeholder="t('plugins.ph_description')" />
                </div>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <label>Base URL</label>
                  <p>{{ t("plugins.hint_base_url") }}</p>
                </div>
                <div class="setting-control">
                  <input v-model="apiBaseUrl" placeholder="https://api.example.com" />
                </div>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_document_url") }}</label>
                  <p>{{ t("plugins.hint_doc_url") }}</p>
                </div>
                <div class="setting-control">
                  <input v-model="apiDocumentUrl" placeholder="https://api.example.com/openapi.json" />
                </div>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_project") }}</label>
                </div>
                <div class="setting-control">
                  <input v-model="apiProjectName" :placeholder="t('plugins.ph_project')" />
                </div>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_auth_type") }}</label>
                </div>
                <div class="setting-control">
                  <select v-model="apiAuthType">
                    <option value="none">none</option>
                    <option value="bearer">bearer</option>
                    <option value="api_key">api_key</option>
                    <option value="basic">basic</option>
                  </select>
                </div>
              </div>
              <div v-if="apiAuthType === 'bearer' || apiAuthType === 'api_key'" class="setting-item">
                <div class="setting-info">
                  <label>{{ apiAuthType === "api_key" ? "API Key" : "Bearer Token" }}</label>
                </div>
                <div class="setting-control">
                  <input v-model="apiAuthToken" type="password" :placeholder="t('plugins.ph_token')" />
                </div>
              </div>
              <div v-if="apiAuthType === 'api_key'" class="setting-item">
                <div class="setting-info">
                  <label>{{ t("plugins.form_header_name") }}</label>
                </div>
                <div class="setting-control">
                  <input v-model="apiKeyHeader" placeholder="X-API-Key" />
                </div>
              </div>
              <div v-if="apiAuthType === 'basic'" class="setting-item">
                <div class="setting-info"><label>{{ t("plugins.form_username") }}</label></div>
                <div class="setting-control"><input v-model="apiUsername" placeholder="username" /></div>
              </div>
              <div v-if="apiAuthType === 'basic'" class="setting-item">
                <div class="setting-info"><label>{{ t("plugins.form_password") }}</label></div>
                <div class="setting-control"><input v-model="apiPassword" type="password" placeholder="password" /></div>
              </div>
              <div class="field-block">
                <label>{{ t("plugins.form_headers_json") }}</label>
                <textarea v-model="apiHeadersJson" rows="5" spellcheck="false"></textarea>
              </div>
              <div class="check-row">
                <label class="check-label">
                  <input v-model="apiEnabled" type="checkbox">
                  <span>{{ t("plugins.enable_after_create") }}</span>
                </label>
              </div>
            </div>
          </template>
        </div>

        <footer class="tools-modal-foot">
          <button class="ghost-btn" @click="closeModal">{{ t("common.cancel") }}</button>
          <button
            v-if="modalKind === 'mcp'"
            class="primary-btn"
            :disabled="saving"
            @click="saveModal"
          >
            {{ saving ? t("plugins.saving") : (editingMcpKey ? t("plugins.save_changes") : t("plugins.import_mcp_config")) }}
          </button>
          <button v-else class="primary-btn" :disabled="saving" @click="saveModal">
            {{ saving ? t("plugins.saving") : (editingApiId || editingMcpKey ? t("plugins.save_changes") : t("plugins.add_server")) }}
          </button>
        </footer>
      </section>
    </div>

    <div v-if="mcpToolsModalOpen && mcpToolsModalKey" class="tools-modal-backdrop" @click.self="closeMcpToolsModal">
      <section class="tools-modal managed-tools-dialog">
        <header class="tools-modal-head">
          <div>
            <h3>{{ mcpToolsModalTitle }}</h3>
            <p>{{ t("plugins.managed_tools_desc") }}</p>
          </div>
          <button type="button" class="icon-btn" @click="closeMcpToolsModal">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </header>
        <div class="modal-body modal-body-scroll">
          <template v-if="toolsLoadingKey === mcpToolsModalKey">
            <p class="tools-empty">{{ t("plugins.loading_tools") }}</p>
          </template>
          <template v-else-if="!mcpToolsModalTools.length && !mcpToolsModalResources.length && !mcpToolsModalPrompts.length">
            <p class="tools-empty">{{ t("plugins.no_tools_exposed") }}</p>
          </template>
          <div v-else class="tool-list capability-list">
            <section v-if="mcpToolsModalTools.length" class="capability-section">
              <h4>Tools</h4>
              <div v-for="tool in mcpToolsModalTools" :key="tool.key" class="tool-item">
                <div class="tool-item-head">
                  <strong>{{ tool.name }}</strong>
                  <span class="tool-kind">{{ tool.source_kind }}</span>
                </div>
                <p>{{ tool.description || t("plugins.no_description") }}</p>
              </div>
            </section>
            <section v-if="mcpToolsModalResources.length" class="capability-section">
              <h4>Resources</h4>
              <div v-for="resource in mcpToolsModalResources" :key="resource.uri" class="tool-item">
                <div class="tool-item-head">
                  <strong>{{ resource.name || resource.uri }}</strong>
                  <span class="tool-kind">{{ resource.mime_type || "resource" }}</span>
                </div>
                <p>{{ resource.description || resource.uri }}</p>
              </div>
            </section>
            <section v-if="mcpToolsModalPrompts.length" class="capability-section">
              <h4>Prompts</h4>
              <div v-for="prompt in mcpToolsModalPrompts" :key="prompt.name" class="tool-item">
                <div class="tool-item-head">
                  <strong>{{ prompt.name }}</strong>
                  <span class="tool-kind">args {{ prompt.arguments.length }}</span>
                </div>
                <p>{{ prompt.description || t("plugins.no_description") }}</p>
              </div>
            </section>
          </div>
        </div>
        <footer class="tools-modal-foot">
          <button type="button" class="ghost-btn" @click="closeMcpToolsModal">{{ t("common.close") }}</button>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tools-tab-pane {
  display: flex;
  flex-direction: column;
  gap: 20px;
  color: var(--text);
}

.pane-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
}

.section-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}

.head-desc {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 14px;
}

.main-tabs,
.integration-card-head,
.card-title-row,
.integration-actions,
.tag-row,
.provider-strip {
  display: flex;
  align-items: center;
  gap: 10px;
}

.main-tabs {
  gap: 14px;
  border-bottom: 1px solid var(--border);
}

.main-tabs button {
  position: relative;
  padding: 10px 2px 14px;
  background: transparent;
  border: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--muted);
  cursor: pointer;
}

.main-tabs button.active {
  color: var(--text);
}

.main-tabs button.active::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 3px;
  border-radius: 999px;
  background: var(--accent);
}

.count {
  margin-left: 6px;
  color: var(--muted);
}

.panel-section {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.integration-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
  gap: 18px;
  justify-items: start;
}

.integration-card {
  width: 100%;
  max-width: 520px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  box-shadow: var(--shadow-soft);
}

.integration-card--mcp {
  max-width: 460px;
  border-color: color-mix(in srgb, var(--blue) 35%, var(--border));
}

.integration-card-head {
  justify-content: space-between;
}

.card-title-row h5 {
  margin: 0;
  font-size: 16px;
}

.card-desc {
  margin: 0;
  color: var(--muted);
  line-height: 1.5;
  min-height: 42px;
}

.integration-meta {
  display: grid;
  gap: 10px;
}

.meta-item {
  display: flex;
  gap: 10px;
  align-items: center;
  color: var(--muted);
  font-size: 13px;
  word-break: break-all;
}

.integration-actions {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  overflow-x: auto;
}

.integration-actions .ghost-btn {
  padding: 4px 8px;
  font-size: 12px;
  border-radius: 6px;
  white-space: nowrap;
  flex-shrink: 0;
}

.credential-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--muted);
  background: var(--surface-soft);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta-badge,
.provider-chip,
.tag-row span,
.tool-kind {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  padding: 4px 10px;
  font-size: 12px;
  color: var(--muted);
}

.tag-row,
.provider-strip {
  flex-wrap: wrap;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--border-strong);
}

.status-dot.active {
  background: var(--green);
}

.ghost-btn,
.primary-btn,
.icon-btn {
  border-radius: 8px;
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.2s ease;
}

.ghost-btn:hover,
.primary-btn:hover,
.icon-btn:hover {
  border-color: var(--muted);
}

.ghost-btn.danger {
  color: var(--red);
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
}

.primary-btn:disabled,
.ghost-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.plugins-empty {
  padding: 48px 18px;
  border: 1px dashed var(--border-strong);
  border-radius: 14px;
  color: var(--muted);
  text-align: center;
}

.plugins-empty--error,
.inline-error {
  border-color: color-mix(in srgb, var(--red) 55%, var(--border));
  color: var(--red);
}

.inline-error {
  border: 1px solid;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12px;
  background: color-mix(in srgb, var(--red) 8%, transparent);
}

.credential-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--muted);
  background: var(--surface-soft);
  font-size: 12px;
  word-break: break-all;
}

.tools-empty {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.tool-list {
  display: grid;
  gap: 12px;
}

.capability-section {
  display: grid;
  gap: 10px;
}

.capability-section h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}

.tool-item {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
}

.tool-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.tool-item p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.tools-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: color-mix(in srgb, var(--bg) 40%, rgb(15 23 42 / 0.45));
}

.tools-modal {
  width: min(860px, 100%);
  max-height: calc(100vh - 48px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-panel);
}

.integration-modal {
  width: min(760px, 100%);
}

.managed-tools-dialog {
  width: min(560px, 100%);
}

.tools-modal-head,
.tools-modal-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border);
}

.tools-modal-head h3 {
  margin: 0 0 8px;
  font-size: 22px;
}

.tools-modal-head p {
  margin: 0;
  color: var(--muted);
}

.tools-modal-foot {
  border-top: 1px solid var(--border);
  border-bottom: 0;
  justify-content: flex-end;
}

.icon-btn {
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.modal-body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  gap: 20px;
  padding: 24px;
}

.modal-body-scroll {
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
}

.settings-list,
.json-import-panel,
.field-block {
  display: grid;
  gap: 12px;
}


.field-block label,
.setting-info label {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}

.field-block input,
.field-block select,
.field-block textarea,
.setting-control input,
.setting-control select {
  width: 100%;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text);
  background: var(--surface-soft);
  transition: all 0.2s ease;
}

.field-block input:focus,
.field-block select:focus,
.setting-control input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
  outline: none;
}

.field-block textarea,
.json-import-panel textarea {
  min-height: 120px;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}


.mcp-json-textarea {
  min-height: 280px;
  width: 100%;
  padding: 18px !important;
  background-color: #0d1117 !important;
  color: #c9d1d9 !important;
  border: 1px solid var(--border-strong) !important;
  border-radius: 12px !important;
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace !important;
  font-size: 13px !important;
  line-height: 1.6 !important;
  box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.15) !important;
  transition: all 0.2s ease !important;
}

.mcp-json-textarea:focus {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent) !important;
  outline: none;
}

.json-import-panel label {
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.json-import-panel label::before {
  content: '\f1c9';
  font-family: "Font Awesome 6 Free";
  font-weight: 900;
  color: var(--text);
  opacity: 0.7;
}

.field-hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--surface-soft);
  border-radius: 8px;
  border: 1px solid var(--border);
}

.field-hint::before {
  content: '\f0eb';
  font-family: "Font Awesome 6 Free";
  font-weight: 900;
  color: var(--accent);
}

.settings-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px 20px;
  align-items: start;
}

.settings-list > .field-block,
.settings-list > .check-row {
  grid-column: 1 / -1;
}

.setting-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}

.setting-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.setting-info label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.setting-info p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.setting-control {
  display: grid;
  gap: 10px;
}

.check-row {
  display: flex;
  align-items: center;
  margin-top: 8px;
}

.check-label {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  user-select: none;
}

.check-label input[type="checkbox"] {
  appearance: none !important;
  -webkit-appearance: none !important;
  width: 40px !important;
  height: 22px !important;
  background-color: var(--border-strong) !important;
  border-radius: 22px !important;
  position: relative !important;
  cursor: pointer !important;
  outline: none !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  margin: 0 !important;
  border: none !important;
}

.check-label input[type="checkbox"]::after {
  content: '' !important;
  position: absolute !important;
  top: 2px !important;
  left: 2px !important;
  width: 18px !important;
  height: 18px !important;
  background-color: white !important;
  border-radius: 50% !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2) !important;
  background-image: none !important;
}

.check-label input[type="checkbox"]:checked {
  background-color: var(--accent) !important;
  background-image: none !important;
}

.check-label input[type="checkbox"]:checked::after {
  transform: translateX(18px) !important;
}

@media (max-width: 900px) {
  .setting-item {
    grid-template-columns: 1fr;
  }

  .pane-header {
    flex-direction: column;
    align-items: stretch;
  }
}

.meta-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  background: var(--surface-muted);
  padding: 2px 8px;
  border-radius: 6px;
}

.meta-chip.connected {
  color: var(--green);
}

.truncate {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  max-width: 100%;
}

.integration-meta {
  display: grid;
  gap: 6px;
}
</style>
