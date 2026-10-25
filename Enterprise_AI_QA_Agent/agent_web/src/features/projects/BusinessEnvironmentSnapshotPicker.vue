<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpin,
} from "naive-ui";
import { api } from "../../services/api";
import type { BusinessEnvironmentRecord, BusinessEnvironmentSnapshot } from "../../services/businessEnvironments.types";
import type { BusinessEnvironmentSelection } from "../../services/businessPlanning.types";

const props = defineProps<{ projectId: string; modelValue: BusinessEnvironmentSelection | null }>();
const emit = defineEmits<{
  close: [];
  "update:modelValue": [value: BusinessEnvironmentSelection | null];
}>();

const show = ref(true);
const environments = ref<BusinessEnvironmentRecord[]>([]);
const environmentOffset = ref(0);
const moreEnvironments = ref(false);
const environmentId = ref("");
const snapshots = ref<BusinessEnvironmentSnapshot[]>([]);
const snapshotOffset = ref(0);
const moreSnapshots = ref(false);
const loadingEnvironments = ref(false);
const loadingSnapshots = ref(false);
const error = ref("");
let environmentGeneration = 0;
let snapshotGeneration = 0;

const selectedSnapshot = computed(() => snapshots.value.find(snapshot =>
  snapshot.id === props.modelValue?.snapshot_id && snapshot.environment_id === props.modelValue.environment_id) || null);

async function loadEnvironments(append = false) {
  const projectId = props.projectId;
  const generation = ++environmentGeneration;
  const offset = append ? environmentOffset.value + 50 : 0;
  loadingEnvironments.value = true;
  error.value = "";
  try {
    const page = await api.listBusinessEnvironments(projectId, { status: "active", limit: 50, offset });
    if (generation !== environmentGeneration || projectId !== props.projectId) return;
    environments.value = append ? [...environments.value, ...page.items] : page.items;
    environmentOffset.value = page.offset;
    moreEnvironments.value = page.has_more;
    if (environmentId.value && !environments.value.some(item => item.id === environmentId.value)) {
      environmentId.value = "";
      snapshots.value = [];
      emit("update:modelValue", null);
    }
  } catch (err) {
    if (generation === environmentGeneration) error.value = err instanceof Error ? err.message : "测试环境加载失败";
  } finally {
    if (generation === environmentGeneration) loadingEnvironments.value = false;
  }
}

async function loadSnapshots(append = false) {
  if (!environmentId.value) return;
  const projectId = props.projectId;
  const currentEnvironmentId = environmentId.value;
  const generation = ++snapshotGeneration;
  const offset = append ? snapshotOffset.value + 20 : 0;
  loadingSnapshots.value = true;
  error.value = "";
  try {
    const page = await api.listBusinessEnvironmentSnapshots(projectId, currentEnvironmentId, 20, offset);
    if (generation !== snapshotGeneration || projectId !== props.projectId || currentEnvironmentId !== environmentId.value) return;
    snapshots.value = append ? [...snapshots.value, ...page.items] : page.items;
    snapshotOffset.value = page.offset;
    moreSnapshots.value = page.has_more;
  } catch (err) {
    if (generation === snapshotGeneration) error.value = err instanceof Error ? err.message : "环境快照加载失败";
  } finally {
    if (generation === snapshotGeneration) loadingSnapshots.value = false;
  }
}

function close() {
  show.value = false;
  emit("close");
}

function choose(snapshot: BusinessEnvironmentSnapshot) {
  if (snapshot.project_id !== props.projectId || snapshot.environment_id !== environmentId.value) {
    error.value = "快照与当前项目或环境不一致";
    return;
  }
  emit("update:modelValue", {
    environment_id: snapshot.environment_id,
    snapshot_id: snapshot.id,
    expected_snapshot_sha256: snapshot.sha256,
  });
}

