<script setup lang="ts">
import CaseEffectCoveragePanel from "../features/projects/CaseEffectCoveragePanel.vue";
import { computed, h, onMounted, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NIcon,
  NInput,
  NModal,
  NPagination,
  NPopconfirm,
  NSelect,
  NStatistic,
  NTabPane,
  NTabs,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, DataTableRowKey } from "naive-ui";
import { useRoute, useRouter } from "vue-router";

import { api } from "../services/api";
import ProjectCatalog from "../features/projects/ProjectCatalog.vue";
import ProjectFormModal from "../features/projects/ProjectFormModal.vue";
import ProjectExecutionDashboard from "../features/projects/ProjectExecutionDashboard.vue";
import TestCaseTemplateEditor from "../features/projects/TestCaseTemplateEditor.vue";
import BusinessEnvironmentManager from "../features/projects/BusinessEnvironmentManager.vue";
import TestCaseContentDialog from "../features/projects/TestCaseContentDialog.vue";
import CaseImportDialog from "../features/projects/CaseImportDialog.vue";
import CasePlanDialog from "../features/projects/CasePlanDialog.vue";
import RunPlanRevisionPanel from "../features/projects/RunPlanRevisionPanel.vue";
import { waitingReasonLabel } from "../features/projects/executionLabels";
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
  RegressionPlanningSelection,
  LegacySmokeRunSummary,
  TestCaseLifecycleStatus,
  TestCaseRecord,
  TestSuiteBundle,
  TestRunRecord,
  TestRunDetail,
  TestRunItemRecord,
  TestRunPlanRevisionRecord,
  TestRunStatus,
} from "../types";

type ResourceTab = "cases" | "suites" | "runs" | "legacy-smoke" | "regressions";

const CASE_PAGE_SIZE = 20;
const SUITE_PAGE_SIZE = 20;
const RUN_PAGE_SIZE = 20;
const REGRESSION_PAGE_SIZE = 20;
const REGRESSION_BATCH_PAGE_SIZE = 20;

const toast = useMessage();
const route = useRoute();
const router = useRouter();
const selectedProject = ref<ProjectRecord | null>(null);
const selectedId = ref(String(route.params.projectId || ""));
const overview = ref<ProjectOverview | null>(null);
const loading = ref(false);
const error = ref("");
const editorOpen = ref(false);

const resourceTab = ref<ResourceTab>("cases");
const modes = ref<ModeDescriptor[]>([]);
const testCases = ref<TestCaseRecord[]>([]);
const casesLoading = ref(false);
const caseOffset = ref(0);
const casesHaveMore = ref(false);
const caseQuery = ref("");
const caseStatus = ref<"" | TestCaseLifecycleStatus>("");
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
const runDetailOpen = ref(false);
const runDetailLoading = ref(false);
const runDetail = ref<TestRunDetail | null>(null);

const legacySmokeRuns = ref<LegacySmokeRunSummary[]>([]);
const legacySmokeBindings = ref<string[]>([]);
const legacySmokeLoading = ref(false);
const legacySmokeCursor = ref<string | undefined>(undefined);
const legacySmokeNextCursor = ref<string | null>(null);
const legacySmokeCursorStack = ref<Array<string | undefined>>([]);
const legacySmokeScopeInput = ref("");

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
const templateEditorOpen = ref(false);
const businessEnvironmentOpen = ref(false);
const caseImportOpen = ref(false);
const casePlanOpen = ref(false);
const casePlanTaskId = ref("");
const casePlanRegression = ref<RegressionPlanningSelection>();
const contentCase = ref<TestCaseRecord | null>(null);
const generating = ref(false);
const generationForm = ref({ objective: "", mode_key: "", model_key: "" });

const suiteEditorOpen = ref(false);
const suiteSaving = ref(false);
const suiteForm = ref({ name: "", description: "" });

const runEditorOpen = ref(false);
const runSaving = ref(false);
const runSuite = ref<TestSuiteBundle | null>(null);
const runForm = ref({ mode_key: "", session_id: "" });

const selected = computed(() => selectedProject.value);
const isDetailRoute = computed(() => route.name === "project-detail");
const routeProjectId = computed(() => String(route.params.projectId || ""));
const hasGenerationSources = computed(() => {
  const graph = overview.value?.graph;
  return Boolean(
    (overview.value?.api_doc_count ?? 0) > 0
    || (overview.value?.session_count ?? 0) > 0
    || (graph?.page_count ?? 0) > 0
    || (graph?.element_count ?? 0) > 0
    || (graph?.entity_count ?? 0) > 0
    || (graph?.edge_count ?? 0) > 0
  );
});
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

const RESOURCE_TABS: ResourceTab[] = ["cases", "suites", "runs", "legacy-smoke", "regressions"];

function tabFromRoute(): ResourceTab {
  const value = String(route.query.tab || "");
  return RESOURCE_TABS.includes(value as ResourceTab) ? (value as ResourceTab) : "cases";
}

async function loadSelectedProject() {
  const projectId = routeProjectId.value;
  if (!projectId) {
    selectedProject.value = null;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    selectedProject.value = await api.getProject(projectId);
    selectedId.value = projectId;
    resourceTab.value = tabFromRoute();
    await loadProjectDetail();
  } catch (err) {
    selectedProject.value = null;
    resetProjectResources();
    error.value = err instanceof Error ? err.message : "项目加载失败";
    await router.replace({ name: "projects" });
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
  legacySmokeRuns.value = [];
  legacySmokeBindings.value = [];
  legacySmokeCursor.value = undefined;
  legacySmokeNextCursor.value = null;
  legacySmokeCursorStack.value = [];
  regressionFailures.value = [];
  regressionCursor.value = undefined;
  regressionNextCursor.value = null;
  regressionCursorStack.value = [];
  selectedRegressionResults.value = {};
  regressionDrawerOpen.value = false;
  regressionContext.value = null;
  regressionBatches.value = [];
  selectedCasesById.value = {};
  templateEditorOpen.value = false;
  businessEnvironmentOpen.value = false;
  caseImportOpen.value = false;
  casePlanOpen.value = false;
  contentCase.value = null;
  runDetail.value = null;
  runDetailOpen.value = false;
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
        : resourceTab.value === "legacy-smoke"
          ? loadLegacySmokeRuns()
          : loadRegressionFailures(),
  ]);
}

async function selectProject(projectId: string) {
  if (isDetailRoute.value && selectedId.value === projectId) return;
  await router.push({ name: "project-detail", params: { projectId } });
}

function backToProjects() {
  void router.push({ name: "projects" });
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

async function loadLegacySmokeRuns(cursor = legacySmokeCursor.value) {
  if (!selectedId.value) return;
  legacySmokeLoading.value = true;
  try {
    const page = await api.listLegacySmokeRuns(selectedId.value, {
      cursor,
      limit: RUN_PAGE_SIZE,
    });
    legacySmokeRuns.value = page.items;
    legacySmokeBindings.value = page.bindings.map(item => item.project_scope);
    legacySmokeCursor.value = cursor;
    legacySmokeNextCursor.value = page.next_cursor || null;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "旧冒烟历史加载失败");
  } finally {
    legacySmokeLoading.value = false;
  }
}

async function bindLegacySmokeScope() {
  if (!selectedId.value || !legacySmokeScopeInput.value.trim()) return;
  try {
    await api.bindLegacySmokeScope(selectedId.value, legacySmokeScopeInput.value.trim());
    legacySmokeScopeInput.value = "";
    await loadLegacySmokeRuns(undefined);
    toast.success("旧冒烟项目 Scope 绑定成功");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "旧冒烟 Scope 绑定失败");
  }
}

async function unbindLegacySmokeScope(projectScope: string) {
  if (!selectedId.value) return;
  try {
    await api.unbindLegacySmokeScope(selectedId.value, projectScope);
    await loadLegacySmokeRuns(undefined);
    toast.success("旧冒烟 Scope 已解绑");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "旧冒烟 Scope 解绑失败");
  }
}

