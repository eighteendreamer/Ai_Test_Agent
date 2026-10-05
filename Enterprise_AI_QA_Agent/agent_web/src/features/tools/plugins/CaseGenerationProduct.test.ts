// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import saved from "../../../../../docs/upgrade_2026-10-03/p4-generation-entry-production-first/md-native-finish/generation.json";
import savedView from "../../../../../docs/upgrade_2026-10-03/p4-generation-entry-production-first/md-native-finish/snapshot-view.json";
import retirement from "../../../../../docs/upgrade_2026-10-03/p4-generation-entry-retirement/api-retirement/verification.json";
import observed from "../../../../../docs/upgrade_2026-10-03/p4-generation-product-browser-fixed/generation-product.json";
import observedView from "../../../../../docs/upgrade_2026-10-03/p4-generation-product-browser-fixed/downloaded-history.json";
import observedRetirement from "../../../../../docs/upgrade_2026-10-03/p4-generation-product-browser-fixed/downloaded-generation-scope-after-restart.json";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementReview } from "../../../services/requirements.types";
import type { GenerationGroupView } from "../../../services/caseGeneration.types";
import { HttpError } from "../../../services/http";
import CaseGenerationPanel from "./CaseGenerationPanel.vue";
import CaseGenerationSettings from "./CaseGenerationSettings.vue";
import { normalizedGeneration } from "./caseGenerationProduct";

const service = vi.hoisted(() => ({ getGenerationSettings: vi.fn(), saveGenerationSettings: vi.fn(),
  submitCaseGeneration: vi.fn(), getCaseGenerationSubmission: vi.fn(), listCaseGenerations: vi.fn(),
  readCaseGeneration: vi.fn(), readCaseGenerationScope: vi.fn(), getCaseGenerationTask: vi.fn(), readCaseGenerationResponse: vi.fn(),
  retryCaseGeneration: vi.fn(), getCaseGenerationRetrySubmission: vi.fn(), listCaseGenerationRetries: vi.fn()
} satisfies Record<keyof typeof import("../../../services/caseGeneration.api"), ReturnType<typeof vi.fn>>));
vi.mock("../../../services/caseGeneration.api", () => service);
const api = vi.hoisted(() => ({ listModes: vi.fn(), listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const clone = <T,>(value: T): T => structuredClone(value);
const group = saved.report.group;
const props = () => ({ projectId: group.project_id, documentId: group.document_id,
  view: clone(savedView) as unknown as DocumentImpactSnapshotView, reviews: [clone(saved.scope.review) as unknown as RequirementReview], canWrite: true });
const request = () => normalizedGeneration({ creation_key: group.creation_key, ...clone(saved.scope.request) });
const cacheKey = `qa-agent:case-generation:${group.project_id}:${group.document_id}`;
const wrappers: ReturnType<typeof mount>[] = [];
const keep = (w: ReturnType<typeof mount>) => { wrappers.push(w); return w; };
const panel = (changes = {}) => keep(mount(CaseGenerationPanel, { props: { ...props(), ...changes } }));
const button = (w: ReturnType<typeof mount>, label: string) => w.findAll("button").find(item => item.text() === label)!;
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; };
async function fill(w: ReturnType<typeof mount>) {
  await flushPromises();
  await w.get(`[aria-label="生成基线 ${saved.scope.request.target_case_version_ids[0]}"]`).setValue(true);
  await w.get('[aria-label="增量新建用例"]').setValue(true);
  await w.get('[aria-label="新用例模式元数据"]').setValue(saved.scope.request.new_case_mode_key!);
  await w.get('[aria-label="生成模型配置编号"]').setValue(saved.scope.request.model_key!);
}
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => group.creation_key });
  api.listModes.mockResolvedValue([{ key: "api_testing", name: "API测试", is_test_mode: true }]);
  const original = JSON.parse(saved.scope.modules[0]!.generation!.invocation.messages[0]!.content).evidence_context.incremental_generation.target_case.version;
  api.listTestCaseVersions.mockResolvedValue([clone(original), clone(saved.draft)]);
  service.listCaseGenerations.mockResolvedValue({ items: [clone(group)], has_more: false, limit: 20, offset: 0 });
  service.submitCaseGeneration.mockResolvedValue(clone(group)); service.getCaseGenerationSubmission.mockResolvedValue(clone(group));
  service.readCaseGeneration.mockResolvedValue(clone(saved.report)); service.readCaseGenerationScope.mockResolvedValue(clone(saved.scope));
  service.readCaseGenerationResponse.mockResolvedValue(clone(saved.responses[0]));
  service.getGenerationSettings.mockResolvedValue(clone(group.config));
  service.listCaseGenerationRetries.mockResolvedValue({ items: [], has_more: false, limit: 20, offset: 0 });
  service.saveGenerationSettings.mockImplementation((_p, payload) => Promise.resolve({ ...clone(group.config), revision: 2, payload }));
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); });

