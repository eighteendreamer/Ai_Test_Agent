<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../../services/api";
import { HttpError } from "../../services/http";
import type {
  BusinessEnvironmentCaptureRequest, BusinessEnvironmentRecord, BusinessEnvironmentSnapshot,
  BusinessEnvironmentSnapshotPage, BusinessTargetConfiguration, BusinessTargetConfigurationRecord,
} from "../../services/businessEnvironments.types";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ close: [] }>();
const page = ref<BusinessEnvironmentRecord[]>([]);
const selected = ref<BusinessEnvironmentRecord | null>(null);
const status = ref<"active" | "archived">("active");
const offset = ref(0);
const moreEnvironments = ref(false);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const environmentKey = ref("");
const pendingRegistration = ref<{ request_id: string; environment_key: string } | null>(null);
const configRecord = ref<BusinessTargetConfigurationRecord | null>(null);
const configuration = ref<BusinessTargetConfiguration>({ display_name: "", base_url: "", description: "" });
const configurationMissing = ref(false);
const snapshots = ref<BusinessEnvironmentSnapshot[]>([]);
const snapshotOffset = ref(0);
const moreSnapshots = ref(false);
const pendingCapture = ref<{ environment_id: string; request: BusinessEnvironmentCaptureRequest } | null>(null);
const selectedSnapshot = ref<BusinessEnvironmentSnapshot | null>(null);
const storageWarning = ref("");
const environmentStorageKey = computed(() => `business-environment-pending:${props.projectId}`);
const canEdit = computed(() => selected.value?.status === "active");
const canCapture = computed(() => !!canEdit.value && !!configRecord.value
  && (!pendingCapture.value || pendingCapture.value.environment_id === selected.value?.id));

