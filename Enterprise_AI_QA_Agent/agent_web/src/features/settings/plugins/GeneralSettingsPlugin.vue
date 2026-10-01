<script setup lang="ts">
import { computed, ref, watch, onActivated, onBeforeUnmount, onDeactivated } from "vue";
import { NModal } from "naive-ui";

import DropdownSelect from "../../../components/common/DropdownSelect.vue";
import { useGeneralSettingsStore, type AppFontFamily, type AppFontSize, type GeneralSettingsSnapshot } from "../../../stores/generalSettings";
import type { SettingsPluginDefinition } from "../plugins";
import { t } from "../../../services/i18n";
import { api } from "../../../services/api";

defineProps<{
  plugin?: SettingsPluginDefinition;
}>();

const settingsStore = useGeneralSettingsStore();
const notice = ref("");
const draft = ref<GeneralSettingsSnapshot>({ ...settingsStore.settingsSnapshot });

const languageOptions = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁體中文" },
  { value: "en-US", label: "English" },
  { value: "ja-JP", label: "日本語" },
  { value: "ko-KR", label: "한국어" },
  { value: "fr-FR", label: "Français" },
  { value: "de-DE", label: "Deutsch" },
  { value: "es-ES", label: "Español" },
  { value: "pt-BR", label: "Português" },
  { value: "ru-RU", label: "Русский" },
  { value: "ar-SA", label: "العربية" },
  { value: "hi-IN", label: "हिन्दी" },
  { value: "id-ID", label: "Bahasa Indonesia" },
  { value: "vi-VN", label: "Tiếng Việt" },
  { value: "th-TH", label: "ไทย" },
];

const fontFamilyOptions = computed<Array<{ value: AppFontFamily; label: string; detail: string }>>(() => [
  { value: "system", label: t("settings.font_family_system"), detail: t("settings.font_family_system_desc") },
  { value: "sans", label: t("settings.font_family_sans"), detail: t("settings.font_family_sans_desc") },
  { value: "chinese", label: t("settings.font_family_chinese"), detail: t("settings.font_family_chinese_desc") },
  { value: "serif", label: t("settings.font_family_serif"), detail: t("settings.font_family_serif_desc") },
  { value: "mono", label: t("settings.font_family_mono"), detail: t("settings.font_family_mono_desc") },
]);

const fontSizeOptions = computed<Array<{ value: AppFontSize; label: string; detail: string }>>(() => [
  { value: "small", label: t("settings.font_size_small"), detail: t("settings.font_size_small_desc") },
  { value: "compact", label: t("settings.font_size_compact"), detail: t("settings.font_size_compact_desc") },
  { value: "standard", label: t("settings.font_size_standard"), detail: t("settings.font_size_standard_desc") },
  { value: "comfortable", label: t("settings.font_size_comfortable"), detail: t("settings.font_size_comfortable_desc") },
  { value: "large", label: t("settings.font_size_large"), detail: t("settings.font_size_large_desc") },
]);

const dataActions = computed(() => [
  {
    key: "backup",
    icon: "fa-solid fa-box-archive",
    title: t("settings.data_backup"),
    detail: t("settings.data_backup_desc"),
  },
  {
    key: "import",
    icon: "fa-solid fa-file-import",
    title: t("settings.data_import"),
    detail: t("settings.data_import_desc"),
  },
  {
    key: "cleanup",
    icon: "fa-solid fa-broom",
    title: t("settings.data_cleanup"),
    detail: t("settings.data_cleanup_desc"),
  },
]);

// Index of the current selection, used to look up the active option's detail text.
const fontFamilyIndex = computed(() =>
  Math.max(0, fontFamilyOptions.value.findIndex((item) => item.value === draft.value.fontFamily)),
);

const fontSizeIndex = computed(() =>
  Math.max(0, fontSizeOptions.value.findIndex((item) => item.value === draft.value.fontSize)),
);

const permissionLabel = computed(() => {
  const permission = settingsStore.notificationPermissionStatus;
  if (permission === "granted") {
    return t("settings.notify_granted");
  }
  if (permission === "denied") {
    return t("settings.notify_denied");
  }
  if (permission === "default") {
    return t("settings.notify_default");
  }
  return t("settings.notify_unsupported");
});

const languageLabel = computed(() => {
  return languageOptions.find((item) => item.value === draft.value.language)?.label ?? draft.value.language;
});

const fontFamilyLabel = computed(() => {
  return fontFamilyOptions.value.find((item) => item.value === draft.value.fontFamily)?.label ?? t("settings.font_family_system");
});

const fontSizeLabel = computed(() => {
  return fontSizeOptions.value.find((item) => item.value === draft.value.fontSize)?.label ?? t("settings.font_size_standard");
});

const fontLabel = computed(() => `${fontFamilyLabel.value} / ${fontSizeLabel.value}`);

const lastSavedLabel = computed(() => {
  if (!settingsStore.lastSavedAt) {
    return t("settings.not_saved_yet");
  }
  return new Intl.DateTimeFormat(draft.value.language || "zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(settingsStore.lastSavedAt));
});

const isDirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(settingsStore.settingsSnapshot));

function syncDraftFromStore() {
  draft.value = { ...settingsStore.settingsSnapshot };
}

