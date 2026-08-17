<script setup lang="ts">
import { NDrawer } from "naive-ui";
import { onMounted, ref } from "vue";
import { api } from "../../../services/api";
import { t } from "../../../services/i18n";
import type { SecurityFamilyGroup, SecurityProfileDescriptor } from "../../../types";

const families = ref<SecurityFamilyGroup[]>([]);
const totalCount = ref(0);
const loading = ref(false);
const errorMessage = ref("");
const selectedProfile = ref<SecurityProfileDescriptor | null>(null);
const selectedRunner = ref("");
const drawerOpen = ref(false);
const collapsedFamilies = ref<Set<string>>(new Set());

const FAMILY_ICONS: Record<string, string> = {
  network_recon: "fa-solid fa-network-wired",
  web_scan: "fa-solid fa-globe",
  service_audit: "fa-solid fa-server",
  credential_attack: "fa-solid fa-key",
};

const RISK_COLORS: Record<string, string> = {
  info: "risk-info",
  low: "risk-low",
  medium: "risk-medium",
  high: "risk-high",
};

function familyIcon(family: string): string {
  return FAMILY_ICONS[family] || "fa-solid fa-shield-halved";
}

function riskClass(level: string): string {
  return RISK_COLORS[level] || "risk-info";
}

function toggleFamily(family: string) {
  const s = new Set(collapsedFamilies.value);
  if (s.has(family)) {
    s.delete(family);
  } else {
    s.add(family);
  }
  collapsedFamilies.value = s;
}

function openDetail(profile: SecurityProfileDescriptor, runnerKey: string) {
  selectedProfile.value = profile;
  selectedRunner.value = runnerKey;
  drawerOpen.value = true;
}

async function loadProfiles() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const data = await api.listSecurityProfiles();
    families.value = data.families;
    totalCount.value = data.total_count;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("scanners.load_failed");
  } finally {
    loading.value = false;
  }
}

onMounted(loadProfiles);
</script>