async function changeLegacySmokePage(direction: -1 | 1) {
  if (direction === 1) {
    if (!legacySmokeNextCursor.value) return;
    legacySmokeCursorStack.value.push(legacySmokeCursor.value);
    await loadLegacySmokeRuns(legacySmokeNextCursor.value);
    return;
  }
  if (!legacySmokeCursorStack.value.length) return;
  await loadLegacySmokeRuns(legacySmokeCursorStack.value.pop());
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
  if (isDetailRoute.value) {
    const query = { ...route.query };
    if (tab === "cases") delete query.tab;
    else query.tab = tab;
    if (query.tab !== route.query.tab) void router.replace({ query });
  }
  if (tab === "cases") {
    caseOffset.value = 0;
    await loadTestCases();
  } else if (tab === "suites") {
    suiteOffset.value = 0;
    await loadTestSuites();
  } else if (tab === "runs") {
    runOffset.value = 0;
    await loadTestRuns();
  } else if (tab === "legacy-smoke") {
    legacySmokeCursor.value = undefined;
    legacySmokeNextCursor.value = null;
    legacySmokeCursorStack.value = [];
    await loadLegacySmokeRuns(undefined);
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

function openEdit() {
  editorOpen.value = true;
}

function onProjectSaved(project: ProjectRecord) {
  selectedProject.value = project;
  editorOpen.value = false;
  if (selectedId.value !== project.id) selectedId.value = project.id;
  void loadOverview();
}

async function archiveSelected() {
  if (!selected.value || selected.value.status === "archived") return;
  try {
    await api.archiveProject(selected.value.id);
    toast.success("项目已归档，历史资源仍被保留");
    await router.push({ name: "projects" });
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

async function openPlannedRun(detail: TestRunDetail) {
  casePlanOpen.value = false;
  resourceTab.value = "runs";
  runOffset.value = 0;
  runDetail.value = detail;
  runDetailLoading.value = false;
  runDetailOpen.value = true;
  await Promise.all([loadTestRuns(), loadOverview()]);
}

async function openRunDetail(run: TestRunRecord) {
  runDetailOpen.value = true;
  runDetailLoading.value = true;
  try {
    runDetail.value = await api.getTestRun(run.id);
  } catch (err) {
    runDetailOpen.value = false;
    toast.error(err instanceof Error ? err.message : "测试运行详情加载失败");
  } finally {
    runDetailLoading.value = false;
  }
}

async function decideRunItemApproval(item: TestRunDetail["items"][number], decision: "approved" | "denied") {
  if (!item.approval_id) return;
  try {
    const outcome = await api.resolveTestRunItemApproval(item.id, item.approval_id, decision, `Project operator ${decision}`);
    if (runDetail.value) runDetail.value = await api.getTestRun(runDetail.value.run.id);
    await loadTestRuns();
    toast.success("approval_id" in outcome ? "审批已处理，条目还有待审批操作"
      : decision === "approved" ? "审批已通过，执行结果已保存" : "审批已拒绝，执行结果已保存");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "审批处理失败");
  }
}

async function refreshRunAfterRevision(record: TestRunPlanRevisionRecord) {
  if (!runDetail.value || runDetail.value.run.id !== record.run_id) return;
  try {
    runDetail.value = await api.getTestRun(record.run_id);
    await loadTestRuns();
    toast.success(`运行计划已修订到第 ${record.revision} 版`);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "修订已提交，但运行详情刷新失败");
  }
}

async function reconcileRunResources() {
  if (!runDetail.value) return;
  try {
    runDetail.value = await api.reconcileCancelledRunResources(runDetail.value.run.id);
    toast.success("取消运行的资源补偿已完成");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "资源补偿失败");
  }
}

async function createRegressionRun(run: TestRunRecord) {
  if (!canCreateRegression(run) || regressionRunId.value) return;
  regressionRunId.value = run.id;
  try {
    if (run.execution_strategy === "agent") {
      await openRegressionPlan(await api.getTestRun(run.id), []);
      return;
    }
    const created = await api.createRegressionTestRun(run.id);
    toast.success(`已创建回归运行 ${shortId(created.run.id)}`);
    await Promise.all([loadTestRuns(), loadOverview()]);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "回归运行创建失败");
  } finally {
    regressionRunId.value = "";
  }
}