watch(
  () => [draft.value.fontFamily, draft.value.fontSize, draft.value.reduceMotion] as const,
  () => {
    settingsStore.previewAppearance({
      fontFamily: draft.value.fontFamily,
      fontSize: draft.value.fontSize,
      reduceMotion: draft.value.reduceMotion,
    });
  },
);

onBeforeUnmount(() => {
  pageActive = false;
  stopExportPolling();
  if (isDirty.value) {
    settingsStore.restoreAppearance();
  }
});

function saveSettings() {
  settingsStore.saveGeneralSettings(draft.value);
  syncDraftFromStore();
  notice.value = t("settings.saved");
}

function resetSettings() {
  settingsStore.resetGeneralSettings();
  syncDraftFromStore();
  notice.value = t("settings.reset_done");
}

async function handleDataAction(key: string, _title: string) {
  if (key === "backup") {
    exportOpen.value = true;
    exportLoading.value = true;
    exportSessionCount.value = 0;
    exportDone.value = false;
    try {
      const res = await api.exportPreview();
      exportSessionCount.value = res.session_count;
    } catch (error) {
      notice.value = error instanceof Error ? error.message : t("settings.action_failed", { title: _title, error: "unknown" });
    } finally {
      exportLoading.value = false;
    }
  } else if (key === "import") {
    importOpen.value = true;
    importFile.value = null;
    importResult.value = null;
    importLoading.value = false;
  } else if (key === "cleanup") {
    cleanupOpen.value = true;
    cleanupDays.value = 30;
    cleanupPreview.value = null;
    cleanupDone.value = false;
    cleanupLoading.value = false;
  }
}

// --- Export ---
const exportOpen = ref(false);
const exportLoading = ref(false);
const exportSessionCount = ref(0);
const exportDone = ref(false);
const exportProgress = ref(0);
const exportTotal = ref(0);
let exportTaskId: string | null = null;
let exportPollTimer: ReturnType<typeof setTimeout> | null = null;
let exportPollGeneration = 0;
let pageActive = true;

function stopExportPolling() {
  exportPollGeneration += 1;
  if (exportPollTimer !== null) {
    clearTimeout(exportPollTimer);
    exportPollTimer = null;
  }
}

function scheduleExportPoll() {
  if (!pageActive || !exportTaskId || !exportLoading.value || exportPollTimer !== null) return;
  const generation = exportPollGeneration;
  exportPollTimer = setTimeout(() => {
    exportPollTimer = null;
    void pollExport(generation);
  }, 400);
}

async function pollExport(generation: number) {
  const taskId = exportTaskId;
  if (!pageActive || !taskId || generation !== exportPollGeneration) return;
  try {
    const result = await api.exportProgress(taskId);
    if (!pageActive || taskId !== exportTaskId || generation !== exportPollGeneration) return;
    exportProgress.value = result.progress;
    if (result.status === "done") {
      stopExportPolling();
      exportTaskId = null;
      try {
        await api.exportDownload(taskId);
        exportDone.value = true;
      } catch (error) {
        notice.value = error instanceof Error ? error.message : "Export download failed";
      } finally {
        exportLoading.value = false;
      }
      return;
    }
    if (result.status === "error") throw new Error(result.error || "Export failed");
    scheduleExportPoll();
  } catch (error) {
    if (!pageActive || taskId !== exportTaskId || generation !== exportPollGeneration) return;
    stopExportPolling();
    exportTaskId = null;
    notice.value = error instanceof Error ? error.message : "Export failed";
    exportLoading.value = false;
  }
}

onActivated(() => {
  pageActive = true;
  scheduleExportPoll();
});
onDeactivated(() => {
  pageActive = false;
  stopExportPolling();
});

async function doExport() {
  if (exportTaskId) return;
  exportLoading.value = true;
  exportProgress.value = 0;
  try {
    const { task_id, total } = await api.exportStart();
    exportTaskId = task_id;
    exportTotal.value = total;
    scheduleExportPoll();
  } catch (error) {
    notice.value = error instanceof Error ? error.message : "Export failed";
    exportLoading.value = false;
  }
}

// --- Import ---
const importOpen = ref(false);
const importLoading = ref(false);
const importFile = ref<File | null>(null);
const importResult = ref<{ imported_count: number; skipped_count: number } | null>(null);

function onImportFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  importFile.value = input.files?.[0] ?? null;
}

async function doImport() {
  if (!importFile.value) return;
  importLoading.value = true;
  try {
    const res = await api.importData(importFile.value) as { imported_count: number; skipped_count: number };
    importResult.value = res;
  } catch (error) {
    notice.value = error instanceof Error ? error.message : "Import failed";
  } finally {
    importLoading.value = false;
  }
}

// --- Cleanup ---
const cleanupOpen = ref(false);
const cleanupLoading = ref(false);
const cleanupDays = ref<number>(30);
const cleanupPreview = ref<{ affected_count: number; retained_count: number; total_sessions: number; oldest_session: string | null; newest_session: string | null } | null>(null);
const cleanupDone = ref(false);

