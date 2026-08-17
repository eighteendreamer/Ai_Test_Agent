<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useMessage, NSelect } from "naive-ui";

import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type {
  ApiDocRecord,
  IntegrationImportSourceDescriptor,
  IntegrationImportSourcesResponse,
  IntegrationRecord,
  ProjectRecord,
} from "../../../types";
import { formatServerDateTime } from "../../../utils/datetime";

type ImportMode = "local" | "url" | "integration";

const toast = useMessage();

const docs = ref<ApiDocRecord[]>([]);
const integrations = ref<IntegrationRecord[]>([]);
const projects = ref<ProjectRecord[]>([]);
const loading = ref(false);
const error = ref("");
const selectedDoc = ref<ApiDocRecord | null>(null);
const previewOpen = ref(false);
const previewLoading = ref(false);
const savingMetadata = ref(false);
const deletingId = ref("");
const uploadOpen = ref(false);
const uploadLoading = ref(false);
const uploadMode = ref<ImportMode>("local");

const uploadFile = ref<File | null>(null);
const uploadTitle = ref("");
const uploadProjectId = ref<string | null>(null);
const uploadProjectUrl = ref("");
const remoteUrl = ref("");
const selectedIntegrationId = ref("");
const integrationDocumentUrl = ref("");
const selectedWorkspaceId = ref("");
const selectedImportSourceId = ref("");
const integrationImportCatalog = ref<IntegrationImportSourcesResponse | null>(null);
const integrationImportLoading = ref(false);

const editTitle = ref("");
const editProjectId = ref<string | null>(null);
const editProjectUrl = ref("");

const hasDocs = computed(() => docs.value.length > 0);
const importableIntegrations = computed(() =>
  integrations.value.filter((item) => item.enabled && (item.kind === "api" || item.kind === "mcp")),
);
const selectedIntegration = computed(
  () => importableIntegrations.value.find((item) => item.id === selectedIntegrationId.value) ?? null,
);
const selectedIntegrationIsMcp = computed(() => selectedIntegration.value?.kind === "mcp");
const workspaceOptions = computed(() => integrationImportCatalog.value?.workspaces ?? []);
const integrationImportSources = computed(() => integrationImportCatalog.value?.sources ?? []);
const filteredImportSources = computed(() => {
  if (!integrationImportCatalog.value) {
    return [];
  }
  if (!integrationImportCatalog.value.supports_workspace_selection) {
    return integrationImportCatalog.value.sources;
  }
  if (!selectedWorkspaceId.value) {
    return [];
  }
  return integrationImportCatalog.value.sources.filter((source) => source.workspace_id === selectedWorkspaceId.value);
});
const selectedImportSource = computed<IntegrationImportSourceDescriptor | null>(
  () => filteredImportSources.value.find((item) => item.id === selectedImportSourceId.value) ?? null,
);
const integrationImportHint = computed(() => {
  if (!selectedIntegration.value) {
    return "";
  }
  if (integrationImportLoading.value) {
    return t("apiDocs.hint_loading_sources");
  }
  if (!integrationImportCatalog.value) {
    return "";
  }
  if (integrationImportCatalog.value.supports_workspace_selection && !workspaceOptions.value.length) {
    return t("apiDocs.hint_no_workspaces");
  }
  if (integrationImportCatalog.value.supports_workspace_selection && workspaceOptions.value.length && !selectedWorkspaceId.value) {
    return t("apiDocs.hint_select_workspace");
  }
  if (!filteredImportSources.value.length) {
    return selectedIntegration.value.kind === "mcp"
      ? t("apiDocs.hint_no_mcp_sources")
      : t("apiDocs.hint_no_api_sources");
  }
  return "";
});
const canSubmitIntegrationImport = computed(() => {
  if (uploadMode.value !== "integration") {
    return true;
  }
  if (!selectedIntegrationId.value || integrationImportLoading.value) {
    return false;
  }
  if (!integrationImportCatalog.value) {
    return false;
  }
  if (integrationImportCatalog.value.supports_workspace_selection) {
    return Boolean(selectedWorkspaceId.value && selectedImportSourceId.value);
  }
  return Boolean(selectedImportSourceId.value || integrationDocumentUrl.value.trim() || selectedIntegration.value?.document_url);
});
const projectOptions = computed(() => projects.value.map(project => ({
  label: project.status === "archived" ? `${project.name}（已归档）` : project.name,
  value: project.id,
  disabled: project.status === "archived",
})));

