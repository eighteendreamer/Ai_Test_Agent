import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../../../services/api";
import { HttpError } from "../../../services/http";
import * as requirements from "../../../services/requirements.api";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementBundle, RequirementReview, RequirementReviewRequest } from "../../../services/requirements.types";
import { assertImpactSnapshot } from "./documentImpactIdentity";
import { assertRequirement, assertReview, normalizedReview, readAllRequirementPages } from "./requirementProduct";

type Props = { projectId: string; documentId: string; view: DocumentImpactSnapshotView };
type PointDraft = { key: string; description: string; caseVersions: string[] };
export function useRequirementReview(props: Props) {
  const reviews = ref<RequirementReview[]>([]); const bundles = ref<RequirementBundle[]>([]);
  const ruleId = ref(""); const requirementId = ref(""); const matrixId = ref(""); const revision = ref(0);
  const decision = ref<RequirementReviewRequest["decision"]>("accept");
  const change = ref<NonNullable<RequirementReviewRequest["change"]>>("modified");
  const title = ref(""); const statement = ref(""); const note = ref(""); const points = ref<PointDraft[]>([]);
  const draftParentVersionId = ref<string | null>(null);
  const pending = ref<RequirementReviewRequest | null>(null); const confirmedFailure = ref(false);
  const canWrite = ref(false); const historyReady = ref(false); const loading = ref(false); const busy = ref(false);
  const error = ref(""); const storageWarning = ref(""); const message = ref(""); const storageInvalid = ref(false);
  let epoch = 0; let disposed = false;
  const context = () => ({ epoch, projectId: props.projectId, documentId: props.documentId, view: props.view });
  type Context = ReturnType<typeof context>;
  const current = (ctx: Context) => !disposed && ctx.epoch === epoch && ctx.projectId === props.projectId
    && ctx.documentId === props.documentId && ctx.view.snapshot.id === props.view.snapshot.id;
  const cacheKey = () => `qa-agent:requirement-review:${props.projectId}:${props.documentId}:${props.view.snapshot.id}`;
  const selectedRule = computed(() => props.view.result.rules.find(rule => rule.id === ruleId.value));
  const latest = computed(() => reviews.value.filter(review => review.request.rule_id === ruleId.value)
    .reduce<RequirementReview | null>((last, review) => !last || review.review_number > last.review_number ? review : last, null));
  const ready = computed(() => canWrite.value && historyReady.value && !loading.value && !busy.value
    && !pending.value && !storageInvalid.value && !!selectedRule.value);
  const cases = computed(() => {
    const values = props.view.snapshot.seed.cases as { case: { id: string; project_id: string; title: string; case_key: string }; version_id: string }[] | undefined;
    return (values || []).filter(item => item.case.project_id === props.projectId).map(item => ({
      id: item.version_id, label: `${item.case.case_key} · ${item.case.title} · 固定版本 ${item.version_id}` }));
  });
  const showError = (err: unknown) => { error.value = err instanceof Error ? err.message : "需求评审操作失败"; };
  function persist() {
    try {
      if (pending.value) sessionStorage.setItem(cacheKey(), JSON.stringify(pending.value));
      else sessionStorage.removeItem(cacheKey());
      storageWarning.value = ""; storageInvalid.value = false;
    } catch { storageWarning.value = "无法保存本地评审请求，请保留下方原请求编号与参数。"; }
  }
  function resetDraft() {
    const existing = bundles.value.find(item => item.requirement.id === requirementId.value)?.version;
    draftParentVersionId.value = existing?.id ?? null;
    const candidate = selectedRule.value?.candidate;
    title.value = existing?.title || candidate?.summary || "";
    statement.value = existing?.statement || candidate?.summary || "";
    change.value = existing ? "modified" : candidate?.kind === "added" ? "added" : candidate?.kind === "removed" ? "removed" : "modified";
    points.value = existing?.points.map(point => ({ key: point.key, description: point.description, caseVersions: [] })) || [];
    note.value = "";
  }
  watch(ruleId, () => { requirementId.value = ""; resetDraft(); });
  watch(requirementId, resetDraft);
  function addPoint() { points.value.push({ key: "", description: "", caseVersions: [] }); }
  function validate(payload: RequirementReviewRequest) {
    if (typeof payload.request_id !== "string" || !payload.request_id || payload.request_id.length > 160
      || !props.view.result.rules.some(rule => rule.id === payload.rule_id)
      || !["accept", "reject", "defer"].includes(payload.decision) || typeof payload.note !== "string"
      || !payload.note.trim() || payload.note.length > 8000) throw new Error("评审请求编号、规则或理由无效");
    if (payload.decision !== "accept") return;
    if (!payload.title?.trim() || !payload.statement?.trim() || !payload.change
      || (!!payload.requirement_id !== !!payload.expected_version_id)) throw new Error("请填写需求标题、正文、变化分类及明确父版本");
    const selected = payload.points || [];
    if (payload.change !== "removed" && !selected.length) throw new Error("有效需求至少需要一个明确验证点");
    if (selected.some(point => !point.key.trim() || !point.description.trim())
      || new Set(selected.map(point => point.key)).size !== selected.length) throw new Error("验证点编号和说明不能为空，编号不能重复");
    if (payload.case_links?.some(link => !cases.value.some(item => item.id === link.case_version_id)
      || !link.point_keys.length || link.point_keys.some(key => !selected.some(point => point.key === key)))) throw new Error("用例关联必须属于固定快照及本次验证点");
  }
  async function loadHistory(ctx = context()) {
    loading.value = true; historyReady.value = false;
    const results = await Promise.allSettled([
      readAllRequirementPages(offset => requirements.listRequirementReviews(ctx.projectId, ctx.documentId, ctx.view.snapshot.id, 100, offset),
        () => current(ctx), item => assertReview(item, ctx.projectId, ctx.documentId, ctx.view)),
      readAllRequirementPages(offset => requirements.listRequirements(ctx.projectId, ctx.documentId, 100, offset),
        () => current(ctx), item => assertRequirement(item, ctx.projectId, ctx.documentId)),
      api.getProjectOverview(ctx.projectId),
    ]);
    if (!current(ctx)) return;
    const problems: string[] = [];
    for (const result of results) if (result.status === "rejected") problems.push(result.reason instanceof Error ? result.reason.message : "需求历史读取失败");
    const [reviewRead, bundleRead, projectRead] = results;
    if (reviewRead.status === "fulfilled" && reviewRead.value) reviews.value = reviewRead.value;
    if (bundleRead.status === "fulfilled" && bundleRead.value) bundles.value = bundleRead.value;
    canWrite.value = false;
    if (projectRead.status === "fulfilled") {
      if (projectRead.value.project.id !== ctx.projectId) problems.push("需求入口与当前项目不一致");
      else canWrite.value = projectRead.value.project.status === "active";
    }
    historyReady.value = problems.length === 0; loading.value = false;
    if (problems.length) showError(new Error(problems.join("；")));
    ++revision.value;
  }
  async function accept(value: RequirementReview, ctx: Context, payload: RequirementReviewRequest) {
    if (!current(ctx)) return;
    assertReview(value, ctx.projectId, ctx.documentId, ctx.view, payload);
    pending.value = null; confirmedFailure.value = false; persist();
    message.value = `评审已保存：${value.id} · ${value.request.decision === "accept" ? "接受" : value.request.decision === "reject" ? "拒绝" : "暂缓"}`;
    if (value.requirement_version) matrixId.value = value.requirement_version.requirement_id;
    await loadHistory(ctx);
    if (current(ctx)) resetDraft();
  }
  async function recover() {
    if (!pending.value || busy.value) return;
    const ctx = context(); const payload = JSON.parse(JSON.stringify(normalizedReview(pending.value))) as RequirementReviewRequest;
    busy.value = true; error.value = "";
    try { await accept(await requirements.getRequirementReviewSubmission(ctx.projectId, ctx.documentId, payload.request_id), ctx, payload); }
    catch (err) { if (current(ctx)) showError(err instanceof HttpError && err.status === 404 ? new Error("尚未查到原评审请求；保留编号，可查询或按原参数重试。") : err); }
    finally { if (current(ctx)) busy.value = false; }
  }
  async function submit(retry = false) {
    if (busy.value || (retry ? !pending.value || confirmedFailure.value : !ready.value)) return;
    const ctx = context(); busy.value = true; error.value = ""; message.value = "";
    try {
      if (!retry) {
        const parent = bundles.value.find(item => item.requirement.id === requirementId.value);
        const requestId = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `review-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const payload: RequirementReviewRequest = { request_id: requestId, rule_id: ruleId.value, decision: decision.value,
          note: note.value.trim(), expected_review_id: latest.value?.id ?? null };
        if (decision.value === "accept") {
          if (requirementId.value && !parent) throw new Error("所选需求不在当前文档历史中，请刷新");
          const selected = change.value === "removed" ? [] : points.value;
          Object.assign(payload, { requirement_id: parent?.requirement.id ?? null, expected_version_id: draftParentVersionId.value,
            change: change.value, title: title.value.trim(), statement: statement.value.trim(),
            points: selected.map(point => ({ key: point.key.trim(), description: point.description.trim() })),
            case_links: cases.value.map(item => ({ case_version_id: item.id,
              point_keys: selected.filter(point => point.caseVersions.includes(item.id)).map(point => point.key.trim()) })).filter(link => link.point_keys.length) });
        }
        validate(payload); pending.value = normalizedReview(payload); confirmedFailure.value = false; persist();
      }
      const payload = JSON.parse(JSON.stringify(normalizedReview(pending.value!))) as RequirementReviewRequest;
      await accept(await requirements.submitRequirementReview(ctx.projectId, ctx.documentId, ctx.view.snapshot.id, payload), ctx, payload);
    } catch (err) {
      if (current(ctx)) {
        if (pending.value && err instanceof HttpError && [400, 403, 409, 422].includes(err.status)) confirmedFailure.value = true;
        showError(err);
      }
    } finally { if (current(ctx)) busy.value = false; }
  }
  async function resetFailed() {
    if (!confirmedFailure.value || busy.value) return;
    const ctx = context();
    error.value = "";
    pending.value = null; confirmedFailure.value = false; persist();
    await loadHistory(ctx);
    if (!current(ctx)) return;
    resetDraft();
    message.value = "已重新读取依据，请核对表单后重新评审。";
  }
  async function initialize() {
    ++epoch; busy.value = false; loading.value = false; historyReady.value = false; canWrite.value = false;
    reviews.value = []; bundles.value = []; pending.value = null; confirmedFailure.value = false; matrixId.value = "";
    error.value = ""; storageWarning.value = ""; message.value = ""; storageInvalid.value = false;
    ruleId.value = ""; requirementId.value = ""; decision.value = "accept"; resetDraft();
    const ctx = context();
    try {
      assertImpactSnapshot(ctx.view, ctx.projectId, ctx.documentId);
      const raw = sessionStorage.getItem(cacheKey());
      if (raw) { const payload = JSON.parse(raw) as RequirementReviewRequest; validate(payload); pending.value = normalizedReview(payload); }
    } catch (err) { storageInvalid.value = true; storageWarning.value = err instanceof Error ? err.message : "无法读取保存的评审请求"; }
    await loadHistory(ctx);
    if (!current(ctx)) return;
    ruleId.value = pending.value?.rule_id || props.view.result.rules[0]?.id || "";
    if (pending.value) await recover();
  }
  watch(() => [props.projectId, props.documentId, props.view.snapshot.id], () => { void initialize(); }, { immediate: true });
  onBeforeUnmount(() => { disposed = true; ++epoch; });
  return { reviews, bundles, ruleId, requirementId, matrixId, revision, decision, change, title, statement, note, points,
    pending, confirmedFailure, canWrite, loading, busy, error, storageWarning, message, selectedRule, latest, ready, cases, draftParentVersionId,
    addPoint, submit, recover, resetFailed, loadHistory };
}
