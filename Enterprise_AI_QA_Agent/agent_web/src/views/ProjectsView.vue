<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useMessage } from "naive-ui";

import { api } from "../services/api";
import { formatServerDateTime } from "../utils/datetime";
import type {
  ModeDescriptor,
  ProjectOverview,
  ProjectRecord,
  ProjectStatus,
  RegressionBatchRecord,
  RegressionContext,
  RegressionFailureStatus,
  RegressionFailureSummary,
  TestCaseLifecycleStatus,
  TestCaseRecord,
  TestCaseVersionRecord,
  TestSuiteBundle,
  TestRunRecord,
  TestRunStatus,
} from "../types";

type ResourceTab = "cases" | "suites" | "runs" | "regressions";

const CASE_PAGE_SIZE = 20;
const SUITE_PAGE_SIZE = 20;
const RUN_PAGE_SIZE = 20;
const REGRESSION_PAGE_SIZE = 20;
const REGRESSION_BATCH_PAGE_SIZE = 20;

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

const resourceTab = ref<ResourceTab>("cases");
const modes = ref<ModeDescriptor[]>([]);
const testCases = ref<TestCaseRecord[]>([]);
const casesLoading = ref(false);
const caseOffset = ref(0);
const casesHaveMore = ref(false);
const caseQuery = ref("");
const caseStatus = ref<"" | TestCaseLifecycleStatus>("");
const actionCaseId = ref("");
const selectedCasesById = ref<Record<string, TestCaseRecord>>({});

const suites = ref<TestSuiteBundle[]>([]);
const suitesLoading = ref(false);
const suiteOffset = ref(0);
const suitesHaveMore = ref(false);

const testRuns = ref<TestRunRecord[]>([]);
const runsLoading = ref(false);
const runOffset = ref(0);
const runsHaveMore = ref(false);
const runStatus = ref<"" | TestRunStatus>("");
const regressionRunId = ref("");

const regressionFailures = ref<RegressionFailureSummary[]>([]);
const regressionsLoading = ref(false);
const regressionStatus = ref<"" | RegressionFailureStatus>("");
const regressionMode = ref("");
const regressionCursor = ref<string | undefined>(undefined);
const regressionNextCursor = ref<string | null>(null);
const regressionCursorStack = ref<Array<string | undefined>>([]);
const selectedRegressionResults = ref<Record<string, RegressionFailureSummary>>({});
const regressionDrawerOpen = ref(false);
const regressionDrawerLoading = ref(false);
const regressionContext = ref<RegressionContext | null>(null);
const regressionBatches = ref<RegressionBatchRecord[]>([]);
const regressionBatchNextCursor = ref<string | null>(null);
const regressionBatchesLoading = ref(false);

const generationOpen = ref(false);
const generating = ref(false);
const generationForm = ref({ objective: "", mode_key: "", model_key: "" });

const activationOpen = ref(false);
const activationCase = ref<TestCaseRecord | null>(null);
const versionsLoading = ref(false);
const activating = ref(false);
const versions = ref<TestCaseVersionRecord[]>([]);
const activationVersionId = ref("");

const suiteEditorOpen = ref(false);
const suiteSaving = ref(false);
const suiteForm = ref({ name: "", description: "" });

const runEditorOpen = ref(false);
const runSaving = ref(false);
const runSuite = ref<TestSuiteBundle | null>(null);
const runForm = ref({ mode_key: "", session_id: "" });

const selected = computed(() => projects.value.find(item => item.id === selectedId.value) ?? null);
const generationModes = computed(() => modes.value.filter(item => item.is_test_mode && item.case_driven_policy !== "exempt"));
const selectedActiveCases = computed(() => Object.values(selectedCasesById.value));
const selectedRegressionFailures = computed(() => Object.values(selectedRegressionResults.value));

const lifecycleLabels: Record<TestCaseLifecycleStatus, string> = {
  draft: "草稿",
  pending_review: "待评审",
  active: "已启用",
  disabled: "已停用",
  archived: "已归档",
};

function modeLabel(modeKey: string) {
  return modes.value.find(item => item.key === modeKey)?.name || modeKey;
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function canCreateRegression(run: TestRunRecord) {
  return (
    (run.status === "completed" || run.status === "cancelled")
    && run.stats.failed + run.stats.error + run.stats.blocked > 0
  );
}

async function loadModes() {
  try {
    modes.value = await api.listModes();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "测试模式加载失败");
  }
}

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
      resetProjectResources();
    }
    await loadProjectDetail();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "项目加载失败";
  } finally {
    loading.value = false;
  }
}

function resetProjectResources() {
  overview.value = null;
  resourceTab.value = "cases";
  caseOffset.value = 0;
  suiteOffset.value = 0;
  runOffset.value = 0;
  testCases.value = [];
  suites.value = [];
  testRuns.value = [];
  regressionFailures.value = [];
  regressionCursor.value = undefined;
  regressionNextCursor.value = null;
  regressionCursorStack.value = [];
  selectedRegressionResults.value = {};
  regressionDrawerOpen.value = false;
  regressionContext.value = null;
  regressionBatches.value = [];
  selectedCasesById.value = {};
}

async function loadProjectDetail() {
  if (!selectedId.value) {
    overview.value = null;
    return;
  }
  await Promise.all([
    loadOverview(),
    resourceTab.value === "cases"
      ? loadTestCases()
      : resourceTab.value === "suites"
        ? loadTestSuites()
        : resourceTab.value === "runs"
          ? loadTestRuns()
          : loadRegressionFailures(),
  ]);
}

