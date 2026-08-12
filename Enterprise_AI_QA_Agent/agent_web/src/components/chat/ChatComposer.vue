<script setup lang="ts">
import type { KeyboardEvent } from "vue";
import { computed, ref } from "vue";
import { NDropdown, useMessage } from "naive-ui";

import { api } from "../../services/api";
import { useSessionStore } from "../../stores/session";
import { t } from "../../services/i18n";
import type { InputAttachment, UploadedAttachmentRecord } from "../../types";

const props = defineProps<{
  docked?: boolean;
}>();

const message = useMessage();
const sessionStore = useSessionStore();
const draft = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const pendingAttachments = ref<InputAttachment[]>([]);

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const MAX_FILES_COUNT = 5;
const ALLOWED_FILE_TYPES = [
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/markdown",
  "application/json",
  "image/png",
];

const ALLOWED_EXTENSIONS = [".doc", ".docx", ".xls", ".xlsx", ".md", ".json", ".png"];

const dockedPlaceholder = computed(() => t("home.placeholder_docked"));
const heroPlaceholder = computed(() => t("home.placeholder"));
const busyTitle = computed(() => t("home.busy"));
const idleTitle = computed(() => t("home.idle"));
const placeholder = computed(() => {
  if (sessionStore.selectedModeKey === "compatibility_testing") {
    return "直接发送测试需求，例如：这是 Android App，产品名叫测试，版本 1.0.0；按 Enter 发送";
  }
  return props.docked ? dockedPlaceholder.value : heroPlaceholder.value;
});
const buttonTitle = computed(() => (sessionStore.isBusy ? busyTitle.value : idleTitle.value));
function translatedModeLabel(modeKey?: string | null, fallback?: string | null) {
  const normalizedKey = String(modeKey || "").trim();
  if (!normalizedKey) {
    return fallback || t("mode.default");
  }
  const translationKey = `mode.${normalizedKey}`;
  const translated = t(translationKey);
  return translated === translationKey ? fallback || normalizedKey : translated;
}

const activeModeLabel = computed(() =>
  translatedModeLabel(sessionStore.activeMode?.key, sessionStore.activeMode?.name ?? t("mode.default")),
);
const activeModeSummary = computed(() => sessionStore.activeMode?.summary ?? t("mode.select"));
const modeOptions = computed(() =>
  sessionStore.modes.map((mode) => ({
    label: mode.placeholder
      ? `${translatedModeLabel(mode.key, mode.name)} (${t("mode.placeholder_suffix")})`
      : translatedModeLabel(mode.key, mode.name),
    key: mode.key,
  })),
);

function handleModeSelect(key: string | number) {
  sessionStore.setModeKey(String(key));
}

function openFilePicker() {
  if (!uploading.value) {
    fileInput.value?.click();
  }
}

function removeAttachment(index: number) {
  pendingAttachments.value.splice(index, 1);
}

function toInputAttachment(doc: UploadedAttachmentRecord): InputAttachment {
  return {
    kind: "file",
    name: doc.filename,
    uri: doc.storage_uri,
    content_type: doc.content_type,
    text_excerpt: doc.preview_text ? doc.preview_text.slice(0, 1200) : null,
    metadata: {
      attachment_id: doc.id,
      source: String(doc.metadata?.source || "chat_attachment"),
      format_label: "会话附件",
      uploaded_at: doc.uploaded_at,
      preview_truncated: doc.preview_truncated,
      size_bytes: doc.size_bytes,
      security: doc.metadata?.security ?? null,
    },
  };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",")[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length) {
    return;
  }

  if (pendingAttachments.value.length + files.length > MAX_FILES_COUNT) {
    message.warning(t("home.file_count_limit", { max: String(MAX_FILES_COUNT), current: String(pendingAttachments.value.length) }));
    return;
  }

  const invalidFiles: string[] = [];
  const oversizedFiles: string[] = [];
  const validFiles: File[] = [];

  for (const file of files) {
    if (file.size > MAX_FILE_SIZE) {
      oversizedFiles.push(`${file.name} (${formatAttachmentSize(file.size)})`);
      continue;
    }

    const extension = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
    const isValidType =
      ALLOWED_FILE_TYPES.includes(file.type) || ALLOWED_EXTENSIONS.includes(extension);
    if (!isValidType) {
      invalidFiles.push(file.name);
      continue;
    }

    validFiles.push(file);
  }

  if (oversizedFiles.length) {
    message.error(`以下文件超过 ${formatAttachmentSize(MAX_FILE_SIZE)} 限制：${oversizedFiles.join("，")}`);
  }
  if (invalidFiles.length) {
    message.error(`以下文件类型暂不支持：${invalidFiles.join("，")}`);
  }
  if (!validFiles.length) {
    return;
  }

  uploading.value = true;
  try {
    const uploadedDocs: UploadedAttachmentRecord[] = [];
    for (const file of validFiles) {
      const contentBase64 = await fileToBase64(file);
      const uploaded = await api.uploadAttachment({
        filename: file.name,
        content_base64: contentBase64,
        source: "chat_attachment",
      });
      uploadedDocs.push(uploaded);
    }

    const existingUris = new Set(
      pendingAttachments.value.map((item) => String(item.uri || "")),
    );
    for (const attachment of uploadedDocs.map(toInputAttachment)) {
      const uri = String(attachment.uri || "");
      if (!uri || existingUris.has(uri)) {
        continue;
      }
      pendingAttachments.value.push(attachment);
      existingUris.add(uri);
    }

    message.success(t("home.upload_success", { count: String(uploadedDocs.length) }), { duration: 2000 });
  } catch (error) {
    message.error(error instanceof Error ? error.message : t("home.upload_fail"));
  } finally {
    uploading.value = false;
  }
}