watch(environmentId, () => {
  snapshotGeneration++;
  snapshots.value = [];
  snapshotOffset.value = 0;
  moreSnapshots.value = false;
  emit("update:modelValue", null);
  if (environmentId.value) void loadSnapshots();
});
watch(() => props.projectId, () => {
  environmentGeneration++;
  snapshotGeneration++;
  environmentId.value = "";
  environments.value = [];
  snapshots.value = [];
  environmentOffset.value = 0;
  snapshotOffset.value = 0;
  emit("update:modelValue", null);
  void loadEnvironments();
});
onMounted(() => { void loadEnvironments(); });
</script>

<template>

<NModal
    v-model:show="show"
    preset="card"
    title="选择固定测试环境"
    style="width: min(720px, 94vw)"
    :mask-closable="!loadingEnvironments && !loadingSnapshots"
    :close-on-esc="!loadingEnvironments && !loadingSnapshots"
    @update:show="!$event && close()"
  >
    <p class="dialog-hint">选择一个项目环境及其配置快照。快照只固定目标地址和配置，部署状态仍为未知，不代表需求已经部署。</p>
    <NAlert v-if="error" type="error" :bordered="false" style="margin-bottom: 12px">{{ error }}</NAlert>

    <NForm label-placement="top">
      <NFormItem label="项目环境">
        <NSelect
          :value="environmentId"
          :options="[{ label: '请选择环境', value: '' }, ...environments.map(item => ({ label: `${item.request.environment_key} · ${item.id}`, value: item.id }))]"
          :disabled="loadingEnvironments"
          style="width: 100%"
          @update:value="(value: string) => { environmentId = value; }"
        />
      </NFormItem>
    </NForm>
    <NButton v-if="moreEnvironments" size="small" :loading="loadingEnvironments" @click="loadEnvironments(true)">
      加载更多环境
    </NButton>
    <div v-if="loadingEnvironments" class="dialog-loading"><NSpin size="small" /> 正在加载项目环境…</div>

    <template v-if="environmentId">
      <div v-if="loadingSnapshots" class="dialog-loading"><NSpin size="small" /> 正在读取该环境的固定快照…</div>
      <NEmpty v-else-if="!snapshots.length" description="该环境当前页没有可选快照。请先在项目环境管理中保存目标配置并创建快照。" size="small" />
      <section v-if="snapshots.length" class="snapshot-section">
        <h3>目标配置快照</h3>
        <NRadioGroup
          :value="modelValue?.snapshot_id ?? ''"
          @update:value="(value: string) => { const snapshot = snapshots.find(item => item.id === value); if (snapshot) choose(snapshot); }"
        >
          <div class="policy-checks">
            <NRadio
              v-for="snapshot in snapshots"
              :key="snapshot.id"
              :value="snapshot.id"
              :disabled="snapshot.project_id !== projectId || snapshot.environment_id !== environmentId"
            >
              <strong>{{ snapshot.configuration.payload.display_name }}</strong> · {{ snapshot.configuration.payload.base_url }}<br>
              配置修订 {{ snapshot.configuration.revision }} · {{ new Date(snapshot.captured_at).toLocaleString() }} · 部署状态未知
            </NRadio>
          </div>
        </NRadioGroup>
      </section>
      <NButton v-if="moreSnapshots" size="small" :loading="loadingSnapshots" @click="loadSnapshots(true)">
        加载后续快照
      </NButton>
    </template>
    <NAlert v-if="selectedSnapshot" type="success" :bordered="false" style="margin-top: 12px">
      已固定快照 {{ selectedSnapshot.id }}；提交规划时会同时发送快照哈希。
    </NAlert>

    <template #footer>
      <div class="dialog-actions">
        <NButton :disabled="loadingEnvironments || loadingSnapshots" @click="close">完成</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
@import "./caseDialogs.css";
.snapshot-choice { grid-template-columns: auto 1fr !important; align-items: start; }
.snapshot-choice input { width: auto; margin-top: 4px; }
.snapshot-section { margin: 12px 0; }
.policy-checks { display: flex; flex-direction: column; gap: 10px; }
.dialog-loading { display: flex; align-items: center; gap: 8px; color: var(--text-secondary, #64748b); padding: 8px 0; }
</style>
