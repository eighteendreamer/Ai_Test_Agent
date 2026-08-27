<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import AssistantMarkdown from "./AssistantMarkdown.vue";
import { api } from "../../services/api";
import { useSessionStore } from "../../stores/session";
import type {
  CompatibilityArtifactRecord,
  CompatibilityDispatchPlan,
  CompatibilityEnvironmentSpec,
  CompatibilityExecutionReport,
  CompatibilityPlan,
  CompatibilityProductAccessManifest,
  CompatibilityQueuedTask,
  CompatibilityRunnerRecord,
  CompatibilityRunnerTask,
  CompatibilityRunnerTaskSummary,
  CompatibilityTestRunnerOutput,
} from "../../types/compatibility-testing";

const sessionStore = useSessionStore();
const runners = ref<CompatibilityRunnerRecord[]>([]);
const queuedTasks = ref<CompatibilityQueuedTask[]>([]);
const taskSummary = ref<CompatibilityRunnerTaskSummary | null>(null);
const executionReport = ref<CompatibilityExecutionReport | null>(null);
const isLoadingRunners = ref(false);
const isRequeueingTasks = ref(false);
const isCleaningRunners = ref(false);
const isConfirmingRunnerCleanup = ref(false);
const isDraftingPlan = ref(false);
const dispatchingPlanId = ref("");
const runnerError = ref("");
const directPlanOutputs = ref<Array<{
  jobId: string;
  status: string;
  summary: string;
  output: CompatibilityTestRunnerOutput;
}>>([]);
const selectedCaseIdsByPlan = ref<Record<string, string[]>>({});
const selectedEnvironmentIdsByPlan = ref<Record<string, string[]>>({});
const riskConfirmedByPlan = ref<Record<string, boolean>>({});
const productType = ref("web");
const productName = ref("");
const productVersion = ref("");
const entryUrl = ref("");
const artifactUri = ref("");
const packageName = ref("");
const activity = ref("");
const bundleId = ref("");
const miniProgramPath = ref("");
const command = ref("");
const authStrategy = ref("unspecified");
const usernameRef = ref("");
const passwordRef = ref("");
const tokenRef = ref("");
const baseApi = ref("");
const requiresVpn = ref(false);
const testScope = ref("");
const priorityFlows = ref("");
const excludeScope = ref("");
const forbiddenActions = ref("");
const dataPolicy = ref("test data only");
const wizardStep = ref(1);
const wizardStepTouched = ref<number[]>([]);
const wizardReplies = ref<Record<number, string>>({});
const processedWizardMessageIds = new Set<string>();
const wizardSteps = [
  { id: 1, label: "产品", title: "先认识一下要测试的产品", prompt: "它是什么类型、叫什么名字、当前版本是多少？" },
  { id: 2, label: "接入", title: "把产品交给 Runner", prompt: "告诉我可访问的入口，或提供安装包 / 启动方式。" },
  { id: 3, label: "登录", title: "测试需要怎样登录？", prompt: "选择认证方式；如果需要凭据，只填写安全引用，不要直接粘贴密码。" },
  { id: 4, label: "范围", title: "这次重点验证哪些路径？", prompt: "列出模块和关键流程，我会据此生成用例。" },
  { id: 5, label: "边界", title: "哪些动作必须被保护？", prompt: "告诉我禁测范围、禁止动作，以及测试数据策略。" },
  { id: 6, label: "确认", title: "准备好生成测试计划了吗？", prompt: "检查下面的摘要，确认后我会生成环境矩阵和测试用例。" },
];
let refreshTimer: number | undefined;

const productTypes = [
  { value: "web", label: "Web" },
  { value: "h5", label: "H5" },
  { value: "android_app", label: "Android App" },
  { value: "ios_app", label: "iOS App" },
  { value: "wechat_mini_program", label: "微信小程序" },
  { value: "alipay_mini_program", label: "支付宝小程序" },
  { value: "linux_app", label: "Linux App" },
];

const authStrategies = [
  { value: "unspecified", label: "未指定" },
  { value: "none", label: "无需登录" },
  { value: "account", label: "测试账号" },
  { value: "token", label: "Token" },
  { value: "cookie", label: "Cookie" },
  { value: "manual", label: "人工接管" },
];