async function selectProject(projectId: string) {
  if (selectedId.value === projectId) return;
  selectedId.value = projectId;
  resetProjectResources();
  await loadProjectDetail();
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

async function loadTestCases() {
  if (!selectedId.value) return;
  casesLoading.value = true;
  try {
    const page = await api.listTestCases(selectedId.value, {
      status: caseStatus.value || undefined,
      query: caseQuery.value.trim() || undefined,
      limit: CASE_PAGE_SIZE,
      offset: caseOffset.value,
    });
    testCases.value = page.items;
    casesHaveMore.value = page.has_more;
    for (const testCase of page.items) {
      if (selectedCasesById.value[testCase.id]) {
        if (testCase.lifecycle_status === "active" && testCase.active_version_id) {
          selectedCasesById.value[testCase.id] = testCase;
        } else {
          delete selectedCasesById.value[testCase.id];
        }
      }
    }
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "测试用例加载失败");
  } finally {
    casesLoading.value = false;
  }
}

async function loadTestSuites() {
  if (!selectedId.value) return;
  suitesLoading.value = true;
  try {
    const page = await api.listTestSuites(selectedId.value, {
      limit: SUITE_PAGE_SIZE,
      offset: suiteOffset.value,
    });
    suites.value = page.items;
    suitesHaveMore.value = page.has_more;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "测试套件加载失败");
  } finally {
    suitesLoading.value = false;
  }
}

async function loadTestRuns() {
  if (!selectedId.value) return;
  runsLoading.value = true;
  try {
    const page = await api.listTestRuns(selectedId.value, {
      status: runStatus.value || undefined,
      limit: RUN_PAGE_SIZE,
      offset: runOffset.value,
    });
    testRuns.value = page.items;
    runsHaveMore.value = page.has_more;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "测试运行加载失败");
  } finally {
    runsLoading.value = false;
  }
}

async function loadRegressionFailures(cursor = regressionCursor.value) {
  if (!selectedId.value) return;
  regressionsLoading.value = true;
  try {
    const page = await api.listRegressionFailures(selectedId.value, {
      failure_status: regressionStatus.value || undefined,
      mode_key: regressionMode.value || undefined,
      cursor,
      limit: REGRESSION_PAGE_SIZE,
    });
    regressionFailures.value = page.items;
    regressionCursor.value = cursor;
    regressionNextCursor.value = page.next_cursor || null;
    const visibleIds = new Set(page.items.map(item => item.source_result_id));
    for (const resultId of Object.keys(selectedRegressionResults.value)) {
      if (!visibleIds.has(resultId)) delete selectedRegressionResults.value[resultId];
    }
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "回归失败项加载失败");
  } finally {
    regressionsLoading.value = false;
  }
}

async function searchRegressionFailures() {
  regressionCursor.value = undefined;
  regressionNextCursor.value = null;
  regressionCursorStack.value = [];
  selectedRegressionResults.value = {};
  await loadRegressionFailures(undefined);
}

async function changeRegressionPage(direction: -1 | 1) {
  if (direction === 1) {
    if (!regressionNextCursor.value) return;
    regressionCursorStack.value.push(regressionCursor.value);
    await loadRegressionFailures(regressionNextCursor.value);
    return;
  }
  if (!regressionCursorStack.value.length) return;
  await loadRegressionFailures(regressionCursorStack.value.pop());
}

async function switchResourceTab(tab: ResourceTab) {
  resourceTab.value = tab;
  if (tab === "cases") {
    caseOffset.value = 0;
    await loadTestCases();
  } else if (tab === "suites") {
    suiteOffset.value = 0;
    await loadTestSuites();
  } else if (tab === "runs") {
    runOffset.value = 0;
    await loadTestRuns();
  } else {
    await searchRegressionFailures();
  }
}

async function searchCases() {
  caseOffset.value = 0;
  await loadTestCases();
}

async function changeCasePage(direction: -1 | 1) {
  caseOffset.value = Math.max(0, caseOffset.value + direction * CASE_PAGE_SIZE);
  await loadTestCases();
}

async function changeSuitePage(direction: -1 | 1) {
  suiteOffset.value = Math.max(0, suiteOffset.value + direction * SUITE_PAGE_SIZE);
  await loadTestSuites();
}

async function changeRunPage(direction: -1 | 1) {
  runOffset.value = Math.max(0, runOffset.value + direction * RUN_PAGE_SIZE);
  await loadTestRuns();
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

function openGeneration() {
  if (!generationModes.value.length) {
    toast.error("当前没有可用于生成测试用例的测试模式");
    return;
  }
  generationForm.value = { objective: "", mode_key: generationModes.value[0].key, model_key: "" };
  generationOpen.value = true;
}

async function generateCases() {
  if (!selectedId.value || !generationForm.value.objective.trim() || !generationForm.value.mode_key) {
    toast.error("请填写生成目标并选择测试模式");
    return;
  }
  generating.value = true;
  try {
    const result = await api.generateTestCases(selectedId.value, {
      objective: generationForm.value.objective.trim(),
      mode_key: generationForm.value.mode_key,
      ...(generationForm.value.model_key.trim() ? { model_key: generationForm.value.model_key.trim() } : {}),
    });
    generationOpen.value = false;
    caseOffset.value = 0;
    await Promise.all([loadTestCases(), loadOverview()]);
    toast.success(`已生成 ${result.items.length} 条测试用例草稿`);
    if (result.warnings.length) toast.warning(result.warnings.join("；"));
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "测试用例生成失败");
  } finally {
    generating.value = false;
  }
}

async function submitReview(testCase: TestCaseRecord) {
  actionCaseId.value = testCase.id;
  try {
    await api.submitTestCaseReview(testCase.id);
    await Promise.all([loadTestCases(), loadOverview()]);
    toast.success("测试用例已提交评审");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "提交评审失败");
  } finally {
    actionCaseId.value = "";
  }
}

