<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { NModal, NDataTable, useDialog } from "naive-ui";

import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type {
  DockerContainer,
  DockerOverviewResponse,
  DockerTemplate,
} from "../../../types";
import type { SettingsPluginDefinition } from "../plugins";

defineProps<{
  plugin?: SettingsPluginDefinition;
}>();

type TabKey = "overview" | "images" | "containers" | "templates";
type MessageTone = "success" | "error" | "info";

const loading = ref(false);
const overview = ref<DockerOverviewResponse | null>(null);
const activeTab = ref<TabKey>("overview");
const busyAction = ref("");
const messageVisible = ref(false);
const messageText = ref("");
const messageTone = ref<MessageTone>("info");
let messageTimer: ReturnType<typeof setTimeout> | null = null;

const logsOpen = ref(false);
const logsTarget = ref<DockerContainer | null>(null);
const logsContent = ref("");
const logsLoading = ref(false);

const portsOpen = ref(false);
const portsTarget = ref<DockerContainer | null>(null);
const portsRows = ref<{ host: string; hostPort: string; containerPort: string; protocol: string }[]>([]);

const tabs = computed<{ key: TabKey; label: string }[]>(() => [
  { key: "overview", label: t("docker.tab_overview") },
  { key: "images", label: t("docker.tab_images") },
  { key: "containers", label: t("docker.tab_containers") },
  { key: "templates", label: t("docker.tab_templates") },
]);

const dialog = useDialog();

const portsColumns = computed(() => [
  { title: t("docker.col_host"), key: "host", width: 140 },
  { title: t("docker.col_host_port"), key: "hostPort", width: 100 },
  { title: t("docker.col_container_port"), key: "containerPort", width: 120 },
  { title: t("docker.col_protocol"), key: "protocol", width: 80 },
]);

const environment = computed(() => overview.value?.environment);
const summary = computed(() => overview.value?.summary);
const requiredImages = computed(() => overview.value?.required_images ?? []);
const images = computed(() => overview.value?.images ?? []);
const containers = computed(() => overview.value?.containers ?? []);
const templates = computed(() => overview.value?.templates ?? []);

const categoryTranslationKeys: Record<string, string> = {
  infrastructure: "docker.category_infrastructure",
  security: "docker.category_security",
  performance: "docker.category_performance",
};

const purposeTranslationKeys: Record<string, string> = {
  redis: "docker.purpose_redis",
  rustfs: "docker.purpose_rustfs",
  mysql: "docker.purpose_mysql",
  postgres: "docker.purpose_postgres",
  memgraph: "docker.purpose_memgraph",
  security_runner: "docker.purpose_security_runner",
  perf_k6_default: "docker.purpose_k6",
  perf_jmeter_default: "docker.purpose_jmeter",
};

function categoryLabel(category: string) {
  const key = categoryTranslationKeys[category.toLowerCase()];
  return key ? t(key) : category;
}

function purposeLabel(key: string, fallback: string) {
  const translationKey = purposeTranslationKeys[key];
  return translationKey ? t(translationKey) : fallback;
}

const daemonReady = computed(() => environment.value?.daemon_available ?? false);
const cliReady = computed(() => environment.value?.cli_available ?? false);

onMounted(() => {
  void loadOverview();
});

async function loadOverview() {
  loading.value = true;
  try {
    overview.value = await api.dockerOverview();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : t("docker.load_failed"));
  } finally {
    loading.value = false;
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
  }, 3200);
}

function actionKey(...parts: string[]) {
  return parts.join(":");
}

async function withAction(key: string, runner: () => Promise<void>) {
  busyAction.value = key;
  try {
    await runner();
  } catch (err) {
    showMessage("error", err instanceof Error ? err.message : t("docker.action_failed"));
  } finally {
    busyAction.value = "";
  }
}

async function pullImage(image: string) {
  await withAction(actionKey("pull", image), async () => {
    const res = await api.dockerPullImage({ image });
    showMessage("success", res.message || t("docker.pull_success", { image }));
    await loadOverview();
  });
}