const directPlans = computed(() => {
  return directPlanOutputs.value
    .map((item) => {
      const plan = item.output.plan;
      if (!plan?.plan_id) {
        return null;
      }
      return {
        jobId: item.jobId,
        planKey: plan.plan_id,
        status: item.status,
        summary: item.summary,
        plan,
        dispatchPlan: item.output.dispatch_plan,
        runnerQueue: item.output.runner_queue
          ? {
              queued_task_count: item.output.runner_queue.queued_task_count,
              backend: item.output.runner_queue.backend,
            }
          : undefined,
        missingComponents: item.output.missing_components ?? [],
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item));
});

const toolJobPlans = computed(() => {
  return sessionStore.recentToolJobs
    .filter((job) => job.tool_key === "compatibility-test-runner")
    .map((job) => {
      const plan = job.output_payload?.plan;
      const dispatchPlan = job.output_payload?.dispatch_plan;
      const runnerQueue = job.output_payload?.runner_queue;
      const missingComponents = job.output_payload?.missing_components;
      if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
        return null;
      }
      return {
        jobId: job.id,
        planKey: String((plan as { plan_id?: unknown }).plan_id || job.id),
        status: job.status,
        summary: job.summary,
        plan: plan as unknown as CompatibilityPlan,
        dispatchPlan:
          dispatchPlan && typeof dispatchPlan === "object" && !Array.isArray(dispatchPlan)
            ? dispatchPlan as unknown as CompatibilityDispatchPlan
            : undefined,
        runnerQueue:
          runnerQueue && typeof runnerQueue === "object" && !Array.isArray(runnerQueue)
            ? runnerQueue as { queued_task_count?: number; backend?: string }
            : undefined,
        missingComponents: Array.isArray(missingComponents)
          ? missingComponents.map((value) => String(value))
          : [],
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item?.plan?.plan_id))
    .slice(0, 3);
});

const plans = computed(() => {
  const seenPlanIds = new Set<string>();
  const merged = [];
  for (const item of [...directPlans.value, ...toolJobPlans.value]) {
    if (seenPlanIds.has(item.plan.plan_id)) continue;
    seenPlanIds.add(item.plan.plan_id);
    merged.push(item);
  }
  return merged.slice(0, 3);
});

const currentReportArtifacts = computed(() => {
  return (executionReport.value?.artifacts ?? []).slice(0, 12);
});

const activeRunnerCount = computed(() => runners.value.filter((runner) => runnerIsActive(runner)).length);

const runnerStatusSummary = computed(() => {
  const offline = Math.max(0, runners.value.length - activeRunnerCount.value);
  return `在线 ${activeRunnerCount.value} / 离线 ${offline}`;
});

const noActiveRunners = computed(() => runners.value.length > 0 && activeRunnerCount.value === 0);

const displayedRunners = computed(() => {
  return [...runners.value]
    .sort((left, right) => {
      const activeDelta = Number(runnerIsActive(right)) - Number(runnerIsActive(left));
      if (activeDelta) return activeDelta;
      const heartbeatDelta = runnerHeartbeatTime(right) - runnerHeartbeatTime(left);
      if (heartbeatDelta) return heartbeatDelta;
      return left.runner_id.localeCompare(right.runner_id);
    })
    .slice(0, 8);
});

const hiddenRunnerCount = computed(() => Math.max(0, runners.value.length - displayedRunners.value.length));

const usesWebEntrypoint = computed(() => ["web", "h5"].includes(productType.value));
const usesMobileEntrypoint = computed(() => ["android_app", "ios_app"].includes(productType.value));
const usesMiniProgramEntrypoint = computed(() => ["wechat_mini_program", "alipay_mini_program"].includes(productType.value));
const usesLinuxEntrypoint = computed(() => productType.value === "linux_app");
const currentWizardStep = computed(() => wizardSteps.find((step) => step.id === wizardStep.value) ?? wizardSteps[0]);
const wizardProgress = computed(() => Math.round((wizardStep.value / wizardSteps.length) * 100));
const wizardIsLastStep = computed(() => wizardStep.value === wizardSteps.length);

function wizardStepComplete(step: number) {
  return Boolean(wizardReplies.value[step]) || step === 3;
}

function wizardAnswer(step: number) {
  if (wizardReplies.value[step]) return wizardReplies.value[step];
  if (step === 1) return [productName.value || "未命名产品", productVersion.value ? `v${productVersion.value}` : "版本待定", productTypes.find((item) => item.value === productType.value)?.label].filter(Boolean).join(" · ");
  if (step === 2) return [entryUrl.value, artifactUri.value, command.value].filter((value) => value.trim()).join(" · ") || "稍后补充接入信息";
  if (step === 3) return authStrategies.find((item) => item.value === authStrategy.value)?.label || "未指定";
  if (step === 4) return [testScope.value, priorityFlows.value].filter((value) => value.trim()).join("；") || "暂未指定重点流程";
  if (step === 5) return [excludeScope.value ? `排除：${excludeScope.value}` : "", forbiddenActions.value ? `禁止：${forbiddenActions.value}` : "", dataPolicy.value ? `数据：${dataPolicy.value}` : ""].filter(Boolean).join("；");
  return "已检查摘要，准备生成";
}

function markWizardStep(step: number) {
  if (!wizardStepTouched.value.includes(step)) wizardStepTouched.value = [...wizardStepTouched.value, step];
}

function nextWizardStep() {
  if (!wizardStepComplete(wizardStep.value)) return;
  markWizardStep(wizardStep.value);
  wizardStep.value = Math.min(wizardSteps.length, wizardStep.value + 1);
}

function previousWizardStep() {
  wizardStep.value = Math.max(1, wizardStep.value - 1);
}

function goToWizardStep(step: number) {
  if (step <= wizardStep.value || wizardStepTouched.value.includes(step - 1)) wizardStep.value = step;
}

function applyNaturalLanguageAnswer(step: number, content: string) {
  const answer = content.trim();
  if (step === 1) {
    const normalized = answer.toLowerCase();
    const type = normalized.includes("android") ? "android_app"
      : normalized.includes("ios") || normalized.includes("iphone") ? "ios_app"
        : normalized.includes("linux") ? "linux_app"
          : normalized.includes("小程序") ? (normalized.includes("支付宝") ? "alipay_mini_program" : "wechat_mini_program")
            : normalized.includes("h5") ? "h5" : normalized.includes("web") ? "web" : productType.value;
    productType.value = type;
    const versionMatch = answer.match(/(?:版本|v)\s*[:：]?\s*([\dA-Za-z._-]+)/i);
    if (versionMatch) productVersion.value = versionMatch[1];
    const nameMatch = answer.match(/(?:产品名(?:叫|是)?|产品名称(?:叫|是)?|叫)\s*[“「]?([^，,。；;\s”」]+)[”」]?/i);
    if (nameMatch) productName.value = nameMatch[1];
  } else if (step === 2) {
    const urlMatch = answer.match(/https?:\/\/[^\s，,。；;)）]+/i);
    if (urlMatch) entryUrl.value = urlMatch[0];
    const artifactMatch = answer.match(/[^\s，,。；;]+\.(?:apk|ipa|aab|deb|AppImage|zip)/i);
    if (artifactMatch) artifactUri.value = artifactMatch[0];
    const commandMatch = answer.match(/(?:启动命令|运行)\s*[:：]?\s*([^，,。；;]+)/i);
    if (commandMatch) command.value = commandMatch[1].trim();
  } else if (step === 3) {
    if (/无需|不需要|免登录/.test(answer)) authStrategy.value = "none";
    else if (/token/i.test(answer)) authStrategy.value = "token";
    else if (/cookie/i.test(answer)) authStrategy.value = "cookie";
    else if (/人工|手动/.test(answer)) authStrategy.value = "manual";
    else if (/账号|帐号|密码/.test(answer)) authStrategy.value = "account";
    const urlMatch = answer.match(/https?:\/\/[^\s，,。；;)）]+/i);
    if (urlMatch) baseApi.value = urlMatch[0];
  } else if (step === 4) {
    testScope.value = answer;
    priorityFlows.value = answer;
  } else if (step === 5) {
    if (/排除|不测|不包含/.test(answer)) excludeScope.value = answer;
    if (/禁止|不要|不能|不可/.test(answer)) forbiddenActions.value = answer;
    dataPolicy.value = answer;
  }
}

function handleWizardMessage(messageId: string, content: string) {
  if (!messageId || processedWizardMessageIds.has(messageId) || !content.trim()) return;
  processedWizardMessageIds.add(messageId);
  const step = wizardStep.value;
  wizardReplies.value = { ...wizardReplies.value, [step]: content.trim() };
  applyNaturalLanguageAnswer(step, content);
  markWizardStep(step);
  if (step < wizardSteps.length) {
    wizardStep.value = step + 1;
  } else if (/确认|生成|开始|可以/.test(content)) {
    void draftCompatibilityPlan();
  }
}

watch(
  () => sessionStore.messages,
  (messages) => {
    if (sessionStore.selectedModeKey !== "compatibility_testing") return;
    const latestUserMessage = [...messages].reverse().find((message) => message.role === "user");
    if (latestUserMessage) handleWizardMessage(latestUserMessage.id, latestUserMessage.content);
  },
  { deep: true },
);

function availabilityTone(availability: string) {
  if (availability === "available") return "online";
  if (availability === "planned_only") return "degraded";
  return "offline";
}

function matchingRunnerText(metadata: Record<string, unknown> | undefined) {
  const ids = metadata?.matching_runner_ids;
  if (!Array.isArray(ids) || !ids.length) return "";
  return ids.map((item) => String(item)).join(" / ");
}

function runnerIsActive(runner: CompatibilityRunnerRecord) {
  return runner.status === "online" || runner.status === "busy";
}

function runnerHeartbeatTime(runner: CompatibilityRunnerRecord) {
  const value = Date.parse(runner.heartbeat_at || "");
  return Number.isFinite(value) ? value : 0;
}

function runnerOfflineReason(runner: CompatibilityRunnerRecord) {
  const reason = runner.metadata?.offline_reason;
  return typeof reason === "string" && reason ? reason : "";
}

function selectableEnvironmentIds(plan: CompatibilityPlan) {
  return plan.environments
    .filter((environment) => environment.availability === "available")
    .map((environment) => environment.environment_id);
}

function selectedCaseIds(plan: CompatibilityPlan) {
  if (Object.prototype.hasOwnProperty.call(selectedCaseIdsByPlan.value, plan.plan_id)) {
    return selectedCaseIdsByPlan.value[plan.plan_id] ?? [];
  }
  return plan.cases.map((testCase) => testCase.case_id);
}

function selectedEnvironmentIds(plan: CompatibilityPlan) {
  if (Object.prototype.hasOwnProperty.call(selectedEnvironmentIdsByPlan.value, plan.plan_id)) {
    return selectedEnvironmentIdsByPlan.value[plan.plan_id] ?? [];
  }
  return selectableEnvironmentIds(plan);
}

function isCaseSelected(plan: CompatibilityPlan, caseId: string) {
  return selectedCaseIds(plan).includes(caseId);
}

function isEnvironmentSelected(plan: CompatibilityPlan, environmentId: string) {
  return selectedEnvironmentIds(plan).includes(environmentId);
}

function setCaseSelected(plan: CompatibilityPlan, caseId: string, checked: boolean) {
  const current = new Set(selectedCaseIds(plan));
  if (checked) current.add(caseId);
  else current.delete(caseId);
  selectedCaseIdsByPlan.value = {
    ...selectedCaseIdsByPlan.value,
    [plan.plan_id]: [...current],
  };
}

function onCaseSelectionChanged(plan: CompatibilityPlan, caseId: string, event: Event) {
  setCaseSelected(plan, caseId, Boolean((event.target as HTMLInputElement | null)?.checked));
}

function setEnvironmentSelected(plan: CompatibilityPlan, environmentId: string, checked: boolean) {
  const current = new Set(selectedEnvironmentIds(plan));
  if (checked) current.add(environmentId);
  else current.delete(environmentId);
  selectedEnvironmentIdsByPlan.value = {
    ...selectedEnvironmentIdsByPlan.value,
    [plan.plan_id]: [...current],
  };
}