async function openActivation(testCase: TestCaseRecord) {
  activationCase.value = testCase;
  activationOpen.value = true;
  versionsLoading.value = true;
  versions.value = [];
  activationVersionId.value = "";
  try {
    versions.value = (await api.listTestCaseVersions(testCase.id)).sort((left, right) => right.version - left.version);
    activationVersionId.value = testCase.active_version_id
      || versions.value.find(item => item.version === testCase.latest_version)?.id
      || versions.value[0]?.id
      || "";
  } catch (err) {
    activationOpen.value = false;
    toast.error(err instanceof Error ? err.message : "用例版本加载失败");
  } finally {
    versionsLoading.value = false;
  }
}

async function activateVersion() {
  if (!activationCase.value || !activationVersionId.value) {
    toast.error("请选择要启用的用例版本");
    return;
  }
  activating.value = true;
  try {
    await api.activateTestCase(activationCase.value.id, activationVersionId.value);
    activationOpen.value = false;
    await Promise.all([loadTestCases(), loadOverview()]);
    toast.success("指定用例版本已启用");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "启用用例版本失败");
  } finally {
    activating.value = false;
  }
}

function toggleCaseSelection(testCase: TestCaseRecord, checked: boolean) {
  if (checked && testCase.lifecycle_status === "active" && testCase.active_version_id) {
    selectedCasesById.value[testCase.id] = testCase;
  } else {
    delete selectedCasesById.value[testCase.id];
  }
}

function openSuiteEditor() {
  if (!selectedActiveCases.value.length) {
    toast.error("请先勾选至少一条已启用用例");
    return;
  }
  suiteForm.value = { name: "", description: "" };
  suiteEditorOpen.value = true;
}

async function createSuite() {
  if (!selectedId.value || !suiteForm.value.name.trim()) {
    toast.error("请填写套件名称");
    return;
  }
  if (!selectedActiveCases.value.length) {
    toast.error("请至少勾选一条已启用用例");
    return;
  }
  suiteSaving.value = true;
  try {
    await api.createTestSuite(selectedId.value, {
      name: suiteForm.value.name.trim(),
      description: suiteForm.value.description.trim() || null,
      items: selectedActiveCases.value.map(item => ({
        case_id: item.id,
        case_version_id: item.active_version_id as string,
      })),
    });
    suiteEditorOpen.value = false;
    selectedCasesById.value = {};
    resourceTab.value = "suites";
    suiteOffset.value = 0;
    await Promise.all([loadTestSuites(), loadOverview()]);
    toast.success("固定版本测试套件已创建");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "创建测试套件失败");
  } finally {
    suiteSaving.value = false;
  }
}

function openRunEditor(bundle: TestSuiteBundle) {
  const availableModes = generationModes.value;
  if (!availableModes.length) {
    toast.error("当前没有可执行的用例驱动测试模式");
    return;
  }
  runSuite.value = bundle;
  runForm.value = { mode_key: availableModes[0].key, session_id: "" };
  runEditorOpen.value = true;
}

async function createRun() {
  if (!runSuite.value || !runForm.value.mode_key) {
    toast.error("请选择测试模式");
    return;
  }
  runSaving.value = true;
  try {
    await api.createTestRun(runSuite.value.suite.id, {
      mode_key: runForm.value.mode_key,
      ...(runForm.value.session_id.trim() ? { session_id: runForm.value.session_id.trim() } : {}),
    });
    runEditorOpen.value = false;
    resourceTab.value = "runs";
    runOffset.value = 0;
    await Promise.all([loadTestRuns(), loadOverview()]);
    toast.success("测试运行已创建，等待 Worker 领取固定版本用例");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "创建测试运行失败");
  } finally {
    runSaving.value = false;
  }
}

async function cancelRun(run: TestRunRecord) {
  try {
    await api.cancelTestRun(run.id, "Cancelled from project management");
    await loadTestRuns();
    toast.success("测试运行已取消");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "取消测试运行失败");
  }
}

async function createRegressionRun(run: TestRunRecord) {
  if (!canCreateRegression(run) || regressionRunId.value) return;
  regressionRunId.value = run.id;
  try {
    const created = await api.createRegressionTestRun(run.id);
    toast.success(`已创建回归运行 ${shortId(created.run.id)}`);
    await Promise.all([loadTestRuns(), loadOverview()]);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "回归运行创建失败");
  } finally {
    regressionRunId.value = "";
  }
}

function toggleRegressionSelection(failure: RegressionFailureSummary, checked: boolean) {
  if (!checked) {
    delete selectedRegressionResults.value[failure.source_result_id];
    return;
  }
  const selectedSourceRunId = selectedRegressionFailures.value[0]?.source_run_id;
  if (selectedSourceRunId && selectedSourceRunId !== failure.source_run_id) {
    toast.warning("一次回归只能选择同一个原始运行中的失败项");
    return;
  }
  selectedRegressionResults.value[failure.source_result_id] = failure;
}

function regressionSelectionDisabled(failure: RegressionFailureSummary) {
  const selectedSourceRunId = selectedRegressionFailures.value[0]?.source_run_id;
  return Boolean(selectedSourceRunId && selectedSourceRunId !== failure.source_run_id);
}

async function createSelectedRegressionRun() {
  const selectedFailures = selectedRegressionFailures.value;
  const sourceRunId = selectedFailures[0]?.source_run_id;
  if (!sourceRunId || regressionRunId.value) return;
  regressionRunId.value = sourceRunId;
  try {
    const created = await api.createRegressionTestRun(sourceRunId, {
      result_ids: selectedFailures.map(item => item.source_result_id),
    });
    selectedRegressionResults.value = {};
    await Promise.all([loadRegressionFailures(), loadOverview()]);
    toast.success(`已创建 ${selectedFailures.length} 条失败项的回归运行 ${shortId(created.run.id)}`);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "批量回归运行创建失败");
  } finally {
    regressionRunId.value = "";
  }
}