function formatDateShort(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const cleanupDaysOptions = [
  { value: 7, label: "7" },
  { value: 30, label: "30" },
  { value: 90, label: "90" },
  { value: 180, label: "180" },
  { value: 0, label: t("settings.data_cleanup_all") },
];

async function doCleanupPreview() {
  cleanupLoading.value = true;
  try {
    const res = await api.cleanupData({ action: "cleanup", dry_run: true, time_range_days: cleanupDays.value }) as Record<string, unknown>;
    const details = (res.details || {}) as Record<string, unknown>;
    cleanupPreview.value = {
      affected_count: Number(res.affected_count || 0),
      retained_count: Number(details.retained_sessions || 0),
      total_sessions: Number(details.total_sessions || 0),
      oldest_session: (details.oldest_session as string) || null,
      newest_session: (details.newest_session as string) || null,
    };
  } catch (error) {
    notice.value = error instanceof Error ? error.message : "Preview failed";
  } finally {
    cleanupLoading.value = false;
  }
}

async function doCleanupConfirm() {
  cleanupLoading.value = true;
  try {
    await api.cleanupData({
      action: "cleanup", dry_run: false, time_range_days: cleanupDays.value,
      confirm: true, confirm_all_sessions: cleanupDays.value === 0,
    });
    cleanupDone.value = true;
  } catch (error) {
    notice.value = error instanceof Error ? error.message : "Cleanup failed";
  } finally {
    cleanupLoading.value = false;
  }
}
</script>

<template>
  <section class="general-settings">
    <header class="general-header">
      <div class="general-header__main">
        <div class="general-logo">
          <i class="fa-solid fa-sliders"></i>
        </div>
        <div class="general-title-wrapper">
          <h1 class="general-title">{{ t("settings.general") }}</h1>
          <p class="general-desc">
            {{ t("settings.system_language_desc") }}
          </p>
        </div>
      </div>
    
    </header>

    <div class="general-meta">
      <div class="meta-item">
        <span class="meta-label">{{ t("settings.system_language") }}</span>
        <span class="meta-value">{{ languageLabel }}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">{{ t("settings.notify_title") }}</span>
        <span class="meta-value">{{ permissionLabel }}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">{{ t("settings.font_family") }}</span>
        <span class="meta-value">{{ fontLabel }}</span>
      </div>
    </div>

    <div v-if="notice" class="general-notice">
      <i class="fa-solid fa-circle-info"></i>
      <span>{{ notice }}</span>
    </div>

    <div class="general-sections">
      <section class="general-section">
        <div class="section-header">
          <h2 class="section-title">{{ t("settings.language_title") }}</h2>
          <p class="section-desc">{{ t("settings.system_language_desc") }}</p>
        </div>
        <div class="list-container language-setting-container">
          <div class="list-item">
            <div class="list-item__icon"><i class="fa-solid fa-language"></i></div>
            <div class="list-item__content">
              <label class="select-field select-field--compact">
                <div class="field-header">
                  <h3>{{ t("settings.system_language") }}</h3>
                </div>
                <DropdownSelect
                  v-model="draft.language"
                  :options="languageOptions"
                  button-class="settings-provider-select"
                  menu-class="settings-provider-select-menu"
                  placeholder=""
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      <section class="general-section">
        <div class="section-header">
          <h2 class="section-title">{{ t("settings.notify_title") }}</h2>
          <p class="section-desc">{{ t("settings.notify_desc") }}</p>
        </div>
        <div class="list-container">
          <label class="list-item switch-item">
            <div class="list-item__icon"><i class="fa-solid fa-check-double"></i></div>
            <div class="list-item__content">
              <h3>{{ t("settings.notify_session_complete") }}</h3>
              <p>{{ t("settings.notify_session_complete_desc") }}</p>
            </div>
            <div class="list-item__action">
              <div class="switch-wrapper">
                <input v-model="draft.notifySessionCompleteWhenAway" type="checkbox" />
                <span class="switch-visual"></span>
              </div>
            </div>
          </label>
          <label class="list-item switch-item">
            <div class="list-item__icon"><i class="fa-solid fa-clipboard-check"></i></div>
            <div class="list-item__content">
              <h3>{{ t("settings.notify_approval") }}</h3>
              <p>{{ t("settings.notify_approval_desc") }}</p>
            </div>
            <div class="list-item__action">
              <div class="switch-wrapper">
                <input v-model="draft.notifyApprovalRequiredWhenAway" type="checkbox" />
                <span class="switch-visual"></span>
              </div>
            </div>
          </label>
          <label class="list-item switch-item">
            <div class="list-item__icon"><i class="fa-solid fa-eye-slash"></i></div>
            <div class="list-item__content">
              <h3>{{ t("settings.notify_away_only") }}</h3>
              <p>{{ t("settings.notify_away_only_desc") }}</p>
            </div>
            <div class="list-item__action">
              <div class="switch-wrapper">
                <input v-model="draft.notificationsAwayOnly" type="checkbox" />
                <span class="switch-visual"></span>
              </div>
            </div>
          </label>
        </div>
      </section>

      <section class="general-section">
        <div class="section-header">
          <h2 class="section-title">{{ t("settings.font_title") }}</h2>
          <p class="section-desc">{{ t("settings.font_desc") }}</p>
        </div>
        <div class="list-container">
          <div class="list-item slider-item">
            <div class="list-item__icon"><i class="fa-solid fa-font"></i></div>
            <div class="list-item__content">
              <div class="slider-field">
                <div class="slider-field__head">
                  <h3>{{ t("settings.font_family") }}</h3>
                  <span class="meta-value">{{ fontFamilyLabel }}</span>
                </div>
                <p>{{ fontFamilyOptions[fontFamilyIndex]?.detail }}</p>
                <div class="segmented-control" role="radiogroup" :aria-label="t('settings.font_family')">
                  <button
                    v-for="option in fontFamilyOptions"
                    :key="option.value"
                    type="button"
                    class="segmented-option"
                    :class="{ active: draft.fontFamily === option.value }"
                    role="radio"
                    :aria-checked="draft.fontFamily === option.value"
                    @click="draft.fontFamily = option.value"
                  >{{ option.label }}</button>
                </div>
              </div>
            </div>
          </div>
          <div class="list-item slider-item">
            <div class="list-item__icon"><i class="fa-solid fa-text-height"></i></div>
            <div class="list-item__content">
              <div class="slider-field">
                <div class="slider-field__head">
                  <h3>{{ t("settings.font_size") }}</h3>
                  <span class="meta-value">{{ fontSizeLabel }}</span>
                </div>
                <p>{{ fontSizeOptions[fontSizeIndex]?.detail }}</p>
                <div class="segmented-control" role="radiogroup" :aria-label="t('settings.font_size')">
                  <button
                    v-for="option in fontSizeOptions"
                    :key="option.value"
                    type="button"
                    class="segmented-option"
                    :class="{ active: draft.fontSize === option.value }"
                    role="radio"
                    :aria-checked="draft.fontSize === option.value"
                    @click="draft.fontSize = option.value"
                  >{{ option.label }}</button>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <div class="list-container vertical" style="margin-top: 16px;">
          <div class="font-preview">
            <div class="font-preview__header">
              <i class="fa-solid fa-eye"></i>
              <span class="meta-label">{{ t("settings.font_preview") }}</span>
            </div>
            <strong class="preview-text">{{ t("settings.font_preview_text") }}</strong>
            <code>runtime.trace_id = "general-settings"</code>
          </div>

          <label class="list-item switch-item">
            <div class="list-item__icon"><i class="fa-solid fa-person-running"></i></div>
            <div class="list-item__content">
              <h3>{{ t("settings.reduce_motion") }}</h3>
              <p>{{ t("settings.reduce_motion_desc") }}</p>
            </div>
            <div class="list-item__action">
              <div class="switch-wrapper">
                <input v-model="draft.reduceMotion" type="checkbox" />
                <span class="switch-visual"></span>
              </div>
            </div>
          </label>
        </div>
      </section>

      <section class="general-section">
        <div class="section-header">
          <h2 class="section-title">{{ t("settings.data_title") }}</h2>
          <p class="section-desc">{{ t("settings.data_desc") }}</p>
        </div>
        <div class="list-container">
          <button
            v-for="item in dataActions"
            :key="item.key"
            type="button"
            class="list-item action-list-item"
            @click="handleDataAction(item.key, item.title)"
          >
            <div class="list-item__icon"><i :class="item.icon"></i></div>
            <div class="list-item__content">
              <h3>{{ item.title }}</h3>
              <p>{{ item.detail }}</p>
            </div>
            <div class="list-item__arrow">
              <i class="fa-solid fa-chevron-right"></i>
            </div>
          </button>
        </div>
      </section>
    </div>

    <!-- Export Modal -->
    <NModal v-model:show="exportOpen" class="data-modal" :mask-closable="!exportLoading">
      <div class="dm-card">
        <div class="dm-head">
          <div class="dm-icon"><i class="fa-solid fa-box-archive"></i></div>
          <h3>{{ t("settings.data_backup") }}</h3>
          <button class="dm-close" :disabled="exportLoading" @click="exportOpen = false"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="dm-body">
          <template v-if="exportDone">
            <div class="dm-success">
              <i class="fa-solid fa-circle-check"></i>
              <span>{{ t("settings.dm_export_done") }}</span>
            </div>
          </template>
          <template v-else-if="exportLoading && exportTotal > 0">
            <p class="dm-info">{{ t("settings.dm_exporting") }}</p>
            <div class="dm-progress-wrap">
              <div class="dm-progress-bar">
                <div class="dm-progress-fill" :style="{ width: Math.round((exportProgress / exportTotal) * 100) + '%' }"></div>
              </div>
              <span class="dm-progress-text">{{ exportProgress }} / {{ exportTotal }}</span>
            </div>
          </template>
          <template v-else-if="exportLoading">
            <div class="dm-loading"><i class="fa-solid fa-spinner fa-spin"></i> {{ t("settings.dm_loading") }}</div>
          </template>
          <template v-else>
            <p class="dm-info">{{ t("settings.dm_export_info") }}</p>
            <div class="dm-stat">
              <span class="dm-stat-label">{{ t("settings.dm_session_count") }}</span>
              <strong>{{ exportSessionCount }}</strong>
            </div>
          </template>
        </div>
        <div v-if="!exportDone" class="dm-actions">
          <button class="action-btn" @click="exportOpen = false">{{ t("common.cancel") }}</button>
          <button class="action-btn primary" :disabled="exportLoading || exportSessionCount === 0" @click="doExport">
            <i v-if="exportLoading" class="fa-solid fa-spinner fa-spin"></i>
            {{ exportLoading ? t("settings.dm_exporting") : t("settings.dm_export_btn") }}
          </button>
        </div>
        <div v-else class="dm-actions">
          <button class="action-btn primary" @click="exportOpen = false">{{ t("common.close") }}</button>
        </div>
      </div>
    </NModal>

    <!-- Import Modal -->
    <NModal v-model:show="importOpen" class="data-modal" :mask-closable="!importLoading">
      <div class="dm-card">
        <div class="dm-head">
          <div class="dm-icon"><i class="fa-solid fa-file-import"></i></div>
          <h3>{{ t("settings.data_import") }}</h3>
          <button class="dm-close" :disabled="importLoading" @click="importOpen = false"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="dm-body">
          <template v-if="importResult">
            <div class="dm-success">
              <i class="fa-solid fa-circle-check"></i>
              <span>{{ t("settings.dm_import_done", { imported: importResult.imported_count, skipped: importResult.skipped_count }) }}</span>
            </div>
          </template>
          <template v-else>
            <p class="dm-info">{{ t("settings.dm_import_info") }}</p>
            <label class="dm-file-picker">
              <input type="file" accept=".json" @change="onImportFileChange">
              <div class="dm-file-area">
                <i class="fa-solid fa-cloud-arrow-up"></i>
                <span v-if="importFile">{{ importFile.name }}</span>
                <span v-else>{{ t("settings.dm_import_select") }}</span>
              </div>
            </label>
          </template>
        </div>
        <div v-if="!importResult" class="dm-actions">
          <button class="action-btn" @click="importOpen = false">{{ t("common.cancel") }}</button>
          <button class="action-btn primary" :disabled="!importFile || importLoading" @click="doImport">
            <i v-if="importLoading" class="fa-solid fa-spinner fa-spin"></i>
            {{ importLoading ? t("settings.dm_importing") : t("settings.dm_import_btn") }}
          </button>
        </div>
        <div v-else class="dm-actions">
          <button class="action-btn primary" @click="importOpen = false">{{ t("common.close") }}</button>
        </div>
      </div>
    </NModal>

    <!-- Cleanup Modal -->
    <NModal v-model:show="cleanupOpen" class="data-modal" :mask-closable="!cleanupLoading">
      <div class="dm-card">
        <div class="dm-head">
          <div class="dm-icon"><i class="fa-solid fa-broom"></i></div>
          <h3>{{ t("settings.data_cleanup") }}</h3>
          <button class="dm-close" :disabled="cleanupLoading" @click="cleanupOpen = false"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="dm-body">
          <template v-if="cleanupDone">
            <div class="dm-success">
              <i class="fa-solid fa-circle-check"></i>
              <span>{{ t("settings.dm_cleanup_done") }}</span>
            </div>
          </template>
          <template v-else>
            <p class="dm-info">{{ t("settings.dm_cleanup_info") }}</p>
            <div class="dm-field">
              <label class="dm-field-label">{{ t("settings.dm_cleanup_range") }}</label>
              <div class="dm-days-row">
                <button
                  v-for="opt in cleanupDaysOptions"
                  :key="opt.value"
                  class="dm-day-btn"
                  :class="{ active: cleanupDays === opt.value }"
                  @click="cleanupDays = opt.value; cleanupPreview = null"
                >{{ opt.label }}{{ opt.value ? t("settings.dm_days_suffix") : "" }}</button>
              </div>
            </div>
            <template v-if="cleanupPreview">
              <div class="dm-warning">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <span>{{ t("settings.dm_cleanup_preview", { retained: cleanupPreview.retained_count, delete: cleanupPreview.affected_count, total: cleanupPreview.total_sessions }) }}</span>
              </div>
              <div v-if="cleanupPreview.oldest_session" class="dm-date-range">
                <i class="fa-solid fa-calendar-range"></i>
                <span>{{ t("settings.dm_cleanup_date_range", { oldest: formatDateShort(cleanupPreview.oldest_session), newest: formatDateShort(cleanupPreview.newest_session) }) }}</span>
              </div>
            </template>
          </template>
        </div>
        <div v-if="!cleanupDone" class="dm-actions">
          <button class="action-btn" @click="cleanupOpen = false">{{ t("common.cancel") }}</button>
          <template v-if="!cleanupPreview">
            <button class="action-btn primary" :disabled="cleanupLoading" @click="doCleanupPreview">
              <i v-if="cleanupLoading" class="fa-solid fa-spinner fa-spin"></i>
              {{ t("settings.dm_cleanup_scan") }}
            </button>
          </template>
          <template v-else>
            <button class="action-btn danger" :disabled="cleanupLoading || cleanupPreview.affected_count === 0" @click="doCleanupConfirm">
              <i v-if="cleanupLoading" class="fa-solid fa-spinner fa-spin"></i>
              {{ cleanupLoading ? t("settings.dm_cleaning") : t("settings.dm_cleanup_confirm", { count: cleanupPreview.affected_count }) }}
            </button>
          </template>
        </div>
        <div v-else class="dm-actions">
          <button class="action-btn primary" @click="cleanupOpen = false">{{ t("common.close") }}</button>
        </div>
      </div>
    </NModal>

    <footer class="general-footer">
      <div class="footer-info">
        <h3>{{ isDirty ? t("settings.unsaved") : t("settings.synced") }}</h3>
        <p>{{ t("settings.local_note") }}</p>
      </div>
      <div class="footer-actions">
        <button type="button" class="action-btn" @click="resetSettings">
          <i class="fa-solid fa-rotate-left"></i>
          <span>{{ t("settings.reset") }}</span>
        </button>
        <button type="button" class="action-btn primary" :disabled="!isDirty" @click="saveSettings">
          <i class="fa-solid fa-check"></i>
          <span>{{ t("settings.save") }}</span>
        </button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.general-settings {
  /* Colors */
  --general-bg: #ffffff;
  --general-bg-subtle: #f8fafc;
  --general-bg-muted: #f1f5f9;
  --general-text-primary: #0f172a;
  --general-text-secondary: #475569;
  --general-text-tertiary: #64748b;
  --general-border: #e2e8f0;
  --general-border-hover: #cbd5e1;
  --general-accent: #111827;

  display: flex;
  flex-direction: column;
  gap: 24px;
  width: 100%;
  color: var(--general-text-primary);
  font-family: var(--app-font-family, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif);
  padding: 8px 0 32px 0;
}

:root[data-theme="dark"] .general-settings {
  --general-bg: #050505;
  --general-bg-subtle: #0b0b0b;
  --general-bg-muted: #1c1c1c;
  --general-text-primary: #f5f5f5;
  --general-text-secondary: #9a9a9a;
  --general-text-tertiary: #666666;
  --general-border: #1c1c1c;
  --general-border-hover: #333333;
  --general-accent: #ffffff;
}

/* Header */
.general-header {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--general-border);
}

