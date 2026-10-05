import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import * as generation from "../../../services/caseGeneration.api";
import type { GenerationGroup, GenerationGroupView, GenerationResponse, GenerationScope, GenerationTaskRequest } from "../../../services/caseGeneration.types";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementReview } from "../../../services/requirements.types";
import type { ModeDescriptor, TestCaseRecord, TestCaseVersionRecord } from "../../../types";
import { assertImpactSnapshot } from "./documentImpactIdentity";
import { assertGenerationGroup, assertGenerationReport, assertGenerationScope, normalizedGeneration } from "./caseGenerationProduct";

type Props = { projectId: string; documentId: string; view: DocumentImpactSnapshotView; reviews: RequirementReview[]; canWrite: boolean };
type CaseReference = { case: TestCaseRecord; version_id: string; content_hash: string };
export function useCaseGeneration(props: Props) {
  const reviewId = ref(""); const targets = ref<string[]>([]); const includeNew = ref(false);
  const modeKey = ref(""); const maxNew = ref(1); const modelKey = ref(""); const modes = ref<ModeDescriptor[]>([]);
  const pending = ref<GenerationTaskRequest | null>(null); const confirmedFailure = ref(false);
  const groups = ref<GenerationGroup[]>([]); const hasMore = ref(false); const historyBusy = ref(false);
  const report = ref<GenerationGroupView | null>(null); const scope = ref<GenerationScope | null>(null);
  const responses = ref<Record<string, GenerationResponse>>({}); const versions = ref<Record<string, TestCaseVersionRecord>>({});
  const sourceVersions = ref<Record<string, TestCaseVersionRecord>>({});
  const busy = ref(false); const error = ref(""); const message = ref(""); const storageWarning = ref("");
  const storageInvalid = ref(false);
  let epoch = 0; let resultEpoch = 0; let historyEpoch = 0; let disposed = false;
  const context = () => ({ epoch, projectId: props.projectId, documentId: props.documentId, snapshotId: props.view.snapshot.id });
  type Context = ReturnType<typeof context>;
  const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.projectId
    && ctx.documentId === props.documentId && ctx.snapshotId === props.view.snapshot.id;
  const cacheKey = () => `qa-agent:case-generation:${props.projectId}:${props.documentId}`;
  const acceptedReviews = computed(() => props.reviews.filter(item => item.request.decision === "accept" && item.requirement_version
    && item.project_id === props.projectId && item.document_id === props.documentId && item.source.snapshot_id === props.view.snapshot.id));
  const selectedReview = computed(() => acceptedReviews.value.find(item => item.id === reviewId.value));
  const removed = computed(() => selectedReview.value?.requirement_version?.change === "removed");
  const cases = computed(() => (props.view.snapshot.seed.cases || []) as CaseReference[]);
  const ready = computed(() => props.canWrite && !busy.value && !pending.value && !storageInvalid.value
    && !!selectedReview.value && (targets.value.length > 0 || (!removed.value && includeNew.value && !!modeKey.value)));
  function showError(err: unknown) { error.value = err instanceof Error ? err.message : "增量生成操作失败"; }
  function persist() {
    try {
      if (pending.value || report.value) sessionStorage.setItem(cacheKey(), JSON.stringify({ pending: pending.value,
        confirmedFailure: confirmedFailure.value, groupId: report.value?.group.id ?? null }));
      else sessionStorage.removeItem(cacheKey());
      storageWarning.value = "";
    } catch { storageWarning.value = "无法保存本地生成请求，请保留原编号和完整参数用于回查。"; }
  }
  function validateRequest(value: GenerationTaskRequest) {
    const fields = Object.keys(normalizedGeneration(value));
    if (Object.keys(value).some(key => !fields.includes(key)) || typeof value.creation_key !== "string" || !value.creation_key || value.creation_key.length > 160
      || typeof value.requirement_id !== "string" || !value.requirement_id || typeof value.requirement_version_id !== "string"
      || !value.requirement_version_id || !Array.isArray(value.target_case_version_ids)
      || value.target_case_version_ids.some(id => typeof id !== "string" || !id)
      || new Set(value.target_case_version_ids).size !== value.target_case_version_ids.length
      || typeof value.include_new_cases !== "boolean" || !Number.isInteger(value.max_new_cases)
      || value.max_new_cases! < 1 || value.max_new_cases! > 200
      || (!value.target_case_version_ids.length && !value.include_new_cases)
      || (value.include_new_cases && !value.new_case_mode_key)) throw new Error("保存的生成请求无效，请核对原编号及完整参数");
  }
  async function loadHistory(more = false) {
    if (historyBusy.value) return;
    const ctx = context(); const ticket = ++historyEpoch;
    const offset = more ? groups.value.length : 0; historyBusy.value = true; error.value = "";
    try {
      const page = await generation.listCaseGenerations(ctx.projectId, ctx.documentId, 20, offset);
      if (!current(ctx) || ticket !== historyEpoch) return;
      if (page.offset !== offset || (page.has_more && !page.items.length)) throw new Error("生成历史分页未前进");
      page.items.forEach(item => assertGenerationGroup(item, ctx.projectId, ctx.documentId));
      groups.value = more ? [...groups.value, ...page.items.filter(item => !groups.value.some(old => old.id === item.id))] : page.items;
      hasMore.value = page.has_more;
    } catch (err) { if (current(ctx) && ticket === historyEpoch) showError(err); }
    finally { if (current(ctx) && ticket === historyEpoch) historyBusy.value = false; }
  }
  async function openGroup(groupId: string, original?: GenerationTaskRequest, ctx = context()) {
    const ticket = ++resultEpoch;
    const [value, fixed] = await Promise.all([generation.readCaseGeneration(ctx.projectId, ctx.documentId, groupId),
      generation.readCaseGenerationScope(ctx.projectId, ctx.documentId, groupId)]);
    if (!current(ctx) || ticket !== resultEpoch) return false;
    if (value.group.id !== groupId) throw new Error("生成历史返回了其他组");
    assertGenerationGroup(value.group, ctx.projectId, ctx.documentId);
    assertGenerationScope(fixed, value.group, original); assertGenerationReport(value, value.group, fixed);
    report.value = value; scope.value = fixed; responses.value = {}; versions.value = {}; persist();
    return true;
  }
  async function readGroup(groupId: string) {
    if (busy.value) return;
    const ctx = context(); busy.value = true; error.value = "";
    try { await openGroup(groupId, undefined, ctx); }
    catch (err) { if (current(ctx)) showError(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function accept(group: GenerationGroup, payload: GenerationTaskRequest, ctx: Context) {
    if (!current(ctx)) return;
    assertGenerationGroup(group, ctx.projectId, ctx.documentId);
    if (!await openGroup(group.id, payload, ctx)) return;
    pending.value = null; confirmedFailure.value = false; persist();
    message.value = `生成提交已确认：${group.id}。模块结果与草稿评审分别查看。`;
    await loadHistory();
  }
  async function recover() {
    if (!pending.value || busy.value) return;
    const ctx = context(); const payload = normalizedGeneration(pending.value); busy.value = true; error.value = "";
    try { await accept(await generation.getCaseGenerationSubmission(ctx.projectId, ctx.documentId, payload.creation_key), payload, ctx); }
    catch (err) { if (current(ctx)) showError(err instanceof HttpError && err.status === 404
      ? new Error("尚未查到原生成提交，保留编号和参数，可回查或按原参数重试。") : err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function submit(retry = false) {
    if (busy.value || (retry ? !pending.value || confirmedFailure.value : !ready.value)) return;
    const ctx = context(); busy.value = true; error.value = ""; message.value = "";
    try {
      if (!retry) {
        const version = selectedReview.value?.requirement_version;
        if (!version) throw new Error("请选择已接受的固定需求版本");
        if (targets.value.some(id => !cases.value.some(item => item.version_id === id))) throw new Error("更新目标不属于原影响快照");
        const chosenCases = targets.value.map(id => cases.value.find(item => item.version_id === id)!.case.id);
        if (new Set(chosenCases).size !== chosenCases.length) throw new Error("同一用例只能选择一个基线版本");
        const creationKey = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `generation-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const fresh = normalizedGeneration({ creation_key: creationKey, requirement_id: version.requirement_id,
          requirement_version_id: version.id, target_case_version_ids: [...targets.value], include_new_cases: !removed.value && includeNew.value,
          new_case_mode_key: !removed.value && includeNew.value ? modeKey.value : null, max_new_cases: maxNew.value,
          model_key: modelKey.value.trim() || null });
        validateRequest(fresh); pending.value = fresh; confirmedFailure.value = false; persist();
      }
      const payload = JSON.parse(JSON.stringify(pending.value)) as GenerationTaskRequest;
      await accept(await generation.submitCaseGeneration(ctx.projectId, ctx.documentId, payload), payload, ctx);
    } catch (err) {
      if (current(ctx)) {
        if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) { confirmedFailure.value = true; persist(); }
        showError(err);
      }
    } finally { if (current(ctx)) busy.value = false; }
  }
  async function resetFailed() {
    if (!confirmedFailure.value || busy.value) return;
    const ctx = context();
    pending.value = null; confirmedFailure.value = false; persist();
    await loadHistory(); if (current(ctx)) message.value = "原提交已明确拒绝，请核对需求和基线后提交新请求。";
  }
  async function refreshReport() {
    if (!report.value || !scope.value || busy.value) return;
    const ctx = context(); const group = report.value.group; const fixed = scope.value; const ticket = ++resultEpoch;
    busy.value = true; error.value = "";
    try {
      const value = await generation.readCaseGeneration(ctx.projectId, ctx.documentId, group.id);
      if (!current(ctx) || ticket !== resultEpoch) return;
      assertGenerationReport(value, group, fixed); report.value = value;
    } catch (err) { if (current(ctx) && ticket === resultEpoch) showError(err); }
    finally { if (current(ctx) && ticket === resultEpoch) busy.value = false; }
  }
  async function readResponse(taskId: string) {
    const ctx = context(); const groupId = report.value?.group.id; const ticket = resultEpoch; error.value = "";
    try {
      const value = await generation.readCaseGenerationResponse(ctx.projectId, ctx.documentId, taskId);
      if (current(ctx) && ticket === resultEpoch && groupId === report.value?.group.id) responses.value = { ...responses.value, [taskId]: value };
    } catch (err) { if (current(ctx) && ticket === resultEpoch && groupId === report.value?.group.id) showError(err); }
  }
  async function readVersion(caseId: string, versionId: string, original = false) {
    const ctx = context(); const groupId = report.value?.group.id; const ticket = resultEpoch; error.value = "";
    try {
      const values = await api.listTestCaseVersions(caseId);
      if (!current(ctx) || (!original && (groupId !== report.value?.group.id || ticket !== resultEpoch))) return;
      const value = values.find(item => item.id === versionId);
      if (!value || values.some(item => item.case_id !== caseId)) throw new Error("用例版本与指定基线或草稿不一致");
      if (original) {
        const source = cases.value.find(item => item.version_id === versionId && item.case.id === caseId);
        if (!source || value.content_hash !== source.content_hash) throw new Error("完整用例与原固定引用不一致");
        sourceVersions.value = { ...sourceVersions.value, [versionId]: value };
      } else {
        const reference = report.value?.modules.flatMap(task => task.checkpoint?.receipt?.versions || [])
          .find(item => item.version_id === versionId && item.case_id === caseId);
        if (!reference || reference.content_hash !== value.content_hash || reference.version !== value.version) throw new Error("完整草稿与原提交收据不一致");
        versions.value = { ...versions.value, [versionId]: value };
      }
    } catch (err) { if (current(ctx) && (original || (groupId === report.value?.group.id && ticket === resultEpoch))) showError(err); }
  }
  function download(value: unknown, filename: string) {
    if (busy.value) return;
    try {
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      try { link.href = url; link.download = filename; link.click(); } finally { URL.revokeObjectURL(url); }
    } catch (err) { showError(err); }
  }
  async function initialize() {
    ++epoch; ++resultEpoch; ++historyEpoch; busy.value = false; historyBusy.value = false;
    pending.value = null; confirmedFailure.value = false; groups.value = []; hasMore.value = false;
    report.value = null; scope.value = null; responses.value = {}; versions.value = {}; sourceVersions.value = {};
    storageInvalid.value = false; storageWarning.value = ""; error.value = ""; message.value = "";
    reviewId.value = acceptedReviews.value[0]?.id || ""; targets.value = []; includeNew.value = false;
    const ctx = context(); let groupId: string | null = null;
    try {
      assertImpactSnapshot(props.view, ctx.projectId, ctx.documentId);
      if (cases.value.some(item => item.case.project_id !== ctx.projectId)) throw new Error("生成基线不属于当前项目");
      const raw = sessionStorage.getItem(cacheKey());
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.pending) { validateRequest(saved.pending); pending.value = normalizedGeneration(saved.pending); }
        confirmedFailure.value = saved.confirmedFailure === true;
        if (typeof saved.groupId === "string") groupId = saved.groupId;
      }
    } catch (err) { storageInvalid.value = true; storageWarning.value = err instanceof Error ? err.message : "原生成请求读取失败"; }
    await Promise.all([loadHistory(), (async () => {
      try { const values = await api.listModes(); if (current(ctx)) modes.value = values.filter(item => item.is_test_mode); }
      catch (err) { if (current(ctx)) showError(err); }
    })()]);
    if (!current(ctx)) return;
    if (pending.value) await recover();
    else if (groupId) await readGroup(groupId);
  }
  watch(reviewId, () => { targets.value = []; includeNew.value = false; });
  watch(() => [props.projectId, props.documentId, props.view.snapshot.id], () => { void initialize(); }, { immediate: true });
  onBeforeUnmount(() => { disposed = true; ++epoch; ++resultEpoch; ++historyEpoch; });
  return { reviewId, targets, includeNew, modeKey, maxNew, modelKey, modes, acceptedReviews, selectedReview, removed, cases, ready,
    pending, confirmedFailure, groups, hasMore, historyBusy, report, scope, responses, versions, sourceVersions,
    busy, error, message, storageWarning, loadHistory, readGroup, submit, recover, resetFailed, refreshReport, readResponse, readVersion, download };
}