async function openRegressionEvidence(failure: RegressionFailureSummary) {
  regressionDrawerOpen.value = true;
  regressionDrawerLoading.value = true;
  regressionContext.value = null;
  regressionBatches.value = [];
  regressionBatchNextCursor.value = null;
  try {
    const [context, batches] = await Promise.all([
      api.getRegressionContext(failure.source_result_id),
      api.listRegressionBatches(failure.source_result_id, {
        limit: REGRESSION_BATCH_PAGE_SIZE,
      }),
    ]);
    regressionContext.value = context;
    regressionBatches.value = batches.items;
    regressionBatchNextCursor.value = batches.next_cursor || null;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "失败证据加载失败");
  } finally {
    regressionDrawerLoading.value = false;
  }
}

async function loadMoreRegressionBatches() {
  if (
    !regressionContext.value
    || !regressionBatchNextCursor.value
    || regressionBatchesLoading.value
  ) return;
  regressionBatchesLoading.value = true;
  try {
    const page = await api.listRegressionBatches(
      regressionContext.value.source_result_id,
      {
        cursor: regressionBatchNextCursor.value,
        limit: REGRESSION_BATCH_PAGE_SIZE,
      },
    );
    regressionBatches.value.push(...page.items);
    regressionBatchNextCursor.value = page.next_cursor || null;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "更多回归批次加载失败");
  } finally {
    regressionBatchesLoading.value = false;
  }
}

onMounted(() => {
  void Promise.all([loadModes(), loadProjects()]);
});
</script>