async function removeImage(image: string) {
  dialog.info({
    title: t("docker.remove_image"),
    content: t("docker.confirm_remove_image", { image }),
    positiveText: t("common.confirm"),
    negativeText: t("common.cancel"),
    positiveButtonProps: { type: "primary" },
    onPositiveClick: () => {
      void withAction(actionKey("remove-image", image), async () => {
        await api.dockerRemoveImage({ image });
        showMessage("success", t("docker.remove_image_success", { image }));
        await loadOverview();
      });
    },
  });
}

async function createFromTemplate(template: DockerTemplate) {
  await withAction(actionKey("create-template", template.key), async () => {
    const res = await api.dockerCreateTemplateContainer(template.key, { pull_if_missing: true });
    showMessage(
      "success",
      res.message || t("docker.create_container_success", { name: res.name || res.container_id }),
    );
    await loadOverview();
    activeTab.value = "containers";
  });
}

async function containerAction(container: DockerContainer, action: "start" | "stop" | "restart" | "pause" | "unpause") {
  await withAction(actionKey("container-action", container.id, action), async () => {
    await api.dockerContainerAction(container.id, { action });
    showMessage("success", t("docker.container_action_success", { action, name: container.name || container.id }));
    await loadOverview();
  });
}

async function removeContainer(container: DockerContainer, force = false) {
  dialog.info({
    title: t("docker.remove_container"),
    content: t("docker.confirm_remove_container", { name: container.name || container.id }),
    positiveText: t("common.confirm"),
    negativeText: t("common.cancel"),
    positiveButtonProps: { type: "primary" },
    onPositiveClick: () => {
      void withAction(actionKey("remove-container", container.id), async () => {
        await api.dockerRemoveContainer(container.id, { force });
        showMessage("success", t("docker.remove_container_success", { name: container.name || container.id }));
        await loadOverview();
      });
    },
  });
}

async function openLogs(container: DockerContainer) {
  logsTarget.value = container;
  logsContent.value = "";
  logsOpen.value = true;
  logsLoading.value = true;
  try {
    const res = await api.dockerContainerLogs(container.id, 200);
    logsContent.value = res.logs || t("docker.no_logs");
  } catch (err) {
    logsContent.value = err instanceof Error ? err.message : t("docker.load_logs_failed");
  } finally {
    logsLoading.value = false;
  }
}

function openPorts(container: DockerContainer) {
  if (!container.ports) return;
  portsTarget.value = container;
  portsRows.value = parsePorts(container.ports);
  portsOpen.value = true;
}

function parsePorts(raw: string) {
  const rows: { host: string; hostPort: string; containerPort: string; protocol: string }[] = [];
  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  for (const part of parts) {
    // Format: host:port->containerPort/protocol  or  port/protocol
    const m = /^(.+?):(\d+)->(\d+)\/(\w+)$/.exec(part);
    if (m) {
      rows.push({ host: m[1], hostPort: m[2], containerPort: m[3], protocol: m[4].toUpperCase() });
    } else {
      const m2 = /^(\d+)\/(\w+)$/.exec(part);
      if (m2) {
        rows.push({ host: "-", hostPort: "-", containerPort: m2[1], protocol: m2[2].toUpperCase() });
      }
    }
  }
  return rows;
}

function formatBytes(text: string) {
  return text || "-";
}

function stateClass(state: string) {
  if (state === "running") return "is-running";
  if (["exited", "dead"].includes(state)) return "is-stopped";
  return "is-pending";
}
</script>

