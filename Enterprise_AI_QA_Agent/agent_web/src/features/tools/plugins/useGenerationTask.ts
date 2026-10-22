import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import * as generation from "../../../services/caseGeneration.api";
import type { GenerationGroup, GenerationScope, GenerationTask, GenerationRetryView, GenerationResponse } from "../../../services/caseGeneration.types";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../../types";
import { assertGenerationGroup, assertGenerationScope, assertGenerationReport } from "./caseGenerationProduct";
import { assertGenerationRetry } from "./generationRetryProduct";

type Props = { projectId: string; documentId: string; groupId: string; taskId: string; parentTaskId: string; retryKey: string };
export function useGenerationTask(props: Props) {
  const task = ref<GenerationTask | null>(null); const group = ref<GenerationGroup | null>(null);
  const scope = ref<GenerationScope | null>(null); const origin = ref<GenerationRetryView | null>(null);
  const versions = ref<Record<string, TestCaseVersionRecord>>({}); const response = ref<GenerationResponse | null>(null);
  const busy = ref(false); const error = ref(""); const permissionError = ref(""); const canWrite = ref(false);
  let epoch = 0; let disposed = false; let timer: ReturnType<typeof setTimeout> | undefined;
  const key = () => JSON.stringify([props.projectId, props.documentId, props.groupId, props.taskId, props.parentTaskId, props.retryKey]);
  const context = () => ({ ...props, epoch, key: key() });
  const current = (ctx: ReturnType<typeof context>) => !disposed && ctx.epoch === epoch && ctx.key === key();
  function stop() { if (timer) clearTimeout(timer); timer = undefined; }
  function show(err: unknown) { error.value = err instanceof Error ? err.message : "固定生成任务读取失败"; }
  async function load() {
    if (busy.value) return;
    stop(); const ctx = context(); busy.value = true; error.value = ""; permissionError.value = ""; canWrite.value = false;
    try {
      if (![ctx.projectId, ctx.documentId, ctx.groupId, ctx.taskId].every(Boolean)
        || Boolean(ctx.parentTaskId) !== Boolean(ctx.retryKey)) throw new Error("缺少固定生成任务身份或完整重试来源");
      const [value, fixed] = await Promise.all([generation.readCaseGeneration(ctx.projectId, ctx.documentId, ctx.groupId),
        generation.readCaseGenerationScope(ctx.projectId, ctx.documentId, ctx.groupId)]);
      if (!current(ctx)) return;
      if (value.group.id !== ctx.groupId) throw new Error("返回了其他生成组");
      assertGenerationGroup(value.group, ctx.projectId, ctx.documentId); assertGenerationScope(fixed, value.group);
      assertGenerationReport(value, value.group, fixed);
      let chosen: GenerationTask; let retry: GenerationRetryView | null = null;
      if (ctx.parentTaskId) {
        const [parent, result] = await Promise.all([generation.getCaseGenerationTask(ctx.projectId, ctx.documentId, ctx.parentTaskId),
          generation.getCaseGenerationRetrySubmission(ctx.projectId, ctx.documentId, ctx.parentTaskId, ctx.retryKey)]);
        if (!current(ctx)) return;
        if (parent.id !== ctx.parentTaskId || result.task.id !== ctx.taskId) throw new Error("重试来源返回了其他父任务或子任务");
        assertGenerationRetry(result, parent, value.group, fixed, ctx.retryKey);
        chosen = result.task; retry = result;
      } else {
        const found = value.modules.find(item => item.id === ctx.taskId);
        if (!found) throw new Error("任务不属于该组原模块；重试任务需要原父任务和请求编号");
        chosen = found;
      }
      if (chosen.version_id !== value.group.version_id || !fixed.modules.some(item => item.id === chosen.module_id)) {
        throw new Error("生成任务与固定原件版本或模块不一致");
      }
      task.value = chosen; group.value = value.group; scope.value = fixed; origin.value = retry;
      try {
        const project = await api.getProjectOverview(ctx.projectId);
        if (!current(ctx)) return;
        if (project.project.id !== ctx.projectId) throw new Error("任务详情与当前项目不一致");
        canWrite.value = project.project.status === "active";
      } catch (err) { if (current(ctx)) permissionError.value = err instanceof Error ? err.message : "项目写权限读取失败"; }
      if (current(ctx) && ["queued", "running"].includes(chosen.status)) timer = setTimeout(() => { void load(); }, 1500);
    } catch (err) { if (current(ctx)) { task.value = null; group.value = null; scope.value = null; origin.value = null; show(err); } }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function readVersion(versionId: string): Promise<TestCaseRecord | null> {
    if (!task.value || busy.value) return null;
    const ctx = context(); const reference = task.value.checkpoint?.receipt?.versions.find(item => item.version_id === versionId);
    error.value = ""; busy.value = true;
    try {
      if (!reference) throw new Error("草稿不属于当前任务收据");
      const [record, values] = await Promise.all([api.getTestCase(reference.case_id), api.listTestCaseVersions(reference.case_id)]);
      if (!current(ctx)) return null;
      const value = values.find(item => item.id === versionId);
      if (record.id !== reference.case_id || record.project_id !== ctx.projectId || values.some(item => item.case_id !== reference.case_id)
        || !value || value.version !== reference.version || value.content_hash !== reference.content_hash) throw new Error("完整草稿与项目或固定收据不一致");
      versions.value = { ...versions.value, [versionId]: value };
      return record;
    } catch (err) { if (current(ctx)) show(err); return null; }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function readResponse() {
    if (!task.value?.checkpoint?.response || busy.value) return;
    const ctx = context(); busy.value = true; error.value = "";
    try {
      const value = await generation.readCaseGenerationResponse(ctx.projectId, ctx.documentId, ctx.taskId);
      if (current(ctx)) response.value = value;
    } catch (err) { if (current(ctx)) show(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  watch(key, () => { ++epoch; stop(); busy.value = false; task.value = null; group.value = null; scope.value = null;
    origin.value = null; versions.value = {}; response.value = null; void load(); }, { immediate: true });
  onBeforeUnmount(() => { disposed = true; ++epoch; stop(); });
  return { task, group, scope, origin, versions, response, busy, error, permissionError, canWrite, load, readVersion, readResponse };
}