function onEnvironmentSelectionChanged(plan: CompatibilityPlan, environmentId: string, event: Event) {
  setEnvironmentSelected(plan, environmentId, Boolean((event.target as HTMLInputElement | null)?.checked));
}

function selectAllCases(plan: CompatibilityPlan) {
  selectedCaseIdsByPlan.value = {
    ...selectedCaseIdsByPlan.value,
    [plan.plan_id]: plan.cases.map((testCase) => testCase.case_id),
  };
}

function clearCases(plan: CompatibilityPlan) {
  selectedCaseIdsByPlan.value = {
    ...selectedCaseIdsByPlan.value,
    [plan.plan_id]: [],
  };
}

function selectAvailableEnvironments(plan: CompatibilityPlan) {
  selectedEnvironmentIdsByPlan.value = {
    ...selectedEnvironmentIdsByPlan.value,
    [plan.plan_id]: selectableEnvironmentIds(plan),
  };
}

function selectionStats(plan: CompatibilityPlan) {
  const cases = selectedCaseIds(plan);
  const environments = selectedEnvironmentIds(plan);
  return {
    cases,
    environments,
    estimatedRuns: cases.length * environments.length,
  };
}

function canDispatch(plan: CompatibilityPlan) {
  const stats = selectionStats(plan);
  const risksConfirmed = !plan.risks.length || Boolean(riskConfirmedByPlan.value[plan.plan_id]);
  return stats.cases.length > 0 && stats.environments.length > 0 && risksConfirmed;
}

function isDispatchingPlan(plan: CompatibilityPlan) {
  return dispatchingPlanId.value === plan.plan_id;
}

function setRiskConfirmed(plan: CompatibilityPlan, checked: boolean) {
  riskConfirmedByPlan.value = {
    ...riskConfirmedByPlan.value,
    [plan.plan_id]: checked,
  };
}

function onRiskConfirmedChanged(plan: CompatibilityPlan, event: Event) {
  setRiskConfirmed(plan, Boolean((event.target as HTMLInputElement | null)?.checked));
}

function valueText(value: unknown) {
  const text = String(value ?? "").trim();
  return text || "-";
}

function environmentDetailText(environment: CompatibilityEnvironmentSpec) {
  const os = [environment.os, environment.os_version].map((item) => String(item || "").trim()).filter(Boolean).join(" ");
  const browser = [environment.browser, environment.browser_version]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(" ");
  return [os, browser, environment.device, environment.viewport, environment.automation_driver]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(" · ");
}

function selectorDetailText(task: CompatibilityRunnerTask) {
  const selector = task.runner_selector ?? {};
  const os = [selector.os, selector.os_version].map((item) => String(item || "").trim()).filter(Boolean).join(" ");
  const browser = [selector.browser, selector.browser_version]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(" ");
  const capabilities = Array.isArray(selector.capabilities) ? selector.capabilities.map((item) => String(item).trim()).filter(Boolean).join(", ") : "";
  return [selector.provider, os, browser, selector.device, capabilities]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(" · ");
}

function taskTone(status: string) {
  if (status === "completed") return "online";
  if (status === "failed" || status === "cancelled") return "offline";
  return "degraded";
}

function reportEnvironmentTone(status: string) {
  if (status === "completed") return "online";
  if (status === "failed" || status === "cancelled") return "offline";
  return "degraded";
}

function resultSummary(task: CompatibilityQueuedTask) {
  const summary = task.result?.summary;
  return typeof summary === "string" && summary.trim() ? summary : "暂无结果摘要";
}

function taskModeCallResults(task: CompatibilityQueuedTask) {
  const rawResults = task.result?.mode_call_results;
  if (!Array.isArray(rawResults)) return [];
  return rawResults
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .map((item) => ({
      toolKey: String(item.tool_key || item.toolKey || "mode-call"),
      status: String(item.status || "unknown"),
      summary: String(item.summary || ""),
    }))
    .slice(0, 4);
}

function taskModeCallSummary(task: CompatibilityQueuedTask) {
  const rawResults = task.result?.mode_call_results;
  if (!Array.isArray(rawResults) || !rawResults.length) return "";
  const statuses = rawResults
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .map((item) => String(item.status || "").trim().toLowerCase());
  const completed = statuses.filter((status) => status === "completed").length;
  const partial = statuses.filter((status) => status === "partial" || status === "waiting_approval").length;
  const failed = statuses.filter((status) => status === "failed" || status === "denied").length;
  const other = Math.max(0, statuses.length - completed - partial - failed);
  return [
    `完成 ${completed}`,
    `部分 ${partial}`,
    `失败 ${failed}`,
    other ? `其他 ${other}` : "",
  ].filter(Boolean).join(" / ");
}

function recoverableTaskSummary(report: CompatibilityExecutionReport) {
  const tasks = report.recoverable_tasks ?? [];
  const failed = tasks.filter((task) => task.status === "failed").length;
  const cancelled = tasks.filter((task) => task.status === "cancelled").length;
  const interrupted = tasks.filter((task) => task.status === "assigned").length;
  return {
    total: tasks.length,
    failed,
    cancelled,
    interrupted,
  };
}

function requeueStatusLabel(status: string) {
  const labels: Record<string, string> = {
    assigned: "执行中",
    cancelled: "已取消",
    completed: "已完成",
    failed: "失败",
    queued: "排队中",
  };
  return labels[status] ?? status;
}

function formatRequeueSkipReason(reason?: string) {
  if (!reason) return "任务状态已变化";
  if (reason === "dispatch_filter_mismatch") return "任务不属于当前调度";
  if (reason === "runner_filter_mismatch") return "任务不属于当前 Runner";
  if (reason === "task_not_found") return "任务不存在或已被清理";
  const statusMatch = reason.match(/^status_(.+)_not_requeueable$/);
  if (statusMatch) {
    return `当前状态为${requeueStatusLabel(statusMatch[1])}，不在本次重跑范围内`;
  }
  return reason;
}

function requeueSkippedMessage(skippedTaskIds: string[], skippedReasons?: Record<string, string>) {
  const firstTaskId = skippedTaskIds[0];
  const reason = formatRequeueSkipReason(skippedReasons?.[firstTaskId]);
  const remaining = skippedTaskIds.length > 1 ? `，另有 ${skippedTaskIds.length - 1} 个任务被跳过` : "";
  return `未重新入队：${firstTaskId}（${reason}）${remaining}`;
}

function cleanupRunnerStatusLabel(status: string) {
  const labels: Record<string, string> = {
    busy: "忙碌",
    disabled: "已禁用",
    offline: "离线",
    online: "在线",
  };
  return labels[status] ?? status;
}

function formatRunnerCleanupSkipReason(reason?: string) {
  if (!reason) return "未满足清理条件";
  if (reason === "runner_has_assigned_tasks") return "仍有执行中任务";
  if (reason === "runner_heartbeat_unknown") return "心跳时间不可识别";
  if (reason === "runner_not_found") return "Runner 不存在或已被清理";
  if (reason === "runner_not_old_enough") return "离线时间未超过保留窗口";
  const statusMatch = reason.match(/^runner_status_(.+)_not_cleanupable$/);
  if (statusMatch) {
    return `当前状态为${cleanupRunnerStatusLabel(statusMatch[1])}，不能清理`;
  }
  return reason;
}

function runnerCleanupSkippedMessage(skippedRunnerIds: string[], skippedReasons?: Record<string, string>) {
  const firstRunnerId = skippedRunnerIds[0];
  const reason = formatRunnerCleanupSkipReason(skippedReasons?.[firstRunnerId]);
  const remaining = skippedRunnerIds.length > 1 ? `，另有 ${skippedRunnerIds.length - 1} 个 Runner 被跳过` : "";
  return `未清理：${firstRunnerId}（${reason}）${remaining}`;
}

function artifactLabel(artifact: CompatibilityArtifactRecord) {
  return artifact.label || artifact.type || artifact.artifact_id;
}