.general-header__main {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  flex: 1;
  min-width: 320px;
}

.general-logo {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--general-bg-subtle);
  border: 1px solid var(--general-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: var(--general-text-primary);
}


.general-title-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.general-eyebrow {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--general-text-tertiary);
}

.general-title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 0 0 2px 0;
  color: var(--general-text-primary);
}

.general-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--general-text-tertiary);
  max-width: 600px;
}

.general-header__actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.save-state {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 16px;
  background: var(--general-bg-subtle);
  border: 1px solid var(--general-border);
  border-radius: 8px;
  text-align: right;
}

.save-state__label {
  font-size: 11px;
  font-weight: 600;
  color: var(--general-text-tertiary);
  text-transform: uppercase;
}

.save-state__value {
  font-size: 13px;
  font-weight: 600;
  color: var(--general-text-primary);
}

/* Meta Data */
.general-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 20px;
  padding-bottom: 32px;
  border-bottom: 1px solid var(--general-border);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--general-text-tertiary);
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
  color: var(--general-text-primary);
}

/* Notice */
.general-notice {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 10px;
  background: rgba(37, 99, 235, 0.06);
  color: #1d4ed8;
  font-size: 13px;
  line-height: 1.6;
}

:root[data-theme="dark"] .general-notice {
  color: #bfdbfe;
  background: rgba(37, 99, 235, 0.12);
}