async function handleSubmit() {
  if (sessionStore.isBusy || !sessionStore.session) {
    return;
  }

  const content = draft.value;
  const attachments = JSON.parse(JSON.stringify(pendingAttachments.value)) as InputAttachment[];
  if (!content.trim() && attachments.length === 0) {
    return;
  }

  draft.value = "";
  pendingAttachments.value = [];

  try {
    await sessionStore.sendMessage(content, attachments);
  } catch (error) {
    draft.value = content;
    pendingAttachments.value = attachments;
    console.error("发送消息失败", error);
    message.error(error instanceof Error ? error.message : "发送消息失败");
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }
  event.preventDefault();
  void handleSubmit();
}

function formatAttachmentSize(value: unknown) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) {
    return "";
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} KB`.replace("KB", "MB");
  }
  if (size >= 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size} B`;
}
</script>

<template>
  <div class="home-composer" :class="{ 'home-composer-docked': docked }">
    <div v-if="pendingAttachments.length" class="home-attachments">
      <div
        v-for="(attachment, index) in pendingAttachments"
        :key="`${attachment.uri || attachment.name}-${index}`"
        class="home-attachment-chip"
      >
        <div class="home-attachment-main">
          <i class="fa-solid fa-file-lines"></i>
          <div class="home-attachment-copy">
            <strong>{{ attachment.name }}</strong>
            <span>
              {{ String(attachment.metadata?.format_label || "会话附件") }}
              <template v-if="formatAttachmentSize(attachment.metadata?.size_bytes)">
                · {{ formatAttachmentSize(attachment.metadata?.size_bytes) }}
              </template>
            </span>
          </div>
        </div>
        <button
          class="home-attachment-remove"
          type="button"
          title="移除附件"
          @click="removeAttachment(index)"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <textarea
      v-model="draft"
      class="home-textarea"
      :placeholder="placeholder"
      @keydown="handleKeydown"
    />

    <div class="home-composer-footer">
      <div class="home-toolbar">
        <button class="home-tool-btn" type="button" :disabled="uploading" @click="openFilePicker">
          <i class="fa-solid" :class="uploading ? 'fa-spinner fa-spin' : 'fa-paperclip'"></i>
          {{ uploading ? "上传中..." : "Attachments" }}
        </button>
        <input
          ref="fileInput"
          class="home-file-input"
          type="file"
          multiple
          :accept="ALLOWED_EXTENSIONS.join(',')"
          @change="handleFileChange"
        >
        <n-dropdown
          trigger="click"
          :placement="docked ? 'top-start' : 'bottom-start'"
          :options="modeOptions"
          @select="handleModeSelect"
        >
          <button class="home-tool-btn home-mode-btn" type="button" :title="activeModeSummary">
            <i class="fa-solid fa-sitemap"></i>
            {{ activeModeLabel }}
            <i class="fa-solid fa-chevron-down home-mode-caret"></i>
          </button>
        </n-dropdown>
      </div>

      <div class="home-send-group">
        <button
          class="home-send-btn"
          :disabled="sessionStore.isBusy || !sessionStore.session || (!draft.trim() && pendingAttachments.length === 0)"
          :title="buttonTitle"
          type="button"
          @click="handleSubmit"
        >
          <i class="fa-solid" :class="sessionStore.isBusy ? 'fa-spinner fa-spin' : 'fa-arrow-up'"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-file-input {
  display: none;
}

.home-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 12px 20px 8px;
  margin: 0;
}

.home-attachment-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-width: 200px;
  max-width: 320px;
  padding: 8px 10px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.96);
}

.home-attachment-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.home-attachment-main i {
  color: #2563eb;
}

.home-attachment-copy {
  min-width: 0;
}

.home-attachment-copy strong,
.home-attachment-copy span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-attachment-copy strong {
  font-size: 13px;
  color: #0f172a;
}

.home-attachment-copy span {
  margin-top: 1px;
  font-size: 11px;
  color: #64748b;
}

.home-attachment-remove {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease;
}

.home-attachment-remove:hover {
  background: rgba(15, 23, 42, 0.06);
  color: #0f172a;
}
</style>