function isImageArtifact(artifact: CompatibilityArtifactRecord) {
  const mime = String(artifact.mime_type || "").toLowerCase();
  const type = String(artifact.type || "").toLowerCase();
  return mime.startsWith("image/") || type.includes("screenshot") || type.includes("image");
}

function artifactPreview(artifact: CompatibilityArtifactRecord) {
  const preview = artifact.metadata?.preview;
  return typeof preview === "string" ? preview : "";
}

function artifactContentUrl(artifact: CompatibilityArtifactRecord) {
  const storageBackend = String(artifact.metadata?.storage_backend || "").toLowerCase();
  const hasReadableBackend = Boolean(artifact.metadata?.local_path) || ["local", "rustfs"].includes(storageBackend);
  if (hasReadableBackend || artifact.uri.startsWith("rustfs://") || artifact.uri.startsWith("/api/")) {
    return `/api/v1/compatibility/artifacts/${encodeURIComponent(artifact.artifact_id)}/content`;
  }
  return artifact.uri;
}

function splitList(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildProductAccessManifest(): CompatibilityProductAccessManifest {
  const artifact = artifactUri.value.trim()
    ? {
        kind: usesWebEntrypoint.value ? "entrypoint" : "build_artifact",
        uri: artifactUri.value.trim(),
      }
    : null;
  return {
    product_type: productType.value,
    name: productName.value.trim() || "未命名产品",
    version: productVersion.value.trim() || null,
    artifact,
    entrypoint: {
      url: entryUrl.value.trim() || null,
      package_name: packageName.value.trim() || null,
      activity: activity.value.trim() || null,
      bundle_id: bundleId.value.trim() || null,
      mini_program_path: miniProgramPath.value.trim() || null,
      command: command.value.trim() || null,
    },
    auth: {
      strategy: authStrategy.value,
      username_ref: usernameRef.value.trim() || null,
      password_ref: passwordRef.value.trim() || null,
      token_ref: tokenRef.value.trim() || null,
      manual_steps: authStrategy.value === "manual" ? ["执行阶段需要人工完成登录或授权"] : [],
    },
    network: {
      requires_vpn: requiresVpn.value,
      base_api: baseApi.value.trim() || null,
      proxy: null,
    },
    test_scope: {
      modules: splitList(testScope.value),
      priority_flows: splitList(priorityFlows.value),
      exclude: splitList(excludeScope.value),
      forbidden_actions: splitList(forbiddenActions.value),
      data_policy: dataPolicy.value.trim() || "unspecified",
    },
    metadata: {
      source: "compatibility_panel",
    },
  };
}

async function draftCompatibilityPlan() {
  const manifest = buildProductAccessManifest();
  isDraftingPlan.value = true;
  runnerError.value = "";
  try {
    const output = await api.draftCompatibilityPlan({
      action: "draft_plan",
      objective: `为 ${manifest.name} 生成兼容性测试计划。`,
      product_access_manifest: manifest,
      product_type: manifest.product_type,
      target_url: manifest.entrypoint.url,
      artifact: manifest.artifact?.uri,
      test_scope: manifest.test_scope.modules,
      priority_flows: manifest.test_scope.priority_flows,
      forbidden_actions: manifest.test_scope.forbidden_actions,
    });
    directPlanOutputs.value = [
      {
        jobId: `direct-${Date.now()}`,
        status: output.status,
        summary: output.summary,
        output,
      },
      ...directPlanOutputs.value,
    ].slice(0, 3);
    await refreshRunnerState();
  } catch (error) {
    runnerError.value = error instanceof Error ? error.message : "兼容性测试计划生成失败";
  } finally {
    isDraftingPlan.value = false;
  }
}

async function refreshRunnerState() {
  isLoadingRunners.value = true;
  runnerError.value = "";
  try {
    const [runnerList, taskList, summary, report] = await Promise.all([
      api.listCompatibilityRunners(),
      api.listCompatibilityRunnerTasks(),
      api.getCompatibilitySummary(),
      api.getCompatibilityReport(),
    ]);
    runners.value = runnerList;
    queuedTasks.value = taskList.slice(0, 12);
    taskSummary.value = summary;
    executionReport.value = report;
  } catch (error) {
    runnerError.value = error instanceof Error ? error.message : "Runner 状态加载失败";
  } finally {
    isLoadingRunners.value = false;
  }
}

async function requeueRecoverableTasks() {
  const recoverableTaskIds = executionReport.value?.recoverable_tasks?.map((task) => task.task_id) ?? [];
  if (!recoverableTaskIds.length) return;
  isRequeueingTasks.value = true;
  runnerError.value = "";
  try {
    const result = await api.requeueCompatibilityTasks({
      task_ids: recoverableTaskIds,
      statuses: ["failed", "cancelled", "assigned"],
      reason: "manual_retry_recoverable_from_compatibility_report",
    });
    await refreshRunnerState();
    const skippedTaskIds = result.skipped_task_ids ?? [];
    if (skippedTaskIds.length) {
      const skippedMessage = requeueSkippedMessage(skippedTaskIds, result.skipped_reasons);
      runnerError.value = result.requeued_count
        ? `已重新入队 ${result.requeued_count} 个任务；${skippedMessage}`
        : skippedMessage;
    }
  } catch (error) {
    runnerError.value = error instanceof Error ? error.message : "可恢复任务重新入队失败";
  } finally {
    isRequeueingTasks.value = false;
  }
}

async function cleanupOfflineRunners() {
  if (!runners.value.length || isCleaningRunners.value) return;
  if (!isConfirmingRunnerCleanup.value) {
    isConfirmingRunnerCleanup.value = true;
    return;
  }
  isCleaningRunners.value = true;
  runnerError.value = "";
  try {
    const result = await api.cleanupCompatibilityRunners({ older_than_seconds: 3600 });
    await refreshRunnerState();
    const skippedRunnerIds = result.skipped_runner_ids ?? [];
    if (result.deleted_count && skippedRunnerIds.length) {
      runnerError.value = `已清理 ${result.deleted_count} 个离线 Runner 记录；${runnerCleanupSkippedMessage(skippedRunnerIds, result.skipped_reasons)}`;
    } else if (result.deleted_count) {
      runnerError.value = `已清理 ${result.deleted_count} 个离线 Runner 记录。`;
    } else if (skippedRunnerIds.length) {
      runnerError.value = runnerCleanupSkippedMessage(skippedRunnerIds, result.skipped_reasons);
    } else {
      runnerError.value = "没有符合条件的离线 Runner 记录。";
    }
  } catch (error) {
    runnerError.value = error instanceof Error ? error.message : "离线 Runner 清理失败";
  } finally {
    isCleaningRunners.value = false;
    isConfirmingRunnerCleanup.value = false;
  }
}

function cancelRunnerCleanup() {
  isConfirmingRunnerCleanup.value = false;
}

async function confirmDispatch(plan: CompatibilityPlan) {
  const stats = selectionStats(plan);
  dispatchingPlanId.value = plan.plan_id;
  runnerError.value = "";
  try {
    const output = await api.dispatchCompatibilityPlan({
      action: "execute_approved_plan",
      plan,
      confirm_risks: Boolean(riskConfirmedByPlan.value[plan.plan_id]) || !plan.risks.length,
      selected_case_ids: stats.cases,
      selected_environment_ids: stats.environments,
    });
    directPlanOutputs.value = [
      {
        jobId: `direct-${Date.now()}`,
        status: output.status,
        summary: output.summary,
        output,
      },
      ...directPlanOutputs.value.filter((item) => item.output.plan?.plan_id !== plan.plan_id),
    ].slice(0, 3);
    await refreshRunnerState();
  } catch (error) {
    runnerError.value = error instanceof Error ? error.message : "Runner 调度计划生成失败";
  } finally {
    dispatchingPlanId.value = "";
  }
}

onMounted(() => {
  for (const message of sessionStore.messages) {
    processedWizardMessageIds.add(message.id);
  }
  void refreshRunnerState();
  refreshTimer = window.setInterval(() => {
    void refreshRunnerState();
  }, 8000);
});

onUnmounted(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
  }
});
</script>