async function openRegressionPlan(parent: TestRunDetail, resultIds: string[]) {
  const projectId = selectedId.value;
  if (parent.run.project_id !== projectId) throw new Error("回归父运行不属于当前项目");
  const chosen = new Map(parent.results.filter(result => ["failed", "error", "blocked"].includes(result.status)
    && (!resultIds.length || resultIds.includes(result.id))).map(result => [result.case_id, result]));
  const versionOptions = await Promise.all([...chosen.values()].map(async result => {
    const record = await api.getTestCase(result.case_id);
    if (record.project_id !== projectId) throw new Error("回归用例不属于当前项目");
    return { caseId: record.id, title: record.title, originalVersionId: result.case_version_id, activeVersionId: record.active_version_id || null };
  }));
  if (selectedId.value !== projectId) return;
  casePlanTaskId.value = "";
  casePlanRegression.value = { parentRunId: parent.run.id, resultIds: [...resultIds], versionOptions };
  casePlanOpen.value = true;
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
    const parent = await api.getTestRun(sourceRunId);
    if (parent.run.id !== sourceRunId || parent.run.project_id !== selectedId.value) throw new Error("回归父运行不属于当前项目");
    if (parent.run.execution_strategy === "agent") {
      await openRegressionPlan(parent, selectedFailures.map(item => item.source_result_id));
      return;
    }
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

const casePage = computed(() => Math.floor(caseOffset.value / CASE_PAGE_SIZE) + 1);
const casePageCount = computed(() => casePage.value + (casesHaveMore.value ? 1 : 0));

const caseStatusOptions = computed(() => [
  { label: "全部状态", value: "" },
  ...Object.entries(lifecycleLabels).map(([value, label]) => ({ label, value })),
]);
const runStatusOptions = [
  { label: "全部运行状态", value: "" },
  { label: "等待执行", value: "queued" },
  { label: "执行中", value: "running" },
  { label: "已完成", value: "completed" },
  { label: "已取消", value: "cancelled" },
];
const regressionStatusOptions = [
  { label: "全部失败状态", value: "" },
  { label: "失败", value: "failed" },
  { label: "错误", value: "error" },
  { label: "阻塞", value: "blocked" },
];
const modeFilterOptions = computed(() => [
  { label: "全部测试模式", value: "" },
  ...modes.value.map((mode) => ({ label: mode.name, value: mode.key })),
]);

function onTabChange(name: string | number) {
  void switchResourceTab(String(name) as ResourceTab);
}

function onCaseStatusChange(value: string) {
  caseStatus.value = value as "" | TestCaseLifecycleStatus;
  void searchCases();
}

function onRunStatusChange(value: string) {
  runStatus.value = value as "" | TestRunStatus;
  runOffset.value = 0;
  void loadTestRuns();
}

function onRegressionStatusChange(value: string) {
  regressionStatus.value = value as "" | RegressionFailureStatus;
  void searchRegressionFailures();
}

function onRegressionModeChange(value: string) {
  regressionMode.value = value;
  void searchRegressionFailures();
}
const suitePage = computed(() => Math.floor(suiteOffset.value / SUITE_PAGE_SIZE) + 1);
const suitePageCount = computed(() => suitePage.value + (suitesHaveMore.value ? 1 : 0));
const runPage = computed(() => Math.floor(runOffset.value / RUN_PAGE_SIZE) + 1);
const runPageCount = computed(() => runPage.value + (runsHaveMore.value ? 1 : 0));

function setCasePage(value: number) {
  caseOffset.value = (value - 1) * CASE_PAGE_SIZE;
  void loadTestCases();
}

function setSuitePage(value: number) {
  suiteOffset.value = (value - 1) * SUITE_PAGE_SIZE;
  void loadTestSuites();
}

function setRunPage(value: number) {
  runOffset.value = (value - 1) * RUN_PAGE_SIZE;
  void loadTestRuns();
}

type TagType = "default" | "success" | "warning" | "error" | "info";

function lifecycleTagType(value: TestCaseLifecycleStatus): TagType {
  return ({ draft: "default", pending_review: "warning", active: "success",
    disabled: "info", archived: "default" } as Record<string, TagType>)[value] || "default";
}

function runTagType(value: string): TagType {
  return ({ queued: "info", running: "warning", completed: "success", cancelled: "default" } as Record<string, TagType>)[value] || "default";
}

function failureTagType(value: string): TagType {
  return ({ failed: "error", error: "warning", blocked: "default" } as Record<string, TagType>)[value] || "default";
}

function priorityTagType(value: string): TagType {
  return ({ P0: "error", P1: "warning", P2: "info", P3: "default" } as Record<string, TagType>)[value] || "default";
}

const checkedCaseIds = computed(() => Object.keys(selectedCasesById.value));
const checkedRegressionIds = computed(() => Object.keys(selectedRegressionResults.value));

function onCaseChecked(keys: DataTableRowKey[]) {
  const checked = new Set(keys.map(String));
  for (const testCase of testCases.value) {
    if (checked.has(testCase.id) && testCase.lifecycle_status === "active" && testCase.active_version_id) {
      selectedCasesById.value[testCase.id] = testCase;
    } else if (!checked.has(testCase.id)) {
      delete selectedCasesById.value[testCase.id];
    }
  }
}

function onRegressionChecked(keys: DataTableRowKey[]) {
  const checked = new Set(keys.map(String));
  for (const failure of regressionFailures.value) {
    if (!checked.has(failure.source_result_id)) {
      delete selectedRegressionResults.value[failure.source_result_id];
    } else if (!selectedRegressionResults.value[failure.source_result_id]) {
      toggleRegressionSelection(failure, true);
    }
  }
}

const caseColumns = computed<DataTableColumns<TestCaseRecord>>(() => [
  {
    type: "selection",
    width: 46,
    disabled: (row) => row.lifecycle_status !== "active" || !row.active_version_id,
  },
  {
    title: "用例",
    key: "title",
    minWidth: 280,
    sorter: (a, b) => a.title.localeCompare(b.title, "zh-CN"),
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", row.title),
      h("small", `${row.case_key} · ${row.case_type}`),
    ]),
  },
  {
    title: "状态",
    key: "lifecycle_status",
    width: 110,
    render: (row) => h(NTag, { size: "small", bordered: false, type: lifecycleTagType(row.lifecycle_status) },
      { default: () => lifecycleLabels[row.lifecycle_status] }),
  },
  { title: "模式", key: "mode_key", width: 150, render: (row) => modeLabel(row.mode_key) },
  {
    title: "优先级",
    key: "priority",
    width: 100,
    render: (row) => h(NTag, { size: "small", bordered: false, type: priorityTagType(row.priority) },
      { default: () => row.priority }),
  },
  {
    title: "当前版本",
    key: "active_version_id",
    width: 180,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("span", row.active_version_id ? shortId(row.active_version_id) : "未启用"),
      h("small", `最新 v${row.latest_version}`),
    ]),
  },
  {
    title: "操作",
    key: "actions",
    width: 180,
    render: (row) => h("div", { class: "row-actions" }, [
      h(NButton, { text: true, size: "small", onClick: () => { contentCase.value = row; } },
        { default: () => "查看内容" }),
      row.lifecycle_status !== "archived"
        ? h(NButton, { text: true, size: "small", type: "primary",
            onClick: () => { contentCase.value = row; } }, { default: () => "评审与启用" })
        : null,
    ]),
  },
]);

const suiteColumns = computed<DataTableColumns<TestSuiteBundle>>(() => [
  {
    title: "套件名称",
    key: "name",
    minWidth: 260,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", row.suite.name),
      h("small", row.suite.id),
    ]),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (row) => h(NTag, { size: "small", bordered: false,
      type: row.suite.status === "active" ? "success" : "default" },
      { default: () => (row.suite.status === "active" ? "启用中" : "已归档") }),
  },
  { title: "条目数量", key: "items", width: 110, render: (row) => row.items.length },
  {
    title: "说明",
    key: "description",
    minWidth: 220,
    ellipsis: { tooltip: true },
    render: (row) => row.suite.description || "—",
  },
  {
    title: "更新时间",
    key: "updated_at",
    width: 190,
    render: (row) => formatServerDateTime(row.suite.updated_at),
  },
  {
    title: "操作",
    key: "actions",
    width: 140,
    render: (row) => h(NButton, {
      text: true,
      size: "small",
      type: "primary",
      disabled: selected.value?.status === "archived" || row.suite.status !== "active",
      onClick: () => openRunEditor(row),
    }, { default: () => "创建运行" }),
  },
]);

const runColumns = computed<DataTableColumns<TestRunRecord>>(() => [
  {
    title: "运行",
    key: "id",
    minWidth: 230,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", shortId(row.id)),
      h("small", `${row.run_kind === "regression" ? "回归" : "普通"} · 套件 ${shortId(row.suite_id)}`),
      row.parent_run_id ? h("small", `来源 ${shortId(row.parent_run_id)}`) : null,
    ]),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (row) => h(NTag, { size: "small", bordered: false, type: runTagType(row.status) },
      { default: () => row.status }),
  },
  { title: "模式", key: "mode_key", width: 150, render: (row) => modeLabel(row.mode_key) },
  {
    title: "进度",
    key: "progress",
    width: 120,
    render: (row) => {
      const done = row.stats.passed + row.stats.failed + row.stats.error
        + row.stats.blocked + row.stats.skipped + row.stats.cancelled;
      return `${done} / ${row.stats.total}`;
    },
  },
  {
    title: "结果统计",
    key: "stats",
    minWidth: 240,
    render: (row) => h("small", `通过 ${row.stats.passed} · 失败 ${row.stats.failed} · 错误 ${row.stats.error} · 阻塞 ${row.stats.blocked}`),
  },
  {
    title: "创建时间",
    key: "created_at",
    width: 190,
    sorter: (a, b) => a.created_at.localeCompare(b.created_at),
    render: (row) => formatServerDateTime(row.created_at),
  },
  {
    title: "操作",
    key: "actions",
    width: 260,
    render: (row) => h("div", { class: "row-actions" }, [
      h(NButton, { text: true, size: "small", type: "primary", onClick: () => openRunDetail(row) },
        { default: () => "详情" }),
      row.status === "queued" || row.status === "running"
        ? h(NPopconfirm, { positiveText: "取消运行", negativeText: "返回", onPositiveClick: () => cancelRun(row) }, {
            trigger: () => h(NButton, { text: true, size: "small", type: "error" }, { default: () => "取消" }),
            default: () => "取消后该运行不再调度，确定继续？",
          })
        : null,
      canCreateRegression(row)
        ? h(NButton, {
            text: true,
            size: "small",
            loading: regressionRunId.value === row.id,
            disabled: Boolean(regressionRunId.value),
            onClick: () => createRegressionRun(row),
          }, { default: () => "失败项回归" })
        : null,
    ]),
  },
]);