<template>
  <div class="tools-tab-pane">
    <div class="pane-header">
      <h3 class="section-title">{{ t("scanners.title") }}</h3>
      <p class="head-desc">
        {{ t("scanners.desc") }}
        <span v-if="totalCount" class="profile-count">{{ totalCount }} {{ t("scanners.profiles_registered") }}</span>
      </p>
    </div>

    <div v-if="errorMessage" class="notice error">{{ errorMessage }}</div>

    <div v-if="loading" class="scanner-loading">
      <i class="fa-solid fa-spinner fa-spin"></i>
      {{ t("scanners.loading") }}
    </div>

    <template v-else>
      <section v-for="group in families" :key="group.family" class="family-section">
        <div class="family-header" @click="toggleFamily(group.family)">
          <div class="family-title">
            <i :class="familyIcon(group.family)"></i>
            <h4>{{ t(`scanners.family.${group.family}`) }}</h4>
            <span class="family-count">{{ group.profiles.length }}</span>
          </div>
          <div class="family-meta">
            <span class="runner-tag">
              <i class="fa-solid fa-microchip"></i>
              {{ group.runner_key }}
            </span>
            <i
              class="fa-solid fa-chevron-down family-toggle"
              :class="{ collapsed: collapsedFamilies.has(group.family) }"
            ></i>
          </div>
        </div>

        <div v-show="!collapsedFamilies.has(group.family)" class="scanner-grid">
          <article
            v-for="profile in group.profiles"
            :key="profile.profile_key"
            class="scanner-card scanner-card--dynamic"
            @click="openDetail(profile, group.runner_key)"
          >
            <div class="sc-top">
              <span class="sc-name">{{ profile.tool_name }}</span>
              <span class="risk-badge" :class="riskClass(profile.risk_level)">
                {{ t(`scanners.risk.${profile.risk_level}`) }}
              </span>
            </div>
            <small class="sc-desc">{{ profile.description }}</small>
            <div class="sc-footer">
              <div class="sc-tags">
                <span v-for="s in profile.surface_types" :key="s" class="surface-tag">{{ s }}</span>
              </div>
              <div class="sc-indicators">
                <span v-if="profile.requires_approval" class="approval-icon" :title="t('scanners.requires_approval')">
                  <i class="fa-solid fa-lock"></i>
                </span>
                <span class="timeout-label">{{ profile.timeout_seconds }}s</span>
              </div>
            </div>
          </article>
        </div>
      </section>
    </template>

    <NDrawer v-model:show="drawerOpen" :width="480" placement="right">
      <div v-if="selectedProfile" class="detail-panel">
        <div class="detail-head">
          <div class="detail-head-icon">
            <i :class="familyIcon(selectedProfile.tool_family)"></i>
          </div>
          <div class="detail-head-title">
            <h4>{{ selectedProfile.tool_name }}</h4>
            <span class="detail-head-subtitle">{{ t(`scanners.family.${selectedProfile.tool_family}`) }}</span>
          </div>
          <button class="icon-btn close-btn" @click="drawerOpen = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div class="detail-body">
          <div class="detail-grid">
            <div class="detail-stat">
              <label>{{ t("scanners.detail.risk_level") }}</label>
              <span class="risk-badge" :class="riskClass(selectedProfile.risk_level)">
                {{ t(`scanners.risk.${selectedProfile.risk_level}`) }}
              </span>
            </div>
            <div class="detail-stat">
              <label>{{ t("scanners.detail.timeout") }}</label>
              <span><i class="fa-solid fa-stopwatch"></i> {{ selectedProfile.timeout_seconds }}s</span>
            </div>
            <div class="detail-stat">
              <label>{{ t("scanners.detail.approval") }}</label>
              <span :class="{'text-warning': selectedProfile.requires_approval}">
                <i :class="selectedProfile.requires_approval ? 'fa-solid fa-lock' : 'fa-solid fa-bolt'"></i>
                {{ selectedProfile.requires_approval ? t("scanners.detail.approval_required") : t("scanners.detail.approval_auto") }}
              </span>
            </div>
          </div>

          <div class="detail-block">
            <h5 class="block-title">Scanner Information</h5>
            <div class="detail-row">
              <label>Profile Key</label>
              <div class="code-box">{{ selectedProfile.profile_key }}</div>
            </div>
            <div class="detail-row">
              <label>{{ t("scanners.detail.runner") }}</label>
              <div class="code-box">{{ selectedRunner }}</div>
            </div>
            <div class="detail-row">
              <label>{{ t("scanners.detail.description") }}</label>
              <p class="detail-desc">{{ selectedProfile.description }}</p>
            </div>
          </div>

          <div class="detail-block">
            <h5 class="block-title">{{ t("scanners.detail.surfaces") }}</h5>
            <div class="detail-tags">
              <span v-for="s in selectedProfile.surface_types" :key="s" class="surface-tag detail-surface-tag">
                <i class="fa-solid fa-bullseye"></i> {{ s }}
              </span>
            </div>
          </div>

          <div class="detail-block">
            <h5 class="block-title">Verification Contract</h5>
            <div class="detail-row">
              <label>Parser</label>
              <div class="code-box">
                {{ selectedProfile.parser_key || "Raw evidence only" }}
              </div>
            </div>
            <div class="detail-row">
              <label>Allowed Arguments</label>
              <div class="detail-tags">
                <span v-for="argument in selectedProfile.allowed_arguments" :key="argument" class="surface-tag">
                  {{ argument }}
                </span>
              </div>
            </div>
            <div class="detail-row">
              <label>Assertions</label>
              <div class="detail-tags">
                <span
                  v-for="operators, kind in selectedProfile.verification_capabilities.assertions"
                  :key="kind"
                  class="surface-tag"
                  :title="operators.join(', ')"
                >
                  {{ kind }}
                </span>
              </div>
            </div>
            <div v-if="selectedProfile.verification_capabilities.structured_output" class="detail-row">
              <label>Parsed Fields</label>
              <div class="detail-tags">
                <span
                  v-for="field in selectedProfile.verification_capabilities.parsed_fields"
                  :key="field"
                  class="surface-tag"
                >
                  {{ field }}
                </span>
              </div>
            </div>
            <div class="detail-row">
              <label>Evidence Policy</label>
              <div class="code-box">{{ selectedProfile.artifact_policy }}</div>
            </div>
          </div>
        </div>
      </div>
    </NDrawer>
  </div>
</template>

<style scoped>
.tools-tab-pane {
  animation: fadeIn 0.3s ease;
}

.pane-header {
  margin-bottom: 24px;
}

.pane-header .section-title {
  margin: 0 0 8px;
  font-size: 18px;
}

.pane-header .head-desc {
  margin: 0;
}

.profile-count {
  margin-left: 8px;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--muted);
  font-size: 12px;
}