function requestId() {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID() : `ui-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
}
function showError(err: unknown) { error.value = err instanceof Error ? err.message : "项目环境操作失败"; }
function persistPending() {
  try {
    const value = { registration: pendingRegistration.value, capture: pendingCapture.value };
    if (value.registration || value.capture) window.sessionStorage.setItem(environmentStorageKey.value, JSON.stringify(value));
    else window.sessionStorage.removeItem(environmentStorageKey.value);
    storageWarning.value = "";
  } catch { storageWarning.value = "浏览器未能保存待核对请求号。请保留页面中的请求编号，关闭页面前先核对服务端记录。"; }
}
function restorePending() {
  try {
    const raw = window.sessionStorage.getItem(environmentStorageKey.value);
    if (!raw) return;
    const value = JSON.parse(raw);
    const registration = value?.registration;
    if (registration && typeof registration.request_id === "string" && typeof registration.environment_key === "string") {
      pendingRegistration.value = registration;
      environmentKey.value = registration.environment_key;
    }
    const capture = value?.capture;
    if (capture && typeof capture.environment_id === "string" && typeof capture.request?.request_id === "string"
      && Number.isInteger(capture.request.expected_config_revision) && typeof capture.request.expected_config_sha256 === "string") {
      pendingCapture.value = capture;
    }
  } catch { storageWarning.value = "无法读取已保存的环境请求身份，请先查询环境列表及快照历史。"; }
}

async function loadEnvironments(append = false) {
  const offsetValue = append ? offset.value + 50 : 0;
  loading.value = true; error.value = "";
  try {
    const result = await api.listBusinessEnvironments(props.projectId, { status: status.value, limit: 50, offset: offsetValue });
    page.value = append ? [...page.value, ...result.items] : result.items;
    offset.value = result.offset;
    moreEnvironments.value = result.has_more;
    if (selected.value && !page.value.some(item => item.id === selected.value?.id)) selected.value = null;
  } catch (err) { showError(err); }
  finally { loading.value = false; }
}

function isMissingConfiguration(err: unknown) {
  return err instanceof HttpError && err.status === 409 && err.message === "环境目标尚未配置，请先保存目标设置";
}
function sameConfiguration(left: BusinessTargetConfiguration, right: BusinessTargetConfiguration) {
  return left.display_name === right.display_name && left.base_url === right.base_url
    && (left.description || "") === (right.description || "");
}
function acceptConfiguration(value: BusinessTargetConfigurationRecord) {
  if (value.project_id !== props.projectId || value.environment_id !== selected.value?.id) throw new Error("目标配置不属于当前项目环境");
  configRecord.value = value;
  configuration.value = { ...value.payload };
  configurationMissing.value = false;
}

async function readConfiguration() {
  if (!selected.value) return;
  error.value = "";
  try { acceptConfiguration(await api.readBusinessTargetConfiguration(props.projectId, selected.value.id)); }
  catch (err) {
    if (isMissingConfiguration(err)) {
      configRecord.value = null;
      configuration.value = { display_name: selected.value.request.environment_key, base_url: "", description: "" };
      configurationMissing.value = true;
    } else showError(err);
  }
}

async function loadSnapshots(append = false) {
  if (!selected.value) return;
  const selectedId = selected.value.id;
  const offsetValue = append ? snapshotOffset.value + 20 : 0;
  loading.value = true; error.value = "";
  try {
    const result: BusinessEnvironmentSnapshotPage = await api.listBusinessEnvironmentSnapshots(props.projectId, selectedId, 20, offsetValue);
    if (selected.value?.id !== selectedId || result.items.some(item => item.project_id !== props.projectId
      || item.environment_id !== selectedId)) return;
    snapshots.value = append ? [...snapshots.value, ...result.items] : result.items;
    snapshotOffset.value = result.offset;
    moreSnapshots.value = result.has_more;
  } catch (err) { showError(err); }
  finally { loading.value = false; }
}

async function selectEnvironment(value: BusinessEnvironmentRecord) {
  if (value.project_id !== props.projectId) { showError(new Error("环境不属于当前项目")); return; }
  selected.value = value;
  configRecord.value = null; configurationMissing.value = false;
  configuration.value = { display_name: value.request.environment_key, base_url: "", description: "" };
  snapshots.value = []; snapshotOffset.value = 0; moreSnapshots.value = false; selectedSnapshot.value = null;
  error.value = ""; notice.value = "";
  await Promise.all([readConfiguration(), loadSnapshots()]);
}

async function registerEnvironment() {
  const key = (pendingRegistration.value?.environment_key || environmentKey.value).trim();
  if (!key || loading.value) return;
  error.value = ""; notice.value = ""; loading.value = true;
  if (!pendingRegistration.value) pendingRegistration.value = { request_id: requestId(), environment_key: key };
  persistPending();
  const request = { ...pendingRegistration.value };
  try {
    let value: BusinessEnvironmentRecord;
    try { value = await api.getBusinessEnvironmentRegistration(props.projectId, request.request_id); }
    catch (err) {
      if (!(err instanceof HttpError) || err.status !== 404) throw err;
      value = await api.registerBusinessEnvironment(props.projectId, request);
    }
    if (value.project_id !== props.projectId || value.request.request_id !== request.request_id) throw new Error("登记回执与原请求不一致");
    pendingRegistration.value = null; persistPending();
    environmentKey.value = "";
    notice.value = "项目环境已登记";
    await loadEnvironments();
    await selectEnvironment(value);
  } catch (err) {
    showError(err);
    notice.value = "登记结果尚未确认。重试会先按原请求编号查询，不会换号重复登记。";
  } finally { loading.value = false; }
}

async function saveConfiguration() {
  if (!selected.value || !canEdit.value || loading.value || pendingCapture.value) return;
  const payload = { display_name: configuration.value.display_name.trim(), base_url: configuration.value.base_url.trim(),
    description: (configuration.value.description || "").trim() };
  if (!payload.display_name || !payload.base_url) { error.value = "显示名称和目标地址不能为空"; return; }
  const target = selected.value;
  loading.value = true; error.value = ""; notice.value = "";
  try {
    acceptConfiguration(await api.saveBusinessTargetConfiguration(props.projectId, target.id, payload));
    notice.value = "目标配置已保存；创建快照后才能用于新的规划";
    configurationMissing.value = false;
  } catch (writeError) {
    if (writeError instanceof HttpError && writeError.status === 409
      && writeError.message === "环境目标配置已变化，请重读后明确选择新的捕获依据") {
      pendingCapture.value = null; persistPending();
      showError(writeError);
      notice.value = "服务端确认原配置版本已过期；请重读配置，再明确创建新快照。";
      await readConfiguration();
      return;
    }
    try {
      const current = await api.readBusinessTargetConfiguration(props.projectId, target.id);
      acceptConfiguration(current);
      if (sameConfiguration(current.payload, payload)) notice.value = "保存响应未收到，但已重读并确认当前配置与本次内容一致";
      else error.value = `${writeError instanceof Error ? writeError.message : "配置保存失败"}。已重读当前配置；请检查显示值后再决定是否保存。`;
    } catch (readError) {
      if (isMissingConfiguration(readError)) configurationMissing.value = true;
      error.value = `${writeError instanceof Error ? writeError.message : "配置保存失败"}。无法确认当前值，请重新读取配置后再决定下一步。`;
    }
  } finally { loading.value = false; }
}

function sameCapture(snapshot: BusinessEnvironmentSnapshot, pending: NonNullable<typeof pendingCapture.value>) {
  return snapshot.project_id === props.projectId && snapshot.environment_id === pending.environment_id
    && snapshot.request.request_id === pending.request.request_id
    && snapshot.request.expected_config_revision === pending.request.expected_config_revision
    && snapshot.request.expected_config_sha256 === pending.request.expected_config_sha256;
}
async function captureSnapshot() {
  if (!selected.value || !configRecord.value || !canEdit.value || loading.value) return;
  if (!pendingCapture.value) {
    pendingCapture.value = { environment_id: selected.value.id, request: {
      request_id: requestId(), expected_config_revision: configRecord.value.revision, expected_config_sha256: configRecord.value.sha256,
    } };
    persistPending();
  }
  const pending = pendingCapture.value;
  if (pending.environment_id !== selected.value.id) { error.value = "待核对快照请求属于其他环境，请先切回该环境处理"; return; }
  loading.value = true; error.value = ""; notice.value = "";
  try {
    let value: BusinessEnvironmentSnapshot;
    try { value = await api.getBusinessEnvironmentCapture(props.projectId, pending.environment_id, pending.request.request_id); }
    catch (err) {
      if (!(err instanceof HttpError) || err.status !== 404) throw err;
      value = await api.captureBusinessEnvironment(props.projectId, pending.environment_id, pending.request);
    }
    if (!sameCapture(value, pending)) throw new Error("快照回执与原请求内容不一致");
    selectedSnapshot.value = value;
    pendingCapture.value = null; persistPending();
    notice.value = "目标配置快照已保存；部署状态仍为未知";
    await loadSnapshots();
  } catch (writeError) {
    try {
      const value = await api.getBusinessEnvironmentCapture(props.projectId, pending.environment_id, pending.request.request_id);
      if (!sameCapture(value, pending)) throw new Error("查到的快照与原请求内容不一致");
      selectedSnapshot.value = value;
      pendingCapture.value = null; persistPending();
      notice.value = "捕获响应未收到，但已按原请求编号核对到快照；部署状态仍为未知";
      await loadSnapshots();
    } catch {
      showError(writeError);
      notice.value = "快照结果尚未确认。请重试原请求查询；请求编号和配置修订已保留。";
    }
  } finally { loading.value = false; }
}

async function archiveEnvironment() {
  if (!selected.value || selected.value.status !== "active" || loading.value) return;
  loading.value = true; error.value = ""; notice.value = "";
  try {
    const value = await api.archiveBusinessEnvironment(props.projectId, selected.value.id);
    if (value.project_id !== props.projectId || value.id !== selected.value.id || value.status !== "archived") throw new Error("归档回执与当前环境不一致");
    status.value = "archived";
    notice.value = "环境已归档，历史快照仍可查询";
    await loadEnvironments();
    await selectEnvironment(value);
  } catch (err) { showError(err); }
  finally { loading.value = false; }
}

async function changeStatus(value: "active" | "archived") {
  if (status.value === value) return;
  status.value = value; page.value = []; offset.value = 0; moreEnvironments.value = false;
  selected.value = null; configRecord.value = null; snapshots.value = [];
  await loadEnvironments();
}

onMounted(async () => {
  restorePending();
  await loadEnvironments();
  if (pendingCapture.value) {
    const environment = page.value.find(item => item.id === pendingCapture.value?.environment_id);
    if (environment) await selectEnvironment(environment);
  }
});
</script>

<template>
  <div class="case-dialog-backdrop" @click.self="emit('close')">
    <section class="case-dialog" role="dialog" aria-modal="true" aria-label="项目测试环境管理">
      <header><h2>项目测试环境</h2><button type="button" @click="emit('close')">关闭</button></header>
      <p>每个环境属于当前项目。目标地址保存于项目配置；规划使用明确选择的固定快照。快照不证明目标版本已经部署。</p>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p><p v-if="storageWarning" role="alert">{{ storageWarning }}</p>
      <nav aria-label="环境状态">
        <button type="button" :aria-pressed="status === 'active'" @click="changeStatus('active')">有效环境</button>
        <button type="button" :aria-pressed="status === 'archived'" @click="changeStatus('archived')">已归档环境</button>
      </nav>
      <form v-if="status === 'active'" @submit.prevent="registerEnvironment">
        <label>登记环境标识<input v-model="environmentKey" :disabled="loading || !!pendingRegistration" maxlength="80" placeholder="例如 staging 或 production"></label>
        <p v-if="pendingRegistration">原登记请求 {{ pendingRegistration.request_id }} · {{ pendingRegistration.environment_key }}</p>
        <button type="submit" :disabled="loading || (!environmentKey.trim() && !pendingRegistration)">{{ pendingRegistration ? '按原请求核对并继续登记' : '登记项目环境' }}</button>
      </form>
      <button type="button" :disabled="loading" @click="loadEnvironments()">刷新环境</button>
      <p v-if="loading">正在读取…</p>
      <ul v-if="page.length" aria-label="项目环境列表">
        <li v-for="environment in page" :key="environment.id">
          <button type="button" :disabled="loading" :aria-current="selected?.id === environment.id ? 'true' : undefined" @click="selectEnvironment(environment)">
            {{ environment.request.environment_key }} · {{ environment.status }} · {{ environment.id }}
          </button>
        </li>
      </ul>
      <p v-else-if="!loading">当前状态下没有环境</p>
      <button v-if="moreEnvironments" type="button" :disabled="loading" @click="loadEnvironments(true)">加载更多环境</button>

      <section v-if="selected" aria-label="环境目标配置">
        <h3>{{ selected.request.environment_key }} · {{ selected.status }}</h3>
        <p v-if="configRecord">当前配置修订 {{ configRecord.revision }} · {{ configRecord.sha256 }}</p>
        <p v-else-if="configurationMissing">尚未配置目标地址</p>
        <label>目标显示名称<input v-model="configuration.display_name" :disabled="loading || !canEdit"></label>
        <label>目标地址<input v-model="configuration.base_url" :disabled="loading || !canEdit" type="url" placeholder="https://example.test"></label>
        <label>说明（可选）<textarea v-model="configuration.description" :disabled="loading || !canEdit" rows="2"></textarea></label>
        <button type="button" :disabled="loading || !canEdit || !!pendingCapture" @click="saveConfiguration">保存目标配置</button>
        <button type="button" :disabled="loading || !canCapture" @click="captureSnapshot">{{ pendingCapture ? '按原请求核对快照' : '创建配置快照' }}</button>
        <p v-if="pendingCapture?.environment_id === selected.id">快照请求 {{ pendingCapture.request.request_id }} · 配置修订 {{ pendingCapture.request.expected_config_revision }}</p>
        <button v-if="canEdit" type="button" :disabled="loading" @click="archiveEnvironment">归档环境</button>

        <h3>历史目标快照</h3>
        <p>快照按创建时间分页展示。每条记录中的部署状态均为未知。</p>
        <ul v-if="snapshots.length">
          <li v-for="snapshot in snapshots" :key="snapshot.id">
            <button type="button" @click="selectedSnapshot = snapshot">{{ snapshot.configuration.payload.display_name }} · 修订 {{ snapshot.configuration.revision }} · {{ new Date(snapshot.captured_at).toLocaleString() }}</button>
            <span> · {{ snapshot.id }} · 部署状态未知</span>
          </li>
        </ul>
        <p v-else-if="!loading">没有历史快照</p>
        <button v-if="moreSnapshots" type="button" :disabled="loading" @click="loadSnapshots(true)">加载后续快照</button>
        <pre v-if="selectedSnapshot">{{ JSON.stringify(selectedSnapshot, null, 2) }}</pre>
      </section>
      <footer><button type="button" @click="emit('close')">完成</button></footer>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
form { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 12px; }
nav { display: flex; gap: 8px; margin: 12px 0; }
li { margin: 8px 0; overflow-wrap: anywhere; }
section[aria-label="环境目标配置"] { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 12px; }
</style>