const legacySmokeColumns = computed<DataTableColumns<LegacySmokeRunSummary>>(() => [
  {
    title: "旧运行",
    key: "legacy_run_id",
    minWidth: 240,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", shortId(row.legacy_run_id)),
      h("small", `计划 ${shortId(row.legacy_plan_id)} · v${row.legacy_plan_version}`),
    ]),
  },
  { title: "Scope", key: "project_scope", minWidth: 160 },
  {
    title: "原状态",
    key: "legacy_status",
    width: 160,
    render: (row) => h(NTag, { size: "small", bordered: false }, { default: () => `${row.legacy_status} / ${row.legacy_verdict}` }),
  },
  {
    title: "统计",
    key: "stats",
    minWidth: 240,
    render: (row) => h("small", `总计 ${row.total_cases} · 通过 ${row.passed_cases} · 失败 ${row.failed_cases} · 阻塞 ${row.blocked_cases}`),
  },
  { title: "开始时间", key: "started_at", width: 190, render: (row) => formatServerDateTime(row.started_at) },
  {
    title: "用例摘要",
    key: "case_results",
    minWidth: 320,
    render: (row) => h("div", { class: "resource-cell" }, row.case_results.map((item) =>
      h("small", { key: item.legacy_case_id }, `${item.title}：${item.legacy_status} → ${item.mapped_status}（证据 ${item.evidence_count}）`))),
  },
]);

const regressionColumns = computed<DataTableColumns<RegressionFailureSummary>>(() => [
  {
    type: "selection",
    width: 46,
    disabled: (row) => regressionSelectionDisabled(row),
  },
  {
    title: "失败用例",
    key: "case_title",
    minWidth: 280,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", row.case_title),
      h("small", `${row.case_key} · Run ${shortId(row.source_run_id)}`),
      h("small", row.summary),
    ]),
  },
  {
    title: "状态",
    key: "failure_status",
    width: 110,
    render: (row) => h(NTag, { size: "small", bordered: false, type: failureTagType(row.failure_status) },
      { default: () => row.failure_status }),
  },
  { title: "模式", key: "mode_key", width: 150, render: (row) => modeLabel(row.mode_key) },
  {
    title: "证据",
    key: "evidence",
    minWidth: 240,
    render: (row) => h("small", `Evidence ${row.evidence_count} · Artifact ${row.artifact_count} · Verification ${row.verification_count}`),
  },
  {
    title: "回归批次",
    key: "regression_batch_count",
    width: 150,
    render: (row) => h("div", { class: "resource-cell" }, [
      h("strong", String(row.regression_batch_count)),
      h("small", row.latest_regression ? `最新 ${row.latest_regression.item_status}` : "尚未回归"),
    ]),
  },
  { title: "失败时间", key: "failed_at", width: 190, sorter: (a, b) => a.failed_at.localeCompare(b.failed_at),
    render: (row) => formatServerDateTime(row.failed_at) },
  {
    title: "操作",
    key: "actions",
    width: 160,
    render: (row) => h(NButton, { text: true, size: "small", type: "primary",
      onClick: () => openRegressionEvidence(row) }, { default: () => "证据与时间线" }),
  },
]);

const generationModeOptions = computed(() =>
  generationModes.value.map((mode) => ({ label: mode.name, value: mode.key })),
);

function itemTagType(value: string): TagType {
  return ({
    passed: "success",
    failed: "error",
    error: "warning",
    blocked: "default",
    running: "warning",
    queued: "info",
    waiting_approval: "info",
    waiting_resource: "info",
    cancelled: "default",
    skipped: "default",
  } as Record<string, TagType>)[value] || "default";
}

const runItemColumns = computed<DataTableColumns<TestRunItemRecord>>(() => [
  { title: "位置", key: "position", width: 70 },
  {
    title: "状态",
    key: "status",
    width: 190,
    render: (row) => h("div", { class: "resource-cell" }, [
      h(NTag, { size: "small", bordered: false, type: itemTagType(row.status) },
        { default: () => row.status }),
      h("small", shortId(row.case_id)),
      row.status === "waiting_resource"
        ? h("small", waitingReasonLabel(row.waiting_reason || "resource_unavailable"))
        : null,
    ]),
  },
  {
    title: "租约",
    key: "lease_expires_at",
    width: 190,
    render: (row) => h("small", row.lease_expires_at
      ? `到期 ${formatServerDateTime(row.lease_expires_at)}` : "—"),
  },
  {
    title: "审批",
    key: "approval_id",
    width: 170,
    render: (row) => h("small", row.status === "waiting_approval"
      ? `待处理 ${shortId(row.approval_id || "")}`
      : (row.approval_id ? shortId(row.approval_id) : "—")),
  },
  {
    title: "补偿",
    key: "resource_cleanup_completed_at",
    width: 110,
    render: (row) => h("small", row.status === "cancelled"
      ? (row.resource_cleanup_completed_at ? "已完成" : "待补偿") : "—"),
  },
  {
    title: "结果",
    key: "result_id",
    width: 130,
    render: (row) => h("small", row.result_id ? shortId(row.result_id) : "—"),
  },
  {
    title: "操作",
    key: "actions",
    width: 130,
    render: (row) => row.status === "waiting_approval"
      ? h("div", { class: "row-actions" }, [
          h(NButton, { text: true, size: "small", type: "primary",
            onClick: () => decideRunItemApproval(row, "approved") }, { default: () => "通过" }),
          h(NButton, { text: true, size: "small", type: "error",
            onClick: () => decideRunItemApproval(row, "denied") }, { default: () => "拒绝" }),
        ])
      : "—",
  },
]);

onMounted(() => {
  void loadModes();
  if (isDetailRoute.value) void loadSelectedProject();
});

watch(routeProjectId, async (projectId) => {
  if (!isDetailRoute.value) {
    selectedProject.value = null;
    selectedId.value = "";
    resetProjectResources();
    return;
  }
  if (!projectId || projectId === selectedId.value) return;
  selectedId.value = projectId;
  resetProjectResources();
  await loadSelectedProject();
});

watch(() => route.query.tab, (value) => {
  if (!isDetailRoute.value) return;
  const tab = RESOURCE_TABS.includes(String(value || "") as ResourceTab)
    ? (String(value) as ResourceTab)
    : "cases";
  if (tab !== resourceTab.value) void switchResourceTab(tab);
});

watch([selectedId, () => route.query.planning_task_id], ([projectId, taskId]) => {
  if (isDetailRoute.value && projectId === routeProjectId.value && projectId && typeof taskId === "string" && taskId) {
    casePlanTaskId.value = taskId;
    casePlanRegression.value = undefined;
    casePlanOpen.value = true;
  }
}, { immediate: true, flush: "post" });
</script>

