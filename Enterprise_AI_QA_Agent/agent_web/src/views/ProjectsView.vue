<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useMessage } from "naive-ui";

import { api } from "../services/api";
import { formatServerDateTime } from "../utils/datetime";
import type { ProjectOverview, ProjectRecord, ProjectStatus } from "../types";

const toast = useMessage();
const projects = ref<ProjectRecord[]>([]);
const selectedId = ref("");
const overview = ref<ProjectOverview | null>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const query = ref("");
const statusFilter = ref<"" | ProjectStatus>("");
const editorOpen = ref(false);
const editingId = ref("");
const form = ref({ project_key: "", name: "", description: "", base_url: "", graph_scope_key: "" });

const selected = computed(() => projects.value.find(item => item.id === selectedId.value) ?? null);

async function loadProjects() {
  loading.value = true;
  error.value = "";
  try {
    const page = await api.listProjects({
      status: statusFilter.value || undefined,
      query: query.value.trim() || undefined,
      limit: 100,
    });
    projects.value = page.items;
    if (!projects.value.some(item => item.id === selectedId.value)) {
      selectedId.value = projects.value[0]?.id || "";
    }
    await loadOverview();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "项目加载失败";
  } finally {
    loading.value = false;
  }
}

async function loadOverview() {
  if (!selectedId.value) {
    overview.value = null;
    return;
  }
  try {
    overview.value = await api.getProjectOverview(selectedId.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "项目概览加载失败";
  }
}

function openCreate() {
  editingId.value = "";
  form.value = { project_key: "", name: "", description: "", base_url: "", graph_scope_key: "" };
  editorOpen.value = true;
}

function openEdit(project: ProjectRecord) {
  editingId.value = project.id;
  form.value = {
    project_key: project.project_key,
    name: project.name,
    description: project.description || "",
    base_url: project.base_url || "",
    graph_scope_key: project.graph_scope_key || "",
  };
  editorOpen.value = true;
}

async function saveProject() {
  if (!form.value.name.trim() || (!editingId.value && !form.value.project_key.trim())) {
    toast.error("项目标识和名称不能为空");
    return;
  }
  saving.value = true;
  try {
    const common = {
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      base_url: form.value.base_url.trim() || null,
      graph_scope_key: form.value.graph_scope_key.trim() || null,
    };
    const saved = editingId.value
      ? await api.updateProject(editingId.value, common)
      : await api.createProject({ project_key: form.value.project_key.trim(), ...common });
    editorOpen.value = false;
    selectedId.value = saved.id;
    await loadProjects();
    toast.success(editingId.value ? "项目已更新" : "项目已创建");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "保存项目失败");
  } finally {
    saving.value = false;
  }
}

async function archiveSelected() {
  if (!selected.value || selected.value.status === "archived") return;
  try {
    await api.archiveProject(selected.value.id);
    await loadProjects();
    toast.success("项目已归档，历史资源仍被保留");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "归档失败");
  }
}

onMounted(() => void loadProjects());
</script>

