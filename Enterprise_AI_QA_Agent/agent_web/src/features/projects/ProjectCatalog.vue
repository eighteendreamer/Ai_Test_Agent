<script setup lang="ts">
import { computed, h, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NIcon,
  NInput,
  NPagination,
  NPopconfirm,
  NSelect,
  NRadioButton,
  NRadioGroup,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";

import ProjectFormModal from "./ProjectFormModal.vue";
import { api } from "../../services/api";
import { formatServerDateTime } from "../../utils/datetime";
import type { ProjectRecord, ProjectStatus } from "../../types";

const emit = defineEmits<{ select: [projectId: string] }>();
const toast = useMessage();
const route = useRoute();
const router = useRouter();

const PAGE_SIZES = [10, 20, 50];
const items = ref<ProjectRecord[]>([]);
const total = ref(0);
const counts = ref({ all: 0, active: 0, archived: 0 });
const loading = ref(false);
const error = ref("");
const query = ref("");
const status = ref<"" | ProjectStatus>("");
const page = ref(1);
const pageSize = ref(20);
const formOpen = ref(false);
const editingProject = ref<ProjectRecord | null>(null);
let searchTimer: ReturnType<typeof setTimeout> | undefined;
let lastLoadedKey = "";

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "启用中", value: "active" },
  { label: "已归档", value: "archived" },
];

const filterSegments = computed(() => [
  { label: `全部 ${counts.value.all}`, value: "" },
  { label: `启用中 ${counts.value.active}`, value: "active" },
  { label: `已归档 ${counts.value.archived}`, value: "archived" },
]);

function routeString(key: string): string {
  const value = route.query[key];
  return typeof value === "string" ? value : "";
}

function syncFromRoute() {
  query.value = routeString("q");
  const value = routeString("status");
  status.value = value === "active" || value === "archived" ? value : "";
  page.value = Math.max(1, Number(routeString("page")) || 1);
}

function querySnapshot() {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries(route.query)) {
    if (typeof value === "string" && value !== "") next[key] = value;
  }
  next.q = query.value.trim();
  next.status = status.value;
  next.page = String(page.value);
  for (const key of Object.keys(next)) {
    if (!next[key] || (key === "page" && next[key] === "1")) delete next[key];
  }
  return JSON.stringify(next);
}

async function applyFilters(patch: Record<string, string | undefined>) {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...route.query, ...patch })) {
    if (typeof value === "string" && value !== "") next[key] = value;
  }
  if (!("q" in patch)) next.q = query.value.trim();
  if (!("status" in patch)) next.status = status.value;
  if (!("page" in patch)) delete next.page;
  for (const key of Object.keys(next)) {
    if (!next[key]) delete next[key];
  }
  await router.replace({ query: next });
}

async function load() {
  const key = querySnapshot();
  if (loading.value && key === lastLoadedKey) return;
  lastLoadedKey = key;
  loading.value = true;
  error.value = "";
  try {
    const data = await api.listProjects({
      status: status.value || undefined,
      query: query.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    });
    items.value = data.items;
    total.value = data.total;
    counts.value = data.counts;
    if (!data.items.length && page.value > 1 && data.total > 0) {
      await applyFilters({ page: undefined });
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "项目加载失败";
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.query,
  () => {
    syncFromRoute();
    void load();
  },
  { immediate: true, deep: true },
);

watch(query, (value) => {
  if (value === routeString("q")) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    void applyFilters({ q: value.trim() || undefined, page: undefined });
  }, 300);
});

onBeforeUnmount(() => clearTimeout(searchTimer));

function changeStatus(value: string) {
  void applyFilters({ status: value || undefined, page: undefined });
}

function changePage(value: number) {
  page.value = value;
  void applyFilters({ page: value > 1 ? String(value) : undefined });
}

function changePageSize(value: number) {
  pageSize.value = value;
  page.value = 1;
  void load();
}

function openCreate() {
  editingProject.value = null;
  formOpen.value = true;
}

function openEdit(project: ProjectRecord) {
  editingProject.value = project;
  formOpen.value = true;
}

async function archive(project: ProjectRecord) {
  try {
    await api.archiveProject(project.id);
    toast.success("项目已归档，历史资源仍被保留");
    await load();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "归档失败");
  }
}