/* Sections */
.general-sections {
  display: flex;
  flex-direction: column;
  gap: 40px;
}

.general-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

.section-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--general-text-primary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.section-desc {
  margin: 0;
  font-size: 13px;
  color: var(--general-text-secondary);
  line-height: 1.6;
  max-width: 600px;
}

/* Action Button */
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 500;
  color: var(--general-text-primary);
  background: var(--general-bg);
  border: 1px solid var(--general-border);
  border-radius: 8px;
  cursor: pointer;
  transition:
    background-color var(--motion-duration-fast) ease,
    border-color var(--motion-duration-fast) ease,
    color var(--motion-duration-fast) ease,
    transform var(--motion-duration-fast) var(--motion-ease-standard);
}

.action-btn:hover:not(:disabled) {
  background: var(--general-bg-subtle);
  border-color: var(--general-border-hover);
}

.action-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.action-btn.primary {
  background: var(--general-text-primary);
  color: var(--general-bg);
  border-color: var(--general-text-primary);
}

.action-btn.primary:hover:not(:disabled) {
  background: var(--general-text-secondary);
  border-color: var(--general-text-secondary);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* List Container */
.list-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}

.language-setting-container {
  max-width: 320px;
}

.list-container.vertical {
  grid-template-columns: 1fr;
}