<template>
  <main class="projects-page">
    <ProjectCatalog v-if="!isDetailRoute" @select="selectProject" />

    <NAlert v-if="isDetailRoute && error" type="error" :bordered="false" class="detail-alert">
      {{ error }}
    </NAlert>

      <section v-if="isDetailRoute && selected" class="project-detail">
        <div class="detail-breadcrumb">
          <NButton text type="primary" @click="backToProjects">
            <template #icon>
              <NIcon><i class="fa-solid fa-arrow-left" /></NIcon>
            </template>
            返回项目目录
          </NButton>
          <span>/</span>
          <strong>{{ selected.name }}</strong>
        </div>
        <div class="detail-head">
          <div><h2>{{ selected.name }}</h2><p>{{ selected.description || "暂无项目说明" }}</p></div>
          <div class="actions">
            <NButton size="small" @click="router.push(`/projects/${selected.id}/recording-scenes`)">录制场景</NButton>
            <NButton size="small" @click="businessEnvironmentOpen = true">测试环境</NButton>
            <NTooltip trigger="hover" :disabled="selected.status === 'active'">
              <template #trigger>
                <span>
                  <NButton size="small" :disabled="selected.status !== 'active'" @click="openEdit">编辑</NButton>
                </span>
              </template>
              已归档项目不可编辑
            </NTooltip>
            <NPopconfirm
              positive-text="归档"
              negative-text="取消"
              :disabled="selected.status !== 'active'"
              @positive-click="archiveSelected"
            >
              <template #trigger>
                <span>
                  <NButton size="small" type="error" :disabled="selected.status !== 'active'">归档</NButton>
                </span>
              </template>
              归档后项目将不可编辑，历史资源仍会保留。
            </NPopconfirm>
          </div>
        </div>
        <NGrid :cols="4" :x-gap="12" :y-gap="12" class="detail-stats">
          <NGi><NStatistic label="API 文档" :value="overview?.api_doc_count ?? 0" /></NGi>
          <NGi><NStatistic label="测试用例" :value="overview?.test_case_count ?? 0" /></NGi>
          <NGi><NStatistic label="测试套件" :value="overview?.test_suite_count ?? 0" /></NGi>
          <NGi><NStatistic label="测试运行" :value="overview?.test_run_count ?? 0" /></NGi>
          <NGi><NStatistic label="测试会话" :value="overview?.session_count ?? 0" /></NGi>
          <NGi>
            <NStatistic
              label="图谱节点"
              :value="(overview?.graph.page_count ?? 0) + (overview?.graph.element_count ?? 0) + (overview?.graph.entity_count ?? 0) + (overview?.graph.recording_count ?? 0) + (overview?.graph.action_count ?? 0)"
            />
          </NGi>
          <NGi><NStatistic label="图谱关系" :value="overview?.graph.edge_count ?? 0" /></NGi>
        </NGrid>
        <NDescriptions :column="3" bordered size="small" label-placement="left" class="detail-facts">
          <NDescriptionsItem label="Base URL">{{ selected.base_url || "未设置" }}</NDescriptionsItem>
          <NDescriptionsItem label="图谱 Scope">{{ selected.graph_scope_key || "未绑定" }}</NDescriptionsItem>
          <NDescriptionsItem label="项目 ID">{{ selected.id }}</NDescriptionsItem>
        </NDescriptions>
        <NAlert v-if="overview && !overview.graph.available" type="warning" :bordered="false">
          图谱暂不可用：{{ overview.graph.error }}
        </NAlert>

        <ProjectExecutionDashboard :project-id="selected.id" />

        <section class="resource-panel">
          <NTabs :value="resourceTab" type="line" @update:value="onTabChange">
            <NTabPane name="cases" tab="测试用例">
              <div class="case-toolbar">
                <NButton size="small" :disabled="selected.status !== 'active'" @click="templateEditorOpen = true">
                  用例生成模板
                </NButton>
                <NButton size="small" :disabled="selected.status !== 'active'" @click="caseImportOpen = true">
                  导入 Excel / JSON
                </NButton>
                <NButton size="small" :disabled="selected.status !== 'active'" @click="casePlanTaskId = ''; casePlanRegression = undefined; casePlanOpen = true">
                  用例执行规划
                </NButton>
                <NButton size="small" :disabled="selected.status !== 'active'" @click="openSuiteEditor">
                  创建套件
                </NButton>
                <NButton size="small" type="primary" :disabled="selected.status !== 'active'" @click="openGeneration">
                  生成用例
                </NButton>
                <NInput
                  v-model:value="caseQuery"
                  clearable
                  placeholder="搜索用例标识或标题"
                  style="width: 240px"
                  @keyup.enter="searchCases"
                />
                <NSelect
                  :value="caseStatus"
                  :options="caseStatusOptions"
                  style="width: 140px"
                  @update:value="onCaseStatusChange"
                />
                <NButton size="small" @click="searchCases">查询</NButton>
                <small>已选 {{ selectedActiveCases.length }} 条已启用用例</small>
              </div>
              <NDataTable
                :columns="caseColumns"
                :data="testCases"
                :loading="casesLoading"
                :row-key="(row: TestCaseRecord) => row.id"
                :checked-row-keys="checkedCaseIds"
                :scroll-x="1180"
                size="small"
                @update:checked-row-keys="onCaseChecked"
              >
                <template #empty><NEmpty description="暂无符合条件的测试用例" /></template>
              </NDataTable>
              <footer class="resource-pagination">
                <span class="resource-muted">第 {{ casePage }} 页</span>
                <NPagination
                  :page="casePage"
                  :page-count="casePageCount"
                  :page-size="CASE_PAGE_SIZE"
                  @update:page="setCasePage"
                />
              </footer>
            </NTabPane>

            <NTabPane name="suites" tab="测试套件">
              <NDataTable
                :columns="suiteColumns"
                :data="suites"
                :loading="suitesLoading"
                :row-key="(row: TestSuiteBundle) => row.suite.id"
                :scroll-x="1080"
                size="small"
              >
                <template #empty><NEmpty description="暂无测试套件" /></template>
              </NDataTable>
              <footer class="resource-pagination">
                <span class="resource-muted">第 {{ suitePage }} 页</span>
                <NPagination
                  :page="suitePage"
                  :page-count="suitePageCount"
                  :page-size="SUITE_PAGE_SIZE"
                  @update:page="setSuitePage"
                />
              </footer>
            </NTabPane>

            <NTabPane name="runs" tab="测试历史">
              <div class="case-toolbar">
                <NSelect
                  :value="runStatus"
                  :options="runStatusOptions"
                  style="width: 160px"
                  @update:value="onRunStatusChange"
                />
                <NButton size="small" @click="loadTestRuns">刷新</NButton>
                <small>运行只保存调度状态；测试结论以 Runner 结果和证据为准。项目执行条目统计见上方“执行与资源”。</small>
              </div>
              <NDataTable
                :columns="runColumns"
                :data="testRuns"
                :loading="runsLoading"
                :row-key="(row: TestRunRecord) => row.id"
                :scroll-x="1320"
                size="small"
              >
                <template #empty><NEmpty description="暂无测试运行记录" /></template>
              </NDataTable>
              <footer class="resource-pagination">
                <span class="resource-muted">第 {{ runPage }} 页</span>
                <NPagination
                  :page="runPage"
                  :page-count="runPageCount"
                  :page-size="RUN_PAGE_SIZE"
                  @update:page="setRunPage"
                />
              </footer>
            </NTabPane>

            <NTabPane name="legacy-smoke" tab="旧冒烟历史">
              <div class="case-toolbar">
                <NInput
                  v-model:value="legacySmokeScopeInput"
                  clearable
                  placeholder="输入人工确认的旧 project_scope"
                  style="width: 280px"
                  @keyup.enter="bindLegacySmokeScope"
                />
                <NButton
                  size="small"
                  type="primary"
                  :disabled="selected.status === 'archived' || !legacySmokeScopeInput.trim()"
                  @click="bindLegacySmokeScope"
                >
                  绑定 Scope
                </NButton>
                <NButton size="small" @click="loadLegacySmokeRuns(undefined)">刷新</NButton>
                <small>旧 Smoke catalog 仅作为只读历史快照展示，不支持领取、执行或直接回归。</small>
              </div>
              <div v-if="legacySmokeBindings.length" class="case-toolbar">
                <small>已绑定 Scope：</small>
                <NTag
                  v-for="scope in legacySmokeBindings"
                  :key="scope"
                  size="small"
                  closable
                  @close="unbindLegacySmokeScope(scope)"
                >
                  {{ scope }}
                </NTag>
              </div>
              <NDataTable
                :columns="legacySmokeColumns"
                :data="legacySmokeRuns"
                :loading="legacySmokeLoading"
                :row-key="(row: LegacySmokeRunSummary) => row.legacy_run_id"
                :scroll-x="1280"
                size="small"
              >
                <template #empty><NEmpty description="暂无已绑定的旧冒烟历史；请先输入人工确认的 project_scope。" /></template>
              </NDataTable>
              <footer class="resource-pagination">
                <span class="resource-muted">第 {{ legacySmokeCursorStack.length + 1 }} 页</span>
                <div class="resource-pagination-actions">
                  <NButton size="small" :disabled="!legacySmokeCursorStack.length || legacySmokeLoading" @click="changeLegacySmokePage(-1)">上一页</NButton>
                  <NButton size="small" :disabled="!legacySmokeNextCursor || legacySmokeLoading" @click="changeLegacySmokePage(1)">下一页</NButton>
                </div>
              </footer>
            </NTabPane>

            <NTabPane name="regressions" tab="回归中心">
              <div class="case-toolbar">
                <NSelect
                  :value="regressionStatus"
                  :options="regressionStatusOptions"
                  style="width: 160px"
                  @update:value="onRegressionStatusChange"
                />
                <NSelect
                  :value="regressionMode"
                  :options="modeFilterOptions"
                  style="width: 180px"
                  @update:value="onRegressionModeChange"
                />
                <NButton size="small" @click="searchRegressionFailures">刷新</NButton>
                <NButton
                  size="small"
                  type="primary"
                  :loading="Boolean(regressionRunId)"
                  :disabled="selected.status === 'archived' || !selectedRegressionFailures.length || Boolean(regressionRunId)"
                  @click="createSelectedRegressionRun"
                >
                  所选失败项回归（{{ selectedRegressionFailures.length }}）
                </NButton>
                <small>批量回归仅允许选择同一个原始运行；原失败结果和用例版本保持不变。</small>
              </div>
              <NDataTable
                :columns="regressionColumns"
                :data="regressionFailures"
                :loading="regressionsLoading"
                :row-key="(row: RegressionFailureSummary) => row.source_result_id"
                :checked-row-keys="checkedRegressionIds"
                :scroll-x="1360"
                size="small"
                @update:checked-row-keys="onRegressionChecked"
              >
                <template #empty><NEmpty description="暂无失败、错误或阻塞结果" /></template>
              </NDataTable>
              <footer class="resource-pagination">
                <span class="resource-muted">第 {{ regressionCursorStack.length + 1 }} 页</span>
                <div class="resource-pagination-actions">
                  <NButton size="small" :disabled="!regressionCursorStack.length || regressionsLoading" @click="changeRegressionPage(-1)">上一页</NButton>
                  <NButton size="small" :disabled="!regressionNextCursor || regressionsLoading" @click="changeRegressionPage(1)">下一页</NButton>
                </div>
              </footer>
            </NTabPane>
          </NTabs>
        </section>
      </section>
      <section v-else-if="isDetailRoute" class="project-detail empty">项目不存在或已被移除</section>

    <ProjectFormModal v-model:show="editorOpen" :project="selected" @saved="onProjectSaved" />

    <NModal
      v-model:show="generationOpen"
      preset="card"
      title="生成测试用例草稿"
      style="width: min(640px, 92vw)"
      :mask-closable="!generating"
      :close-on-esc="!generating"
    >
      <NAlert v-if="overview && !hasGenerationSources" type="warning" :bordered="false" style="margin-bottom: 12px">
        当前项目没有可追溯的 API 文档、知识图谱或测试会话。请先导入或绑定项目资料，再生成测试用例。
      </NAlert>
      <NForm label-placement="top">
        <NFormItem label="生成目标" required>
          <NInput
            v-model:value="generationForm.objective"
            type="textarea"
            :autosize="{ minRows: 5, maxRows: 9 }"
            placeholder="描述本次需要覆盖的业务目标、范围与通过标准"
          />
        </NFormItem>
        <NFormItem label="测试模式" required>
          <NSelect
            v-model:value="generationForm.mode_key"
            :options="generationModeOptions"
            placeholder="选择测试模式"
          />
        </NFormItem>
        <NFormItem label="模型标识（可选）">
          <NInput v-model:value="generationForm.model_key" placeholder="留空使用服务端默认模型" />
        </NFormItem>
      </NForm>
      <p class="dialog-hint">使用当前项目的生成模板；模板内容与版本将作为本次生成依据保存。</p>
      <template #footer>
        <div class="dialog-actions">
          <NButton :disabled="generating" @click="generationOpen = false">取消</NButton>
          <NButton type="primary" :loading="generating" :disabled="!hasGenerationSources" @click="generateCases">
            生成草稿
          </NButton>
        </div>
      </template>
    </NModal>

    <TestCaseTemplateEditor v-if="templateEditorOpen" :project-id="selectedId" @close="templateEditorOpen = false" @saved="toast.success('项目模板已保存')" />
    <BusinessEnvironmentManager v-if="businessEnvironmentOpen" :key="selectedId" :project-id="selectedId" @close="businessEnvironmentOpen = false" />
    <CaseImportDialog v-if="caseImportOpen" :key="selectedId" :project-id="selectedId" :modes="generationModes" @close="caseImportOpen = false" @imported="loadTestCases" />
    <CasePlanDialog v-if="casePlanOpen" :key="`${selectedId}:${casePlanTaskId}:${JSON.stringify(casePlanRegression)}`" :project-id="selectedId" :initial-task-id="casePlanTaskId" :regression="casePlanRegression" :candidate-case-ids="selectedActiveCases.map(item => item.id)" :modes="modes" @close="casePlanOpen = false" @started="openPlannedRun" />
    <TestCaseContentDialog v-if="contentCase" :key="contentCase.id" :test-case="contentCase" @close="contentCase = null" @saved="loadTestCases(); loadOverview()" />

    <NModal
      v-model:show="suiteEditorOpen"
      preset="card"
      title="创建固定版本测试套件"
      style="width: min(560px, 92vw)"
      :mask-closable="!suiteSaving"
      :close-on-esc="!suiteSaving"
    >
      <NForm label-placement="top">
        <NFormItem label="套件名称" required>
          <NInput v-model:value="suiteForm.name" placeholder="订单服务核心回归套件" maxlength="160" show-count />
        </NFormItem>
        <NFormItem label="套件说明（可选）">
          <NInput
            v-model:value="suiteForm.description"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            maxlength="500"
            show-count
          />
        </NFormItem>
      </NForm>
      <NAlert type="info" :bordered="false">
        已选择 {{ selectedActiveCases.length }} 条用例。套件会固定保存每条用例当前的 active_version_id。
      </NAlert>
      <template #footer>
        <div class="dialog-actions">
          <NButton :disabled="suiteSaving" @click="suiteEditorOpen = false">取消</NButton>
          <NButton type="primary" :loading="suiteSaving" @click="createSuite">创建套件</NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="runEditorOpen"
      preset="card"
      title="创建测试运行"
      style="width: min(580px, 92vw)"
      :mask-closable="!runSaving"
      :close-on-esc="!runSaving"
    >
      <p class="dialog-hint">{{ runSuite?.suite.name }} · {{ runSuite?.items.length ?? 0 }} 条固定版本用例</p>
      <NForm label-placement="top">
        <NFormItem label="测试模式" required>
          <NSelect v-model:value="runForm.mode_key" :options="generationModeOptions" placeholder="选择测试模式" />
        </NFormItem>
        <NFormItem label="关联 Session ID（可选）">
          <NInput v-model:value="runForm.session_id" placeholder="仅接受已绑定当前项目的 Session" />
        </NFormItem>
      </NForm>
      <NAlert type="info" :bordered="false">
        运行不会修改套件或用例版本。Worker 将通过原子领取接口分批获取固定版本内容；失败、错误和阻塞结果可创建独立回归运行。
      </NAlert>
      <template #footer>
        <div class="dialog-actions">
          <NButton :disabled="runSaving" @click="runEditorOpen = false">取消</NButton>
          <NButton type="primary" :loading="runSaving" @click="createRun">创建运行</NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="regressionDrawerOpen"
      preset="card"
      title="证据与回归时间线"
      style="width: min(760px, 94vw)"
    >
      <div v-if="regressionDrawerLoading" class="dialog-loading">
        <NSpin size="small" /> 正在按需加载失败证据与回归批次…
      </div>
      <template v-else-if="regressionContext">
        <div class="context-summary">
          <NTag size="small" :bordered="false" :type="failureTagType(regressionContext.failure_status)">
            {{ regressionContext.failure_status }}
          </NTag>
          <div>
            <strong>{{ regressionContext.summary }}</strong>
            <small v-if="regressionContext.error_message">{{ regressionContext.error_message }}</small>
          </div>
        </div>
        <NDescriptions :column="3" bordered size="small" label-placement="left" class="detail-facts">
          <NDescriptionsItem label="原始 Run">{{ regressionContext.source_run_id }}</NDescriptionsItem>
          <NDescriptionsItem label="用例版本">{{ regressionContext.case_version_id }}</NDescriptionsItem>
          <NDescriptionsItem label="失败时间">{{ formatServerDateTime(regressionContext.failed_at) }}</NDescriptionsItem>
        </NDescriptions>

        <section class="drawer-section">
          <h3>公开证据</h3>
          <NEmpty v-if="!regressionContext.evidence.length" description="无结构化 Evidence" size="small" />
          <article v-for="evidence in regressionContext.evidence" v-else :key="`${evidence.evidence_type}:${evidence.evidence_id}`" class="timeline-card">
            <strong>{{ evidence.label || evidence.evidence_type }}</strong>
            <small>{{ evidence.evidence_type }} · {{ evidence.evidence_id }}</small>
          </article>
          <div class="artifact-links">
            <NButton
              v-for="artifact in regressionContext.artifacts"
              :key="artifact.artifact_id"
              text
              tag="a"
              :href="artifact.content_url || undefined"
              :disabled="!artifact.content_url"
              target="_blank"
              rel="noopener noreferrer"
            >
              Artifact {{ shortId(artifact.artifact_id) }}
            </NButton>
          </div>
        </section>

        <section class="drawer-section">
          <h3>Verification</h3>
          <NEmpty v-if="!regressionContext.verifications.length" description="无 Verification 结论" size="small" />
          <article v-for="verification in regressionContext.verifications" v-else :key="verification.id" class="timeline-card">
            <strong>{{ verification.verifier || verification.id }} · {{ verification.status }}</strong>
            <small>{{ verification.summary }} · {{ verification.passed_count }}/{{ verification.assertion_count }} 通过</small>
          </article>
        </section>

        <section class="drawer-section">
          <h3>回归批次时间线</h3>
          <NEmpty v-if="!regressionBatches.length" description="尚未创建回归批次" size="small" />
          <NTimeline v-else>
            <NTimelineItem
              v-for="batch in regressionBatches"
              :key="batch.run_item_id"
              :title="`${formatServerDateTime(batch.created_at)} · ${batch.item_status}`"
              :content="`Run ${shortId(batch.run_id)} · 版本 ${shortId(batch.case_version_id)}${batch.result_status ? ' · 回归结果 ' + batch.result_status : ''}`"
            />
          </NTimeline>
          <NButton
            v-if="regressionBatchNextCursor"
            size="small"
            :loading="regressionBatchesLoading"
            @click="loadMoreRegressionBatches"
          >
            加载更多批次
          </NButton>
        </section>
      </template>
      <template #footer>
        <div class="dialog-actions">
          <NButton @click="regressionDrawerOpen = false">关闭</NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="runDetailOpen"
      preset="card"
      :title="`测试运行详情${runDetail ? ' · ' + shortId(runDetail.run.id) : ''}`"
      style="width: min(1000px, 96vw)"
    >
      <div v-if="runDetailLoading" class="dialog-loading"><NSpin size="small" /> 正在加载运行条目…</div>
      <template v-else-if="runDetail">
        <NAlert type="info" :bordered="false" style="margin-bottom: 12px">
          进度 {{ runDetail.run.stats.passed + runDetail.run.stats.failed + runDetail.run.stats.error + runDetail.run.stats.blocked + runDetail.run.stats.skipped + runDetail.run.stats.cancelled }} / {{ runDetail.run.stats.total }}
          · 通过 {{ runDetail.run.stats.passed }} · 失败 {{ runDetail.run.stats.failed }} · 错误 {{ runDetail.run.stats.error }}
          · 等待资源 {{ runDetail.run.stats.waiting_resource }} · 阻塞 {{ runDetail.run.stats.blocked }} · 取消 {{ runDetail.run.stats.cancelled }}
        </NAlert>
        <RunPlanRevisionPanel v-if="runDetail.run.execution_strategy === 'agent' && runDetail.run.plan_snapshot"
          :key="`${runDetail.run.id}:${runDetail.run.plan_revision || 0}`" :project-id="runDetail.run.project_id"
          :run-id="runDetail.run.id" :expected-revision="runDetail.run.plan_revision || 0"
          :candidate-case-ids="selectedActiveCases.map(item => item.id)" :modes="modes"
          :can-revise="runDetail.run.status === 'queued' || runDetail.run.status === 'running'"
          @revised="refreshRunAfterRevision" />
        <NDataTable
          :columns="runItemColumns"
          :data="runDetail.items"
          :row-key="(row: TestRunItemRecord) => row.id"
          :scroll-x="1100"
          size="small"
        >
          <template #empty><NEmpty description="暂无运行条目" /></template>
        </NDataTable>
        <CaseEffectCoveragePanel v-for="result in runDetail.results" :key="result.id"
          :coverage="result.actual.effect_cleanup_coverage" :result-label="shortId(result.id)" />
      </template>
      <template #footer>
        <div class="dialog-actions">
          <NButton
            v-if="runDetail && runDetail.run.status === 'cancelled' && runDetail.items.some(item => !item.resource_cleanup_completed_at && (item.approval_id || item.tool_job_id))"
            @click="reconcileRunResources"
          >
            重试资源补偿
          </NButton>
          <NButton type="primary" @click="runDetailOpen = false">关闭</NButton>
        </div>
      </template>
    </NModal>

  </main>
