import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import * as generation from "../../../services/caseGeneration.api";
import type { GenerationGroup, GenerationResponse, GenerationRetryRequest, GenerationRetryView, GenerationScope, GenerationTask } from "../../../services/caseGeneration.types";
import type { TestCaseVersionRecord } from "../../../types";
import { assertGenerationRetry, retryAvailability } from "./generationRetryProduct";

type Props = { parent: GenerationTask; group: GenerationGroup; scope: GenerationScope; canWrite: boolean; disabled: boolean };
export function useGenerationRetries(props: Props) {
  const items = ref<GenerationRetryView[]>([]); const hasMore = ref(false);
  const pending = ref<GenerationRetryRequest | null>(null); const confirmedFailure = ref(false);
  const expanded = ref<string[]>([]); const responses = ref<Record<string, GenerationResponse>>({});
  const versions = ref<Record<string, TestCaseVersionRecord>>({});
  const busy = ref(false); const error = ref(""); const message = ref(""); const storageWarning = ref("");
  const storageInvalid = ref(false); let epoch = 0; let disposed = false; let deferredInit = false;
  const context = () => ({ epoch, projectId: props.group.project_id, documentId: props.group.document_id, parentId: props.parent.id,
    groupId: props.group.id, scopeHash: props.scope.sha256 });
  type Context = ReturnType<typeof context>;
  const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.group.project_id
    && ctx.documentId === props.group.document_id && ctx.parentId === props.parent.id
    && ctx.groupId === props.group.id && ctx.scopeHash === props.scope.sha256;
  const cacheKey = () => `qa-agent:generation-retry:${props.group.project_id}:${props.group.document_id}:${props.parent.id}`;
  const availability = computed(() => retryAvailability(props.parent));
  const ready = computed(() => props.canWrite && !props.disabled && !busy.value && !pending.value && !storageInvalid.value && !!availability.value.strategy);
  function showError(err: unknown) { error.value = err instanceof Error ? err.message : "重试操作失败"; }
  function persist() {
    try {
      if (pending.value || expanded.value.length) sessionStorage.setItem(cacheKey(), JSON.stringify({ pending: pending.value,
        confirmedFailure: confirmedFailure.value, expanded: expanded.value }));
      else sessionStorage.removeItem(cacheKey());
      storageWarning.value = "";
    } catch { storageWarning.value = "无法保存重试请求，请保留父任务和原编号用于回查。"; }
  }
  function validateRequest(value: GenerationRetryRequest) {
    if (!value || Object.keys(value).length !== 1 || typeof value.creation_key !== "string"
      || !value.creation_key || value.creation_key.length > 160) throw new Error("保存的重试编号无效，请核对原编号");
  }
  function accept(value: GenerationRetryView, payload: GenerationRetryRequest) {
    assertGenerationRetry(value, props.parent, props.group, props.scope, payload.creation_key);
    const old = items.value.find(item => item.retry.id === value.retry.id || item.retry.creation_key === value.retry.creation_key);
    if (old && JSON.stringify(old.retry) !== JSON.stringify(value.retry)) throw new Error("保存的重试关系已变化，原编号继续保留");
    items.value = old ? items.value.map(item => item.retry.id === value.retry.id ? value : item) : [...items.value, value];
    pending.value = null; confirmedFailure.value = false; persist();
    message.value = `重试提交已确认：${value.task.id}。原失败任务继续保留。`;
  }
  async function loadHistory(more = false) {
    if (busy.value || props.disabled) return;
    const ctx = context(); const offset = more ? items.value.length : 0; busy.value = true; error.value = "";
    try {
      const page = await generation.listCaseGenerationRetries(ctx.projectId, ctx.documentId, ctx.parentId, 20, offset);
      if (!current(ctx)) return;
      if (page.offset !== offset || (page.has_more && !page.items.length)
        || new Set(page.items.map(item => item.retry.id)).size !== page.items.length) throw new Error("重试历史分页未前进或重复");
      page.items.forEach(item => {
        assertGenerationRetry(item, props.parent, props.group, props.scope);
        const old = items.value.find(saved => saved.retry.id === item.retry.id);
        if (old && JSON.stringify(old.retry) !== JSON.stringify(item.retry)) throw new Error("原重试关系已变化");
      });
      if (more && page.items.some(item => items.value.some(old => old.retry.id === item.retry.id))) throw new Error("重试历史分页重复");
      items.value = more ? [...items.value, ...page.items] : page.items; hasMore.value = page.has_more;
    } catch (err) { if (current(ctx)) showError(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function recover() {
    if (!pending.value || busy.value || props.disabled) return;
    const ctx = context(); const payload = { ...pending.value }; busy.value = true; error.value = "";
    try {
      const value = await generation.getCaseGenerationRetrySubmission(ctx.projectId, ctx.documentId, ctx.parentId, payload.creation_key);
      if (current(ctx)) accept(value, payload);
    } catch (err) { if (current(ctx)) showError(err instanceof HttpError && err.status === 404
      ? new Error("尚未查到原重试提交，保留父任务与原编号，可回查或重试原提交。") : err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function submit(replay = false) {
    if (busy.value || props.disabled || (replay ? !pending.value || confirmedFailure.value : !ready.value)) return;
    const ctx = context(); busy.value = true; error.value = ""; message.value = "";
    try {
      if (!replay) {
        const creationKey = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `generation-retry-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const payload = { creation_key: creationKey }; validateRequest(payload);
        pending.value = payload; confirmedFailure.value = false; persist();
      }
      const payload = { ...pending.value! };
      const value = await generation.retryCaseGeneration(ctx.projectId, ctx.documentId, ctx.parentId, payload);
      if (current(ctx)) accept(value, payload);
    } catch (err) {
      if (current(ctx)) {
        if (err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) { confirmedFailure.value = true; persist(); }
        showError(err);
      }
    } finally { if (current(ctx)) busy.value = false; }
  }
  function resetFailed() {
    if (busy.value || props.disabled || !confirmedFailure.value) return;
    pending.value = null; confirmedFailure.value = false; persist(); message.value = "原重试提交已明确拒绝，请核对失败原因和项目策略。";
  }
  async function refresh(item: GenerationRetryView) {
    if (busy.value || props.disabled) return;
    const ctx = context(); busy.value = true; error.value = "";
    try {
      const value = await generation.getCaseGenerationRetrySubmission(ctx.projectId, ctx.documentId, ctx.parentId, item.retry.creation_key);
      if (!current(ctx)) return;
      assertGenerationRetry(value, props.parent, props.group, props.scope, item.retry.creation_key);
      if (JSON.stringify(value.retry) !== JSON.stringify(item.retry)) throw new Error("原重试关系已变化");
      items.value = items.value.map(old => old.retry.id === item.retry.id ? value : old);
    } catch (err) { if (current(ctx)) showError(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function readResponse(item: GenerationRetryView) {
    if (busy.value || props.disabled || !item.task.checkpoint?.response) return;
    const ctx = context(); busy.value = true; error.value = "";
    try {
      const value = await generation.readCaseGenerationResponse(ctx.projectId, ctx.documentId, item.task.id);
      if (current(ctx)) responses.value = { ...responses.value, [item.task.id]: value };
    } catch (err) { if (current(ctx)) showError(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function readVersion(item: GenerationRetryView, versionId: string) {
    if (busy.value || props.disabled) return;
    const reference = item.task.checkpoint?.receipt?.versions.find(value => value.version_id === versionId);
    if (!reference) return;
    const ctx = context(); busy.value = true; error.value = "";
    try {
      const values = await api.listTestCaseVersions(reference.case_id);
      if (!current(ctx)) return;
      const value = values.find(version => version.id === reference.version_id);
      if (!value || values.some(version => version.case_id !== reference.case_id)
        || value.version !== reference.version || value.content_hash !== reference.content_hash) throw new Error("完整重试草稿与子任务收据不一致");
      versions.value = { ...versions.value, [versionId]: value };
    } catch (err) { if (current(ctx)) showError(err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  function download(value: unknown, filename: string) {
    if (busy.value || props.disabled) return;
    try {
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      try { link.href = url; link.download = filename; link.click(); } finally { URL.revokeObjectURL(url); }
    } catch (err) { showError(err); }
  }
  function toggle(taskId: string) {
    if (busy.value || props.disabled) return;
    expanded.value = expanded.value.includes(taskId) ? expanded.value.filter(id => id !== taskId) : [...expanded.value, taskId]; persist();
  }
  async function initialize() {
    if (props.disabled) { deferredInit = true; return; }
    deferredInit = false;
    ++epoch; items.value = []; hasMore.value = false; pending.value = null; confirmedFailure.value = false;
    expanded.value = []; responses.value = {}; versions.value = {}; busy.value = false;
    error.value = ""; message.value = ""; storageWarning.value = ""; storageInvalid.value = false;
    const ctx = context();
    try {
      const raw = sessionStorage.getItem(cacheKey());
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.pending) { validateRequest(saved.pending); pending.value = { creation_key: saved.pending.creation_key }; }
        confirmedFailure.value = saved.confirmedFailure === true;
        if (!Array.isArray(saved.expanded) || saved.expanded.some((id: unknown) => typeof id !== "string")) throw new Error("保存的重试历史展开信息无效");
        expanded.value = saved.expanded;
      }
    } catch (err) { storageInvalid.value = true; storageWarning.value = err instanceof Error ? err.message : "原重试缓存读取失败"; }
    await loadHistory();
    if (current(ctx) && pending.value) await recover();
  }
  watch(() => [props.group.project_id, props.group.document_id, props.group.id, props.scope.sha256, props.parent.id], () => { void initialize(); }, { immediate: true });
  watch(() => props.disabled, blocked => { if (!blocked && deferredInit) void initialize(); });
  onBeforeUnmount(() => { disposed = true; ++epoch; });
  return { items, hasMore, pending, confirmedFailure, expanded, responses, versions, busy, error, message, storageWarning,
    availability, ready, loadHistory, recover, submit, resetFailed, refresh, readResponse, readVersion, download, toggle };
}