<template>
  <main class="projects-page">
    <header class="page-head">
      <div>
        <p class="eyebrow">SHARED TEST RESOURCES</p>
        <h1>测试项目</h1>
        <p>统一管理 API 文档、知识图谱与跨模式测试历史。</p>
      </div>
      <button class="primary" @click="openCreate"><i class="fa-solid fa-plus"></i> 新建项目</button>
    </header>

    <section class="toolbar">
      <input v-model="query" placeholder="搜索项目名称或标识" @keyup.enter="loadProjects">
      <select v-model="statusFilter" @change="loadProjects">
        <option value="">全部状态</option>
        <option value="active">启用中</option>
        <option value="archived">已归档</option>
      </select>
      <button @click="loadProjects">查询</button>
    </section>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="project-grid">
      <section class="project-list">
        <div v-if="loading" class="empty">正在加载项目…</div>
        <button
          v-for="project in projects"
          v-else
          :key="project.id"
          class="project-row"
          :class="{ active: project.id === selectedId }"
          @click="selectedId = project.id; loadOverview()"
        >
          <span class="project-monogram">{{ project.name.slice(0, 1).toUpperCase() }}</span>
          <span class="project-copy">
            <strong>{{ project.name }}</strong>
            <small>{{ project.project_key }} · {{ formatServerDateTime(project.updated_at) }}</small>
          </span>
          <span class="status" :class="project.status">{{ project.status === "active" ? "启用中" : "已归档" }}</span>
        </button>
        <div v-if="!loading && !projects.length" class="empty">暂无符合条件的测试项目</div>
      </section>

      <section v-if="selected" class="project-detail">
        <div class="detail-head">
          <div><h2>{{ selected.name }}</h2><p>{{ selected.description || "暂无项目说明" }}</p></div>
          <div class="actions">
            <button :disabled="selected.status === 'archived'" @click="openEdit(selected)">编辑</button>
            <button class="danger" :disabled="selected.status === 'archived'" @click="archiveSelected">归档</button>
          </div>
        </div>
        <div class="stats">
          <article><span>API 文档</span><strong>{{ overview?.api_doc_count ?? 0 }}</strong></article>
          <article><span>测试会话</span><strong>{{ overview?.session_count ?? 0 }}</strong></article>
          <article><span>图谱节点</span><strong>{{ (overview?.graph.page_count ?? 0) + (overview?.graph.element_count ?? 0) + (overview?.graph.entity_count ?? 0) }}</strong></article>
          <article><span>图谱关系</span><strong>{{ overview?.graph.edge_count ?? 0 }}</strong></article>
        </div>
        <dl class="facts">
          <div><dt>Base URL</dt><dd>{{ selected.base_url || "未设置" }}</dd></div>
          <div><dt>图谱 Scope</dt><dd>{{ selected.graph_scope_key || "未绑定" }}</dd></div>
          <div><dt>项目 ID</dt><dd>{{ selected.id }}</dd></div>
        </dl>
        <p v-if="overview && !overview.graph.available" class="graph-warning">图谱暂不可用：{{ overview.graph.error }}</p>
      </section>
      <section v-else class="project-detail empty">选择一个项目查看资源概览</section>
    </div>

    <div v-if="editorOpen" class="modal-backdrop" @click.self="editorOpen = false">
      <section class="editor">
        <header><h2>{{ editingId ? "编辑项目" : "新建测试项目" }}</h2><button @click="editorOpen = false">×</button></header>
        <label>项目标识<input v-model="form.project_key" :disabled="Boolean(editingId)" placeholder="orders-api"></label>
        <label>项目名称<input v-model="form.name" placeholder="订单服务"></label>
        <label>项目说明<textarea v-model="form.description" rows="3"></textarea></label>
        <label>Base URL<input v-model="form.base_url" placeholder="https://api.example.com"></label>
        <label>知识图谱 Scope<input v-model="form.graph_scope_key" placeholder="可选：映射现有 project_scope"></label>
        <footer><button @click="editorOpen = false">取消</button><button class="primary" :disabled="saving" @click="saveProject">{{ saving ? "保存中…" : "保存" }}</button></footer>
      </section>
    </div>
  </main>
</template>

<style scoped>
.projects-page{padding:28px 32px;color:var(--text-primary,#111827);min-height:100%}.page-head,.detail-head,.toolbar,.actions,.editor header,.editor footer{display:flex;align-items:center;justify-content:space-between;gap:14px}.page-head h1,.detail-head h2{margin:4px 0}.page-head p,.detail-head p{margin:0;color:var(--text-secondary,#6b7280)}.eyebrow{font-size:11px;letter-spacing:.15em;font-weight:700}.primary{background:#111827!important;color:#fff!important}.toolbar{justify-content:flex-start;margin:24px 0}.toolbar input{min-width:280px}.toolbar input,.toolbar select,.toolbar button,.actions button,.editor input,.editor textarea,.editor button{border:1px solid var(--border-color,#e5e7eb);border-radius:9px;background:var(--surface,#fff);color:inherit;padding:9px 12px}.project-grid{display:grid;grid-template-columns:minmax(300px,38%) 1fr;gap:20px}.project-list,.project-detail{border:1px solid var(--border-color,#e5e7eb);border-radius:14px;background:var(--surface,#fff);overflow:hidden}.project-row{width:100%;display:flex;align-items:center;gap:12px;text-align:left;border:0;border-bottom:1px solid var(--border-color,#e5e7eb);background:transparent;padding:15px;color:inherit}.project-row.active{background:rgba(59,130,246,.08)}.project-monogram{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#111827;color:#fff;font-weight:700}.project-copy{display:flex;flex:1;flex-direction:column;gap:4px}.project-copy small{color:#6b7280}.status{font-size:12px;padding:4px 8px;border-radius:99px}.status.active{background:#dcfce7;color:#166534}.status.archived{background:#f3f4f6;color:#6b7280}.project-detail{padding:22px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}.stats article{padding:16px;border:1px solid var(--border-color,#e5e7eb);border-radius:12px}.stats span{display:block;color:#6b7280;font-size:12px}.stats strong{font-size:26px}.facts div{display:grid;grid-template-columns:120px 1fr;padding:10px 0;border-bottom:1px solid var(--border-color,#e5e7eb)}.facts dt{color:#6b7280}.facts dd{margin:0;word-break:break-all}.danger{color:#b91c1c}.empty{padding:36px;text-align:center;color:#6b7280}.error-banner,.graph-warning{padding:10px 12px;border-radius:8px;background:#fef2f2;color:#b91c1c}.modal-backdrop{position:fixed;inset:0;z-index:80;display:grid;place-items:center;background:rgba(0,0,0,.45)}.editor{width:min(560px,90vw);padding:22px;border-radius:14px;background:var(--surface,#fff)}.editor label{display:flex;flex-direction:column;gap:6px;margin:14px 0}.editor footer{justify-content:flex-end;margin-top:20px}@media(max-width:900px){.project-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
</style>