</template>

<style scoped>
.projects-page {
  padding: 16px 16px;
  color: var(--text);
  background: var(--bg);
  min-height: 100%;
  color-scheme: light;
}

:root[data-theme="dark"] .projects-page {
  color-scheme: dark;
}

.page-head,
.detail-head,
.toolbar,
.actions,
.editor header,
.editor footer,
.resource-head,
.case-toolbar,
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.page-head h1,
.detail-head h2,
.editor h2 {
  margin: 4px 0;
}

.page-head p,
.detail-head p,
.editor header p {
  margin: 0;
  color: var(--muted);
}

.primary {
  background: var(--accent) !important;
  color: var(--bg) !important;
}

.toolbar {
  justify-content: flex-start;
  margin: 24px 0;
}

.toolbar input {
  min-width: 280px;
}

.catalog-summary {
  display: flex;
  align-items: center;
  gap: 18px;
  margin: 4px 0 14px;
  color: var(--muted);
  font-size: 13px;
}

.catalog-summary strong {
  color: var(--text);
  font-size: 16px;
}

.summary-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--muted);
}

.summary-dot.active {
  background: #16a34a;
}

.summary-dot.archived {
  background: #94a3b8;
}

/* shadcn Input: G:\Code_UZIP\ui\apps\v4\registry\new-york-v4\ui\input.tsx */
.projects-page input:not([type="checkbox"]):not([type="radio"]),
.projects-page select,
.projects-page textarea {
  height: 36px;
  min-width: 0;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  padding: 4px 12px;
  font-size: 14px;
  line-height: 1.4;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  outline: none;
  transition: color 150ms ease, box-shadow 150ms ease, border-color 150ms ease;
}