it("submits the accepted fixed requirement and explicit original baseline rather than the current case version", async () => {
  const w = panel(); await fill(w); await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(service.submitCaseGeneration).toHaveBeenCalledWith(group.project_id, group.document_id, request());
  expect(w.text()).toContain("生成提交已确认"); expect(w.text()).toContain("草稿或候选已保存");
  expect(JSON.parse(sessionStorage.getItem(cacheKey)!)).toEqual({ pending: null, confirmedFailure: false, groupId: group.id });
  expect(w.text()).toContain(saved.scope.sha256);
});

it("reads complete original and generated case JSON and the exact saved response", async () => {
  const w = panel(); await flushPromises();
  await button(w, "读取生成基线 retention").trigger("click"); await flushPromises();
  expect(w.text()).toContain("完整步骤11"); expect(w.text()).toContain('"布尔": false'); expect(w.text()).toContain('"零": 0');
  await button(w, `查看生成组 ${group.id}`).trigger("click"); await flushPromises();
  await button(w, `读取完整生成草稿 ${saved.draft.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain(saved.draft.execution_notes); expect(w.text()).toContain("不能丢失");
  await button(w, `读取原生成响应 ${saved.report.modules[0]!.id}`).trigger("click"); await flushPromises();
  const displayed = w.findAll("pre").map(item => JSON.parse(item.text())).find(item => item.raw_response && item.text);
  expect(displayed).toEqual(saved.responses[0]);
  expect(service.readCaseGenerationResponse).toHaveBeenCalledWith(group.project_id, group.document_id, saved.report.modules[0]!.id);
});

it("retains an unknown submitted request through 404 and retries every original parameter unchanged", async () => {
  service.submitCaseGeneration.mockRejectedValueOnce(new Error("actual saved reply lost"));
  const w = panel(); await fill(w); await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("提交结果尚未确认");
  const pending = JSON.parse(sessionStorage.getItem(cacheKey)!).pending;
  service.getCaseGenerationSubmission.mockRejectedValueOnce(new HttpError("not found", 404));
  await button(w, "查询原生成提交").trigger("click"); await flushPromises();
  expect(w.text()).toContain("尚未查到原生成提交"); expect(JSON.parse(sessionStorage.getItem(cacheKey)!).pending).toEqual(pending);
  await button(w, "重试原生成提交").trigger("click"); await flushPromises();
  expect(service.submitCaseGeneration.mock.calls[1]![2]).toEqual(pending); expect(w.text()).toContain("生成提交已确认");
});

it("does not create a pending unknown submission when local validation fails before sending", async () => {
  const w = panel(); await fill(w); await w.get('[aria-label="新建用例数量上限"]').setValue(0);
  await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(service.submitCaseGeneration).not.toHaveBeenCalled();
  expect(w.find('[aria-label="原增量生成请求"]').exists()).toBe(false);
});

it("uses the existing request identity fallback when randomUUID is unavailable", async () => {
  vi.stubGlobal("crypto", {}); service.submitCaseGeneration.mockRejectedValue(new HttpError("reply unknown", 503));
  const w = panel(); await fill(w); await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(service.submitCaseGeneration).toHaveBeenCalledTimes(1);
  const payload = service.submitCaseGeneration.mock.calls[0]![2];
  expect(payload.creation_key).toMatch(/^generation-/); expect(payload.requirement_version_id).toBe(saved.scope.requirement.id);
  expect(JSON.parse(sessionStorage.getItem(cacheKey)!).pending).toEqual(payload);
});

it("recovers the real original group on refresh and archive without sending a new request", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify({ pending: request(), confirmedFailure: false, groupId: null }));
  const w = panel({ canWrite: false }); await flushPromises();
  expect(service.getCaseGenerationSubmission).toHaveBeenCalledWith(group.project_id, group.document_id, group.creation_key);
  expect(service.submitCaseGeneration).not.toHaveBeenCalled(); expect(w.text()).toContain("当前项目不可提交新生成");
  expect(w.text()).toContain("生成提交已确认"); expect(button(w, "提交固定增量生成").attributes("disabled")).toBeDefined();
});

it("restores a saved group without recapturing a new requirement or generation request", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify({ pending: null, confirmedFailure: false, groupId: group.id }));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain(group.scope_sha256); expect(service.submitCaseGeneration).not.toHaveBeenCalled();
});

it("keeps the original request when its saved group has different parameters", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify({ pending: request(), groupId: null }));
  service.readCaseGenerationScope.mockResolvedValue({ ...clone(saved.scope), request: { ...saved.scope.request, max_new_cases: 2 } });
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("原请求参数不一致"); expect(JSON.parse(sessionStorage.getItem(cacheKey)!).pending).toEqual(request());
  expect(w.find('[aria-label="固定增量生成结果"]').exists()).toBe(false);
});

it("rejects a returned draft that no longer matches the immutable receipt", async () => {
  const w = panel(); await flushPromises(); await button(w, `查看生成组 ${group.id}`).trigger("click"); await flushPromises();
  api.listTestCaseVersions.mockResolvedValueOnce([{ ...clone(saved.draft), content_hash: "changed-content" }]);
  await button(w, `读取完整生成草稿 ${saved.draft.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("完整草稿与原提交收据不一致");
  expect(w.findAll("summary").some(item => item.text() === "完整生成草稿 JSON 与自身说明")).toBe(false);
});

it("does not show a foreign group or treat a wrong module receipt as success", async () => {
  const w = panel(); await flushPromises();
  service.readCaseGeneration.mockResolvedValueOnce({ ...clone(saved.report), group: { ...group, project_id: "other" } });
  await button(w, `查看生成组 ${group.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("当前项目"); expect(w.find('[aria-label="固定增量生成结果"]').exists()).toBe(false);
  const bad = clone(saved.report); bad.modules[0]!.checkpoint!.receipt!.scope_sha256 = "wrong";
  service.readCaseGeneration.mockResolvedValueOnce(bad);
  await button(w, `查看生成组 ${group.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("收据不一致");
});

it("shows definite submission rejection without an automatic resend", async () => {
  service.submitCaseGeneration.mockRejectedValueOnce(new HttpError("parent conflict", 409));
  const w = panel(); await fill(w); await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("原提交已明确拒绝"); expect(button(w, "重试原生成提交")).toBeUndefined();
  expect(service.submitCaseGeneration).toHaveBeenCalledTimes(1);
  await button(w, "核对依据并重新提交").trigger("click"); await flushPromises();
  expect(w.find('[aria-label="原增量生成请求"]').exists()).toBe(false); expect(service.submitCaseGeneration).toHaveBeenCalledTimes(1);
});

it("retains a confirmed rejection in local recovery until explicitly handled", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify({ pending: request(), confirmedFailure: true, groupId: null }));
  service.getCaseGenerationSubmission.mockRejectedValue(new HttpError("not found", 404));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("原提交已明确拒绝"); expect(service.submitCaseGeneration).not.toHaveBeenCalled();
});

it("keeps unknown invocation and failed modules visible without a resend action", async () => {
  const unknown = clone(saved.report) as unknown as GenerationGroupView; unknown.modules[0]!.status = "failed"; unknown.modules[0]!.error_code = "invocation_outcome_unknown";
  unknown.modules[0]!.checkpoint = { ...unknown.modules[0]!.checkpoint!, phase: "invoking", response: null, receipt: null };
  service.readCaseGeneration.mockResolvedValue(unknown);
  const w = panel(); await flushPromises(); await button(w, `查看生成组 ${group.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("响应尚未确认"); expect(w.text()).toContain("模块失败");
  expect(service.submitCaseGeneration).not.toHaveBeenCalled();
});

it("keeps a selected accepted version after newer review history arrives", async () => {
  const w = panel(); await fill(w); const newer = clone(saved.scope.review) as unknown as RequirementReview;
  newer.id = "later-review"; newer.requirement_version = { ...newer.requirement_version!, id: "later-requirement-version", version: 2 };
  await w.setProps({ reviews: [...props().reviews, newer] }); await flushPromises();
  expect((w.get('[aria-label="生成依据需求版本"]').element as HTMLSelectElement).value).toBe(saved.scope.review.id);
  await w.get("form:not(details form)").trigger("submit"); await flushPromises();
  expect(service.submitCaseGeneration.mock.calls[0]![2].requirement_version_id).toBe(saved.scope.requirement.id);
});

it("does not let an old group response appear after switching the project and document", async () => {
  const delayed = deferred<typeof saved.report>(); service.readCaseGeneration.mockReturnValueOnce(delayed.promise);
  const w = panel(); await flushPromises(); await button(w, `查看生成组 ${group.id}`).trigger("click");
  await w.setProps({ projectId: "another-project", documentId: "another-document" }); await flushPromises();
  delayed.resolve(clone(saved.report)); await flushPromises();
  expect(w.find('[aria-label="固定增量生成结果"]').exists()).toBe(false);
});

it("reads later generation history pages and rejects a nonadvancing page", async () => {
  service.listCaseGenerations.mockResolvedValueOnce({ items: [clone(group)], has_more: true, limit: 20, offset: 0 });
  const w = panel(); await flushPromises();
  service.listCaseGenerations.mockResolvedValueOnce({ items: [{ ...group, id: "later-group" }], has_more: false, limit: 20, offset: 1 });
  await button(w, "更多生成历史").trigger("click"); await flushPromises();
  expect(service.listCaseGenerations).toHaveBeenLastCalledWith(group.project_id, group.document_id, 20, 1);
  service.listCaseGenerations.mockResolvedValueOnce({ items: [], has_more: true, limit: 20, offset: 0 });
  await button(w, "刷新生成历史").trigger("click"); await flushPromises(); expect(w.text()).toContain("分页未前进");
});

it("waits for the selected historical scope before allowing download of the actual observed groups", async () => {
  const normal = observed.normal.group; const retired = observed.retired.group;
  service.getGenerationSettings.mockResolvedValue(clone(normal.config));
  service.listCaseGenerations.mockResolvedValue({ items: [clone(normal), clone(retired)], has_more: false, limit: 20, offset: 0 });
  service.readCaseGeneration.mockResolvedValueOnce(clone(observed.normal));
  service.readCaseGenerationScope.mockResolvedValueOnce(clone(observed.scope));
  const w = panel({ projectId: normal.project_id, documentId: normal.document_id,
    view: clone(observedView) as unknown as DocumentImpactSnapshotView,
    reviews: [clone(observed.scope.review) as unknown as RequirementReview] });
  await flushPromises(); await button(w, `查看生成组 ${normal.id}`).trigger("click"); await flushPromises();
  const delayed = deferred<typeof observed.retired>(); service.readCaseGeneration.mockReturnValueOnce(delayed.promise);
  service.readCaseGenerationScope.mockResolvedValueOnce(clone(observedRetirement));
  await button(w, `查看生成组 ${retired.id}`).trigger("click");
  expect(button(w, "下载完整生成范围 JSON").attributes("disabled")).toBeDefined();
  expect(w.text()).toContain("正在读取或提交固定生成结果");
  delayed.resolve(clone(observed.retired)); await flushPromises();
  expect(w.get('[aria-label="固定增量生成结果"]').text()).toContain(retired.scope_sha256);
  expect(button(w, "下载完整生成范围 JSON").attributes("disabled")).toBeUndefined();
});

it("shows the real retirement-only scope without new-case controls or a model response", async () => {
  service.listCaseGenerations.mockResolvedValue({ items: [clone(retirement.group)], has_more: false, limit: 20, offset: 0 });
  service.readCaseGeneration.mockResolvedValue(clone(retirement.public_report)); service.readCaseGenerationScope.mockResolvedValue(clone(retirement.public_scope));
  const original = props(); const view = clone(original.view);
  view.snapshot.id = retirement.public_scope.review.source.snapshot_id;
  view.snapshot.task_id = retirement.public_scope.review.source.task_id;
  view.snapshot = { ...view.snapshot, seed: { ...view.snapshot.seed, project_id: retirement.group.project_id,
    document_id: retirement.group.document_id, cases: [retirement.public_scope.modules[0]!.target] } };
  const w = panel({ projectId: retirement.group.project_id, documentId: retirement.group.document_id,
    reviews: [clone(retirement.public_scope.review)], view }); await flushPromises();
  expect(w.find('[aria-label="增量新建用例"]').exists()).toBe(false);
  await button(w, `查看生成组 ${retirement.group.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("原用例停用候选"); expect(w.findAll("button").some(item => item.text().startsWith("读取原生成响应"))).toBe(false);
});

it("saves the project policy without mutating the already frozen group settings", async () => {
  const w = keep(mount(CaseGenerationSettings, { props: { projectId: group.project_id, canWrite: true } })); await flushPromises();
  await w.get('[aria-label="生成完整输入字节上限"]').setValue(1);
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.saveGenerationSettings.mock.calls[0]![1].max_input_bytes).toBe(1);
  expect(group.config.payload.max_input_bytes).toBe(256000); expect(w.text()).toContain("项目生成设置已保存");
});

it("disables policy writes on an archived project and rejects corrupt pending cache", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify({ pending: { ...request(), lease_token: "not-allowed" } }));
  const w = panel({ canWrite: false }); await flushPromises();
  expect(w.text()).toContain("保存的生成请求无效"); expect(service.submitCaseGeneration).not.toHaveBeenCalled();
  expect(w.get('details[aria-label="项目增量生成设置"] fieldset').attributes("disabled")).toBeDefined();
});