.list-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
  border: 1px solid var(--general-border);
  border-radius: 12px;
  background: var(--general-bg);
  transition: border-color 0.2s ease;
}

.list-item:hover {
  border-color: var(--general-border-hover);
}

.list-item__icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--general-text-secondary);
  background: var(--general-bg-subtle);
  border-radius: 8px;
  font-size: 15px;
}

.list-item__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.list-item__content h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--general-text-primary);
}

.list-item__content p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--general-text-secondary);
}

.list-item__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

/* Specific Items */
.action-list-item {
  cursor: pointer;
  text-align: left;
  align-items: center;
}

.list-item__arrow {
  color: var(--general-text-tertiary);
  font-size: 12px;
  transition: transform 0.2s ease;
}

.action-list-item:hover .list-item__arrow {
  transform: translateX(4px);
  color: var(--general-text-primary);
}

.switch-item {
  cursor: pointer;
  align-items: center;
}

/* Switch Wrapper */
.switch-wrapper {
  position: relative;
  width: 44px;
  height: 24px;
}

.switch-wrapper input {
  position: absolute;
  opacity: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
  z-index: 2;
  margin: 0;
}

.switch-visual {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  border-radius: 999px;
  background: var(--general-border-hover);
  transition: background-color 0.2s ease;
  pointer-events: none;
}

