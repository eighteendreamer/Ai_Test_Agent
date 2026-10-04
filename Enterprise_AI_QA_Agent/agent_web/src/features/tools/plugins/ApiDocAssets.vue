<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type { ApiDocRecord, ApiDocumentAsset } from "../../../types";

const props = defineProps<{ document: ApiDocRecord }>();
const downloading = ref("");
const error = ref("");

async function download(asset: ApiDocumentAsset) {
  downloading.value = asset.id;
  error.value = "";
  try {
    const blob = await api.downloadApiDocAsset(props.document.id, asset.id);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    try {
      link.href = url;
      link.download = asset.filename;
      link.click();
    } finally {
      URL.revokeObjectURL(url);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("apiDocs.asset_download_failed");
  } finally {
    downloading.value = "";
  }
}
</script>

<template>
  <section class="document-assets">
    <h4>{{ t("apiDocs.original_file") }}</h4>
    <div v-if="document.original_asset" class="asset-row">
      <span>{{ document.original_asset.filename }} · {{ document.original_asset.size_bytes }} B</span>
      <button class="secondary-btn" :disabled="Boolean(downloading)" @click="download(document.original_asset)">
        {{ t("apiDocs.download_asset") }}
      </button>
    </div>
    <p v-else>{{ t("apiDocs.original_missing") }}</p>
    <template v-if="document.derived_assets?.length">
      <h4>{{ t("apiDocs.derived_files") }}</h4>
      <div v-for="asset in document.derived_assets" :key="asset.id" class="asset-row">
        <span>{{ asset.filename }} · {{ asset.size_bytes }} B · {{ asset.created_at }}</span>
        <button class="secondary-btn" :disabled="Boolean(downloading)" @click="download(asset)">
          {{ t("apiDocs.download_asset") }}
        </button>
      </div>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.document-assets { margin: 16px 0; }
.asset-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0; }
.asset-row span { overflow-wrap: anywhere; }
[role="alert"] { color: var(--red); }
</style>