<template>
  <main class="projects-page">
    <header class="page-head">
      <div>
        <p class="eyebrow">SHARED TEST RESOURCES</p>
        <h1>测试项目</h1>
        <p>统一管理 API 文档、测试用例、固定版本套件、知识图谱与跨模式测试历史。</p>
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
          @click="selectProject(project.id)"
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
          <article><span>测试用例</span><strong>{{ overview?.test_case_count ?? 0 }}</strong></article>
          <article><span>测试套件</span><strong>{{ overview?.test_suite_count ?? 0 }}</strong></article>
          <article><span>测试运行</span><strong>{{ overview?.test_run_count ?? 0 }}</strong></article>
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

        <section class="resource-panel">
          <header class="resource-head">
            <div class="tabs" role="tablist" aria-label="项目测试资源">
              <button :class="{ active: resourceTab === 'cases' }" @click="switchResourceTab('cases')">测试用例</button>
              <button :class="{ active: resourceTab === 'suites' }" @click="switchResourceTab('suites')">测试套件</button>
              <button :class="{ active: resourceTab === 'runs' }" @click="switchResourceTab('runs')">测试历史</button>
              <button :class="{ active: resourceTab === 'regressions' }" @click="switchResourceTab('regressions')">回归中心</button>
            </div>
            <div v-if="resourceTab === 'cases'" class="actions">
              <button :disabled="selected.status === 'archived'" @click="openSuiteEditor">创建套件</button>
              <button class="primary" :disabled="selected.status === 'archived'" @click="openGeneration">生成用例</button>
            </div>
            <div v-else-if="resourceTab === 'regressions'" class="actions">
              <button
                class="primary"
                :disabled="selected.status === 'archived' || !selectedRegressionFailures.length || Boolean(regressionRunId)"
                @click="createSelectedRegressionRun"
              >所选失败项回归（{{ selectedRegressionFailures.length }}）</button>
            </div>
          </header>

          <template v-if="resourceTab === 'cases'">
            <div class="case-toolbar">
              <input v-model="caseQuery" placeholder="搜索用例标识或标题" @keyup.enter="searchCases">
              <select v-model="caseStatus" @change="searchCases">
                <option value="">全部状态</option>
                <option v-for="(label, value) in lifecycleLabels" :key="value" :value="value">{{ label }}</option>
              </select>
              <button @click="searchCases">查询</button>
              <small>已选 {{ selectedActiveCases.length }} 条已启用用例</small>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>选择</th><th>用例</th><th>状态</th><th>模式</th><th>优先级</th><th>当前版本</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-if="casesLoading"><td colspan="7" class="empty">正在加载测试用例…</td></tr>
                  <tr v-for="testCase in testCases" v-else :key="testCase.id">
                    <td>
                      <input
                        type="checkbox"
                        :checked="Boolean(selectedCasesById[testCase.id])"
                        :disabled="testCase.lifecycle_status !== 'active' || !testCase.active_version_id"
                        :aria-label="`选择用例 ${testCase.title}`"
                        @change="toggleCaseSelection(testCase, ($event.target as HTMLInputElement).checked)"
                      >
                    </td>
                    <td><strong>{{ testCase.title }}</strong><small>{{ testCase.case_key }} · {{ testCase.case_type }}</small></td>
                    <td><span class="case-status" :class="testCase.lifecycle_status">{{ lifecycleLabels[testCase.lifecycle_status] }}</span></td>
                    <td>{{ modeLabel(testCase.mode_key) }}</td>
                    <td><span class="priority" :class="testCase.priority.toLowerCase()">{{ testCase.priority }}</span></td>
                    <td>
                      <span v-if="testCase.active_version_id" :title="testCase.active_version_id">{{ shortId(testCase.active_version_id) }}</span>
                      <span v-else>未启用</span>
                      <small>最新 v{{ testCase.latest_version }}</small>
                    </td>
                    <td class="row-actions">
                      <button v-if="testCase.lifecycle_status === 'draft'" :disabled="actionCaseId === testCase.id" @click="submitReview(testCase)">提交评审</button>
                      <button v-if="testCase.lifecycle_status === 'pending_review'" @click="openActivation(testCase)">启用版本</button>
                      <span v-if="testCase.lifecycle_status !== 'draft' && testCase.lifecycle_status !== 'pending_review'">—</span>
                    </td>
                  </tr>
                  <tr v-if="!casesLoading && !testCases.length"><td colspan="7" class="empty">暂无符合条件的测试用例</td></tr>
                </tbody>
              </table>
            </div>
            <footer class="pagination">
              <span>第 {{ Math.floor(caseOffset / CASE_PAGE_SIZE) + 1 }} 页</span>
              <div><button :disabled="caseOffset === 0 || casesLoading" @click="changeCasePage(-1)">上一页</button><button :disabled="!casesHaveMore || casesLoading" @click="changeCasePage(1)">下一页</button></div>
            </footer>
          </template>

          <template v-else-if="resourceTab === 'suites'">
            <div class="table-wrap suites-table">
              <table>
                <thead><tr><th>套件名称</th><th>状态</th><th>条目数量</th><th>说明</th><th>更新时间</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-if="suitesLoading"><td colspan="6" class="empty">正在加载测试套件…</td></tr>
                  <tr v-for="bundle in suites" v-else :key="bundle.suite.id">
                    <td><strong>{{ bundle.suite.name }}</strong><small>{{ bundle.suite.id }}</small></td>
                    <td><span class="case-status" :class="bundle.suite.status">{{ bundle.suite.status === "active" ? "启用中" : "已归档" }}</span></td>
                    <td>{{ bundle.items.length }}</td>
                    <td>{{ bundle.suite.description || "—" }}</td>
                    <td>{{ formatServerDateTime(bundle.suite.updated_at) }}</td>
                    <td class="row-actions"><button :disabled="selected.status === 'archived' || bundle.suite.status !== 'active'" @click="openRunEditor(bundle)">创建运行</button></td>
                  </tr>
                  <tr v-if="!suitesLoading && !suites.length"><td colspan="6" class="empty">暂无测试套件</td></tr>
                </tbody>
              </table>
            </div>
            <footer class="pagination">
              <span>第 {{ Math.floor(suiteOffset / SUITE_PAGE_SIZE) + 1 }} 页</span>
              <div><button :disabled="suiteOffset === 0 || suitesLoading" @click="changeSuitePage(-1)">上一页</button><button :disabled="!suitesHaveMore || suitesLoading" @click="changeSuitePage(1)">下一页</button></div>
            </footer>
          </template>

          <template v-else-if="resourceTab === 'runs'">
            <div class="case-toolbar">
              <select v-model="runStatus" @change="runOffset = 0; loadTestRuns()">
                <option value="">全部运行状态</option>
                <option value="queued">等待执行</option>
                <option value="running">执行中</option>
                <option value="completed">已完成</option>
                <option value="cancelled">已取消</option>
              </select>
              <button @click="loadTestRuns">刷新</button>
              <small>运行只保存调度状态；测试结论以 Runner 结果和证据为准。</small>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>运行</th><th>状态</th><th>模式</th><th>进度</th><th>结果统计</th><th>创建时间</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-if="runsLoading"><td colspan="7" class="empty">正在加载测试运行…</td></tr>
                  <tr v-for="run in testRuns" v-else :key="run.id">
                    <td><strong>{{ shortId(run.id) }}</strong><small>{{ run.run_kind === "regression" ? "回归" : "普通" }} · 套件 {{ shortId(run.suite_id) }}</small><small v-if="run.parent_run_id">来源 {{ shortId(run.parent_run_id) }}</small></td>
                    <td><span class="case-status" :class="run.status">{{ run.status }}</span></td>
                    <td>{{ modeLabel(run.mode_key) }}</td>
                    <td>{{ run.stats.passed + run.stats.failed + run.stats.error + run.stats.blocked + run.stats.skipped + run.stats.cancelled }} / {{ run.stats.total }}</td>
                    <td><small>通过 {{ run.stats.passed }} · 失败 {{ run.stats.failed }} · 错误 {{ run.stats.error }} · 阻塞 {{ run.stats.blocked }}</small></td>
                    <td>{{ formatServerDateTime(run.created_at) }}</td>
                    <td class="row-actions">
                      <button v-if="run.status === 'queued' || run.status === 'running'" @click="cancelRun(run)">取消</button>
                      <button v-if="canCreateRegression(run)" :disabled="Boolean(regressionRunId)" @click="createRegressionRun(run)">{{ regressionRunId === run.id ? "创建中…" : "失败项回归" }}</button>
                      <span v-if="run.status !== 'queued' && run.status !== 'running' && !canCreateRegression(run)">—</span>
                    </td>
                  </tr>
                  <tr v-if="!runsLoading && !testRuns.length"><td colspan="7" class="empty">暂无测试运行记录</td></tr>
                </tbody>
              </table>
            </div>
            <footer class="pagination">
              <span>第 {{ Math.floor(runOffset / RUN_PAGE_SIZE) + 1 }} 页</span>
              <div><button :disabled="runOffset === 0 || runsLoading" @click="changeRunPage(-1)">上一页</button><button :disabled="!runsHaveMore || runsLoading" @click="changeRunPage(1)">下一页</button></div>
            </footer>
          </template>

          <template v-else>
            <div class="case-toolbar">
              <select v-model="regressionStatus" @change="searchRegressionFailures">
                <option value="">全部失败状态</option>
                <option value="failed">失败</option>
                <option value="error">错误</option>
                <option value="blocked">阻塞</option>
              </select>
              <select v-model="regressionMode" @change="searchRegressionFailures">
                <option value="">全部测试模式</option>
                <option v-for="mode in modes" :key="mode.key" :value="mode.key">{{ mode.name }}</option>
              </select>
              <button @click="searchRegressionFailures">刷新</button>
              <small>批量回归仅允许选择同一个原始运行；原失败结果和用例版本保持不变。</small>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>选择</th><th>失败用例</th><th>状态</th><th>模式</th><th>证据</th><th>回归批次</th><th>失败时间</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-if="regressionsLoading"><td colspan="8" class="empty">正在加载失败用例…</td></tr>
                  <tr v-for="failure in regressionFailures" v-else :key="failure.source_result_id">
                    <td>
                      <input
                        type="checkbox"
                        :checked="Boolean(selectedRegressionResults[failure.source_result_id])"
                        :disabled="regressionSelectionDisabled(failure)"
                        :aria-label="`选择失败项 ${failure.case_title}`"
                        @change="toggleRegressionSelection(failure, ($event.target as HTMLInputElement).checked)"
                      >
                    </td>
                    <td><strong>{{ failure.case_title }}</strong><small>{{ failure.case_key }} · Run {{ shortId(failure.source_run_id) }}</small><small>{{ failure.summary }}</small></td>
                    <td><span class="case-status" :class="failure.failure_status">{{ failure.failure_status }}</span></td>
                    <td>{{ modeLabel(failure.mode_key) }}</td>
                    <td><small>Evidence {{ failure.evidence_count }} · Artifact {{ failure.artifact_count }} · Verification {{ failure.verification_count }}</small></td>
                    <td><strong>{{ failure.regression_batch_count }}</strong><small v-if="failure.latest_regression">最新 {{ failure.latest_regression.item_status }}</small><small v-else>尚未回归</small></td>
                    <td>{{ formatServerDateTime(failure.failed_at) }}</td>
                    <td class="row-actions"><button @click="openRegressionEvidence(failure)">证据与时间线</button></td>
                  </tr>
                  <tr v-if="!regressionsLoading && !regressionFailures.length"><td colspan="8" class="empty">暂无失败、错误或阻塞结果</td></tr>
                </tbody>
              </table>
            </div>
            <footer class="pagination">
              <span>第 {{ regressionCursorStack.length + 1 }} 页</span>
              <div><button :disabled="!regressionCursorStack.length || regressionsLoading" @click="changeRegressionPage(-1)">上一页</button><button :disabled="!regressionNextCursor || regressionsLoading" @click="changeRegressionPage(1)">下一页</button></div>
            </footer>
          </template>
        </section>
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

    <div v-if="generationOpen" class="modal-backdrop" @click.self="generationOpen = false">
      <section class="editor">
        <header><h2>生成测试用例草稿</h2><button @click="generationOpen = false">×</button></header>
        <label>生成目标<textarea v-model="generationForm.objective" rows="5" placeholder="描述本次需要覆盖的业务目标、范围与通过标准"></textarea></label>
        <label>测试模式<select v-model="generationForm.mode_key"><option v-for="mode in generationModes" :key="mode.key" :value="mode.key">{{ mode.name }}</option></select></label>
        <label>模型标识（可选）<input v-model="generationForm.model_key" placeholder="留空使用服务端默认模型"></label>
        <footer><button @click="generationOpen = false">取消</button><button class="primary" :disabled="generating" @click="generateCases">{{ generating ? "生成中…" : "生成草稿" }}</button></footer>
      </section>
    </div>

    <div v-if="activationOpen" class="modal-backdrop" @click.self="activationOpen = false">
      <section class="editor version-editor">
        <header><div><h2>启用指定版本</h2><p>{{ activationCase?.title }}</p></div><button @click="activationOpen = false">×</button></header>
        <div v-if="versionsLoading" class="empty">正在加载用例版本…</div>
        <label v-for="version in versions" v-else :key="version.id" class="version-option">
          <input v-model="activationVersionId" type="radio" :value="version.id">
          <span><strong>v{{ version.version }}</strong><small>{{ version.model_key }} · Prompt {{ version.prompt_version }} · {{ formatServerDateTime(version.created_at) }}</small></span>
        </label>
        <div v-if="!versionsLoading && !versions.length" class="empty">该用例暂无可启用版本</div>
        <footer><button @click="activationOpen = false">取消</button><button class="primary" :disabled="activating || !activationVersionId" @click="activateVersion">{{ activating ? "启用中…" : "启用所选版本" }}</button></footer>
      </section>
    </div>

    <div v-if="suiteEditorOpen" class="modal-backdrop" @click.self="suiteEditorOpen = false">
      <section class="editor suite-editor">
        <header><h2>创建固定版本测试套件</h2><button @click="suiteEditorOpen = false">×</button></header>
        <label>套件名称<input v-model="suiteForm.name" placeholder="订单服务核心回归套件"></label>
        <label>套件说明（可选）<textarea v-model="suiteForm.description" rows="3"></textarea></label>
        <div class="selection-summary"><strong>已选择 {{ selectedActiveCases.length }} 条用例</strong><small>套件会固定保存每条用例当前的 active_version_id。</small></div>
        <footer><button @click="suiteEditorOpen = false">取消</button><button class="primary" :disabled="suiteSaving" @click="createSuite">{{ suiteSaving ? "创建中…" : "创建套件" }}</button></footer>
      </section>
    </div>

    <div v-if="runEditorOpen" class="modal-backdrop" @click.self="runEditorOpen = false">
      <section class="editor">
        <header><div><h2>创建测试运行</h2><p>{{ runSuite?.suite.name }} · {{ runSuite?.items.length ?? 0 }} 条固定版本用例</p></div><button @click="runEditorOpen = false">×</button></header>
        <label>测试模式<select v-model="runForm.mode_key"><option v-for="mode in generationModes" :key="mode.key" :value="mode.key">{{ mode.name }}</option></select></label>
        <label>关联 Session ID（可选）<input v-model="runForm.session_id" placeholder="仅接受已绑定当前项目的 Session"></label>
        <div class="selection-summary"><strong>运行不会修改套件或用例版本</strong><small>Worker 将通过原子领取接口分批获取固定版本内容；失败、错误和阻塞结果可创建独立回归运行。</small></div>
        <footer><button @click="runEditorOpen = false">取消</button><button class="primary" :disabled="runSaving" @click="createRun">{{ runSaving ? "创建中…" : "创建运行" }}</button></footer>
      </section>
    </div>

    <div v-if="regressionDrawerOpen" class="modal-backdrop regression-backdrop" @click.self="regressionDrawerOpen = false">
      <section class="editor regression-drawer">
        <header><div><h2>证据与回归时间线</h2><p v-if="regressionContext">Result {{ shortId(regressionContext.source_result_id) }}</p></div><button @click="regressionDrawerOpen = false">×</button></header>
        <div v-if="regressionDrawerLoading" class="empty">正在按需加载失败证据与回归批次…</div>
        <template v-else-if="regressionContext">
          <section class="context-summary">
            <span class="case-status" :class="regressionContext.failure_status">{{ regressionContext.failure_status }}</span>
            <div><strong>{{ regressionContext.summary }}</strong><small v-if="regressionContext.error_message">{{ regressionContext.error_message }}</small></div>
          </section>
          <dl class="facts compact-facts">
            <div><dt>原始 Run</dt><dd>{{ regressionContext.source_run_id }}</dd></div>
            <div><dt>用例版本</dt><dd>{{ regressionContext.case_version_id }}</dd></div>
            <div><dt>失败时间</dt><dd>{{ formatServerDateTime(regressionContext.failed_at) }}</dd></div>
          </dl>

          <section class="drawer-section">
            <h3>公开证据</h3>
            <div v-if="!regressionContext.evidence.length" class="drawer-empty">无结构化 Evidence</div>
            <article v-for="evidence in regressionContext.evidence" v-else :key="`${evidence.evidence_type}:${evidence.evidence_id}`" class="timeline-card">
              <strong>{{ evidence.label || evidence.evidence_type }}</strong><small>{{ evidence.evidence_type }} · {{ evidence.evidence_id }}</small>
            </article>
            <div class="artifact-links">
              <a
                v-for="artifact in regressionContext.artifacts"
                :key="artifact.artifact_id"
                :href="artifact.content_url || undefined"
                :aria-disabled="!artifact.content_url"
                target="_blank"
                rel="noopener noreferrer"
              >Artifact {{ shortId(artifact.artifact_id) }}</a>
            </div>
          </section>

          <section class="drawer-section">
            <h3>Verification</h3>
            <div v-if="!regressionContext.verifications.length" class="drawer-empty">无 Verification 结论</div>
            <article v-for="verification in regressionContext.verifications" v-else :key="verification.id" class="timeline-card">
              <strong>{{ verification.verifier || verification.id }} · {{ verification.status }}</strong>
              <small>{{ verification.summary }} · {{ verification.passed_count }}/{{ verification.assertion_count }} 通过</small>
            </article>
          </section>

          <section class="drawer-section">
            <h3>回归批次时间线</h3>
            <div v-if="!regressionBatches.length" class="drawer-empty">尚未创建回归批次</div>
            <article v-for="batch in regressionBatches" v-else :key="batch.run_item_id" class="timeline-card">
              <strong>{{ formatServerDateTime(batch.created_at) }} · {{ batch.item_status }}</strong>
              <small>Run {{ shortId(batch.run_id) }} · 版本 {{ shortId(batch.case_version_id) }}</small>
              <small v-if="batch.result_status">回归结果：{{ batch.result_status }}</small>
            </article>
            <button v-if="regressionBatchNextCursor" :disabled="regressionBatchesLoading" @click="loadMoreRegressionBatches">{{ regressionBatchesLoading ? "加载中…" : "加载更多批次" }}</button>
          </section>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.projects-page{padding:28px 32px;color:var(--text-primary,#111827);min-height:100%}.page-head,.detail-head,.toolbar,.actions,.editor header,.editor footer,.resource-head,.case-toolbar,.pagination{display:flex;align-items:center;justify-content:space-between;gap:14px}.page-head h1,.detail-head h2,.editor h2{margin:4px 0}.page-head p,.detail-head p,.editor header p{margin:0;color:var(--text-secondary,#6b7280)}.eyebrow{font-size:11px;letter-spacing:.15em;font-weight:700}.primary{background:#111827!important;color:#fff!important}.toolbar{justify-content:flex-start;margin:24px 0}.toolbar input{min-width:280px}.toolbar input,.toolbar select,.toolbar button,.actions button,.editor input,.editor textarea,.editor select,.editor button,.case-toolbar input,.case-toolbar select,.case-toolbar button,.pagination button,.row-actions button{border:1px solid var(--border-color,#e5e7eb);border-radius:9px;background:var(--surface,#fff);color:inherit;padding:9px 12px}.project-grid{display:grid;grid-template-columns:minmax(300px,32%) 1fr;gap:20px}.project-list,.project-detail{border:1px solid var(--border-color,#e5e7eb);border-radius:14px;background:var(--surface,#fff);overflow:hidden}.project-row{width:100%;display:flex;align-items:center;gap:12px;text-align:left;border:0;border-bottom:1px solid var(--border-color,#e5e7eb);background:transparent;padding:15px;color:inherit}.project-row.active{background:rgba(59,130,246,.08)}.project-monogram{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#111827;color:#fff;font-weight:700}.project-copy{display:flex;flex:1;flex-direction:column;gap:4px}.project-copy small,td small,.selection-summary small,.version-option small{display:block;color:#6b7280}.status,.case-status,.priority{font-size:12px;padding:4px 8px;border-radius:99px;white-space:nowrap}.status.active,.case-status.active,.case-status.completed{background:#dcfce7;color:#166534}.status.archived,.case-status.archived,.case-status.disabled,.case-status.cancelled{background:#f3f4f6;color:#6b7280}.case-status.draft,.case-status.queued{background:#e0f2fe;color:#075985}.case-status.pending_review,.case-status.running{background:#fef3c7;color:#92400e}.priority.p0{background:#fee2e2;color:#991b1b}.priority.p1{background:#ffedd5;color:#9a3412}.priority.p2{background:#e0f2fe;color:#075985}.priority.p3{background:#f3f4f6;color:#4b5563}.project-detail{padding:22px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:10px;margin:24px 0}.stats article{padding:14px;border:1px solid var(--border-color,#e5e7eb);border-radius:12px}.stats span{display:block;color:#6b7280;font-size:12px}.stats strong{font-size:24px}.facts{margin-bottom:22px}.facts div{display:grid;grid-template-columns:120px 1fr;padding:10px 0;border-bottom:1px solid var(--border-color,#e5e7eb)}.facts dt{color:#6b7280}.facts dd{margin:0;word-break:break-all}.danger{color:#b91c1c}.empty{padding:36px;text-align:center;color:#6b7280}.error-banner,.graph-warning{padding:10px 12px;border-radius:8px;background:#fef2f2;color:#b91c1c}.resource-panel{border-top:1px solid var(--border-color,#e5e7eb);padding-top:20px}.resource-head{margin-bottom:14px}.tabs{display:flex;gap:6px;padding:4px;border-radius:10px;background:var(--surface-muted,#f3f4f6)}.tabs button{border:0;border-radius:7px;background:transparent;color:inherit;padding:8px 14px}.tabs button.active{background:var(--surface,#fff);box-shadow:0 1px 3px rgba(0,0,0,.12);font-weight:700}.case-toolbar{justify-content:flex-start;margin-bottom:12px}.case-toolbar input{min-width:220px}.case-toolbar small{margin-left:auto;color:#6b7280}.table-wrap{overflow-x:auto;border:1px solid var(--border-color,#e5e7eb);border-radius:10px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:11px 10px;text-align:left;border-bottom:1px solid var(--border-color,#e5e7eb);vertical-align:middle}th{color:#6b7280;background:var(--surface-muted,#f9fafb);font-size:12px;white-space:nowrap}tbody tr:last-child td{border-bottom:0}td strong{display:block}.row-actions button{padding:6px 9px;white-space:nowrap}.pagination{margin-top:12px;color:#6b7280;font-size:12px}.pagination div{display:flex;gap:8px}.pagination button{padding:7px 10px}.suites-table td:nth-child(4){max-width:260px}.modal-backdrop{position:fixed;inset:0;z-index:80;display:grid;place-items:center;background:rgba(0,0,0,.45)}.editor{width:min(560px,90vw);max-height:86vh;overflow:auto;padding:22px;border-radius:14px;background:var(--surface,#fff)}.editor label{display:flex;flex-direction:column;gap:6px;margin:14px 0}.editor footer{justify-content:flex-end;margin-top:20px}.version-editor{width:min(680px,90vw)}.version-option{flex-direction:row!important;align-items:flex-start;padding:12px;border:1px solid var(--border-color,#e5e7eb);border-radius:10px}.version-option input{margin-top:4px}.version-option span{min-width:0}.selection-summary{display:flex;flex-direction:column;gap:5px;padding:12px;border-radius:10px;background:var(--surface-muted,#f3f4f6)}button:disabled{cursor:not-allowed;opacity:.5}@media(max-width:1200px){.stats{grid-template-columns:repeat(3,1fr)}.project-grid{grid-template-columns:minmax(280px,34%) 1fr}}@media(max-width:900px){.projects-page{padding:20px}.project-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}.resource-head,.case-toolbar{align-items:stretch;flex-direction:column}.case-toolbar small{margin-left:0}}
.regression-backdrop{place-items:stretch end}.regression-drawer{width:min(720px,94vw);height:100vh;max-height:none;border-radius:14px 0 0 14px}.context-summary{display:flex;align-items:flex-start;gap:12px;padding:14px;border-radius:10px;background:var(--surface-muted,#f3f4f6)}.context-summary div{min-width:0}.context-summary small,.timeline-card small{display:block;margin-top:4px;color:#6b7280}.compact-facts{margin:12px 0}.drawer-section{padding:14px 0;border-top:1px solid var(--border-color,#e5e7eb)}.drawer-section h3{margin:0 0 10px}.drawer-empty{padding:12px;color:#6b7280}.timeline-card{margin:8px 0;padding:11px;border:1px solid var(--border-color,#e5e7eb);border-radius:9px}.artifact-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.artifact-links a{padding:7px 10px;border-radius:8px;background:rgba(59,130,246,.1);color:#1d4ed8;text-decoration:none}.artifact-links a[aria-disabled="true"]{pointer-events:none;opacity:.5}.drawer-section>button{margin-top:8px;border:1px solid var(--border-color,#e5e7eb);border-radius:9px;background:var(--surface,#fff);color:inherit;padding:8px 11px}
</style>