function projectName(projectId?: string | null, legacyName?: string | null) {
  return projects.value.find(project => project.id === projectId)?.name || legacyName || t("apiDocs.no_project");
}

function formatBytes(value: number) {
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${Math.round(value / 1024)} KB`;
  }
  return `${value} B`;
}

function resetUploadForm() {
  uploadMode.value = "local";
  uploadFile.value = null;
  uploadTitle.value = "";
  uploadProjectId.value = null;
  uploadProjectUrl.value = "";
  remoteUrl.value = "";
  selectedIntegrationId.value = "";
  integrationDocumentUrl.value = "";
  selectedWorkspaceId.value = "";
  selectedImportSourceId.value = "";
  integrationImportCatalog.value = null;
}

function resetPreviewForm() {
  editTitle.value = "";
  editProjectId.value = null;
  editProjectUrl.value = "";
}

function syncPreviewForm(doc: ApiDocRecord | null) {
  editTitle.value = doc?.title || "";
  editProjectId.value = doc?.project_id || null;
  editProjectUrl.value = doc?.project_url || "";
}

function upsertDoc(doc: ApiDocRecord) {
  const index = docs.value.findIndex((item) => item.id === doc.id);
  if (index >= 0) {
    const next = [...docs.value];
    next[index] = doc;
    docs.value = next;
    return;
  }
  docs.value = [doc, ...docs.value];
}

function handleOpenUpload() {
  uploadOpen.value = true;
  resetUploadForm();
}

function handleExternalOpenUpload() {
  handleOpenUpload();
}

function closeUpload() {
  uploadOpen.value = false;
  resetUploadForm();
}

function closePreview() {
  previewOpen.value = false;
  selectedDoc.value = null;
  resetPreviewForm();
}

function onUploadFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  uploadFile.value = input.files?.[0] ?? null;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",")[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error(t("apiDocs.read_file_failed")));
    reader.readAsDataURL(file);
  });
}

async function loadDocs() {
  loading.value = true;
  error.value = "";
  try {
    docs.value = await api.listApiDocs();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("apiDocs.load_docs_failed");
  } finally {
    loading.value = false;
  }
}

async function loadIntegrations() {
  try {
    integrations.value = await api.listIntegrations();
  } catch {
    integrations.value = [];
  }
}

async function loadProjects() {
  try {
    projects.value = (await api.listProjects({ limit: 200 })).items;
  } catch {
    projects.value = [];
  }
}

async function loadIntegrationImportCatalog(integrationId: string, workspaceId?: string | null) {
  integrationImportLoading.value = true;
  if (!workspaceId) {
    selectedWorkspaceId.value = "";
    selectedImportSourceId.value = "";
    integrationImportCatalog.value = null;
  } else {
    selectedImportSourceId.value = "";
  }
  try {
    integrationImportCatalog.value = await api.listIntegrationImportSources(integrationId, workspaceId ?? null);
    if (
      !workspaceId &&
      integrationImportCatalog.value.supports_workspace_selection &&
      integrationImportCatalog.value.workspaces.length === 1
    ) {
      selectedWorkspaceId.value = integrationImportCatalog.value.workspaces[0].id;
    }
    if (
      !integrationImportCatalog.value.supports_workspace_selection &&
      integrationImportCatalog.value.sources.length === 1
    ) {
      selectedImportSourceId.value = integrationImportCatalog.value.sources[0].id;
    }
  } catch (err) {
    integrationImportCatalog.value = null;
    toast.error(err instanceof Error ? err.message : t("apiDocs.load_import_sources_failed"));
  } finally {
    integrationImportLoading.value = false;
  }
}

async function submitUpload() {
  uploadLoading.value = true;
  try {
    let created: ApiDocRecord;
    if (uploadMode.value === "local") {
      if (!uploadFile.value) {
        toast.error(t("apiDocs.select_local_file"));
        return;
      }
      const contentBase64 = await fileToBase64(uploadFile.value);
      created = await api.uploadApiDoc({
        filename: uploadFile.value.name,
        content_base64: contentBase64,
        source: "tools_api_docs_local",
        title: uploadTitle.value.trim() || null,
        project_id: uploadProjectId.value,
        project_url: uploadProjectUrl.value.trim() || null,
      });
    } else if (uploadMode.value === "url") {
      if (!remoteUrl.value.trim()) {
        toast.error(t("apiDocs.enter_doc_url"));
        return;
      }
      created = await api.importApiDocFromUrl({
        url: remoteUrl.value.trim(),
        title: uploadTitle.value.trim() || null,
        project_id: uploadProjectId.value,
        project_url: uploadProjectUrl.value.trim() || null,
        source: "tools_api_docs_url",
      });
    } else {
      if (!selectedIntegrationId.value) {
        toast.error(t("apiDocs.select_integration"));
        return;
      }
      if (!canSubmitIntegrationImport.value) {
        toast.error(integrationImportHint.value || t("apiDocs.integration_not_ready"));
        return;
      }
      created = await api.importApiDocFromIntegration({
        integration_id: selectedIntegrationId.value,
        title: uploadTitle.value.trim() || null,
        project_id: uploadProjectId.value,
        project_url: uploadProjectUrl.value.trim() || null,
        document_url: integrationDocumentUrl.value.trim() || null,
        workspace_id: selectedWorkspaceId.value || null,
        import_source_id: selectedImportSourceId.value || null,
        source: "tools_api_docs_integration",
      });
    }

    upsertDoc(created);
    await loadDocs();
    toast.success(t("apiDocs.imported_success"), { duration: 2200 });
    setTimeout(() => closeUpload(), 280);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : t("apiDocs.import_failed"));
  } finally {
    uploadLoading.value = false;
  }
}

async function openPreview(docId: string) {
  previewOpen.value = true;
  previewLoading.value = true;
  error.value = "";
  try {
    selectedDoc.value = await api.getApiDoc(docId);
    syncPreviewForm(selectedDoc.value);
  } catch (err) {
    const detail = err instanceof Error ? err.message : t("apiDocs.load_detail_failed");
    error.value = detail;
    selectedDoc.value = null;
    resetPreviewForm();
    toast.error(detail);
  } finally {
    previewLoading.value = false;
  }
}

async function saveDocMetadata() {
  if (!selectedDoc.value || savingMetadata.value) {
    return;
  }

  savingMetadata.value = true;
  error.value = "";
  try {
    const updated = await api.updateApiDoc(selectedDoc.value.id, {
      title: editTitle.value.trim() || null,
      project_id: editProjectId.value,
      project_url: editProjectUrl.value.trim() || null,
    });
    selectedDoc.value = updated;
    syncPreviewForm(updated);
    upsertDoc(updated);
    toast.success(t("apiDocs.doc_updated"), { duration: 2200 });
  } catch (err) {
    const detail = err instanceof Error ? err.message : t("apiDocs.update_failed");
    error.value = detail;
    toast.error(detail);
  } finally {
    savingMetadata.value = false;
  }
}

async function deleteDoc(doc: ApiDocRecord) {
  if (deletingId.value) {
    return;
  }

  deletingId.value = doc.id;
  error.value = "";
  try {
    await api.deleteApiDoc(doc.id);
    docs.value = docs.value.filter((item) => item.id !== doc.id);
    if (selectedDoc.value?.id === doc.id) {
      closePreview();
    }
    toast.success(`${t("apiDocs.deleted_doc")}: ${doc.title}`, { duration: 2200 });
  } catch (err) {
    const detail = err instanceof Error ? err.message : t("apiDocs.delete_failed");
    error.value = detail;
    toast.error(detail);
  } finally {
    deletingId.value = "";
  }
}

watch(selectedIntegrationId, async (integrationId) => {
  integrationDocumentUrl.value = "";
  selectedWorkspaceId.value = "";
  selectedImportSourceId.value = "";
  integrationImportCatalog.value = null;
  if (!integrationId || uploadMode.value !== "integration") {
    return;
  }
  await loadIntegrationImportCatalog(integrationId);
});

watch(selectedWorkspaceId, async (workspaceId) => {
  if (!workspaceId) {
    selectedImportSourceId.value = "";
    return;
  }
  if (!selectedIntegrationId.value || uploadMode.value !== "integration") {
    return;
  }
  await loadIntegrationImportCatalog(selectedIntegrationId.value, workspaceId);
  const candidates = filteredImportSources.value;
  if (candidates.length === 1) {
    selectedImportSourceId.value = candidates[0].id;
  }
});

watch(uploadMode, (mode) => {
  if (mode !== "integration") {
    selectedIntegrationId.value = "";
    integrationDocumentUrl.value = "";
    selectedWorkspaceId.value = "";
    selectedImportSourceId.value = "";
    integrationImportCatalog.value = null;
  }
});

onMounted(() => {
  void loadDocs();
  void loadIntegrations();
  void loadProjects();
  window.addEventListener("qa-agent:open-api-doc-upload", handleExternalOpenUpload);
});

onBeforeUnmount(() => {
  window.removeEventListener("qa-agent:open-api-doc-upload", handleExternalOpenUpload);
});
</script>

<template>
  <div class="tools-tab-pane">
    <div class="pane-header">
      <div>
        <h3 class="section-title">{{ t("apiDocs.title") }}</h3>
        <p class="head-desc">
          {{ t("apiDocs.desc") }}
        </p>
      </div>
    </div>

    <div v-if="loading && !hasDocs" class="api-doc-empty">
      <i class="fa-solid fa-spinner fa-spin"></i>
      <span>{{ t("apiDocs.loading") }}</span>
    </div>

    <div v-else-if="error && !hasDocs" class="api-doc-empty api-doc-empty--error">
      <i class="fa-solid fa-circle-exclamation"></i>
      <strong>{{ t("apiDocs.load_failed") }}</strong>
      <span>{{ error }}</span>
    </div>

    <div v-else-if="!hasDocs" class="api-doc-empty">
      <i class="fa-solid fa-file-circle-plus"></i>
      <strong>{{ t("apiDocs.no_docs") }}</strong>
      <span>{{ t("apiDocs.no_docs_hint") }}</span>
    </div>

    <div v-else class="api-docs-list">
      <article v-for="doc in docs" :key="doc.id" class="api-doc-card">
        <div class="api-doc-header-row">
          <div class="api-doc-icon" :class="{ postman: doc.format_label.includes('Postman') }">
            <i class="fa-solid" :class="doc.format_label.includes('Postman') ? 'fa-rocket' : 'fa-file-code'"></i>
          </div>
          <div class="api-doc-actions">
            <button class="icon-btn" title="{{ t('apiDocs.view_detail') }}" @click="openPreview(doc.id)">
              <i class="fa-regular fa-eye"></i>
            </button>
            <button
              class="icon-btn danger"
              :disabled="deletingId === doc.id"
              :title="t('apiDocs.delete_doc')"
              @click="deleteDoc(doc)"
            >
              <i class="fa-solid" :class="deletingId === doc.id ? 'fa-spinner fa-spin' : 'fa-trash-can'"></i>
            </button>
          </div>
        </div>

        <div class="api-doc-content">
          <div class="api-doc-head">
            <h4>{{ doc.title }}</h4>
            <span class="badge" :class="doc.format_label.includes('Postman') ? 'badge-orange' : 'badge-blue'">
              {{ doc.format_label }}
            </span>
          </div>
          <p>{{ doc.filename }}</p>

          <div class="api-doc-meta">
            <div class="meta-item">
              <i class="fa-solid fa-diagram-project"></i>
              <span>{{ projectName(doc.project_id, doc.legacy_project_name || doc.project_name) }}</span>
            </div>
            <div class="meta-item">
              <i class="fa-solid fa-globe"></i>
              <span>{{ doc.project_url || t("apiDocs.no_project_url") }}</span>
            </div>
            <div class="meta-item">
              <i class="fa-solid fa-database"></i>
              <span>MinIO · {{ formatBytes(doc.size_bytes) }}</span>
            </div>
            <div class="meta-item" v-if="doc.endpoint_count !== null && doc.endpoint_count !== undefined">
              <i class="fa-solid fa-link"></i>
              <span>{{ doc.endpoint_count }} {{ t("apiDocs.endpoints_unit") }}</span>
            </div>
            <div class="meta-item">
              <i class="fa-regular fa-clock"></i>
              <span>{{ formatServerDateTime(doc.updated_at) }}</span>
            </div>
          </div>
        </div>
      </article>
    </div>

    <div v-if="previewOpen" class="tools-modal-backdrop" @click.self="closePreview">
      <section class="tools-modal api-doc-preview-modal">
        <header class="tools-modal-head">
          <div>
            <h3>{{ selectedDoc?.title || t("apiDocs.preview_title") }}</h3>
            <p v-if="selectedDoc">{{ selectedDoc.filename }} · {{ selectedDoc.format_label }}</p>
          </div>
          <div class="tools-modal-head-actions">
            <button v-if="selectedDoc" class="primary-btn head-save-btn" :disabled="savingMetadata" @click="saveDocMetadata">
              {{ savingMetadata ? t("apiDocs.saving") : t("apiDocs.save_doc_info") }}
            </button>
            <button class="icon-btn" @click="closePreview"><i class="fa-solid fa-xmark"></i></button>
          </div>
        </header>

        <div class="tools-modal-body">
          <div v-if="previewLoading" class="api-doc-empty">
            <i class="fa-solid fa-spinner fa-spin"></i>
            <span>{{ t("apiDocs.loading_detail") }}</span>
          </div>
          <template v-else-if="selectedDoc">
            <div class="preview-form-grid">
              <label class="field-block">
                <span>{{ t("apiDocs.form_title") }}</span>
                <input v-model="editTitle" placeholder="e.g. User Center OpenAPI v2">
              </label>
              <label class="field-block">
                <span>{{ t("apiDocs.form_project") }}</span>
                <n-select
                  v-model:value="editProjectId"
                  filterable
                  clearable
                  :options="projectOptions"
                  :placeholder="t('apiDocs.no_project')"
                  class="custom-select"
                />
              </label>
              <label class="field-block">
                <span>{{ t("apiDocs.form_project_url") }}</span>
                <input v-model="editProjectUrl" placeholder="e.g. https://api.example.com">
                <small class="field-hint">
                  {{ t("apiDocs.hint_project_url") }}
                </small>
              </label>
            </div>

            <div class="preview-meta-grid">
              <div><strong>{{ t("apiDocs.meta_project") }}</strong>{{ projectName(selectedDoc.project_id, selectedDoc.legacy_project_name || selectedDoc.project_name) }}</div>
              <div><strong>{{ t("apiDocs.meta_project_url") }}</strong>{{ selectedDoc.project_url || t("apiDocs.not_set") }}</div>
              <div><strong>{{ t("apiDocs.meta_content_type") }}</strong>{{ selectedDoc.content_type }}</div>
              <div><strong>{{ t("apiDocs.meta_source") }}</strong>{{ selectedDoc.source }}</div>
              <div><strong>{{ t("apiDocs.meta_updated_at") }}</strong>{{ formatServerDateTime(selectedDoc.updated_at) }}</div>
              <div><strong>MinIO URI：</strong>{{ selectedDoc.storage_uri }}</div>
            </div>

            <div v-if="selectedDoc.preview_error" class="preview-inline-error">{{ selectedDoc.preview_error }}</div>
            <div v-else class="preview-code-shell">
              <div class="preview-toolbar">
                <span>{{ t("apiDocs.file_content") }}</span>
                <span v-if="selectedDoc.preview_truncated">{{ t("apiDocs.preview_truncated") }}</span>
              </div>
              <pre class="preview-code"><code>{{ selectedDoc.preview_text || t("apiDocs.no_preview_content") }}</code></pre>
            </div>
          </template>
        </div>
      </section>
    </div>

    <div v-if="uploadOpen" class="tools-modal-backdrop" @click.self="closeUpload">
      <section class="tools-modal upload-modal">
        <header class="tools-modal-head">
          <div>
            <h3>{{ t("apiDocs.import_title") }}</h3>
            <p>{{ t("apiDocs.import_desc") }}</p>
          </div>
          <button class="icon-btn" @click="closeUpload"><i class="fa-solid fa-xmark"></i></button>
        </header>

        <div class="tools-modal-body">
          <div class="import-mode-tabs">
            <button :class="{ active: uploadMode === 'local' }" @click="uploadMode = 'local'">{{ t("apiDocs.mode_local") }}</button>
            <button :class="{ active: uploadMode === 'url' }" @click="uploadMode = 'url'">{{ t("apiDocs.mode_url") }}</button>
            <button :class="{ active: uploadMode === 'integration' }" @click="uploadMode = 'integration'">{{ t("apiDocs.mode_integration") }}</button>
          </div>

          <template v-if="uploadMode === 'local'">
            <label class="upload-drop">
              <i class="fa-solid fa-file-arrow-up"></i>
              <strong>{{ uploadFile?.name || t("apiDocs.select_local_file") }}</strong>
              <span>{{ t("apiDocs.upload_format_hint") }}</span>
              <input type="file" accept=".json,.yaml,.yml,.har,.txt,.md,.log,.pdf" @change="onUploadFileChange">
            </label>
          </template>

          <template v-else-if="uploadMode === 'url'">
            <label class="field-block modal-field">
              <span>{{ t("apiDocs.form_doc_url") }}</span>
              <input v-model="remoteUrl" placeholder="e.g. http://127.0.0.1:8003/docs">
              <small class="field-hint">{{ t("apiDocs.hint_url_format") }}</small>
            </label>
          </template>

          <template v-else>
            <label class="field-block modal-field">
              <span>{{ t("apiDocs.select_source") }}</span>
              <select v-model="selectedIntegrationId">
                <option value="">{{ t("apiDocs.ph_select_source") }}</option>
                <option v-for="integration in importableIntegrations" :key="integration.id" :value="integration.id">
                  {{ integration.name }} · {{ integration.kind.toUpperCase() }}
                </option>
              </select>
              <small class="field-hint">{{ t("apiDocs.hint_source") }}</small>
            </label>

            <div v-if="selectedIntegration" class="integration-brief">
              <div><strong>{{ t("apiDocs.meta_type") }}</strong>{{ selectedIntegration.kind.toUpperCase() }}</div>
              <div><strong>{{ t("apiDocs.meta_default_project") }}</strong>{{ selectedIntegration.project_name || t("apiDocs.not_set") }}</div>
              <div>
                <strong>{{ selectedIntegrationIsMcp ? t("apiDocs.meta_mcp_endpoint") : t("apiDocs.meta_default_doc_url") }}：</strong>
                {{ selectedIntegrationIsMcp ? (selectedIntegration.endpoint_url || t("apiDocs.not_set")) : (selectedIntegration.document_url || t("apiDocs.not_set")) }}
              </div>
            </div>

            <label v-if="workspaceOptions.length" class="field-block modal-field">
              <span>{{ t("apiDocs.select_workspace") }}</span>
              <select v-model="selectedWorkspaceId" :disabled="integrationImportLoading">
                <option value="">{{ t("apiDocs.ph_select_workspace") }}</option>
                <option v-for="workspace in workspaceOptions" :key="workspace.id" :value="workspace.id">
                  {{ workspace.name }} · {{ workspace.document_count }} {{ t("apiDocs.docs_unit") }}
                </option>
              </select>
              <small class="field-hint">{{ t("apiDocs.hint_workspace") }}</small>
            </label>

            <label v-if="filteredImportSources.length" class="field-block modal-field">
              <span>{{ selectedIntegrationIsMcp ? t("apiDocs.select_api_doc") : t("apiDocs.select_import_source") }}</span>
              <select v-model="selectedImportSourceId" :disabled="integrationImportLoading">
                <option value="">{{ t("apiDocs.ph_select") }}</option>
                <option v-for="source in filteredImportSources" :key="source.id" :value="source.id">
                  {{ source.label }}<template v-if="source.project_name"> · {{ source.project_name }}</template>
                </option>
              </select>
              <small v-if="selectedImportSource?.summary" class="field-hint">{{ selectedImportSource.summary }}</small>
            </label>

            <label v-if="!selectedIntegrationIsMcp" class="field-block modal-field">
              <span>{{ t("apiDocs.doc_url_override") }}</span>
              <input v-model="integrationDocumentUrl" :placeholder="t('apiDocs.ph_doc_url_override')">
            </label>

            <div v-if="integrationImportHint" class="integration-inline-hint">
              {{ integrationImportHint }}
            </div>
          </template>

          <div class="upload-form-grid">
            <label class="field-block modal-field">
              <span>{{ t("apiDocs.form_title_optional") }}</span>
              <input v-model="uploadTitle" placeholder="e.g. User Center OpenAPI v2">
            </label>

            <label class="field-block modal-field">
              <span>{{ t("apiDocs.form_project_recommended") }}</span>
              <n-select
                v-model:value="uploadProjectId"
                filterable
                clearable
                :options="projectOptions"
                :placeholder="t('apiDocs.no_project')"
                class="custom-select"
                :to="false"
              />
              <small class="field-hint">
                API 文档将通过稳定项目 ID 绑定；已归档项目不可用于新绑定。
              </small>
            </label>

            <label class="field-block modal-field">
              <span>{{ t("apiDocs.form_project_url_recommended") }}</span>
              <input v-model="uploadProjectUrl" placeholder="e.g. https://api.example.com">
              <small class="field-hint">
                {{ t("apiDocs.hint_upload_project_url") }}
              </small>
            </label>
          </div>

          <div class="modal-actions">
            <button class="secondary-btn" @click="closeUpload">{{ t("common.cancel") }}</button>
            <button class="primary-btn" :disabled="uploadLoading || !canSubmitIntegrationImport" @click="submitUpload">
              {{ uploadLoading ? t("apiDocs.importing") : t("apiDocs.start_import") }}
            </button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tools-tab-pane {
  animation: fadeIn 0.25s ease;
}

.pane-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.pane-header .section-title {
  margin: 0 0 8px;
  font-size: 18px;
}

.pane-header .head-desc {
  margin: 0;
  max-width: 760px;
  color: var(--muted);
}

.api-doc-empty {
  display: grid;
  place-items: center;
  gap: 10px;
  min-height: 220px;
  border: 1px dashed var(--border);
  border-radius: 18px;
  background: rgba(248, 250, 252, 0.6);
  color: var(--muted);
  text-align: center;
}

.api-doc-empty--error {
  border-color: rgba(239, 68, 68, 0.22);
  background: rgba(254, 242, 242, 0.8);
  color: #991b1b;
}

.api-doc-empty i {
  font-size: 24px;
}

.api-docs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}

.api-doc-card {
  display: flex;
  flex-direction: column;
  width: 320px;
  min-height: 310px;
  padding: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  transition: all 0.2s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.api-doc-card:hover {
  border-color: var(--border-strong);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.06);
  transform: translateY(-2px);
}

.api-doc-header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}

.api-doc-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}

.api-doc-icon.postman {
  background: rgba(249, 115, 22, 0.1);
  color: #f97316;
}

.api-doc-actions {
  display: flex;
  gap: 6px;
}

.api-doc-content {
  display: flex;
  flex: 1;
  flex-direction: column;
}

.api-doc-head {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}

.api-doc-head h4 {
  margin: 0;
  font-size: 18px;
  line-height: 1.3;
}

.api-doc-content p {
  margin: 0 0 16px;
  font-size: 14px;
  color: var(--muted);
  line-height: 1.6;
  word-break: break-all;
}

.api-doc-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px dashed var(--border);
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  font-family: var(--font-mono, monospace);
}

.badge {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  display: inline-block;
}

.badge-blue {
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
}

.badge-orange {
  background: rgba(249, 115, 22, 0.1);
  color: #ea580c;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.icon-btn:hover {
  background: var(--surface-muted);
  color: var(--text);
}

.icon-btn.danger:hover {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.tools-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2400;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.36);
}

.tools-modal {
  width: min(960px, 100%);
  max-height: min(88vh, 900px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 24px;
  background: #ffffff;
  border: 1px solid rgba(15, 23, 42, 0.08);
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.18);
}

.tools-modal-head {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 24px 28px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
}

.tools-modal-head h3 {
  margin: 0 0 8px;
}

.tools-modal-head p {
  margin: 0;
  color: var(--muted);
}

.tools-modal-head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.head-save-btn {
  padding: 6px 14px;
  font-size: 13px;
}

.tools-modal-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 24px;
}

.preview-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 20px 28px 0;
  align-items: start;
}

.preview-form-grid .field-block {
  margin: 0;
}

.preview-meta-grid {
  display: grid;
  gap: 10px;
  padding: 20px 28px 0;
  color: #475569;
}

.preview-inline-error,
.integration-inline-hint {
  margin: 16px 28px 0;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(59, 130, 246, 0.06);
  color: #1e3a8a;
  font-size: 13px;
}

.integration-inline-hint--success {
  background: rgba(22, 163, 74, 0.08);
  color: #166534;
}

.preview-code-shell {
  margin: 20px 28px 0;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 18px;
  overflow: hidden;
  background: #f8fafc;
}

.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
  font-size: 13px;
  color: #475569;
}

.preview-code {
  margin: 0;
  padding: 18px;
  max-height: none;
  overflow: visible;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.72;
  color: #0f172a;
  font-family: "Consolas", "SFMono-Regular", monospace;
}

.import-mode-tabs {
  display: flex;
  gap: 10px;
  padding: 20px 28px 0;
}

.import-mode-tabs button {
  border: 1px solid #E5E7EB;
  border-radius: 999px;
  padding: 8px 16px;
  background: #ffffff;
  color: #6B7280;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.import-mode-tabs button:hover {
  background: #f9fafb;
  color: #1F2937;
}

.import-mode-tabs button.active {
  background: #111827;
  color: #ffffff;
  border-color: #111827;
}

.upload-drop {
  display: grid;
  gap: 8px;
  margin: 24px 28px 0;
  padding: 22px;
  border: 1px dashed rgba(37, 99, 235, 0.34);
  border-radius: 18px;
  background: rgba(239, 246, 255, 0.8);
  cursor: pointer;
}

.upload-drop i {
  font-size: 22px;
  color: #2563eb;
}

.upload-drop strong {
  color: #0f172a;
}

.upload-drop span {
  color: #64748b;
  font-size: 13px;
}

.upload-drop input {
  display: none;
}

.upload-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
  padding: 0 28px;
  align-items: start;
}

.upload-form-grid .modal-field {
  margin: 16px 0 0;
}

.field-block {
  display: grid;
  gap: 8px;
  font-size: 13px;
  color: #1F2937;
}

.modal-field,
.field-block {
  margin: 16px 28px 0;
}

.field-block span {
  font-weight: 500;
}

.field-block > input,
.field-block > select {
  width: 100%;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  background: #ffffff;
  color: #1F2937;
  transition: all 0.2s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}

.field-block > input:focus,
.field-block > select:focus {
  outline: none;
  border-color: #111827;
  box-shadow: 0 0 0 1px #111827;
}

.field-block > input::placeholder {
  color: #9CA3AF;
}

.field-hint {
  color: #6B7280;
  font-size: 12px;
  line-height: 1.5;
}

/* NSelect Custom Styles */
:deep(.custom-select .n-base-selection) {
  --n-border: 1px solid #E5E7EB !important;
  --n-border-hover: 1px solid #111827 !important;
  --n-border-focus: 1px solid #111827 !important;
  --n-border-active: 1px solid #111827 !important;
  --n-box-shadow-focus: 0 0 0 1px #111827 !important;
  --n-box-shadow-active: 0 0 0 1px #111827 !important;
  --n-caret-color: #111827 !important;
  --n-text-color: #1F2937 !important;
  --n-placeholder-color: #9CA3AF !important;
  --n-border-radius: 8px !important;
  height: 42px !important;
  min-height: 42px !important;
  background: #ffffff;
  transition: all 0.2s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  display: flex;
  align-items: center;
}

:deep(.custom-select .n-base-selection-label) {
  height: 100% !important;
  display: flex !important;
  align-items: center !important;
}

:deep(.custom-select .n-base-selection-tags) {
  align-items: center !important;
}

:deep(.custom-select .n-base-selection-input) {
  font-size: 14px !important;
  color: #1F2937 !important;
  height: 100% !important;
  line-height: normal !important;
  display: flex !important;
  align-items: center !important;
}

:deep(.custom-select .n-base-selection-placeholder) {
  color: #9CA3AF !important;
  font-size: 14px !important;
  height: 100% !important;
  display: flex !important;
  align-items: center !important;
}

:deep(.custom-select .n-base-suffix) {
  height: 100% !important;
  display: flex !important;
  align-items: center !important;
}

:deep(.n-base-select-menu) {
  --n-option-text-color: #1F2937 !important;
  --n-option-text-color-active: #111827 !important;
  --n-option-color-active: #f9fafb !important;
  --n-option-color-pending: #f9fafb !important;
  --n-border-radius: 8px !important;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06) !important;
  border: 1px solid #E5E7EB !important;
}

.integration-brief {
  margin: 16px 28px 0;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(248, 250, 252, 0.8);
  color: #475569;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 20px 28px 0;
}

@media (max-width: 760px) {
  .api-doc-card {
    width: 100%;
  }

  .preview-form-grid {
    grid-template-columns: 1fr;
    padding-right: 0;
  }

  .import-mode-tabs {
    flex-wrap: wrap;
  }
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
