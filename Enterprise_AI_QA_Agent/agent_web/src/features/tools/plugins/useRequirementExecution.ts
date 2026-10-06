import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import * as service from "../../../services/requirements.api";
import type { RequirementMatrix, RequirementExecutionRequest, RequirementExecutionReview, RequirementExecutionCandidate } from "../../../services/requirements.types";
import type { TestCaseVersionRecord } from "../../../types";
import { assertExecution, assertExecutionView, executionCoverage, executionCoverageHash, normalizedExecution, readBodyChecks, validateExecutionRequest } from "./requirementExecutionProduct";

type Props = { matrix: RequirementMatrix; canWrite: boolean; disabled: boolean };
type Row = { result_id: string; verification_id: string; check_index: number; point_keys: string[];
  case_id: string; case_version_id: string; check: Record<string, unknown> };
export function useRequirementExecution(props: Props, showMatrix: (value: RequirementMatrix) => void) {
  const history = ref<RequirementExecutionReview[]>([]); const hasMore = ref(false); const choice = ref("latest");
  const candidates = ref<RequirementExecutionCandidate[]>([]); const candidateMore = ref(false); const resultId = ref("");
  const full = ref<ReturnType<typeof readBodyChecks> | null>(null); const fixedCase = ref<TestCaseVersionRecord | null>(null);
  const rows = ref<Row[]>([]); const note = ref(""); const previousId = ref<string | null>(null);
  const pending = ref<RequirementExecutionRequest | null>(null); const confirmedFailure = ref(false);
  const busy = ref(false); const error = ref(""); const message = ref(""); const storageWarning = ref("");
  const historyReady = ref(false); const storageInvalid = ref(false); const projectActive = ref(false);
  let epoch = 0; let disposed = false; let deferred = false;
  const canEdit = computed(() => props.canWrite && projectActive.value);
  const ready = computed(() => canEdit.value && historyReady.value && !busy.value && !props.disabled && !pending.value
    && !storageInvalid.value && !!note.value.trim() && rows.value.every(row => row.point_keys.length > 0));
  const context = () => ({ epoch, project: props.matrix.requirement.project_id, doc: props.matrix.requirement.document_id,
    requirement: props.matrix.requirement.id, version: props.matrix.version.id, sha: props.matrix.version.content_sha256,
    coverage: executionCoverage(props.matrix), coverageSha: executionCoverageHash(props.matrix), basis: props.matrix });
  type Context = ReturnType<typeof context>;
  const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.project === props.matrix.requirement.project_id
    && ctx.doc === props.matrix.requirement.document_id && ctx.requirement === props.matrix.requirement.id
    && ctx.version === props.matrix.version.id && ctx.sha === props.matrix.version.content_sha256
    && ctx.coverage === executionCoverage(props.matrix) && ctx.coverageSha === executionCoverageHash(props.matrix);
  const key = () => `qa-agent:requirement-execution:${props.matrix.requirement.project_id}:${props.matrix.requirement.document_id}:${props.matrix.requirement.id}:${props.matrix.version.id}:${executionCoverage(props.matrix)}`;
  function persist() {
    try {
      if (pending.value) sessionStorage.setItem(key(), JSON.stringify({ requirement_sha256: props.matrix.version.content_sha256,
        coverage_sha256: executionCoverageHash(props.matrix), pending: normalizedExecution(pending.value), confirmedFailure: confirmedFailure.value }));
      else sessionStorage.removeItem(key());
      storageWarning.value = "";
    } catch { storageWarning.value = "无法保存执行请求，请保留原编号和完整参数回查。"; }
  }
  async function operate(action: (ctx: Context) => Promise<void>) {
    if (busy.value || props.disabled) return;
    const ctx = context(); busy.value = true; error.value = "";
    try { await action(ctx); }
    catch (err) { if (current(ctx)) error.value = err instanceof Error ? err.message : "执行关联操作失败"; }
    finally { if (current(ctx)) busy.value = false; }
  }
  function validateHistory(items: RequirementExecutionReview[], ctx: Context, more: boolean) {
    let revision = more ? history.value[history.value.length - 1]?.revision ?? Infinity : Infinity;
    const ids = new Set<string>();
    for (const item of items) {
      assertExecution(item, ctx.basis, undefined, false);
      if (ids.has(item.id) || item.revision >= revision || (more && history.value.some(old => old.id === item.id))) throw new Error("执行历史分页重复或顺序不一致");
      const old = history.value.find(old => old.id === item.id);
      if (old && JSON.stringify(old) !== JSON.stringify(item)) throw new Error("固定执行历史已变化");
      ids.add(item.id); revision = item.revision;
    }
  }
  async function readLatest(ctx: Context) {
    const [page, matrix, overview] = await Promise.all([
      service.listRequirementExecution(ctx.project, ctx.doc, ctx.requirement, ctx.version, 20, 0),
      service.readRequirementMatrix(ctx.project, ctx.doc, ctx.requirement, ctx.version, ctx.coverage), api.getProjectOverview(ctx.project),
    ]);
    if (!current(ctx)) return;
    if (page.offset !== 0 || (page.has_more && !page.items.length)) throw new Error("执行历史分页未前进");
    validateHistory(page.items, ctx, false); assertExecutionView(matrix, ctx.basis);
    const matching = page.items.find(item => item.request.coverage_id === ctx.coverage);
    if (matching && matrix.execution_review?.id !== matching.id) throw new Error("固定覆盖的最新矩阵与执行历史不一致");
    if (overview.project.id !== ctx.project) throw new Error("项目状态与当前项目不一致");
    projectActive.value = overview.project.status === "active";
    history.value = page.items; hasMore.value = page.has_more; previousId.value = page.items[0]?.id ?? null;
    rows.value = (matrix.execution_review?.checks ?? []).map(check => ({ result_id: check.result_id,
      verification_id: check.verification_id, check_index: check.check_index, point_keys: [...check.point_keys],
      case_id: check.case_id, case_version_id: check.case_version_id, check: check.check }));
    historyReady.value = true; choice.value = "latest"; showMatrix(matrix);
  }
  async function refreshBasis() { if (!pending.value) { historyReady.value = false; await operate(readLatest); } }
  async function moreHistory() {
    await operate(async ctx => {
      const offset = history.value.length;
      const page = await service.listRequirementExecution(ctx.project, ctx.doc, ctx.requirement, ctx.version, 20, offset);
      if (!current(ctx)) return;
      if (page.offset !== offset || (page.has_more && !page.items.length)) throw new Error("执行历史分页未前进");
      validateHistory(page.items, ctx, true); history.value.push(...page.items); hasMore.value = page.has_more;
    });
  }
  async function selectHistory() {
    await operate(async ctx => {
      const selection = choice.value;
      if (selection !== "initial" && selection !== "latest" && !history.value.some(item => item.id === selection && item.request.coverage_id === ctx.coverage)) throw new Error("执行历史不属于所选覆盖依据");
      const matrix = await service.readRequirementMatrix(ctx.project, ctx.doc, ctx.requirement, ctx.version, ctx.coverage, selection === "latest" ? undefined : selection);
      if (current(ctx)) { assertExecutionView(matrix, ctx.basis, selection); showMatrix(matrix); }
    });
  }
  async function loadCandidates(more = false) {
    await operate(async ctx => {
      const offset = more ? candidates.value.length : 0;
      const page = await service.listRequirementExecutionCandidates(ctx.project, ctx.doc, ctx.requirement, ctx.version, ctx.coverage, 20, offset);
      if (!current(ctx)) return;
      if (page.offset !== offset || (page.has_more && !page.items.length) || new Set(page.items.map(item => item.result_id)).size !== page.items.length
        || page.items.some(item => !ctx.basis.points.some(point => point.case_links.some(link => link.case_id === item.case_id && link.case_version_id === item.case_version_id))
          || (more && candidates.value.some(old => old.result_id === item.result_id)))) throw new Error("执行候选身份或分页不一致");
      candidates.value = more ? [...candidates.value, ...page.items] : page.items; candidateMore.value = page.has_more;
      if (!more) { resultId.value = ""; full.value = null; fixedCase.value = null; }
    });
  }
  async function readResult() {
    await operate(async ctx => {
      const candidate = candidates.value.find(item => item.result_id === resultId.value);
      full.value = null; fixedCase.value = null;
      if (!candidate) throw new Error("请从当前覆盖的实际结果中选择");
      const [detail, record, versions] = await Promise.all([api.getTestRun(candidate.run_id), api.getTestCase(candidate.case_id), api.listTestCaseVersions(candidate.case_id)]);
      if (!current(ctx)) return;
      const version = versions.find(value => value.id === candidate.case_version_id);
      const covered = ctx.basis.coverage_review?.case_links.find(link => link.case_version_id === candidate.case_version_id);
      if (record.id !== candidate.case_id || record.project_id !== ctx.project || !version || version.case_id !== record.id
        || !/^[0-9a-f]{64}$/.test(version.content_hash) || (covered && covered.content_hash !== version.content_hash)
        || !ctx.basis.points.some(point => point.case_links.some(link => link.case_version_id === version.id && link.case_id === record.id))) throw new Error("原执行的完整固定用例与当前覆盖不一致");
      full.value = readBodyChecks(candidate, detail, ctx.basis); fixedCase.value = version;
    });
  }
  function allowedPoints(row: Pick<Row, "case_id" | "case_version_id">) {
    return props.matrix.points.filter(point => point.case_links.some(link => link.case_id === row.case_id && link.case_version_id === row.case_version_id)).map(point => point.point);
  }
  function addCheck(verificationId: string, index: number) {
    if (!canEdit.value || busy.value || props.disabled || pending.value || !full.value || rows.value.length >= 200) return;
    const selected = full.value.checks.find(check => check.verification_id === verificationId && check.check_index === index);
    const result = full.value.result;
    if (!selected || rows.value.some(row => row.result_id === result.id && row.verification_id === verificationId && row.check_index === index)) { error.value = "该正文核验已选择或不存在"; return; }
    rows.value.push({ result_id: result.id, verification_id: verificationId, check_index: index, point_keys: [],
      case_id: result.case_id, case_version_id: result.case_version_id, check: selected.check });
  }
  function removeCheck(index: number) { if (canEdit.value && !busy.value && !props.disabled && !pending.value) rows.value.splice(index, 1); }
  async function accept(value: RequirementExecutionReview, payload: RequirementExecutionRequest, ctx: Context) {
    assertExecution(value, ctx.basis, payload);
    const old = history.value.find(item => item.id === value.id);
    if (old && JSON.stringify(old) !== JSON.stringify(value)) throw new Error("原执行确认记录已变化");
    pending.value = null; confirmedFailure.value = false; persist(); message.value = `执行确认已保存：${value.id}。仅所选正文核验参与判定。`;
    await readLatest(ctx);
  }
  async function recover() {
    if (!pending.value) return;
    await operate(async ctx => {
      const payload = normalizedExecution(pending.value!);
      try { const value = await service.getRequirementExecutionSubmission(ctx.project, ctx.doc, payload.request_id); if (current(ctx)) await accept(value, payload, ctx); }
      catch (err) { if (err instanceof HttpError && err.status === 404) throw new Error("未查到原执行请求，不能据此判断未提交；原编号继续保留"); throw err; }
    });
  }
  async function submit(resend = false) {
    if ((!resend && !ready.value) || (resend && (!pending.value || confirmedFailure.value || !canEdit.value))) return;
    await operate(async ctx => {
      if (!resend) {
        const id = typeof crypto?.randomUUID === "function" ? crypto.randomUUID() : `execution-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const payload = normalizedExecution({ request_id: id, coverage_id: ctx.coverage, expected_execution_id: previousId.value, note: note.value,
          checks: rows.value.map(row => ({ result_id: row.result_id, verification_id: row.verification_id, check_index: row.check_index, point_keys: [...row.point_keys] })) });
        validateExecutionRequest(payload, ctx.basis); pending.value = payload; confirmedFailure.value = false; persist();
      }
      const payload = normalizedExecution(pending.value!);
      try { const value = await service.submitRequirementExecution(ctx.project, ctx.doc, ctx.requirement, ctx.version, payload); if (current(ctx)) await accept(value, payload, ctx); }
      catch (err) { if (current(ctx) && pending.value && err instanceof HttpError && [400, 401, 403, 404, 409, 422].includes(err.status)) { confirmedFailure.value = true; persist(); } throw err; }
    });
  }
  async function resetFailed() {
    if (!confirmedFailure.value || busy.value || props.disabled || !canEdit.value) return;
    pending.value = null; confirmedFailure.value = false; persist(); await refreshBasis();
    if (historyReady.value) message.value = "已重读全局最新确认和固定覆盖，请重新核对后提交新决定。";
  }
  async function initialize() {
    if (props.disabled) { deferred = true; return; }
    deferred = false; ++epoch; history.value = []; hasMore.value = false; choice.value = "latest"; candidates.value = []; candidateMore.value = false;
    resultId.value = ""; full.value = null; fixedCase.value = null; rows.value = []; note.value = ""; previousId.value = null;
    pending.value = null; confirmedFailure.value = false; busy.value = false; error.value = ""; message.value = "";
    storageWarning.value = ""; storageInvalid.value = false; historyReady.value = false; projectActive.value = false;
    const ctx = context();
    try {
      const raw = sessionStorage.getItem(key());
      if (raw) { const saved = JSON.parse(raw);
        if (saved.requirement_sha256 !== ctx.sha || saved.coverage_sha256 !== ctx.coverageSha) throw new Error("原执行请求与固定需求或覆盖哈希不一致");
        validateExecutionRequest(saved.pending, ctx.basis); pending.value = normalizedExecution(saved.pending); confirmedFailure.value = saved.confirmedFailure === true;
      }
    } catch (err) { storageInvalid.value = true; storageWarning.value = err instanceof Error ? err.message : "原执行请求缓存读取失败"; }
    await operate(readLatest); if (!current(ctx)) return;
    if (pending.value) await recover(); const originalError = error.value;
    if (current(ctx)) await loadCandidates(); if (current(ctx) && originalError) error.value = originalError;
  }
  watch(() => [props.matrix.requirement.project_id, props.matrix.requirement.document_id, props.matrix.requirement.id,
    props.matrix.version.id, props.matrix.version.content_sha256, executionCoverage(props.matrix), executionCoverageHash(props.matrix)].join(":"), () => { void initialize(); }, { immediate: true });
  watch(() => props.disabled, blocked => { if (!blocked && deferred) void initialize(); });
  onBeforeUnmount(() => { disposed = true; ++epoch; });
  return { history, hasMore, choice, candidates, candidateMore, resultId, full, fixedCase, rows, note, pending, confirmedFailure,
    busy, error, message, storageWarning, canEdit, ready, refreshBasis, moreHistory, selectHistory, loadCandidates, readResult,
    allowedPoints, addCheck, removeCheck, recover, submit, resetFailed };
}
