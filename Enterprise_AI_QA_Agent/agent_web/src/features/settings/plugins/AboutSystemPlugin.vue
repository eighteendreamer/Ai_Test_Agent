<script setup lang="ts">
import { ref } from "vue";
import { NModal } from "naive-ui";

import type { SettingsPluginDefinition } from "../plugins";
import { t } from "../../../services/i18n";

defineProps<{
  plugin?: SettingsPluginDefinition;
}>();

const copyrightPreviewOpen = ref(false);
const copyrightPreviewSrc = "/about/software-copyright.jpg";
const copyrightPdfSrc = "/about/software-copyright.pdf";

const copyrightFacts = [
  { labelKey: "about.copyright_field_name", valueKey: "about.copyright_name" },
  { labelKey: "about.copyright_field_alias", valueKey: "about.copyright_alias" },
  { labelKey: "about.copyright_field_version", valueKey: "about.copyright_version" },
  { labelKey: "about.copyright_field_owners", valueKey: "about.copyright_owners" },
  { labelKey: "about.copyright_field_method", valueKey: "about.copyright_method" },
  { labelKey: "about.copyright_field_scope", valueKey: "about.copyright_scope" },
  { labelKey: "about.copyright_field_reg_no", valueKey: "about.copyright_reg_no" },
  { labelKey: "about.copyright_field_cert_no", valueKey: "about.copyright_cert_no" },
  { labelKey: "about.copyright_field_date", valueKey: "about.copyright_date" },
];

const repoUrl = "https://github.com/eighteendreamer/Ai_Test_Agent";
const issuesUrl = `${repoUrl}/issues`;
const isDesktopBuild = import.meta.env.VITE_QA_AGENT_DESKTOP === "1";
const docsUrl = isDesktopBuild ? "/docs/" : "http://localhost:5173/";
const docsTarget = isDesktopBuild ? "_self" : "_blank";

const stackItems = [
  {
    icon: "fa-brands fa-python",
    title: t("about.stack_backend_title"),
    description: t("about.stack_backend_desc"),
  },
  {
    icon: "fa-brands fa-vuejs",
    title: t("about.stack_frontend_title"),
    description: t("about.stack_frontend_desc"),
  },
  {
    icon: "fa-solid fa-diagram-project",
    title: t("about.stack_arch_title"),
    description: t("about.stack_arch_desc"),
  },
  {
    icon: "fa-solid fa-database",
    title: t("about.stack_eng_title"),
    description: t("about.stack_eng_desc"),
  },
];

const highlights = [
  t("about.highlight_1"),
  t("about.highlight_2"),
  t("about.highlight_3"),
];

const infoCards = [
  {
    icon: "fa-solid fa-user-astronaut",
    title: t("about.author_title"),
    value: "程序员Eighteen",
    detail: t("about.author_detail"),
  },
  {
    icon: "fa-solid fa-scale-balanced",
    title: t("about.license_title"),
    value: t("about.license_value"),
    detail: t("about.license_detail"),
  },
  {
    icon: "fa-solid fa-comment-dots",
    title: t("about.feedback_title"),
    value: "GitHub Issues",
    detail: t("about.feedback_detail"),
  },
  {
    icon: "fa-solid fa-book-open",
    title: t("about.docs_title"),
    value: "README / docs",
    detail: t("about.docs_detail"),
  },
];
</script>

