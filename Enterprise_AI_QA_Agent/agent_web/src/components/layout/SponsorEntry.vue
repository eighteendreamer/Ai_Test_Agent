<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NCarousel, NModal } from "naive-ui";

import { api } from "../../services/api";
import { t } from "../../services/i18n";
import type { SponsorRecord } from "../../types";

const sponsors = ref<SponsorRecord[]>([]);
const listOpen = ref(false);

onMounted(async () => {
  try {
    sponsors.value = await api.listSponsors();
  } catch (error) {
    console.warn("[Sponsors] Failed to load sponsor list:", error);
    sponsors.value = [];
  }
});

function openWebsite(sponsor: SponsorRecord) {
  window.open(sponsor.website_url, "_blank", "noopener,noreferrer");
}
</script>

<template>
  <div v-if="sponsors.length" class="sponsor-entry">
    <NCarousel
      class="sponsor-carousel"
      autoplay
      effect="fade"
      :show-dots="false"
      :draggable="false"
      :interval="4000"
    >
      <button
        v-for="sponsor in sponsors"
        :key="sponsor.id"
        type="button"
        class="sponsor-slide"
        :title="`${sponsor.name} · ${sponsor.website_url}`"
        @click="openWebsite(sponsor)"
      >
        <span class="sponsor-logo-chip">
          <img :src="sponsor.logo_url" :alt="sponsor.name" class="sponsor-logo" />
        </span>
      </button>
    </NCarousel>
    <button
      type="button"
      class="sponsor-list-btn"
      :title="t('sponsors.title')"
      @click="listOpen = true"
    >
      <i class="fa-solid fa-list"></i>
    </button>

    <NModal v-model:show="listOpen">
      <div class="sponsor-modal">
        <header class="sponsor-modal__header">
          <h3>{{ t("sponsors.title") }}</h3>
          <button type="button" class="sponsor-modal__close" @click="listOpen = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </header>
        <div class="sponsor-modal__list">
          <div v-for="sponsor in sponsors" :key="sponsor.id" class="sponsor-card">
            <span class="sponsor-logo-chip sponsor-logo-chip--lg">
              <img :src="sponsor.logo_url" :alt="sponsor.name" class="sponsor-logo" />
            </span>
            <div class="sponsor-card__body">
              <div class="sponsor-card__head">
                <strong>{{ sponsor.name }}</strong>
                <span v-if="sponsor.sponsor_type" class="sponsor-card__type">{{ sponsor.sponsor_type }}</span>
              </div>
              <span class="sponsor-card__url">{{ sponsor.website_url }}</span>
              <p v-if="sponsor.description" class="sponsor-card__desc">{{ sponsor.description }}</p>
            </div>
            <button type="button" class="sponsor-card__visit" @click="openWebsite(sponsor)">
              <i class="fa-solid fa-arrow-up-right-from-square"></i>
              <span>{{ t("sponsors.visit") }}</span>
            </button>
          </div>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.sponsor-entry {
  /* Colors */
  --sponsor-bg: #ffffff;
  --sponsor-bg-subtle: #f8fafc;
  --sponsor-text-primary: #0f172a;
  --sponsor-text-secondary: #475569;
  --sponsor-text-tertiary: #64748b;
  --sponsor-border: #e2e8f0;
  --sponsor-border-hover: #cbd5e1;
  --sponsor-chip-bg: #101216;

  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
}

:root[data-theme="dark"] .sponsor-entry {
  --sponsor-bg: #050505;
  --sponsor-bg-subtle: #0b0b0b;
  --sponsor-text-primary: #f5f5f5;
  --sponsor-text-secondary: #9a9a9a;
  --sponsor-text-tertiary: #666666;
  --sponsor-border: #1c1c1c;
  --sponsor-border-hover: #333333;
}

.sponsor-carousel {
  width: 96px;
  height: 28px;
}

.sponsor-slide {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.sponsor-logo-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 7px;
  background: var(--sponsor-chip-bg);
}

.sponsor-logo-chip--lg {
  padding: 10px 14px;
  border-radius: 10px;
  flex-shrink: 0;
}

.sponsor-logo {
  display: block;
  height: 16px;
  width: auto;
  max-width: 76px;
  object-fit: contain;
}

.sponsor-logo-chip--lg .sponsor-logo {
  height: 30px;
  max-width: 140px;
}

.sponsor-list-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary, var(--sponsor-text-secondary));
  cursor: pointer;
  font-size: 12px;
}

.sponsor-list-btn:hover {
  background: var(--sponsor-bg-subtle);
  color: var(--text, var(--sponsor-text-primary));
}

.sponsor-modal {
  width: min(560px, 92vw);
  max-height: 84vh;
  overflow: auto;
  padding: 18px;
  border-radius: 14px;
  background: var(--sponsor-bg, #fff);
  color: var(--sponsor-text-primary);
}

.sponsor-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.sponsor-modal__header h3 {
  margin: 0;
  font-size: 15px;
}

.sponsor-modal__close {
  width: 32px;
  height: 32px;
  border: 1px solid var(--sponsor-border);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.sponsor-modal__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sponsor-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  border: 1px solid var(--sponsor-border);
  border-radius: 12px;
  background: var(--sponsor-bg);
  transition: border-color 0.2s ease;
}

.sponsor-card:hover {
  border-color: var(--sponsor-border-hover);
}

.sponsor-card__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sponsor-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sponsor-card__head strong {
  font-size: 14px;
  font-weight: 600;
}

.sponsor-card__type {
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: var(--sponsor-text-secondary);
  background: var(--sponsor-bg-subtle);
  border: 1px solid var(--sponsor-border);
  border-radius: 6px;
}

.sponsor-card__url {
  font-size: 12px;
  color: var(--sponsor-text-tertiary);
  word-break: break-all;
}

.sponsor-card__desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--sponsor-text-secondary);
}

.sponsor-card__visit {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--sponsor-text-primary);
  background: var(--sponsor-bg);
  border: 1px solid var(--sponsor-border);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.sponsor-card__visit:hover {
  background: var(--sponsor-bg-subtle);
  border-color: var(--sponsor-border-hover);
}
</style>