.notice {
  margin-bottom: 12px;
  border-radius: 10px;
  padding: 10px 12px;
}

.notice.error {
  color: #fecaca;
  background: rgba(127, 29, 29, 0.35);
}

.scanner-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 32px;
  justify-content: center;
  color: var(--muted);
  font-size: 14px;
}

/* --- Family sections --- */
.family-section {
  margin-bottom: 24px;
}

.family-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--surface-muted, #f1f5f9);
  cursor: pointer;
  user-select: none;
  margin-bottom: 12px;
  transition: background 0.15s ease;
}

.family-header:hover {
  background: var(--surface-soft, #e2e8f0);
}

.family-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.family-title i {
  font-size: 16px;
  color: var(--muted);
}

.family-title h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.family-count {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface);
  border: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.family-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.runner-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 10px;
  border-radius: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  font-size: 11px;
  color: var(--muted);
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
}

.family-toggle {
  font-size: 12px;
  color: var(--muted);
  transition: transform 0.2s ease;
}

.family-toggle.collapsed {
  transform: rotate(-90deg);
}

/* --- Override global scanner-grid for dynamic layout --- */
.scanner-grid {
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)) !important;
}

/* --- Dynamic card --- */
.scanner-card--dynamic {
  align-items: stretch !important;
  text-align: left !important;
  justify-content: flex-start !important;
  cursor: pointer;
  padding: 14px 16px !important;
  gap: 6px !important;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.scanner-card--dynamic:hover {
  box-shadow: var(--shadow-soft, 0 2px 8px rgba(0, 0, 0, 0.06));
}

.sc-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sc-name {
  font-weight: 700 !important;
  font-size: 14px !important;
}

.sc-desc {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  line-height: 1.45;
  min-height: 36px;
}

.sc-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 6px;
}

.sc-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.surface-tag {
  padding: 1px 7px;
  border-radius: 4px;
  background: var(--surface-muted, #f1f5f9);
  color: var(--muted);
  font-size: 11px;
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
}

.sc-indicators {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.approval-icon {
  color: var(--muted);
  font-size: 12px;
}

.timeout-label {
  font-size: 11px;
  color: var(--muted);
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
}

/* --- Risk badges --- */
.risk-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.risk-info {
  background: #e2e8f0;
  color: #475569;
}

.risk-low {
  background: #dcfce7;
  color: #166534;
}

.risk-medium {
  background: #fef3c7;
  color: #92400e;
}

.risk-high {
  background: #fecaca;
  color: #991b1b;
}

/* --- Drawer detail --- */
.detail-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface, #fff);
}

.detail-head {
  padding: 32px 24px 24px;
  background: linear-gradient(180deg, var(--surface-muted, #f8fafc) 0%, var(--surface, #fff) 100%);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: flex-start;
  gap: 16px;
  position: relative;
}

.detail-head-icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  background: #eff6ff;
  color: #3b82f6;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
}

.detail-head-title {
  flex: 1;
}

.detail-head-title h4 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.2;
}

.detail-head-subtitle {
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.icon-btn.close-btn {
  position: absolute;
  top: 16px;
  right: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.02);
}

.detail-body {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 28px;
  overflow-y: auto;
  flex: 1;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  background: var(--surface-muted, #f8fafc);
  padding: 16px;
  border-radius: 12px;
  border: 1px solid var(--border);
}

.detail-stat {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-stat label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--muted);
  letter-spacing: 0.05em;
}

.detail-stat span {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
}

.detail-stat span i {
  color: var(--muted);
}

.text-warning {
  color: #d97706 !important;
}
.text-warning i {
  color: inherit !important;
}

.detail-block {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.block-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
}

.block-title::before {
  content: '';
  display: block;
  width: 4px;
  height: 14px;
  background: #3b82f6;
  border-radius: 2px;
}

.detail-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-row label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.code-box {
  background: var(--surface-muted, #f8fafc);
  border: 1px solid var(--border);
  padding: 10px 14px;
  border-radius: 8px;
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
  font-size: 13px;
  color: var(--text);
  word-break: break-all;
}

.detail-desc {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-secondary, #475569);
  background: var(--surface-muted, #f8fafc);
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--border);
}

.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-surface-tag {
  padding: 6px 12px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  color: #334155;
  font-size: 12px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.detail-surface-tag i {
  color: #94a3b8;
}

.mono {
  font-family: "JetBrains Mono", "Cascadia Code", monospace;
  font-size: 13px;
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