<template>
  <section class="about-system">
    <header class="about-header">
      <div class="about-header__main">
        <div class="about-logo">
          <img src="/logo.svg" alt="" class="about-logo__img" />
        </div>
        <div class="about-title-wrapper">
          <h1 class="about-title">{{ t("about.title") }}</h1>
          <p class="about-desc">
        {{ t("about.desc") }}
      </p>
        </div>
      </div>
      
      <div class="about-header__actions">
        <a class="action-btn primary" :href="repoUrl" target="_blank" rel="noreferrer">
          <i class="fa-brands fa-github"></i>
          <span>{{ t("about.repo") }}</span>
        </a>
        <a class="action-btn" :href="issuesUrl" target="_blank" rel="noreferrer">
          <i class="fa-solid fa-bug"></i>
          <span>{{ t("about.feedback") }}</span>
        </a>
        <a class="action-btn" :href="docsUrl" :target="docsTarget" rel="noreferrer">
          <i class="fa-solid fa-book-open"></i>
          <span>{{ t("about.read_docs") }}</span>
        </a>
      </div>
    </header>

    <div class="about-meta">
      <div class="meta-item">
        <span class="meta-label">{{ t("about.meta_position") }}</span>
        <span class="meta-value">QA Agent Workbench</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">{{ t("about.meta_frontend") }}</span>
        <span class="meta-value">Vue 3 + Vite</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">{{ t("about.meta_backend") }}</span>
        <span class="meta-value">FastAPI + LangGraph</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">{{ t("about.meta_repo") }}</span>
        <span class="meta-value">GitHub / Ai_Test_Agent</span>
      </div>
    </div>

    <div class="about-sections">
      <section class="about-section">
        <h2 class="section-title">{{ t("about.section_features") }}</h2>
        <div class="feature-list">
          <div class="feature-item" v-for="item in highlights" :key="item">
            <i class="fa-solid fa-check feature-icon"></i>
            <span>{{ item }}</span>
          </div>
        </div>
        <div class="badge-row">
          <span class="badge"><i class="fa-solid fa-wand-magic-sparkles"></i> Runtime-first</span>
          <span class="badge"><i class="fa-solid fa-shield-halved"></i> Harness-driven</span>
          <span class="badge"><i class="fa-solid fa-sitemap"></i> Multi-agent Ready</span>
        </div>
      </section>

      <section class="about-section">
        <h2 class="section-title">{{ t("about.section_architecture") }}</h2>
        <div class="list-container">
          <div class="list-item" v-for="item in stackItems" :key="item.title">
            <div class="list-item__icon"><i :class="item.icon"></i></div>
            <div class="list-item__content">
              <h3>{{ item.title }}</h3>
              <p>{{ item.description }}</p>
            </div>
          </div>
        </div>
      </section>

      <section class="about-section">
        <h2 class="section-title">{{ t("about.section_copyright") }}</h2>
        <div class="copyright-card">
          <button type="button" class="copyright-preview" @click="copyrightPreviewOpen = true">
            <img :src="copyrightPreviewSrc" :alt="t('about.copyright_preview_alt')">
          </button>
          <div class="copyright-body">
            <dl class="copyright-facts">
              <div v-for="fact in copyrightFacts" :key="fact.labelKey">
                <dt>{{ t(fact.labelKey) }}</dt>
                <dd>{{ t(fact.valueKey) }}</dd>
              </div>
            </dl>
            <div class="copyright-actions">
              <button type="button" class="action-btn primary" @click="copyrightPreviewOpen = true">
                <i class="fa-solid fa-certificate"></i>
                <span>{{ t("about.copyright_view") }}</span>
              </button>
              <a class="action-btn" :href="copyrightPdfSrc" target="_blank" rel="noreferrer">
                <i class="fa-solid fa-file-pdf"></i>
                <span>{{ t("about.copyright_open_pdf") }}</span>
              </a>
            </div>
          </div>
        </div>
      </section>

      <section class="about-section">
        <h2 class="section-title">{{ t("about.section_feedback") }}</h2>
        <div class="list-container">
          <div class="list-item" v-for="item in infoCards" :key="item.title">
            <div class="list-item__icon"><i :class="item.icon"></i></div>
            <div class="list-item__content">
              <div class="list-item__header">
                <h3>{{ item.title }}</h3>
                <span class="list-item__value">{{ item.value }}</span>
              </div>
              <p>{{ item.detail }}</p>
            </div>
          </div>
        </div>
      </section>
    </div>

    <NModal v-model:show="copyrightPreviewOpen">
      <div class="copyright-modal">
        <header>
          <h3>{{ t("about.copyright_preview_alt") }}</h3>
          <button type="button" class="copyright-modal__close" @click="copyrightPreviewOpen = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </header>
        <img :src="copyrightPreviewSrc" :alt="t('about.copyright_preview_alt')">
        <a class="action-btn" :href="copyrightPdfSrc" target="_blank" rel="noreferrer">
          <i class="fa-solid fa-file-pdf"></i>
          <span>{{ t("about.copyright_open_pdf") }}</span>
        </a>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.about-system {
  /* Colors */
  --about-bg: #ffffff;
  --about-bg-subtle: #f8fafc;
  --about-bg-muted: #f1f5f9;
  --about-text-primary: #0f172a;
  --about-text-secondary: #475569;
  --about-text-tertiary: #64748b;
  --about-border: #e2e8f0;
  --about-border-hover: #cbd5e1;

  display: flex;
  flex-direction: column;
  gap: 24px;
  width: 100%;
  color: var(--about-text-primary);
  font-family: var(--app-font-family, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif);
  padding: 8px 0 32px 0;
}