<template>
  <section v-if="sessionStore.session" class="observability-panel compatibility-panel">
    <div class="observability-head">
      <div>
        <strong>兼容性测试计划</strong>
        <p>展示产品接入、环境矩阵、Runner 在线状态、任务队列和待接入 Provider。</p>
      </div>
      <button type="button" class="home-tool-btn ghost" :disabled="isLoadingRunners" @click="refreshRunnerState">
        刷新
      </button>
    </div>

    <div class="compatibility-wizard">
      <div class="wizard-progress-head">
        <div>
          <span class="wizard-eyebrow">对话式配置 · {{ wizardStep }}/{{ wizardSteps.length }}</span>
          <strong>{{ currentWizardStep.title }}</strong>
          <p>{{ currentWizardStep.prompt }}</p>
        </div>
        <span class="wizard-progress-value">{{ wizardProgress }}%</span>
      </div>
      <div class="wizard-progress-track"><span :style="{ width: `${wizardProgress}%` }"></span></div>

      <div class="wizard-steps" aria-label="配置进度">
        <button
          v-for="step in wizardSteps"
          :key="step.id"
          type="button"
          :class="['wizard-step', { active: step.id === wizardStep, done: wizardStepTouched.includes(step.id) }]"
          :disabled="step.id > wizardStep && !wizardStepTouched.includes(step.id - 1)"
          @click="goToWizardStep(step.id)"
        >
          <span>{{ wizardStepTouched.includes(step.id) ? "✓" : step.id }}</span>{{ step.label }}
        </button>
      </div>

      <div class="wizard-conversation">
        <div v-for="step in wizardSteps.filter((item) => item.id < wizardStep && wizardStepTouched.includes(item.id))" :key="`answer-${step.id}`" class="wizard-turn wizard-turn-complete">
          <div class="wizard-avatar wizard-user-avatar">我</div>
          <div class="wizard-turn-content">
            <small>{{ step.title }}</small>
            <div class="wizard-answer">{{ wizardAnswer(step.id) }}</div>
          </div>
          <button type="button" class="wizard-edit" @click="goToWizardStep(step.id)">修改</button>
        </div>

        <div class="wizard-turn wizard-turn-active">
          <div class="wizard-avatar">蛛</div>
          <div class="wizard-turn-content">
            <small>御策天检</small>
            <div class="wizard-question">{{ currentWizardStep.prompt }}</div>
            <div class="wizard-chat-hint">
              <p>请直接在页面底部的聊天框回答，我会自动识别信息并继续下一步。</p>
              <div class="wizard-chat-example">例如：<strong>“这是 Android App，产品名叫测试，版本 1.0.0”</strong></div>
              <div v-if="wizardReplies[wizardStep]" class="wizard-detected">
                <span>已识别本轮回答</span>
                <strong>{{ wizardReplies[wizardStep] }}</strong>
              </div>
            </div>
            <div v-if="false">
            <div v-if="wizardStep === 1" class="wizard-fields wizard-fields-3">
              <label><span>产品类型</span><select v-model="productType"><option v-for="type in productTypes" :key="type.value" :value="type.value">{{ type.label }}</option></select></label>
              <label><span>产品名称</span><input v-model="productName" type="text" placeholder="例如：企业工作台" /></label>
              <label><span>版本</span><input v-model="productVersion" type="text" placeholder="1.0.0" /></label>
            </div>
            <div v-else-if="wizardStep === 2" class="wizard-fields wizard-fields-2">
              <label v-if="usesWebEntrypoint"><span>入口 URL</span><input v-model="entryUrl" type="url" placeholder="https://test.example.com" /></label>
              <label v-if="usesMobileEntrypoint || usesMiniProgramEntrypoint || usesLinuxEntrypoint"><span>构建产物</span><input v-model="artifactUri" type="text" placeholder="apk / ipa / dist / deb / AppImage" /></label>
              <label v-if="productType === 'android_app'"><span>包名</span><input v-model="packageName" type="text" placeholder="com.example.app" /></label>
              <label v-if="productType === 'android_app'"><span>Activity</span><input v-model="activity" type="text" placeholder=".MainActivity" /></label>
              <label v-if="productType === 'ios_app'"><span>Bundle ID</span><input v-model="bundleId" type="text" placeholder="com.example.ios" /></label>
              <label v-if="usesMiniProgramEntrypoint"><span>小程序路径</span><input v-model="miniProgramPath" type="text" placeholder="dist/wechat" /></label>
              <label v-if="usesLinuxEntrypoint"><span>启动命令</span><input v-model="command" type="text" placeholder="./app --test" /></label>
            </div>
            <div v-else-if="wizardStep === 3" class="wizard-fields wizard-fields-2">
              <label><span>认证方式</span><select v-model="authStrategy"><option v-for="strategy in authStrategies" :key="strategy.value" :value="strategy.value">{{ strategy.label }}</option></select></label>
              <label><span>Base API（可选）</span><input v-model="baseApi" type="url" placeholder="https://api-test.example.com" /></label>
              <label v-if="authStrategy === 'account'"><span>账号引用</span><input v-model="usernameRef" type="text" placeholder="secret://qa-user" /></label>
              <label v-if="authStrategy === 'account'"><span>密码引用</span><input v-model="passwordRef" type="text" placeholder="secret://qa-password" /></label>
              <label v-if="authStrategy === 'token'"><span>Token 引用</span><input v-model="tokenRef" type="text" placeholder="secret://qa-token" /></label>
              <label class="wizard-check"><input v-model="requiresVpn" type="checkbox" /><span>需要 VPN 才能访问</span></label>
            </div>
            <div v-else-if="wizardStep === 4" class="wizard-fields">
              <label><span>测试模块</span><textarea v-model="testScope" rows="2" placeholder="登录、首页、搜索、订单"></textarea></label>
              <label><span>核心流程</span><textarea v-model="priorityFlows" rows="2" placeholder="登录后搜索商品并查看详情"></textarea></label>
            </div>
            <div v-else-if="wizardStep === 5" class="wizard-fields">
              <label><span>排除范围</span><textarea v-model="excludeScope" rows="2" placeholder="真实支付、生产数据删除"></textarea></label>
              <label><span>禁止动作</span><textarea v-model="forbiddenActions" rows="2" placeholder="删除数据、真实扣款、发送外部消息"></textarea></label>
              <label><span>数据策略</span><input v-model="dataPolicy" type="text" placeholder="仅使用测试数据" /></label>
            </div>
            <div v-else class="wizard-review">
              <div class="wizard-review-row"><span>产品</span><strong>{{ wizardAnswer(1) }}</strong></div>
              <div class="wizard-review-row"><span>接入</span><strong>{{ wizardAnswer(2) }}</strong></div>
              <div class="wizard-review-row"><span>认证</span><strong>{{ wizardAnswer(3) }}</strong></div>
              <div class="wizard-review-row"><span>范围</span><strong>{{ wizardAnswer(4) }}</strong></div>
              <div class="wizard-review-row"><span>边界</span><strong>{{ wizardAnswer(5) }}</strong></div>
            </div>
            </div>
          </div>
        </div>
      </div>

      <div class="wizard-actions">
        <span class="wizard-hint">{{ wizardStep === wizardSteps.length ? "确认后，AI 会生成兼容性测试计划" : "请在下方聊天框直接回答，按 Enter 发送" }}</span>
      </div>
    </div>

    <div v-if="false" class="compatibility-intake">
      <div class="compatibility-subhead">
        <strong>产品接入</strong>
        <span class="registry-tag light">Manifest</span>
      </div>
      <div class="compatibility-form-grid">
        <label>
          <span>产品类型</span>
          <select v-model="productType">
            <option v-for="type in productTypes" :key="type.value" :value="type.value">{{ type.label }}</option>
          </select>
        </label>
        <label>
          <span>产品名称</span>
          <input v-model="productName" type="text" placeholder="未命名产品" />
        </label>
        <label>
          <span>版本</span>
          <input v-model="productVersion" type="text" placeholder="1.0.0" />
        </label>
        <label v-if="usesWebEntrypoint">
          <span>入口 URL</span>
          <input v-model="entryUrl" type="url" placeholder="https://test.example.com" />
        </label>
        <label v-if="usesMobileEntrypoint || usesMiniProgramEntrypoint || usesLinuxEntrypoint">
          <span>构建产物</span>
          <input v-model="artifactUri" type="text" placeholder="apk / ipa / dist / deb / AppImage" />
        </label>
        <label v-if="productType === 'android_app'">
          <span>包名</span>
          <input v-model="packageName" type="text" placeholder="com.example.app" />
        </label>
        <label v-if="productType === 'android_app'">
          <span>Activity</span>
          <input v-model="activity" type="text" placeholder=".MainActivity" />
        </label>
        <label v-if="productType === 'ios_app'">
          <span>Bundle ID</span>
          <input v-model="bundleId" type="text" placeholder="com.example.ios" />
        </label>
        <label v-if="usesMiniProgramEntrypoint">
          <span>小程序路径</span>
          <input v-model="miniProgramPath" type="text" placeholder="dist/wechat" />
        </label>
        <label v-if="usesLinuxEntrypoint">
          <span>启动命令</span>
          <input v-model="command" type="text" placeholder="./app --test" />
        </label>
        <label>
          <span>认证方式</span>
          <select v-model="authStrategy">
            <option v-for="strategy in authStrategies" :key="strategy.value" :value="strategy.value">{{ strategy.label }}</option>
          </select>
        </label>
        <label>
          <span>账号引用</span>
          <input v-model="usernameRef" type="text" placeholder="secret://qa-user" />
        </label>
        <label>
          <span>密码引用</span>
          <input v-model="passwordRef" type="text" placeholder="secret://qa-password" />
        </label>
        <label>
          <span>Token 引用</span>
          <input v-model="tokenRef" type="text" placeholder="secret://qa-token" />
        </label>
        <label>
          <span>Base API</span>
          <input v-model="baseApi" type="url" placeholder="https://api-test.example.com" />
        </label>
        <label class="compatibility-check">
          <input v-model="requiresVpn" type="checkbox" />
          <span>需要 VPN</span>
        </label>
      </div>
      <div class="compatibility-form-grid wide">
        <label>
          <span>测试模块</span>
          <textarea v-model="testScope" rows="2" placeholder="登录，首页，搜索"></textarea>
        </label>
        <label>
          <span>核心流程</span>
          <textarea v-model="priorityFlows" rows="2" placeholder="登录后搜索商品"></textarea>
        </label>
        <label>
          <span>排除范围</span>
          <textarea v-model="excludeScope" rows="2" placeholder="真实支付，生产数据删除"></textarea>
        </label>
        <label>
          <span>禁止动作</span>
          <textarea v-model="forbiddenActions" rows="2" placeholder="删除数据，真实扣款"></textarea>
        </label>
      </div>
      <div class="compatibility-actions">
        <label class="compatibility-data-policy">
          <span>数据策略</span>
          <input v-model="dataPolicy" type="text" />
        </label>
        <button type="button" class="home-tool-btn" :disabled="sessionStore.isBusy || isDraftingPlan || Boolean(dispatchingPlanId)" @click="draftCompatibilityPlan">
          {{ isDraftingPlan ? "生成中..." : "生成兼容性测试计划" }}
        </button>
      </div>
    </div>

    <div class="compatibility-section">
      <div class="compatibility-subhead">
        <strong>Runner 状态</strong>
        <span class="compatibility-inline-actions">
          <button
            v-if="hiddenRunnerCount || noActiveRunners"
            type="button"
            class="compatibility-link-btn"
            :disabled="isCleaningRunners"
            @click="cleanupOfflineRunners"
          >
            {{ isConfirmingRunnerCleanup ? "确认清理" : "清理离线记录" }}
          </button>
          <button
            v-if="isConfirmingRunnerCleanup"
            type="button"
            class="compatibility-link-btn"
            :disabled="isCleaningRunners"
            @click="cancelRunnerCleanup"
          >
            取消
          </button>
          <span class="registry-tag light">{{ runnerStatusSummary }}</span>
        </span>
      </div>
      <p v-if="runnerError" class="compatibility-risk">{{ runnerError }}</p>
      <p v-if="isConfirmingRunnerCleanup" class="compatibility-risk">
        将清理超过 1 小时未在线且没有执行中任务的 Runner 记录；任务历史、报告和证据不会被删除。
      </p>
      <div v-if="displayedRunners.length" class="compatibility-matrix">
        <div v-for="runner in displayedRunners" :key="runner.runner_id" class="compatibility-env-row">
          <span :class="['runtime-status-square-dot', runnerIsActive(runner) ? 'is-online' : 'is-degraded']"></span>
          <span>
            <strong>{{ runner.name || runner.runner_id }} · {{ runner.status }}</strong>
            <small>
              {{ runner.os || "-" }} · 并发 {{ runner.active_task_ids.length }}/{{ runner.max_parallel }}
            </small>
            <small>
              心跳 {{ runner.heartbeat_at }}
              <template v-if="runnerOfflineReason(runner)"> · {{ runnerOfflineReason(runner) }}</template>
            </small>
            <small>{{ runner.capabilities.slice(0, 8).join(" / ") || "未声明能力" }}</small>
          </span>
        </div>
      </div>
      <p v-if="hiddenRunnerCount" class="compatibility-risk">另有 {{ hiddenRunnerCount }} 个历史离线 Runner 已折叠。</p>
      <p v-if="noActiveRunners" class="compatibility-risk">当前没有在线 Runner，生成的计划会保留环境矩阵，但可执行环境需要先启动或注册 Runner。</p>
      <p v-if="!runners.length" class="compatibility-risk">暂无 Runner 在线。可使用后端脚本注册本机 Runner。</p>
    </div>

    <div v-if="queuedTasks.length" class="compatibility-section">
      <div class="compatibility-subhead">
        <strong>Runner 任务</strong>
        <span class="registry-tag light">{{ queuedTasks.length }} 条最近任务</span>
      </div>
      <div class="compatibility-matrix">
        <div v-for="task in queuedTasks" :key="task.task_id" class="compatibility-env-row">
          <span :class="['runtime-status-square-dot', `is-${taskTone(task.status)}`]"></span>
          <span>
            <strong>{{ task.status }} · {{ task.case_ids.length }} 条用例</strong>
            <small>
              {{ task.environment_id }} · {{ task.assigned_runner_id || "未分配" }} · {{ task.updated_at }}
            </small>
            <small>{{ resultSummary(task) }}</small>
            <small v-if="taskModeCallSummary(task)">模式调用：{{ taskModeCallSummary(task) }}</small>
            <span v-if="taskModeCallResults(task).length" class="compatibility-mode-call-list">
              <span
                v-for="call in taskModeCallResults(task)"
                :key="`${task.task_id}-${call.toolKey}`"
                :class="['registry-tag', `is-${taskTone(call.status)}`]"
                :title="call.summary"
              >
                {{ call.toolKey }} · {{ call.status }}
              </span>
            </span>
          </span>
        </div>
      </div>
    </div>

    <div v-if="taskSummary && taskSummary.total" class="compatibility-section">
      <div class="compatibility-subhead">
        <strong>执行摘要</strong>
        <span class="registry-tag light">{{ taskSummary.artifact_count }} 个证据</span>
      </div>
      <dl class="tool-activity-grid">
        <div>
          <dt>总任务</dt>
          <dd>{{ taskSummary.total }}</dd>
        </div>
        <div>
          <dt>排队</dt>
          <dd>{{ taskSummary.queued }}</dd>
        </div>
        <div>
          <dt>执行中</dt>
          <dd>{{ taskSummary.assigned }}</dd>
        </div>
        <div>
          <dt>通过</dt>
          <dd>{{ taskSummary.completed }}</dd>
        </div>
        <div>
          <dt>失败</dt>
          <dd>{{ taskSummary.failed }}</dd>
        </div>
        <div>
          <dt>取消</dt>
          <dd>{{ taskSummary.cancelled }}</dd>
        </div>
      </dl>
    </div>

    <div v-if="executionReport && executionReport.total_tasks" class="compatibility-section">
      <div class="compatibility-subhead">
        <strong>兼容性报告</strong>
        <span class="compatibility-inline-actions">
          <button
            v-if="executionReport.recoverable_tasks?.length"
            type="button"
            class="compatibility-link-btn"
            :disabled="isRequeueingTasks"
            @click="requeueRecoverableTasks"
          >
            重跑可恢复任务
          </button>
          <span class="registry-tag light">{{ executionReport.status }} · {{ executionReport.pass_rate }}%</span>
        </span>
      </div>
      <p class="compatibility-risk">{{ executionReport.summary }}</p>
      <p v-if="executionReport.recoverable_tasks?.length" class="compatibility-risk">
        可重跑 {{ recoverableTaskSummary(executionReport).total }} 个任务：
        失败 {{ recoverableTaskSummary(executionReport).failed }}，
        取消 {{ recoverableTaskSummary(executionReport).cancelled }}，
        中断 {{ recoverableTaskSummary(executionReport).interrupted }}
      </p>
      <div v-if="executionReport.environments.length" class="compatibility-matrix">
        <div
          v-for="environment in executionReport.environments"
          :key="environment.environment_id"
          class="compatibility-env-row"
        >
          <span :class="['runtime-status-square-dot', `is-${reportEnvironmentTone(environment.status)}`]"></span>
          <span>
            <strong>{{ environment.environment_id }} · {{ environment.status }}</strong>
            <small>
              通过 {{ environment.completed }} / 失败 {{ environment.failed }} / 取消 {{ environment.cancelled }} /
              待完成 {{ environment.pending }} ·
              证据 {{ environment.artifact_count }}
            </small>
          </span>
          <span></span>
        </div>
      </div>
      <div v-if="executionReport.failures.length" class="compatibility-failure-list">
        <strong>失败摘要</strong>
        <p v-for="failure in executionReport.failures.slice(0, 5)" :key="failure.task_id" class="compatibility-risk">
          {{ failure.environment_id }}：{{ failure.summary }}
        </p>
      </div>
      <details class="compatibility-report-markdown">
        <summary>查看 Markdown 报告</summary>
        <div class="conversation-entry-markdown compatibility-report-markdown-body">
          <AssistantMarkdown :content="executionReport.markdown" />
        </div>
      </details>
    </div>

    <div v-if="currentReportArtifacts.length" class="compatibility-section">
      <div class="compatibility-subhead">
        <strong>证据与日志</strong>
        <span class="registry-tag light">{{ currentReportArtifacts.length }} 条当前报告证据</span>
      </div>
      <div class="compatibility-evidence-list">
        <a
          v-for="artifact in currentReportArtifacts"
          :key="artifact.artifact_id"
          class="compatibility-evidence-item"
          :href="artifactContentUrl(artifact) || undefined"
          target="_blank"
          rel="noreferrer"
        >
          <img v-if="artifactContentUrl(artifact) && isImageArtifact(artifact)" :src="artifactContentUrl(artifact)" :alt="artifactLabel(artifact)" />
          <span>
            <strong>{{ artifactLabel(artifact) }}</strong>
            <small>{{ artifact.type }} · {{ artifact.environment_id }} · {{ artifact.created_at }}</small>
            <small v-if="artifactPreview(artifact)">{{ artifactPreview(artifact) }}</small>
          </span>
        </a>
      </div>
    </div>

    <div class="tool-activity-list">
      <article v-for="item in plans" :key="item.planKey" class="tool-activity-item">
        <div class="tool-activity-head">
          <div>
            <strong>{{ item.plan.product.name }} v{{ item.plan.version }}</strong>
            <p>{{ item.summary || item.plan.status }}</p>
          </div>
          <span class="registry-tag light">{{ item.plan.status }}</span>
        </div>

        <dl class="tool-activity-grid">
          <div>
            <dt>产品类型</dt>
            <dd>{{ item.plan.product.product_type }}</dd>
          </div>
          <div>
            <dt>环境</dt>
            <dd>{{ item.plan.environments.length }}</dd>
          </div>
          <div>
            <dt>用例</dt>
            <dd>{{ item.plan.cases.length }}</dd>
          </div>
          <div>
            <dt>风险</dt>
            <dd>{{ item.plan.risks.length }}</dd>
          </div>
          <div>
            <dt>任务量</dt>
            <dd>{{ item.plan.estimated_task_count }}</dd>
          </div>
          <div>
            <dt>预计耗时</dt>
            <dd>{{ item.plan.estimated_duration_minutes }} 分钟</dd>
          </div>
          <div v-if="item.dispatchPlan">
            <dt>调度任务</dt>
            <dd>{{ item.dispatchPlan.tasks.length }}</dd>
          </div>
          <div v-if="item.runnerQueue">
            <dt>队列</dt>
            <dd>{{ item.runnerQueue.queued_task_count ?? 0 }} · {{ item.runnerQueue.backend || "-" }}</dd>
          </div>
          <div v-if="item.dispatchPlan">
            <dt>跳过环境</dt>
            <dd>{{ item.dispatchPlan.skipped_environments.length }}</dd>
          </div>
        </dl>

        <div class="compatibility-section">
          <div class="compatibility-subhead">
            <strong>环境矩阵</strong>
            <button type="button" class="compatibility-link-btn" @click="selectAvailableEnvironments(item.plan)">
              选择可执行环境
            </button>
          </div>
          <div class="compatibility-matrix">
            <div v-for="env in item.plan.environments" :key="env.environment_id" class="compatibility-env-row">
              <input
                type="checkbox"
                :checked="isEnvironmentSelected(item.plan, env.environment_id)"
                :disabled="env.availability !== 'available'"
                @change="onEnvironmentSelectionChanged(item.plan, env.environment_id, $event)"
              />
              <span>
                <strong>[{{ env.priority }}] {{ env.name }}</strong>
                <small>
                  {{ env.provider }} · {{ env.availability }}
                  <template v-if="env.unavailable_reason"> · {{ env.unavailable_reason }}</template>
                </small>
                <small v-if="environmentDetailText(env)">
                  {{ environmentDetailText(env) }}
                </small>
                <small v-if="matchingRunnerText(env.metadata)">
                  Runner：{{ matchingRunnerText(env.metadata) }}
                </small>
              </span>
              <span :class="['runtime-status-square-dot', `is-${availabilityTone(env.availability)}`]"></span>
            </div>
          </div>
        </div>

        <div class="compatibility-section">
          <div class="compatibility-subhead">
            <strong>测试用例</strong>
            <span class="compatibility-inline-actions">
              <button type="button" class="compatibility-link-btn" @click="selectAllCases(item.plan)">全选</button>
              <button type="button" class="compatibility-link-btn" @click="clearCases(item.plan)">清空</button>
            </span>
          </div>
          <div class="compatibility-case-selector">
            <label v-for="testCase in item.plan.cases" :key="testCase.case_id" class="compatibility-case-option">
              <input
                type="checkbox"
                :checked="isCaseSelected(item.plan, testCase.case_id)"
                @change="onCaseSelectionChanged(item.plan, testCase.case_id, $event)"
              />
              <span>
                <strong>[{{ testCase.priority }}] {{ testCase.name }}</strong>
                <small>
                  {{ testCase.risk_level }}
                  <template v-if="testCase.requires_manual_approval"> · 需审批</template>
                </small>
              </span>
            </label>
          </div>
        </div>

        <div v-if="item.plan.risks.length" class="compatibility-section">
          <strong>风险与人工接管</strong>
          <p v-for="risk in item.plan.risks" :key="risk.risk_id" class="compatibility-risk">
            {{ valueText(risk.action) }}：{{ risk.reason }} {{ risk.suggested_control }}
          </p>
          <label class="compatibility-risk-confirm">
            <input
              type="checkbox"
              :checked="Boolean(riskConfirmedByPlan[item.plan.plan_id])"
              @change="onRiskConfirmedChanged(item.plan, $event)"
            />
            <span>确认已知风险和人工接管点，并允许按所选范围生成调度计划</span>
          </label>
        </div>

        <div v-if="item.dispatchPlan" class="compatibility-section">
          <strong>Runner 调度计划</strong>
          <div class="compatibility-matrix">
            <div v-for="task in item.dispatchPlan.tasks" :key="task.task_id" class="compatibility-env-row">
              <span class="runtime-status-square-dot is-degraded"></span>
              <span>
                <strong>{{ task.status }} · {{ task.case_ids.length }} 条用例</strong>
                <small>
                  环境 {{ task.environment_id }} ·
                  {{ task.mode_calls.map((call) => call.tool_key).join(" / ") || "未规划调用" }}
                </small>
                <small v-if="selectorDetailText(task)">
                  {{ selectorDetailText(task) }}
                </small>
              </span>
            </div>
          </div>
        </div>

        <div v-if="item.missingComponents.length" class="compatibility-section">
          <strong>待接入组件</strong>
          <div class="compatibility-case-list">
            <span v-for="component in item.missingComponents" :key="component" class="registry-tag">
              {{ component }}
            </span>
          </div>
        </div>

        <div v-if="!item.dispatchPlan" class="compatibility-actions">
          <span class="compatibility-selection-summary">
            已选 {{ selectionStats(item.plan).environments.length }} 个环境 /
            {{ selectionStats(item.plan).cases.length }} 条用例 /
            预计 {{ selectionStats(item.plan).estimatedRuns }} 次执行
          </span>
          <button
            type="button"
            class="home-tool-btn"
            :disabled="sessionStore.isBusy || isDraftingPlan || Boolean(dispatchingPlanId) || !canDispatch(item.plan)"
            @click="confirmDispatch(item.plan)"
          >
            {{ isDispatchingPlan(item.plan) ? "调度生成中..." : "确认并生成 Runner 调度计划" }}
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.compatibility-wizard {
  display: grid;
  gap: 16px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--surface) 94%, var(--accent) 6%), var(--surface));
}