.projects-page textarea {
  height: auto;
  min-height: 80px;
  padding: 8px 12px;
  resize: vertical;
}

.projects-page select {
  background-color: var(--surface);
  color: var(--text);
  cursor: pointer;
}

.projects-page select option {
  background-color: var(--surface);
  color: var(--text);
}

.projects-page input::placeholder,
.projects-page textarea::placeholder {
  color: var(--muted);
}

.projects-page input:focus-visible,
.projects-page select:focus-visible,
.projects-page textarea:focus-visible {
  border-color: color-mix(in srgb, var(--text, #111827) 42%, var(--border, #e5e7eb));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--text, #111827) 16%, transparent);
}

.projects-page input:disabled,
.projects-page select:disabled,
.projects-page textarea:disabled {
  pointer-events: none;
  cursor: not-allowed;
  opacity: 0.5;
}

.projects-page input::selection,
.projects-page textarea::selection {
  background: var(--accent);
  color: var(--bg);
}

.scope-input {
  width: 280px;
}

.toolbar button,
.actions button,
.editor button,
.case-toolbar button,
.pagination button,
.row-actions button {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  background: var(--surface, #fff);
  color: inherit;
  padding: 9px 12px;
}

.project-grid {
  display: grid;
  grid-template-columns: minmax(300px, 32%) 1fr;
  gap: 20px;
}

.project-catalog {
  display: grid;
  grid-template-columns: repeat(3, minmax(240px, 1fr));
  gap: 14px;
}

.project-card {
  min-height: 210px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 14px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface, #fff);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
}

.project-card:hover,
.project-card:focus-visible {
  border-color: color-mix(in srgb, var(--text) 28%, var(--border));
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
}

.project-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.project-card-head .project-copy {
  min-width: 0;
}

.project-card-head .project-copy strong,
.project-card-head .project-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-card-description {
  min-height: 42px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.project-card-meta {
  display: flex;
  flex-direction: column;
  gap: 7px;
  color: var(--muted);
  font-size: 12px;
}

.project-card-meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-card-meta i {
  width: 16px;
  margin-right: 4px;
  text-align: center;
}

.project-card-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.detail-breadcrumb {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 18px;
  color: var(--muted);
  font-size: 13px;
}

.detail-breadcrumb button {
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
  cursor: pointer;
}

.detail-breadcrumb button:hover,
.detail-breadcrumb button:focus-visible {
  color: var(--text);
}

.detail-breadcrumb strong {
  color: var(--text);
}

.project-list,
.project-detail {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface, #fff);
  overflow: hidden;
}

.project-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  padding: 15px;
  color: inherit;
}

.project-row.active {
  background: rgba(59, 130, 246, 0.08);
}

.project-monogram {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--bg);
  font-weight: 700;
}

.project-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.project-copy small,
td small,
.selection-summary small,
.version-option small {
  display: block;
  color: var(--muted);
}

.status,
.case-status,
.priority {
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 99px;
  white-space: nowrap;
}

.status.active,
.case-status.active,
.case-status.completed {
  background: #dcfce7;
  color: #166534;
}

.status.archived,
.case-status.archived,
.case-status.disabled,
.case-status.cancelled {
  background: #f3f4f6;
  color: #6b7280;
}

.case-status.draft,
.case-status.queued {
  background: #e0f2fe;
  color: #075985;
}

.case-status.pending_review,
.case-status.running,
.case-status.waiting_resource {
  background: #fef3c7;
  color: #92400e;
}

.priority.p0 {
  background: #fee2e2;
  color: #991b1b;
}

.priority.p1 {
  background: #ffedd5;
  color: #9a3412;
}

.priority.p2 {
  background: #e0f2fe;
  color: #075985;
}

.priority.p3 {
  background: #f3f4f6;
  color: #4b5563;
}

.project-detail {
  padding: 22px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(88px, 1fr));
  gap: 10px;
  margin: 24px 0;
}

.stats article {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
}

.stats span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.stats strong {
  font-size: 24px;
}

.facts {
  margin-bottom: 22px;
}

.facts div {
  display: grid;
  grid-template-columns: 120px 1fr;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}

.facts dt {
  color: var(--muted);
}

.facts dd {
  margin: 0;
  word-break: break-all;
}

.danger {
  color: var(--red);
}

.empty {
  padding: 36px;
  text-align: center;
  color: var(--muted);
}

.error-banner,
.graph-warning {
  padding: 10px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--red) 12%, var(--surface));
  color: var(--red);
}