.switch-visual::after {
  content: "";
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  transition: transform 0.2s ease;
}

.switch-wrapper input:checked + .switch-visual {
  background: var(--general-text-primary);
}

.switch-wrapper input:checked + .switch-visual::after {
  transform: translateX(20px);
}

/* Fields */
.select-field {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.field-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--general-text-primary);
}

.field-header p {
  margin: 0;
  font-size: 13px;
  color: var(--general-text-secondary);
}

/* Slider Field */
.slider-item {
  flex-direction: row;
}

.slider-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.slider-field__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.slider-field__head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--general-text-primary);
}

.slider-control {
  margin-top: 16px;
  padding: 0 12px 12px;
}

/* Segmented control for the 5 discrete font tiers. */
.segmented-control {
  display: flex;
  margin-top: 14px;
  padding: 4px;
  gap: 4px;
  border: 1px solid var(--general-border);
  border-radius: 10px;
  background: var(--general-bg-subtle);
}

.segmented-option {
  flex: 1;
  min-width: 0;
  padding: 8px 6px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--general-text-secondary);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition:
    background-color var(--motion-duration-normal) ease,
    color var(--motion-duration-normal) ease,
    box-shadow var(--motion-duration-normal) ease,
    transform var(--motion-duration-fast) var(--motion-ease-standard);
}

.segmented-option:active:not(.active) {
  transform: scale(0.98);
}

.segmented-option:hover:not(.active) {
  color: var(--general-text-primary);
  background: var(--general-bg-muted);
}

.segmented-option.active {
  background: var(--general-bg);
  color: var(--general-text-primary);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.12);
}

:root[data-theme="dark"] .segmented-option.active {
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}

/* Font Preview */
.font-preview {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border: 1px solid var(--general-border);
  border-radius: 12px;
  background: var(--general-bg-subtle);
  margin-top: 4px;
}

.font-preview__header {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--general-text-tertiary);
}

.preview-text {
  /* Whole-UI scaling is handled by Electron page zoom on desktop. */
  font-size: 16px;
  color: var(--general-text-primary);
  line-height: 1.5;
}

.font-preview code {
  width: fit-content;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--general-border);
  font-size: 12px;
  color: var(--general-text-secondary);
}

/* Footer */
.general-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-top: 32px;
  border-top: 1px solid var(--general-border);
  flex-wrap: wrap;
}

.footer-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.footer-info h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--general-text-primary);
}

.footer-info p {
  margin: 0;
  font-size: 13px;
  color: var(--general-text-secondary);
}

.footer-actions {
  display: flex;
  gap: 12px;
}

/* Naive UI Overrides */
:deep(.n-slider) {
  --n-rail-height: 6px !important;
  --n-handle-size: 20px !important;
  --n-fill-color: var(--general-text-primary) !important;
  --n-fill-color-hover: var(--general-text-primary) !important;
  --n-handle-color: var(--general-bg) !important;
  --n-handle-border-color: var(--general-text-primary) !important;
  --n-handle-border-color-hover: var(--general-text-primary) !important;
  --n-handle-border-color-focus: var(--general-text-primary) !important;
  --n-handle-box-shadow-hover: 0 1px 4px rgba(0, 0, 0, 0.15) !important;
  --n-handle-box-shadow-active: 0 1px 4px rgba(0, 0, 0, 0.15) !important;
  --n-handle-box-shadow-focus: 0 0 0 3px rgba(15, 23, 42, 0.15) !important;
}

:root[data-theme="dark"] :deep(.n-slider) {
  --n-handle-box-shadow-focus: 0 0 0 3px rgba(248, 250, 252, 0.15) !important;
}

:deep(.n-slider-mark) {
  font-size: 13px;
  font-weight: 500;
  color: var(--general-text-tertiary);
  margin-top: 10px;
}

:deep(.n-slider-rail) {
  background: var(--general-bg-muted) !important;
}

:deep(.n-slider-handle-indicator) {
  font-size: 14px !important;
  padding: 6px 10px !important;
  border-radius: 8px !important;
  background-color: var(--general-text-primary) !important;
  color: var(--general-bg) !important;
  font-weight: 600 !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
}

@media (max-width: 640px) {
  .general-header__main {
    flex-direction: column;
    align-items: flex-start;
  }

  .general-footer {
    flex-direction: column;
    align-items: flex-start;
  }

  .footer-actions {
    width: 100%;
  }

  .footer-actions button {
    flex: 1;
    justify-content: center;
  }
}

/* Data Management Modals */
:deep(.data-modal) {
  width: min(480px, calc(100vw - 40px));
}