.wizard-progress-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.wizard-progress-head strong,
.wizard-turn-content small,
.wizard-question {
  display: block;
}

.wizard-progress-head strong {
  margin-top: 4px;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.wizard-progress-head p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.wizard-eyebrow {
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.wizard-progress-value {
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
}

.wizard-progress-track {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--border);
}

.wizard-progress-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width .24s ease;
}

.wizard-steps {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 6px;
}

.wizard-step {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 0;
  padding: 7px 6px;
  border: 0;
  border-radius: 8px;
  color: var(--text-muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.wizard-step span {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 50%;
  font-size: 11px;
}

.wizard-step.active { color: var(--text); background: color-mix(in srgb, var(--accent) 10%, transparent); }
.wizard-step.active span,
.wizard-step.done span { border-color: var(--accent); color: var(--accent); }
.wizard-step:disabled { cursor: not-allowed; opacity: .55; }

.wizard-conversation { display: grid; gap: 12px; }

.wizard-turn { display: flex; align-items: flex-start; gap: 10px; }
.wizard-turn-complete { opacity: .82; }
.wizard-avatar {
  display: grid;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 9px;
  color: var(--accent-contrast, #fff);
  background: var(--accent);
  font-size: 12px;
  font-weight: 700;
}
.wizard-user-avatar { color: var(--text); background: var(--surface-subtle, rgba(127, 127, 127, .12)); }

.wizard-turn-content { min-width: 0; flex: 1; }
.wizard-turn-content small { color: var(--text-muted); font-size: 11px; }
.wizard-question { margin: 2px 0 12px; color: var(--text); font-size: 14px; line-height: 1.5; }
.wizard-answer { margin-top: 3px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 9px; color: var(--text); background: var(--surface-subtle, rgba(127, 127, 127, .06)); font-size: 13px; }
.wizard-edit { align-self: center; border: 0; color: var(--accent); background: transparent; font-size: 12px; cursor: pointer; }
.wizard-chat-hint { display: grid; gap: 8px; padding: 14px; border: 1px dashed color-mix(in srgb, var(--accent) 42%, var(--border)); border-radius: 11px; background: color-mix(in srgb, var(--accent) 5%, transparent); }
.wizard-chat-hint p { margin: 0; color: var(--text); font-size: 13px; line-height: 1.6; }
.wizard-chat-example { color: var(--text-muted); font-size: 12px; line-height: 1.5; }
.wizard-chat-example strong { color: var(--text); font-weight: 500; }
.wizard-detected { display: grid; gap: 3px; padding-top: 8px; border-top: 1px solid var(--border); }
.wizard-detected span { color: var(--accent); font-size: 11px; }
.wizard-detected strong { color: var(--text); font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }

.wizard-fields { display: grid; gap: 10px; }
.wizard-fields-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.wizard-fields-3 { grid-template-columns: 1.2fr 1.5fr .8fr; }
.wizard-fields label { display: grid; gap: 5px; min-width: 0; }
.wizard-fields label > span { color: var(--text-muted); font-size: 12px; }
.wizard-fields input, .wizard-fields select, .wizard-fields textarea { width: 100%; padding: 10px 11px; border: 1px solid var(--border); border-radius: 9px; color: var(--text); background: var(--surface); font: inherit; font-size: 13px; }
.wizard-fields textarea { resize: vertical; }
.wizard-check { display: flex !important; align-items: center; align-self: end; grid-auto-flow: column; justify-content: flex-start; }
.wizard-check input { width: auto; }

.wizard-review { display: grid; gap: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 10px; }
.wizard-review-row { display: grid; grid-template-columns: 64px 1fr; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
.wizard-review-row:last-child { border-bottom: 0; }
.wizard-review-row span { color: var(--text-muted); }
.wizard-review-row strong { min-width: 0; overflow-wrap: anywhere; font-weight: 500; }

.wizard-actions { display: flex; align-items: center; gap: 10px; }
.wizard-secondary { padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: transparent; cursor: pointer; }
.wizard-hint { margin-right: auto; color: var(--text-muted); font-size: 12px; }

@media (max-width: 720px) {
  .compatibility-wizard { padding: 14px; }
  .wizard-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .wizard-fields-2, .wizard-fields-3 { grid-template-columns: 1fr; }
  .wizard-actions { flex-wrap: wrap; }
  .wizard-hint { width: 100%; order: -1; }
}

.compatibility-section {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.compatibility-intake {
  display: grid;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.compatibility-subhead {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.compatibility-form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}

.compatibility-form-grid.wide {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.compatibility-form-grid label,
.compatibility-data-policy {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.compatibility-form-grid label > span,
.compatibility-data-policy > span {
  color: var(--text-muted);
  font-size: 12px;
}

.compatibility-form-grid input,
.compatibility-form-grid select,
.compatibility-form-grid textarea,
.compatibility-data-policy input {
  width: 100%;
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
}

.compatibility-form-grid textarea {
  resize: vertical;
}

.compatibility-check {
  display: flex !important;
  grid-template-columns: none;
  gap: 8px !important;
  align-items: end;
  padding-bottom: 8px;
}

.compatibility-check input {
  width: auto;
}

.compatibility-matrix,
.compatibility-case-list {
  display: grid;
  gap: 8px;
}

.compatibility-inline-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.compatibility-mode-call-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.compatibility-link-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font: inherit;
}

.compatibility-link-btn:hover {
  color: var(--text);
}

.compatibility-case-selector {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
}

.compatibility-case-option,
.compatibility-risk-confirm {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.compatibility-case-option small {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
}

.compatibility-evidence-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.compatibility-failure-list,
.compatibility-report-markdown {
  display: grid;
  gap: 8px;
}

.compatibility-report-markdown {
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.compatibility-report-markdown summary {
  cursor: pointer;
  color: var(--text-muted);
}

.compatibility-report-markdown-body {
  max-height: 280px;
  overflow: auto;
  margin: 0;
  word-break: break-word;
  color: var(--text);
  font-size: 13px;
}

.compatibility-evidence-item {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
}

.compatibility-evidence-item img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--border);
}

.compatibility-evidence-item small {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
}

.compatibility-env-row {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) 12px;
  gap: 8px;
  align-items: start;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.compatibility-env-row small {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
}

.compatibility-case-list {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.compatibility-risk {
  margin: 0;
  color: var(--text-muted);
}

.compatibility-actions {
  display: flex;
  gap: 8px;
  align-items: end;
  justify-content: space-between;
  margin-top: 12px;
}

.compatibility-data-policy {
  flex: 1;
  max-width: 260px;
}

.compatibility-selection-summary {
  color: var(--text-muted);
  font-size: 12px;
}
</style>