:root[data-theme="dark"] .about-system {
  --about-bg: #050505;
  --about-bg-subtle: #0b0b0b;
  --about-bg-muted: #1c1c1c;
  --about-text-primary: #f5f5f5;
  --about-text-secondary: #9a9a9a;
  --about-text-tertiary: #666666;
  --about-border: #1c1c1c;
  --about-border-hover: #333333;
}

/* Header */
.about-header {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--about-border);
}

.about-header__main {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  flex: 1;
  min-width: 320px;
}

.about-logo {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--about-bg-muted);
  border: 1px solid var(--about-border);
  border-radius: 8px;
}

.about-logo__img {
  width: 22px;
  height: 22px;
  object-fit: contain;
}

:root[data-theme="dark"] .about-logo__img {
  filter: brightness(0) invert(1);
}

.about-title-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.about-eyebrow {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--about-text-tertiary);
}

.about-title {
  margin: 0 0 2px 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--about-text-primary);
  line-height: 1.2;
}

.about-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--about-text-secondary);
  max-width: 600px;
}

.about-header__actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 500;
  color: var(--about-text-primary);
  background: var(--about-bg);
  border: 1px solid var(--about-border);
  border-radius: 8px;
  text-decoration: none;
  transition: all 0.2s ease;
}

.action-btn:hover {
  background: var(--about-bg-subtle);
  border-color: var(--about-border-hover);
}

.action-btn.primary {
  background: var(--about-text-primary);
  color: var(--about-bg);
  border-color: var(--about-text-primary);
}

.action-btn.primary:hover {
  background: var(--about-text-secondary);
  border-color: var(--about-text-secondary);
}

/* Meta Data */
.about-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 20px;
  padding-bottom: 32px;
  border-bottom: 1px solid var(--about-border);
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
  color: var(--about-text-tertiary);
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
  color: var(--about-text-primary);
}

/* Sections */
.about-sections {
  display: flex;
  flex-direction: column;
  gap: 40px;
}

.about-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--about-text-primary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

/* Feature List */
.feature-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.feature-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--about-text-secondary);
}

.feature-icon {
  margin-top: 4px;
  font-size: 12px;
  color: var(--about-text-tertiary);
}

.badge-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--about-text-secondary);
  background: var(--about-bg-subtle);
  border: 1px solid var(--about-border);
  border-radius: 6px;
}

.badge i {
  font-size: 11px;
  opacity: 0.8;
}

/* List Container */
.list-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.list-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
  border: 1px solid var(--about-border);
  border-radius: 12px;
  background: var(--about-bg);
  transition: border-color 0.2s ease;
}

.list-item:hover {
  border-color: var(--about-border-hover);
}

.list-item__icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--about-text-secondary);
  background: var(--about-bg-subtle);
  border-radius: 8px;
  font-size: 15px;
}

.list-item__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.list-item__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.list-item__content h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--about-text-primary);
}

.list-item__value {
  font-size: 13px;
  font-weight: 500;
  color: var(--about-text-primary);
}

.list-item__content p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--about-text-secondary);
}

.copyright-card {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 20px;
  padding: 16px;
  border: 1px solid var(--about-border);
  border-radius: 12px;
  background: var(--about-bg);
}

.copyright-preview {
  display: block;
  padding: 0;
  border: 1px solid var(--about-border);
  border-radius: 8px;
  background: var(--about-bg-subtle);
  overflow: hidden;
  cursor: zoom-in;
}

.copyright-preview img {
  display: block;
  width: 100%;
  height: auto;
}

.copyright-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.copyright-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px 16px;
  margin: 0;
}

.copyright-facts div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.copyright-facts dt {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--about-text-tertiary);
}

.copyright-facts dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--about-text-primary);
  word-break: break-word;
}

.copyright-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.copyright-modal {
  width: min(720px, 92vw);
  max-height: 88vh;
  overflow: auto;
  padding: 18px;
  border-radius: 14px;
  background: var(--about-bg, #fff);
}

.copyright-modal header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.copyright-modal h3 {
  margin: 0;
  font-size: 15px;
}

.copyright-modal__close {
  width: 32px;
  height: 32px;
  border: 1px solid var(--about-border, #e2e8f0);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.copyright-modal img {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid var(--about-border, #e2e8f0);
  border-radius: 8px;
  margin-bottom: 12px;
}

@media (max-width: 640px) {
  .about-header__main {
    flex-direction: column;
    align-items: flex-start;
  }
  
  .list-item__header {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .copyright-card {
    grid-template-columns: 1fr;
  }

  .copyright-preview {
    max-width: 220px;
  }
}
</style>
