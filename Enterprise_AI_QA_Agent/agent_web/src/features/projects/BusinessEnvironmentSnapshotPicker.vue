<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../../services/api";
import type { BusinessEnvironmentRecord, BusinessEnvironmentSnapshot } from "../../services/businessEnvironments.types";
import type { BusinessEnvironmentSelection } from "../../services/businessPlanning.types";

const props = defineProps<{ projectId: string; modelValue: BusinessEnvironmentSelection | null }>();
const emit = defineEmits<{
  close: [];
  "update:modelValue": [value: BusinessEnvironmentSelection | null];
}>();

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
  <div class="case-dialog-backdrop" @click.self="emit('close')">
    <section class="case-dialog" role="dialog" aria-modal="true" aria-label="选择固定测试环境">
      <header><h2>选择固定测试环境</h2><button type="button" @click="emit('close')">关闭</button></header>
      <p>选择一个项目环境及其配置快照。快照只固定目标地址和配置，部署状态仍为未知，不代表需求已经部署。</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <label>项目环境
        <select v-model="environmentId" :disabled="loadingEnvironments">
          <option value="">请选择环境</option>
          <option v-for="item in environments" :key="item.id" :value="item.id">{{ item.request.environment_key }} · {{ item.id }}</option>
        </select>
      </label>
      <button v-if="moreEnvironments" type="button" :disabled="loadingEnvironments" @click="loadEnvironments(true)">加载更多环境</button>
      <p v-if="loadingEnvironments">正在加载项目环境…</p>
      <template v-if="environmentId">
        <p v-if="loadingSnapshots">正在读取该环境的固定快照…</p>
        <p v-else-if="!snapshots.length">该环境当前页没有可选快照。请先在项目环境管理中保存目标配置并创建快照。</p>
        <fieldset v-if="snapshots.length">
          <legend>目标配置快照</legend>
          <label v-for="snapshot in snapshots" :key="snapshot.id" class="snapshot-choice">
            <input type="radio" name="business-environment-snapshot" :checked="modelValue?.snapshot_id === snapshot.id"
              :disabled="snapshot.project_id !== projectId || snapshot.environment_id !== environmentId"
              @change="choose(snapshot)">
            <span><strong>{{ snapshot.configuration.payload.display_name }}</strong> · {{ snapshot.configuration.payload.base_url }}<br>
              配置修订 {{ snapshot.configuration.revision }} · {{ new Date(snapshot.captured_at).toLocaleString() }} · 部署状态未知</span>
          </label>
        </fieldset>
        <button v-if="moreSnapshots" type="button" :disabled="loadingSnapshots" @click="loadSnapshots(true)">加载后续快照</button>
      </template>
      <p v-if="selectedSnapshot" role="status">已固定快照 {{ selectedSnapshot.id }}；提交规划时会同时发送快照哈希。</p>
      <footer><button type="button" @click="emit('close')">完成</button></footer>
    </section>
  </div>
</template>

<style scoped>
@import "./caseDialogs.css";
.snapshot-choice { grid-template-columns: auto 1fr !important; align-items: start; }
.snapshot-choice input { width: auto; margin-top: 4px; }
</style>
