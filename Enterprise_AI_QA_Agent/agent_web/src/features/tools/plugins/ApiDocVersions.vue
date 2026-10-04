<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import { t } from "../../../services/i18n";
import type { ApiDocRecord, ApiDocVersionRecord, ApiDocVersionResult, ApiDocContentResult } from "../../../types";
import { documentFileBase64, prepareDocumentRequest, readDocumentRequest, type PendingDocumentRequest } from "./documentUploadRequest";
import ApiDocAssets from "./ApiDocAssets.vue";

const props = defineProps<{ document: ApiDocRecord; canWrite: boolean }>();
const emit = defineEmits<{ updated: [document: ApiDocRecord] }>();
const versions = ref<ApiDocVersionRecord[]>([]);
const preview = ref<ApiDocContentResult | null>(null);
const selected = ref<ApiDocVersionRecord | null>(null);
const pending = ref<PendingDocumentRequest | null>(null);
const file = ref<File | null>(null);
const busy = ref(false);
const loading = ref(false);
const more = ref(false);
const rejected = ref(false);
const error = ref("");
const notice = ref("");
let revision = 0;
let previewRevision = 0;
const key = () => `qa-agent:api-doc-version:${props.document.id}`;

async function loadVersions(append = false) {
  const requestRevision = ++revision;
  const docId = props.document.id;
  loading.value = true;
  try {
    const batch = await api.listApiDocVersions(docId, append ? versions.value.length : 0);
    if (requestRevision !== revision || docId !== props.document.id) return;
    versions.value = append ? [...versions.value, ...batch] : batch;
    more.value = batch.length === 20;
  } catch (err) {
    if (requestRevision === revision) error.value = err instanceof Error ? err.message : t("apiDocs.load_detail_failed");
  } finally {
    if (requestRevision === revision) loading.value = false;
  }
}

async function openVersion(version: ApiDocVersionRecord) {
  const requestRevision = ++previewRevision;
  selected.value = version;
  preview.value = null;
  error.value = "";
  try {
    const result = await api.readApiDocVersionContent(props.document.id, version.id);
    if (requestRevision === previewRevision) preview.value = result;
  } catch (err) {
    if (requestRevision === previewRevision) error.value = err instanceof Error ? err.message : t("apiDocs.load_detail_failed");
  }
}

async function finish(result: ApiDocVersionResult) {
  // A replay returns its original snapshot. Refresh the current projection separately.
  const current = await api.getApiDoc(props.document.id);
  sessionStorage.removeItem(key());
  pending.value = null;
  file.value = null;
  rejected.value = false;
  notice.value = result.created_new ? `${t("apiDocs.version_saved")} V${result.version.version}` : t("apiDocs.content_unchanged");
  emit("updated", current);
  await loadVersions();
}

async function submit(initialize = false) {
  if (busy.value || !props.canWrite || !props.document.project_id) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    if (initialize) {
      const body = { project_id: props.document.project_id,
        expected_catalog_revision: pending.value?.expected_catalog_revision ?? props.document.catalog_revision ?? 0 };
      pending.value = await prepareDocumentRequest(key(), body, { ...body, operation: "initialize" });
      await finish(await api.initializeApiDocHistory(props.document.id, { ...body, request_id: pending.value.request_id }));
    } else {
      if (!file.value) throw new Error(t("apiDocs.select_local_file"));
      const parent = pending.value?.expected_parent_version_id ?? props.document.current_version_id;
      if (!parent) throw new Error(t("apiDocs.initialize_history"));
      const body = { filename: file.value.name, content_base64: await documentFileBase64(file.value),
        project_id: props.document.project_id, expected_parent_version_id: parent, source: "tools_api_docs_version" };
      pending.value = await prepareDocumentRequest(key(), body, { operation: "version", filename: body.filename,
        project_id: body.project_id, expected_parent_version_id: parent });
      await finish(await api.uploadApiDocVersion(props.document.id, { ...body, request_id: pending.value.request_id }));
    }
  } catch (err) {
    rejected.value = err instanceof HttpError && [400, 409, 422].includes(err.status);
    error.value = err instanceof Error ? err.message : t("apiDocs.import_failed");
  } finally {
    busy.value = false;
  }
}