.generation-warning {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid #f0c36d;
  border-radius: 8px;
  background: #fff8e6;
  color: #8a5a00;
  font-size: 13px;
  line-height: 1.5;
}

.resource-panel {
  border-top: 1px solid var(--border);
  padding-top: 20px;
}

.resource-head {
  margin-bottom: 14px;
}

.tabs {
  display: flex;
  gap: 6px;
  padding: 4px;
  border-radius: 10px;
  background: var(--surface-muted, #f3f4f6);
}

.tabs button {
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  padding: 8px 14px;
}

.tabs button.active {
  background: var(--surface, #fff);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  font-weight: 700;
}

.case-toolbar {
  justify-content: flex-start;
  margin-bottom: 12px;
}

.case-toolbar input {
  min-width: 220px;
}

.case-toolbar small {
  margin-left: auto;
  color: var(--muted);
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  padding: 11px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}

th {
  color: var(--muted);
  background: var(--surface-muted);
  font-size: 12px;
  white-space: nowrap;
}

tbody tr:last-child td {
  border-bottom: 0;
}

td strong {
  display: block;
}

.row-actions button {
  padding: 6px 9px;
  white-space: nowrap;
}

.pagination {
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
}

.pagination div {
  display: flex;
  gap: 8px;
}

.pagination button {
  padding: 7px 10px;
}

.suites-table td:nth-child(4) {
  max-width: 260px;
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  background: rgba(0, 0, 0, 0.45);
}

.editor {
  width: min(560px, 90vw);
  max-height: 86vh;
  overflow: auto;
  padding: 22px;
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
}

.editor label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 14px 0;
}

.editor footer {
  justify-content: flex-end;
  margin-top: 20px;
}

.version-editor {
  width: min(680px, 90vw);
}

.version-option {
  flex-direction: row !important;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.version-option input {
  margin-top: 4px;
}

.version-option span {
  min-width: 0;
}

.selection-summary {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 12px;
  border-radius: 10px;
  background: var(--surface-muted, #f3f4f6);
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

@media (max-width: 1200px) {
  .stats {
    grid-template-columns: repeat(3, 1fr);
  }

  .project-catalog {
    grid-template-columns: repeat(2, minmax(240px, 1fr));
  }

  .project-grid {
    grid-template-columns: minmax(280px, 34%) 1fr;
  }
}

@media (max-width: 900px) {
  .projects-page {
    padding: 16px;
  }

  .project-catalog {
    grid-template-columns: 1fr;
  }

  .project-grid {
    grid-template-columns: 1fr;
  }

  .stats {
    grid-template-columns: repeat(2, 1fr);
  }

  .resource-head,
  .case-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .case-toolbar small {
    margin-left: 0;
  }
}

.regression-backdrop {
  place-items: stretch end;
}

.regression-drawer {
  width: min(720px, 94vw);
  height: 100vh;
  max-height: none;
  border-radius: 14px 0 0 14px;
}

.run-detail-editor {
  width: min(920px, 94vw);
}

.run-items-table {
  max-height: 55vh;
  overflow: auto;
}

.context-summary {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
  background: var(--surface-muted, #f3f4f6);
}

.context-summary div {
  min-width: 0;
}

.context-summary small,
.timeline-card small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
}

.compact-facts {
  margin: 12px 0;
}

.drawer-section {
  padding: 14px 0;
  border-top: 1px solid var(--border);
}

.drawer-section h3 {
  margin: 0 0 10px;
}

.drawer-empty {
  padding: 12px;
  color: var(--muted);
}

.timeline-card {
  margin: 8px 0;
  padding: 11px;
  border: 1px solid var(--border);
  border-radius: 9px;
}

.artifact-links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.artifact-links a {
  padding: 7px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--blue) 16%, transparent);
  color: var(--blue);
  text-decoration: none;
}

.artifact-links a[aria-disabled="true"] {
  pointer-events: none;
  opacity: 0.5;
}

.drawer-section > button {
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface, #fff);
  color: inherit;
  padding: 8px 11px;
}
</style>