.dm-card {
  border-radius: 16px;
  background: var(--general-bg, #fff);
  color: var(--general-text-primary, #0f172a);
  border: 1px solid var(--general-border, #e2e8f0);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
  overflow: hidden;
}

:root[data-theme="dark"] .dm-card {
  background: var(--surface, #050505);
  border-color: var(--border, #1c1c1c);
  color: var(--text, #f5f5f5);
}

.dm-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--general-border, #e2e8f0);
}

.dm-head h3 {
  margin: 0;
  flex: 1;
  font-size: 16px;
  font-weight: 600;
}

.dm-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--surface-strong, #eef2f7);
  font-size: 16px;
}

:root[data-theme="dark"] .dm-icon {
  background: var(--border, #1c1c1c);
  color: var(--text, #f5f5f5);
}

.dm-close {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--general-text-tertiary, #64748b);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dm-close:hover { color: var(--general-text-primary, #0f172a); background: var(--general-bg-muted, #f1f5f9); }

.dm-body {
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dm-info {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-soft, #6b7280);
}

:root[data-theme="dark"] .dm-info {
  color: var(--muted, #9a9a9a);
}

.dm-stat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--surface-soft, #f8fafc);
  border: 1px solid var(--border-strong, #e2e8f0);
}

:root[data-theme="dark"] .dm-stat {
  background: var(--panel, #0b0b0b);
  border-color: var(--border, #1c1c1c);
}

.dm-stat-label {
  font-size: 13px;
  color: var(--text-soft, #6b7280);
}

:root[data-theme="dark"] .dm-stat-label {
  color: var(--muted, #9a9a9a);
}

.dm-stat strong {
  font-size: 20px;
  font-weight: 700;
}

.dm-progress-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0;
}

.dm-progress-bar {
  height: 8px;
  border-radius: 4px;
  background: var(--border-strong, #e2e8f0);
  overflow: hidden;
}

:root[data-theme="dark"] .dm-progress-bar {
  background: var(--border, #1c1c1c);
}

.dm-progress-fill {
  height: 100%;
  border-radius: 4px;
  background: #3b82f6;
  transition: width 0.3s ease;
}

.dm-progress-text {
  font-size: 12px;
  color: var(--text-soft, #6b7280);
  text-align: right;
}

:root[data-theme="dark"] .dm-progress-text {
  color: var(--muted, #9a9a9a);
}

.dm-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 0;
  justify-content: center;
  color: var(--text-soft, #6b7280);
  font-size: 13px;
}

:root[data-theme="dark"] .dm-loading {
  color: var(--muted, #9a9a9a);
}

.dm-success {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-radius: 10px;
  background: rgba(22, 163, 74, 0.08);
  color: #16a34a;
  font-size: 13px;
  font-weight: 500;
}

:root[data-theme="dark"] .dm-success {
  background: rgba(22, 163, 74, 0.15);
  color: #86efac;
}

.dm-warning {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 16px;
  border-radius: 10px;
  background: rgba(234, 179, 8, 0.08);
  color: #a16207;
  font-size: 13px;
  line-height: 1.5;
}

:root[data-theme="dark"] .dm-warning {
  background: rgba(234, 179, 8, 0.12);
  color: #fde68a;
}

.dm-warning i { margin-top: 2px; flex-shrink: 0; }

.dm-date-range {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-radius: 10px;
  background: rgba(59, 130, 246, 0.08);
  color: #1d4ed8;
  font-size: 13px;
  line-height: 1.5;
}

:root[data-theme="dark"] .dm-date-range {
  background: rgba(59, 130, 246, 0.12);
  color: #93c5fd;
}

.dm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 22px;
  border-top: 1px solid var(--border-strong, #e2e8f0);
}

:root[data-theme="dark"] .dm-actions {
  border-top-color: var(--border, #1c1c1c);
}

.action-btn.danger {
  background: #dc2626;
  color: #fff;
  border-color: #dc2626;
}

.action-btn.danger:hover:not(:disabled) {
  background: #b91c1c;
  border-color: #b91c1c;
}

.dm-file-picker {
  display: block;
  cursor: pointer;
}

.dm-file-picker input { display: none; }

.dm-file-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 16px;
  border: 2px dashed var(--border-strong, #cbd5e1);
  border-radius: 12px;
  color: var(--text-soft, #6b7280);
  font-size: 13px;
  transition: border-color 0.2s, background 0.2s;
}

:root[data-theme="dark"] .dm-file-area {
  border-color: var(--border, #1c1c1c);
  color: var(--muted, #9a9a9a);
}

.dm-file-area:hover {
  border-color: var(--text, #0f172a);
  background: var(--surface-soft, #f8fafc);
}

:root[data-theme="dark"] .dm-file-area:hover {
  border-color: var(--text, #f5f5f5);
  background: var(--panel, #0b0b0b);
}

.dm-file-area i { font-size: 24px; }

.dm-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dm-field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-soft, #6b7280);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

:root[data-theme="dark"] .dm-field-label {
  color: var(--muted, #9a9a9a);
}

.dm-days-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dm-day-btn {
  padding: 6px 14px;
  border: 1px solid var(--border-strong, #e2e8f0);
  border-radius: 8px;
  background: var(--surface, #fff);
  color: var(--text, #0f172a);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

:root[data-theme="dark"] .dm-day-btn {
  border-color: var(--border, #1c1c1c);
  background: var(--surface, #050505);
  color: var(--text, #f5f5f5);
}

.dm-day-btn:hover { border-color: var(--text-soft, #cbd5e1); }

:root[data-theme="dark"] .dm-day-btn:hover { border-color: var(--muted, #9a9a9a); }

.dm-day-btn.active {
  background: var(--text, #0f172a);
  color: var(--surface, #fff);
  border-color: var(--text, #0f172a);
}

:root[data-theme="dark"] .dm-day-btn.active {
  background: var(--text, #f5f5f5);
  color: var(--surface, #050505);
  border-color: var(--text, #f5f5f5);
}
</style>