async function recover(resetRejected = false) {
  if (!pending.value || busy.value || !pending.value.project_id) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.getApiDocVersionSubmission(props.document.id, pending.value.request_id, pending.value.project_id);
    await finish(result);
  } catch (err) {
    if (err instanceof HttpError && err.status === 404 && resetRejected && rejected.value) {
      const current = await api.getApiDoc(props.document.id);
      sessionStorage.removeItem(key());
      pending.value = null;
      rejected.value = false;
      emit("updated", current);
      notice.value = t("apiDocs.retry_current_version");
    } else {
      error.value = err instanceof HttpError && err.status === 404 ? t("apiDocs.submission_not_found") :
        err instanceof Error ? err.message : t("apiDocs.import_failed");
    }
  } finally {
    busy.value = false;
  }
}

watch(() => props.document.id, () => {
  ++previewRevision;
  preview.value = null;
  selected.value = null;
  error.value = "";
  notice.value = "";
  file.value = null;
  try { pending.value = readDocumentRequest(key()); }
  catch (err) { error.value = err instanceof Error ? err.message : t("apiDocs.pending_invalid"); }
  void loadVersions();
}, { immediate: true });
</script>

<template>
  <section class="document-versions">
    <h4>{{ t("apiDocs.version_history") }}</h4>
    <p>{{ t("apiDocs.current_version") }}: {{ document.current_version_id ? `V${document.version_number}` : t("apiDocs.history_not_initialized") }}</p>
    <div v-if="canWrite && document.project_id" class="version-actions">
      <template v-if="document.current_version_id">
        <label>{{ t("apiDocs.new_version_file") }}
          <input :aria-label="t('apiDocs.new_version_file')" type="file" accept=".json,.yaml,.yml,.har,.txt,.md,.log,.pdf,.docx" :disabled="busy"
            @change="file = ($event.target as HTMLInputElement).files?.[0] ?? null">
        </label>
        <button class="primary-btn" :disabled="busy || !file" @click="submit()">{{ t("apiDocs.upload_new_version") }}</button>
      </template>
      <button v-else class="primary-btn" :disabled="busy" @click="submit(true)">{{ t("apiDocs.initialize_history") }}</button>
    </div>
    <p v-if="!document.project_id">{{ t("apiDocs.version_requires_project") }}</p>
    <div v-if="pending" class="pending-request">
      <p>{{ t("apiDocs.pending_upload") }} {{ pending.filename }} {{ pending.expected_parent_version_id ? t("apiDocs.pending_parent_fixed") : '' }}</p>
      <button class="secondary-btn" :disabled="busy" @click="recover()">{{ t("apiDocs.find_submission") }}</button>
      <button v-if="rejected" class="secondary-btn" :disabled="busy" @click="recover(true)">{{ t("apiDocs.prepare_current_retry") }}</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div v-for="version in versions" :key="version.id" class="version-row">
      <span>V{{ version.version }} · {{ version.snapshot.original_asset?.filename || version.snapshot.filename }} · {{ version.created_at }}</span>
      <button class="secondary-btn" @click="openVersion(version)">{{ t("apiDocs.view_version") }} V{{ version.version }}</button>
    </div>
    <button class="secondary-btn" :disabled="loading" @click="loadVersions()">{{ t("apiDocs.refresh_versions") }}</button>
    <button v-if="more" class="secondary-btn" :disabled="loading" @click="loadVersions(true)">{{ t("apiDocs.more_versions") }}</button>
    <div v-if="selected" class="document-version-preview">
      <h5>{{ t("apiDocs.version_snapshot") }} V{{ selected.version }}</h5>
      <p>{{ selected.snapshot.title }} · {{ selected.parent_version_id || t("apiDocs.first_version") }}</p>
      <ApiDocAssets :document="selected.snapshot" />
      <p v-if="preview?.read_error" role="alert">{{ preview.read_error }}</p>
      <template v-else-if="preview">
        <p v-if="preview.truncated">{{ t("apiDocs.preview_truncated") }}</p>
        <pre>{{ preview.content }}</pre>
      </template>
    </div>
  </section>
</template>

<style scoped>
.document-versions { margin: 20px 0; padding: 16px; border: 1px solid var(--border); border-radius: 12px; }
.version-actions, .version-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.version-row { justify-content: space-between; }
.document-version-preview pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
[role="alert"] { color: var(--red); }
</style>
