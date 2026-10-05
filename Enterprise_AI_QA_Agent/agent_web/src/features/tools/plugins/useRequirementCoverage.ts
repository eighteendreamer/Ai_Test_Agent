import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import * as service from "../../../services/requirements.api";
import type { RequirementCoverageRequest, RequirementCoverageReview, RequirementMatrix } from "../../../services/requirements.types";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../../types";
import { assertCoverage, assertCoverageMatrix, normalizedCoverage, validateCoverageRequest } from "./requirementCoverageProduct";

type Props = { matrix: RequirementMatrix; canWrite: boolean; disabled: boolean };
type Association = { caseId: string; versionId: string; contentHash: string; points: string[]; taskId: string;
  record?: TestCaseRecord; version?: TestCaseVersionRecord };
export function useRequirementCoverage(props: Props, showMatrix: (value: RequirementMatrix) => void) {
  const history = ref<RequirementCoverageReview[]>([]); const hasMore = ref(false); const choice = ref("latest");
  const cases = ref<TestCaseRecord[]>([]); const caseMore = ref(false); const query = ref(""); let loadedQuery = "";
  const caseId = ref(""); const record = ref<TestCaseRecord | null>(null); const versions = ref<TestCaseVersionRecord[]>([]); const versionId = ref("");
  const rows = ref<Association[]>([]); const note = ref(""); const previousId = ref<string | null>(null);
  const pending = ref<RequirementCoverageRequest | null>(null); const confirmedFailure = ref(false);
  const busy = ref(false); const error = ref(""); const message = ref(""); const storageWarning = ref("");
  const storageInvalid = ref(false); const historyReady = ref(false); const projectActive = ref(false);
  let epoch = 0; let disposed = false; let deferred = false;
  const selected = computed(() => versions.value.find(value => value.id === versionId.value));
  const canEdit = computed(() => props.canWrite && projectActive.value);
  const ready = computed(() => canEdit.value && historyReady.value && !props.disabled && !busy.value
    && !pending.value && !storageInvalid.value && !!note.value.trim() && rows.value.every(row => !!row.version && row.points.length > 0)
    && (props.matrix.version.change !== "removed" || !rows.value.length));
  const context = () => ({ epoch, project: props.matrix.requirement.project_id, doc: props.matrix.requirement.document_id,
    requirement: props.matrix.requirement.id, version: props.matrix.version.id, sha: props.matrix.version.content_sha256, basis: props.matrix });
  type Context = ReturnType<typeof context>;
  const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.project === props.matrix.requirement.project_id
    && ctx.doc === props.matrix.requirement.document_id && ctx.requirement === props.matrix.requirement.id
    && ctx.version === props.matrix.version.id && ctx.sha === props.matrix.version.content_sha256;
  const key = () => `qa-agent:requirement-coverage:${props.matrix.requirement.project_id}:${props.matrix.requirement.document_id}:${props.matrix.requirement.id}:${props.matrix.version.id}`;
  function persist() {
    try {
      if (pending.value) sessionStorage.setItem(key(), JSON.stringify({ requirement_sha256: props.matrix.version.content_sha256,
        pending: normalizedCoverage(pending.value), confirmedFailure: confirmedFailure.value }));
      else sessionStorage.removeItem(key());
      storageWarning.value = "";
    } catch { storageWarning.value = "无法保存覆盖请求，请保留原编号和完整参数用于回查。"; }
  }
  async function operate(action: (ctx: Context) => Promise<void>) {
    if (busy.value || props.disabled) return;
    const ctx = context(); busy.value = true; error.value = "";
    try { await action(ctx); }
    catch (err) { if (current(ctx)) error.value = err instanceof Error ? err.message : "覆盖操作失败"; }
    finally { if (current(ctx)) busy.value = false; }
  }
  function validatePage(items: RequirementCoverageReview[], ctx: Context, offset: number, more: boolean) {
    const ids = new Set<string>(); let priorRevision = more ? history.value[history.value.length - 1]?.revision ?? Infinity : Infinity;
    items.forEach(item => {
      assertCoverage(item, ctx.basis);
      if (ids.has(item.id) || item.revision >= priorRevision || (more && history.value.some(old => old.id === item.id))) throw new Error("覆盖历史分页重复或顺序不一致");
      const old = history.value.find(value => value.id === item.id);
      if (old && JSON.stringify(old) !== JSON.stringify(item)) throw new Error("固定覆盖历史已变化");
      ids.add(item.id); priorRevision = item.revision;
    });
    if (more && offset !== history.value.length) throw new Error("覆盖历史分页位置不一致");
  }
  function editFrom(value: RequirementMatrix) {
    previousId.value = value.coverage_review?.id ?? null;
    const links = value.coverage_review?.case_links ?? value.version.case_links;
    rows.value = links.map(link => {
      const old = rows.value.find(row => row.versionId === link.case_version_id);
      const saved = value.coverage_review?.case_links.find(item => item.case_version_id === link.case_version_id);
      return { caseId: link.case_id, versionId: link.case_version_id, contentHash: saved?.content_hash ?? old?.contentHash ?? "",
        points: [...link.point_keys], taskId: saved?.generation?.task_id ?? "", record: old?.record, version: old?.version };
    });
  }
  async function readLatest(ctx: Context) {
    const [page, matrix, overview] = await Promise.all([
      service.listRequirementCoverage(ctx.project, ctx.doc, ctx.requirement, ctx.version, 20, 0),
      service.readRequirementMatrix(ctx.project, ctx.doc, ctx.requirement, ctx.version), api.getProjectOverview(ctx.project),
    ]);
    if (!current(ctx)) return;
    if (page.offset !== 0 || (page.has_more && !page.items.length)) throw new Error("覆盖历史分页未前进");
    validatePage(page.items, ctx, 0, false); assertCoverageMatrix(matrix, ctx.basis);
    if ((page.items[0]?.id ?? null) !== (matrix.coverage_review?.id ?? null)) throw new Error("最新矩阵与覆盖历史不一致，请重新读取");
    if (overview.project.id !== ctx.project) throw new Error("项目状态与当前项目不一致");
    projectActive.value = overview.project.status === "active";
    history.value = page.items; hasMore.value = page.has_more; historyReady.value = true;
    choice.value = "latest"; editFrom(matrix); showMatrix(matrix);
  }
  async function refreshBasis() {
    if (pending.value) return;
    historyReady.value = false;
    await operate(readLatest);
  }
  async function moreHistory() {
    await operate(async ctx => {
      const offset = history.value.length;
      const page = await service.listRequirementCoverage(ctx.project, ctx.doc, ctx.requirement, ctx.version, 20, offset);
      if (!current(ctx)) return;
      if (page.offset !== offset || (page.has_more && !page.items.length)) throw new Error("覆盖历史分页未前进");
      validatePage(page.items, ctx, offset, true); history.value.push(...page.items); hasMore.value = page.has_more;
    });
  }
  async function selectHistory() {
    await operate(async ctx => {
      const selection = choice.value;
      const matrix = await service.readRequirementMatrix(ctx.project, ctx.doc, ctx.requirement, ctx.version, selection === "latest" ? undefined : selection);
      if (!current(ctx)) return;
      assertCoverageMatrix(matrix, ctx.basis, selection); showMatrix(matrix);
    });
  }
  async function loadCases(more = false) {
    await operate(async ctx => {
      const requested = more ? loadedQuery : query.value.trim(); const offset = more ? cases.value.length : 0;
      const page = await api.listTestCases(ctx.project, { query: requested || undefined, limit: 20, offset });
      if (!current(ctx)) return;
      if (page.offset !== offset || (page.has_more && !page.items.length) || new Set(page.items.map(item => item.id)).size !== page.items.length
        || page.items.some(item => item.project_id !== ctx.project || (more && cases.value.some(old => old.id === item.id)))) throw new Error("项目用例目录身份或分页不一致");
      cases.value = more ? [...cases.value, ...page.items] : page.items; caseMore.value = page.has_more; loadedQuery = requested;
    });
  }
  async function fullCase(id: string, ctx: Context) {
    const [item, values] = await Promise.all([api.getTestCase(id), api.listTestCaseVersions(id)]);
    if (item.project_id !== ctx.project || item.id !== id || !values.length || values.some(value => value.case_id !== id || !/^[0-9a-f]{64}$/.test(value.content_hash))
      || new Set(values.map(value => value.id)).size !== values.length) throw new Error("完整用例与当前项目、身份或版本不一致");
    return { item, values: [...values].sort((a, b) => b.version - a.version) };
  }
  async function readCase() {
    await operate(async ctx => {
      const id = caseId.value; const selectedId = record.value?.id === id ? versionId.value : "";
      record.value = null; versions.value = []; versionId.value = "";
      if (!cases.value.some(item => item.id === id)) throw new Error("请从当前项目目录选择用例");
      const { item, values } = await fullCase(id, ctx);
      if (!current(ctx)) return;
      record.value = item; versions.value = values; versionId.value = values.some(value => value.id === selectedId) ? selectedId : values[0].id;
    });
  }
  async function readAssociation(row: Association) {
    await operate(async ctx => {
      const { item, values } = await fullCase(row.caseId, ctx);
      if (!current(ctx)) return;
      const fixed = values.find(value => value.id === row.versionId);
      if (!fixed || (row.contentHash && row.contentHash !== fixed.content_hash)) throw new Error("关联的固定版本或内容哈希已变化");
      row.record = item; row.version = fixed; row.contentHash = fixed.content_hash;
    });
  }
  function addVersion() {
    if (busy.value || props.disabled || pending.value || !canEdit.value || !selected.value || !record.value) return;
    const fixed = selected.value;
    if (rows.value.some(row => row.versionId === fixed.id)) { error.value = "已选中该用例版本，请编辑其验证点"; return; }
    if (rows.value.length >= 100) { error.value = "本次最多关联100个版本"; return; }
    rows.value.push({ caseId: record.value.id, versionId: fixed.id, contentHash: fixed.content_hash, points: [], taskId: "",
      record: record.value, version: fixed });
  }
  function removeVersion(index: number) { if (!busy.value && !props.disabled && !pending.value && canEdit.value) rows.value.splice(index, 1); }
  async function accept(value: RequirementCoverageReview, payload: RequirementCoverageRequest, ctx: Context) {
    assertCoverage(value, ctx.basis, payload);
    value.case_links.forEach(link => {
      const known = rows.value.find(row => row.versionId === link.case_version_id);
      if (known && (known.caseId !== link.case_id || (known.contentHash && known.contentHash !== link.content_hash))) throw new Error("确认返回的用例与实际读取版本不一致");
    });
    const old = history.value.find(item => item.id === value.id);
    if (old && JSON.stringify(old) !== JSON.stringify(value)) throw new Error("原确认记录已变化，原编号继续保留");
    pending.value = null; confirmedFailure.value = false; persist();
    message.value = `覆盖确认已保存：${value.id}。设计覆盖不表示测试已通过。`;
    // Confirmation is durable even if the following read fails; do not re-POST it.
    await readLatest(ctx);
  }
  async function recover() {
    if (!pending.value) return;
    await operate(async ctx => {
      const payload = normalizedCoverage(pending.value!);
      try {
        const value = await service.getRequirementCoverageSubmission(ctx.project, ctx.doc, payload.request_id);
        if (current(ctx)) await accept(value, payload, ctx);
      } catch (err) {
        if (err instanceof HttpError && err.status === 404) throw new Error("未查到原覆盖请求，不能据此判断未提交；原编号继续保留");
        throw err;
      }
    });
  }
  async function submit(resend = false) {
    if ((!resend && !ready.value) || (resend && (!pending.value || confirmedFailure.value))) return;
    await operate(async ctx => {
      if (!resend) {
        const id = typeof crypto?.randomUUID === "function" ? crypto.randomUUID() : `coverage-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const payload = normalizedCoverage({ request_id: id, expected_coverage_id: previousId.value, note: note.value,
          case_links: rows.value.map(row => ({ case_version_id: row.versionId, content_hash: row.contentHash, point_keys: [...row.points], generation_task_id: row.taskId.trim() || null })) });
        validateCoverageRequest(payload, ctx.basis); pending.value = payload; confirmedFailure.value = false; persist();
      }
      const payload = normalizedCoverage(pending.value!);
      try {
        const value = await service.submitRequirementCoverage(ctx.project, ctx.doc, ctx.requirement, ctx.version, payload);
        if (current(ctx)) await accept(value, payload, ctx);
      } catch (err) {
        if (current(ctx) && pending.value && err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) { confirmedFailure.value = true; persist(); }
        throw err;
      }
    });
  }
  async function resetFailed() {
    if (!confirmedFailure.value || busy.value || props.disabled) return;
    const ctx = context();
    pending.value = null; confirmedFailure.value = false; persist();
    await refreshBasis();
    if (current(ctx) && historyReady.value) message.value = "已重新读取最新覆盖，请核对完整用例及验证点后再提交新决定。";
  }
  async function initialize() {
    if (props.disabled) { deferred = true; return; }
    deferred = false; ++epoch; history.value = []; hasMore.value = false; choice.value = "latest";
    cases.value = []; caseMore.value = false; query.value = ""; loadedQuery = ""; caseId.value = "";
    record.value = null; versions.value = []; versionId.value = ""; rows.value = []; note.value = ""; previousId.value = null;
    pending.value = null; confirmedFailure.value = false; busy.value = false; error.value = ""; message.value = "";
    storageWarning.value = ""; storageInvalid.value = false; historyReady.value = false; projectActive.value = false;
    const ctx = context();
    try {
      const raw = sessionStorage.getItem(key());
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.requirement_sha256 !== ctx.sha) throw new Error("保存的覆盖请求与固定需求哈希不一致，请核对原参数");
        validateCoverageRequest(saved.pending, ctx.basis); pending.value = normalizedCoverage(saved.pending); confirmedFailure.value = saved.confirmedFailure === true;
      }
    } catch (err) { storageInvalid.value = true; storageWarning.value = err instanceof Error ? err.message : "原覆盖请求缓存读取失败"; }
    await operate(readLatest);
    if (!current(ctx)) return;
    if (pending.value) await recover();
    const recoveryError = error.value;
    if (current(ctx)) await loadCases();
    if (current(ctx) && recoveryError) error.value = recoveryError;
  }
  watch(() => [props.matrix.requirement.project_id, props.matrix.requirement.document_id, props.matrix.requirement.id,
    props.matrix.version.id, props.matrix.version.content_sha256].join(":"), () => { void initialize(); }, { immediate: true });
  watch(() => props.disabled, blocked => { if (!blocked && deferred) void initialize(); });
  onBeforeUnmount(() => { disposed = true; ++epoch; });
  return { history, hasMore, choice, cases, caseMore, query, caseId, record, versions, versionId, selected,
    rows, note, pending, confirmedFailure, busy, error, message, storageWarning, canEdit, ready, historyReady,
    refreshBasis, moreHistory, selectHistory, loadCases, readCase, readAssociation, addVersion, removeVersion,
    recover, submit, resetFailed };
}