<template>
  <section class="settings-pane docker-management">
    <transition name="settings-toast">
      <div
        v-if="messageVisible"
        class="settings-message"
        :class="{
          'is-success': messageTone === 'success',
          'is-error': messageTone === 'error',
          'is-info': messageTone === 'info',
        }"
      >
        {{ messageText }}
      </div>
    </transition>

    <div class="settings-pane-head">
      <div>
        <h3>{{ t("docker.title") }}</h3>
        <p>{{ t("docker.desc") }}</p>
      </div>
      <button
        type="button"
        class="secondary-btn narrow"
        :disabled="loading"
        @click="loadOverview"
      >
        <i class="fa-solid" :class="loading ? 'fa-spinner fa-spin' : 'fa-rotate-right'"></i>
        {{ t("docker.refresh") }}
      </button>
    </div>

    <div class="settings-pane-block">
      <div class="docker-status-bar">
        <div class="docker-status-item">
          <span class="docker-status-label">{{ t("docker.status_cli") }}</span>
          <span class="docker-status-value" :class="cliReady ? 'is-ok' : 'is-bad'">
            {{ cliReady ? t("docker.status_ready") : t("docker.status_missing") }}
          </span>
        </div>
        <div class="docker-status-item">
          <span class="docker-status-label">{{ t("docker.status_daemon") }}</span>
          <span class="docker-status-value" :class="daemonReady ? 'is-ok' : 'is-bad'">
            {{ daemonReady ? t("docker.status_ready") : t("docker.status_unavailable") }}
          </span>
        </div>
        <div v-if="environment?.client_version" class="docker-status-item">
          <span class="docker-status-label">{{ t("docker.client_version") }}</span>
          <span class="docker-status-value">{{ environment.client_version }}</span>
        </div>
        <div v-if="environment?.server_version" class="docker-status-item">
          <span class="docker-status-label">{{ t("docker.server_version") }}</span>
          <span class="docker-status-value">{{ environment.server_version }}</span>
        </div>
        <div v-if="environment?.error" class="docker-status-item docker-status-error">
          <span class="docker-status-label">{{ t("docker.status_error") }}</span>
          <span class="docker-status-value is-bad">{{ environment.error }}</span>
        </div>
      </div>
    </div>

    <div v-if="daemonReady && summary" class="settings-pane-block docker-summary-grid">
      <div class="stat-card">
        <div>
          <div class="stat-label">{{ t("docker.summary_required") }}</div>
          <div class="stat-value">{{ summary.required_installed }} / {{ summary.required_total }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">{{ t("docker.summary_images") }}</div>
          <div class="stat-value">{{ summary.image_count }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">{{ t("docker.summary_containers") }}</div>
          <div class="stat-value">{{ summary.container_count }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">{{ t("docker.summary_running") }}</div>
          <div class="stat-value">{{ summary.running_count }}</div>
        </div>
      </div>
    </div>

    <div class="settings-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Overview -->
    <div v-if="activeTab === 'overview'" class="settings-pane-block">
      <h4 class="docker-section-title">{{ t("docker.required_images") }}</h4>
      <div v-if="!daemonReady" class="settings-empty">
        {{ environment?.error || t("docker.daemon_not_ready") }}
      </div>
      <div v-else-if="requiredImages.length === 0" class="settings-empty">
        {{ t("docker.no_required_images") }}
      </div>
      <div v-else class="docker-required-grid">
        <div
          v-for="item in requiredImages"
          :key="item.key"
          class="docker-required-card"
          :class="item.installed ? 'is-installed' : 'is-missing'"
        >
          <div class="docker-required-card__head">
            <span class="docker-required-card__category">{{ item.category }}</span>
            <span class="docker-required-card__badge" :class="item.installed ? 'is-ok' : 'is-warn'">
              {{ item.installed ? t("docker.installed") : t("docker.not_installed") }}
            </span>
          </div>
          <strong class="docker-required-card__image">{{ item.image }}</strong>
          <p class="docker-required-card__purpose">{{ purposeLabel(item.key, item.purpose) }}</p>
          <div class="docker-required-card__meta">
            <span>{{ t("docker.containers_count") }}: {{ item.container_count }}</span>
            <span v-if="item.size">{{ item.size }}</span>
          </div>
          <div class="docker-required-card__actions">
            <button
              v-if="!item.installed"
              type="button"
              class="primary-btn narrow"
              :disabled="busyAction === actionKey('pull', item.image)"
              @click="pullImage(item.image)"
            >
              <i class="fa-solid" :class="busyAction === actionKey('pull', item.image) ? 'fa-spinner fa-spin' : 'fa-download'"></i>
              {{ t("docker.pull") }}
            </button>
            <button
              v-if="item.template_key"
              type="button"
              class="secondary-btn narrow"
              :disabled="busyAction === actionKey('create-template', item.template_key) || !templates.find((t) => t.key === item.template_key)"
              @click="createFromTemplate(templates.find((t) => t.key === item.template_key) as DockerTemplate)"
            >
              <i class="fa-solid" :class="busyAction === actionKey('create-template', item.template_key) ? 'fa-spinner fa-spin' : 'fa-plus'"></i>
              {{ t("docker.create_container") }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Images -->
    <div v-if="activeTab === 'images'" class="settings-pane-block">
      <div class="table-shell">
        <table class="prototype-table">
          <thead>
            <tr>
              <th>{{ t("docker.col_repository") }}</th>
              <th>{{ t("docker.col_tag") }}</th>
              <th>{{ t("docker.col_image_id") }}</th>
              <th>{{ t("docker.col_size") }}</th>
              <th>{{ t("docker.col_created") }}</th>
              <th class="align-right">{{ t("docker.col_actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="image in images" :key="image.id">
              <td>{{ image.repository || "<none>" }}</td>
              <td>{{ image.tag || "<none>" }}</td>
              <td class="mono">{{ image.id.slice(0, 16) }}</td>
              <td>{{ formatBytes(image.size) }}</td>
              <td>{{ image.created_at }}</td>
              <td class="align-right">
                <button
                  type="button"
                  class="icon-btn docker-action-btn docker-action-btn--danger"
                  :disabled="busyAction === actionKey('remove-image', image.reference)"
                  :title="t('docker.remove_image')"
                  @click="removeImage(image.reference)"
                >
                  <i
                    class="fa-solid"
                    :class="busyAction === actionKey('remove-image', image.reference) ? 'fa-spinner fa-spin' : 'fa-trash-can'"
                  ></i>
                </button>
              </td>
            </tr>
            <tr v-if="images.length === 0">
              <td colspan="6" class="docker-table-empty">{{ t("docker.no_images") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Containers -->
    <div v-if="activeTab === 'containers'" class="settings-pane-block">
      <div class="table-shell">
        <table class="prototype-table">
          <thead>
            <tr>
              <th>{{ t("docker.col_name") }}</th>
              <th>{{ t("docker.col_image") }}</th>
              <th class="docker-col-state">{{ t("docker.col_state") }}</th>
              <th class="docker-col-status">{{ t("docker.col_status") }}</th>
              <th class="docker-col-ports">{{ t("docker.col_ports") }}</th>
              <th class="align-right">{{ t("docker.col_actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="container in containers" :key="container.id">
              <td>
                <span class="mono">{{ container.name || container.id.slice(0, 12) }}</span>
                <span v-if="container.managed" class="docker-managed-badge">{{ t("docker.managed") }}</span>
              </td>
              <td>{{ container.image }}</td>
              <td class="docker-col-state">
                <span class="docker-state-dot" :class="stateClass(container.state)"></span>
                {{ container.state }}
              </td>
              <td class="docker-col-status">{{ container.status }}</td>
              <td
                class="docker-col-ports mono"
                :class="{ 'is-clickable': container.ports }"
                :title="container.ports || undefined"
                @click="container.ports && openPorts(container)"
              >
                <span>{{ container.ports || "-" }}</span>
                <i v-if="container.ports" class="fa-solid fa-expand docker-col-ports__icon"></i>
              </td>
              <td class="align-right">
                <div class="docker-row-actions">
                  <button
                    v-if="container.state !== 'running'"
                    type="button"
                    class="icon-btn docker-action-btn"
                    :disabled="busyAction === actionKey('container-action', container.id, 'start')"
                    :title="t('docker.start')"
                    @click="containerAction(container, 'start')"
                  >
                    <i class="fa-solid fa-play"></i>
                  </button>
                  <button
                    v-if="container.state === 'running'"
                    type="button"
                    class="icon-btn docker-action-btn"
                    :disabled="busyAction === actionKey('container-action', container.id, 'stop')"
                    :title="t('docker.stop')"
                    @click="containerAction(container, 'stop')"
                  >
                    <i class="fa-solid fa-stop"></i>
                  </button>
                  <button
                    type="button"
                    class="icon-btn docker-action-btn"
                    :disabled="busyAction === actionKey('container-action', container.id, 'restart')"
                    :title="t('docker.restart')"
                    @click="containerAction(container, 'restart')"
                  >
                    <i class="fa-solid fa-rotate-right"></i>
                  </button>
                  <button
                    type="button"
                    class="icon-btn docker-action-btn"
                    :disabled="logsLoading && logsTarget?.id === container.id"
                    :title="t('docker.logs')"
                    @click="openLogs(container)"
                  >
                    <i class="fa-solid fa-file-lines"></i>
                  </button>
                  <button
                    type="button"
                    class="icon-btn docker-action-btn docker-action-btn--danger"
                    :disabled="busyAction === actionKey('remove-container', container.id)"
                    :title="t('docker.remove_container')"
                    @click="removeContainer(container)"
                  >
                    <i
                      class="fa-solid"
                      :class="busyAction === actionKey('remove-container', container.id) ? 'fa-spinner fa-spin' : 'fa-trash-can'"
                    ></i>
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="containers.length === 0">
              <td colspan="6" class="docker-table-empty">{{ t("docker.no_containers") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Templates -->
    <div v-if="activeTab === 'templates'" class="settings-pane-block">
      <div class="docker-templates-grid">
        <div v-for="template in templates" :key="template.key" class="docker-template-card">
          <div class="docker-template-card__head">
            <span class="docker-template-card__category">{{ categoryLabel(template.category) }}</span>
          </div>
          <strong class="docker-template-card__title">{{ template.default_name }}</strong>
          <p class="docker-template-card__image mono">{{ template.image }}</p>
          <p class="docker-template-card__purpose">{{ purposeLabel(template.key, template.purpose) }}</p>
          <div class="docker-template-card__meta">
            <div v-if="template.ports.length">
              <i class="fa-solid fa-network-wired"></i>
              {{ template.ports.map((p) => `${p.host_port}:${p.container_port}`).join(", ") }}
            </div>
            <div v-if="template.volumes.length">
              <i class="fa-solid fa-hard-drive"></i>
              {{ template.volumes.map((v) => v.target).join(", ") }}
            </div>
            <div v-if="template.environment_keys.length">
              <i class="fa-solid fa-key"></i>
              {{ template.environment_keys.join(", ") }}
            </div>
          </div>
          <div class="docker-template-card__actions">
            <button
              type="button"
              class="primary-btn narrow"
              :disabled="busyAction === actionKey('create-template', template.key)"
              @click="createFromTemplate(template)"
            >
              <i class="fa-solid" :class="busyAction === actionKey('create-template', template.key) ? 'fa-spinner fa-spin' : 'fa-plus'"></i>
              {{ t("docker.one_click_create") }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Logs modal -->
    <n-modal
      v-model:show="logsOpen"
      preset="card"
      :title="logsTarget ? t('docker.logs_title', { name: logsTarget.name || logsTarget.id.slice(0, 12) }) : t('docker.logs')"
      style="width: min(900px, 92vw); max-height: 80vh;"
      :bordered="false"
    >
      <div class="docker-logs-shell">
        <pre v-if="logsLoading" class="docker-logs-content">{{ t("docker.loading_logs") }}</pre>
        <pre v-else class="docker-logs-content">{{ logsContent }}</pre>
      </div>
    </n-modal>

    <!-- Ports modal -->
    <n-modal
      v-model:show="portsOpen"
      preset="card"
      :title="portsTarget ? t('docker.ports_title', { name: portsTarget.name || portsTarget.id.slice(0, 12) }) : t('docker.col_ports')"
      style="width: min(720px, 92vw);"
      :bordered="false"
    >
      <n-data-table
        :columns="portsColumns"
        :data="portsRows"
        :bordered="true"
        :single-line="false"
        :max-height="420"
        size="small"
      />
    </n-modal>
  </section>
</template>

<style scoped>
.docker-management {
  position: relative;
}

.docker-status-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 32px;
  padding: 14px 18px;
  border: 1px dashed var(--border);
  border-radius: 12px;
  background: var(--surface-soft);
}

.docker-status-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.docker-status-label {
  color: var(--muted);
}

.docker-status-value {
  font-weight: 600;
}

.docker-status-value.is-ok {
  color: var(--green);
}

.docker-status-value.is-bad {
  color: var(--red);
}

.docker-status-error {
  width: 100%;
}

.docker-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.docker-section-title {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 600;
}

.docker-required-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.docker-required-card,
.docker-template-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.docker-required-card__head,
.docker-template-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.docker-required-card__category,
.docker-template-card__category {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: var(--muted);
}

.docker-required-card__badge {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 999px;
}

.docker-required-card__badge.is-ok {
  background: #dcfce7;
  color: #166534;
}

.docker-required-card__badge.is-warn {
  background: #fee2e2;
  color: #991b1b;
}

.docker-required-card__image,
.docker-template-card__title {
  font-size: 15px;
  word-break: break-all;
}

.docker-required-card__purpose,
.docker-template-card__purpose {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.6;
}

.docker-required-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
}

.docker-required-card__actions,
.docker-template-card__actions {
  margin-top: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.docker-templates-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.docker-template-card__image {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.docker-template-card__meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.docker-template-card__meta i {
  width: 16px;
}

.docker-table-empty {
  text-align: center;
  color: var(--muted);
  padding: 28px;
}

.docker-row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.docker-action-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}

.docker-action-btn:hover:not(:disabled) {
  background: var(--surface-soft);
  border-color: var(--border-strong);
}

.docker-action-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.docker-action-btn--danger {
  color: var(--red);
  border-color: #fecaca;
}

.docker-action-btn--danger:hover:not(:disabled) {
  background: #fef2f2;
}

.docker-col-state {
  width: 90px;
  white-space: nowrap;
}

.docker-col-status {
  min-width: 120px;
  max-width: 220px;
  white-space: normal;
  word-break: break-word;
}

.docker-col-ports {
  width: 160px;
  max-width: 160px;
  max-height: 48px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.5;
  white-space: normal;
  position: relative;
}

.docker-col-ports.is-clickable {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.docker-col-ports.is-clickable:hover {
  background: var(--surface-soft);
}

.docker-col-ports__icon {
  position: absolute;
  right: 6px;
  bottom: 4px;
  font-size: 10px;
  color: var(--muted);
  opacity: 0.7;
  pointer-events: none;
}

.docker-state-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  margin-right: 6px;
}

.docker-state-dot.is-running {
  background: var(--green);
}

.docker-state-dot.is-stopped {
  background: var(--red);
}

.docker-state-dot.is-pending {
  background: var(--muted);
}

.docker-managed-badge {
  display: inline-block;
  margin-left: 8px;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--muted);
}

.docker-logs-shell {
  max-height: 60vh;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-soft);
}

.docker-logs-content {
  margin: 0;
  padding: 14px;
  font-family: "JetBrains Mono", Menlo, Monaco, Consolas, "Courier New", monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
}

.settings-message.is-info {
  background: #eff6ff;
  border-color: #bfdbfe;
  color: #1e40af;
}
</style>