const columns = computed<DataTableColumns<ProjectRecord>>(() => [
  {
    title: "项目",
    key: "name",
    minWidth: 260,
    sorter: (a, b) => a.name.localeCompare(b.name, "zh-CN"),
    render: (project) => h("div", { class: "catalog-project-cell" }, [
      h("span", { class: "catalog-project-mark" }, project.name.slice(0, 1).toUpperCase()),
      h("div", { class: "catalog-project-copy" }, [
        h("strong", project.name),
        h("small", project.project_key),
      ]),
    ]),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (project) => h(NTag, {
      type: project.status === "active" ? "success" : "default",
      size: "small",
      bordered: false,
    }, { default: () => (project.status === "active" ? "启用中" : "已归档") }),
  },
  {
    title: "Base URL",
    key: "base_url",
    minWidth: 220,
    ellipsis: { tooltip: true },
    render: (project) => project.base_url || h("span", { class: "catalog-muted" }, "未设置"),
  },
  {
    title: "更新时间",
    key: "updated_at",
    width: 190,
    sorter: (a, b) => a.updated_at.localeCompare(b.updated_at),
    render: (project) => formatServerDateTime(project.updated_at),
  },
  {
    title: "操作",
    key: "actions",
    width: 220,
    render: (project) => h("div", { class: "catalog-actions" }, [
      h(NButton, { text: true, type: "primary", size: "small", onClick: () => emit("select", project.id) },
        { default: () => "详情" }),
      h(NTooltip, { trigger: "hover", disabled: project.status === "active" }, {
        trigger: () => h("span", [
          h(NButton, {
            text: true,
            size: "small",
            disabled: project.status !== "active",
            onClick: () => openEdit(project),
          }, { default: () => "编辑" }),
        ]),
        default: () => "已归档项目不可编辑",
      }),
      h(NPopconfirm, {
        positiveText: "归档",
        negativeText: "取消",
        disabled: project.status !== "active",
        onPositiveClick: () => archive(project),
      }, {
        trigger: () => h("span", [
          h(NButton, {
            text: true,
            type: "error",
            size: "small",
            disabled: project.status !== "active",
          }, { default: () => "归档" }),
        ]),
        default: () => `归档后「${project.name}」将不可编辑，历史资源仍会保留。`,
      }),
    ]),
  },
]);
</script>

<template>
  <section class="project-catalog-page">
    <header class="catalog-head">
      <div>
        <h1>测试项目</h1>
        <p>查找和管理测试项目，进入项目后查看完整资料与测试资源。</p>
      </div>
      <NButton type="primary" @click="openCreate">
        <template #icon>
          <NIcon><i class="fa-solid fa-plus" /></NIcon>
        </template>
        新建项目
      </NButton>
    </header>

    <div class="catalog-toolbar">
      <NInput
        v-model:value="query"
        clearable
        placeholder="搜索项目名称或标识"
        style="width: 320px"
        @keyup.enter="applyFilters({ q: query.trim() || undefined, page: undefined })"
      >
        <template #prefix>
          <NIcon><i class="fa-solid fa-magnifying-glass" /></NIcon>
        </template>
      </NInput>
      <NSelect
        :value="status"
        :options="statusOptions"
        style="width: 150px"
        @update:value="changeStatus"
      />
      <NButton @click="applyFilters({ q: query.trim() || undefined, page: undefined })">查询</NButton>
      <NRadioGroup :value="status" size="small" @update:value="changeStatus">
        <NRadioButton v-for="option in filterSegments" :key="option.value" :value="option.value">
          {{ option.label }}
        </NRadioButton>
      </NRadioGroup>
    </div>

    <NAlert v-if="error" type="error" :bordered="false" class="catalog-alert">
      {{ error }}
    </NAlert>

    <NDataTable
      :columns="columns"
      :data="items"
      :loading="loading"
      :row-key="(row: ProjectRecord) => row.id"
      :scroll-x="960"
      :bordered="false"
      size="small"
    >
      <template #empty>
        <NEmpty description="暂无符合条件的测试项目">
          <template #extra>
            <NButton size="small" @click="openCreate">新建项目</NButton>
          </template>
        </NEmpty>
      </template>
    </NDataTable>

    <footer class="catalog-footer">
      <span class="catalog-muted">共 {{ total }} 个项目</span>
      <NPagination
        :page="page"
        :page-size="pageSize"
        :item-count="total"
        :page-sizes="PAGE_SIZES"
        show-size-picker
        show-quick-jumper
        @update:page="changePage"
        @update:page-size="changePageSize"
      />
    </footer>

    <ProjectFormModal
      v-model:show="formOpen"
      :project="editingProject"
      @saved="load"
    />
  </section>
</template>

<style scoped>
.project-catalog-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.catalog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.catalog-head h1 {
  margin: 0;
  font-size: 24px;
}

.catalog-head p {
  margin: 6px 0 0;
  color: var(--muted);
}

.catalog-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.catalog-alert {
  margin: 0;
}

.catalog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.catalog-muted {
  color: var(--muted);
}

:deep(.catalog-project-cell) {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

:deep(.catalog-project-mark) {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  flex: none;
  background: color-mix(in srgb, var(--text) 8%, transparent);
  font-weight: 700;
}

:deep(.catalog-project-copy) {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

:deep(.catalog-project-copy small) {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.catalog-actions) {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
