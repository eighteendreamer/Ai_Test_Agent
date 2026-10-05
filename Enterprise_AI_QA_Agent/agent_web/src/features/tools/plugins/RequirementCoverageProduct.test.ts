// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import saved from "../../../../../docs/upgrade_2026-10-03/p4-requirement-coverage-production-first/docx-native-finish/requirement-coverage.json";
import type { RequirementCoverageRequest, RequirementCoverageReview, RequirementMatrix as Matrix } from "../../../services/requirements.types";
import { HttpError } from "../../../services/http";
import RequirementCoveragePanel from "./RequirementCoveragePanel.vue";
import RequirementMatrix from "./RequirementMatrix.vue";
import { assertCoverage, normalizedCoverage, validateCoverageRequest } from "./requirementCoverageProduct";

const service = vi.hoisted(() => ({ listRequirementCoverage: vi.fn(), getRequirementCoverageSubmission: vi.fn(),
  submitRequirementCoverage: vi.fn(), readRequirementMatrix: vi.fn(), listRequirementVersions: vi.fn() }));
vi.mock("../../../services/requirements.api", () => service);
const project = vi.hoisted(() => ({ getProjectOverview: vi.fn(), listTestCases: vi.fn(), getTestCase: vi.fn(), listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api: project }));
const clone = <T,>(value: T): T => structuredClone(value);
const basis = () => clone(saved.initial) as unknown as Matrix;
const first = () => clone(saved.first) as unknown as RequirementCoverageReview;
const second = () => clone(saved.second) as unknown as RequirementCoverageReview;
const candidate = () => clone(saved.references[0]);
const cacheKey = `qa-agent:requirement-coverage:${saved.first.project_id}:${saved.first.document_id}:${saved.first.requirement_id}:${saved.first.requirement_version_id}`;
const page = <T,>(items: T[], offset = 0, has_more = false) => ({ items, offset, limit: 20, has_more });
let current: Matrix; let history: RequirementCoverageReview[];
const wrappers: ReturnType<typeof mount>[] = [];
const keep = (w: ReturnType<typeof mount>) => { wrappers.push(w); return w; };
const button = (w: ReturnType<typeof mount>, text: string) => w.findAll("button").find(item => item.text() === text)!;
const lastMatrix = (w: ReturnType<typeof mount>) => { const values = w.emitted("matrix") ?? []; return values[values.length - 1]?.[0]; };
const panel = (props = {}) => keep(mount(RequirementCoveragePanel, { props: { matrix: basis(), canWrite: true, disabled: false, ...props } }));
const cache = (payload = saved.first_payload, rejected = false) => sessionStorage.setItem(cacheKey, JSON.stringify({ requirement_sha256: saved.first.requirement_version_sha256,
  pending: payload, confirmedFailure: rejected }));
async function edit(w: ReturnType<typeof mount>) {
  await flushPromises();
  for (const row of basis().version.case_links) await button(w, `移除覆盖版本 ${row.case_version_id}`).trigger("click");
  await w.get('[aria-label="覆盖候选用例"]').setValue(candidate().approved.id); await flushPromises();
  await w.get('[aria-label="覆盖候选完整版本"]').setValue(candidate().version.id);
  await button(w, "加入所选固定版本").trigger("click");
  await w.get(`[aria-label="覆盖 ${candidate().version.id} 验证点 retention"]`).setValue(true);
  await w.get(`[aria-label="覆盖 ${candidate().version.id} 生成任务"]`).setValue(saved.first_payload.case_links[0].generation_task_id);
  await w.get('[aria-label="覆盖确认说明"]').setValue(saved.first_payload.note);
}
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => saved.first_payload.request_id });
  current = basis(); history = [];
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "active" } });
  project.listTestCases.mockResolvedValue(page([candidate().approved]));
  project.getTestCase.mockResolvedValue(candidate().approved); project.listTestCaseVersions.mockResolvedValue([candidate().version]);
  service.listRequirementCoverage.mockImplementation((_p, _d, _r, _v, _l, offset = 0) => Promise.resolve(page(clone(history), offset)));
  service.readRequirementMatrix.mockImplementation((_p, _d, _r, _v, choice) => Promise.resolve(clone(choice === "initial" ? saved.initial : choice === saved.first.id ? saved.first_matrix : current)));
  service.getRequirementCoverageSubmission.mockResolvedValue(first());
  service.submitRequirementCoverage.mockImplementation(async () => { current = clone(saved.first_matrix) as unknown as Matrix; history = [first()]; return first(); });
  service.listRequirementVersions.mockResolvedValue([basis().version]);
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("selects actual project versions and preserves all 12 steps, JSON values, notes and provenance", async () => {
  const w = panel(); await edit(w);
  expect(project.listTestCases).toHaveBeenCalledWith(saved.first.project_id, { query: undefined, limit: 20, offset: 0 });
  expect(w.text()).toContain("完整步骤11"); expect(w.text()).toContain("false"); expect(w.text()).toContain("null");
  expect(w.text()).toContain("不能丢失"); expect(w.text()).toContain(candidate().version.execution_notes);
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementCoverage).toHaveBeenCalledWith(saved.first.project_id, saved.first.document_id,
    saved.first.requirement_id, saved.first.requirement_version_id, normalizedCoverage(saved.first_payload as RequirementCoverageRequest));
  expect(w.text()).toContain("覆盖确认已保存：" + saved.first.id); expect(sessionStorage.getItem(cacheKey)).toBeNull();
  expect(lastMatrix(w)).toEqual(saved.first_matrix);
});

it("keeps an unknown POST and recovers the original GET after reopening without another send", async () => {
  service.submitRequirementCoverage.mockImplementation(async () => { current = clone(saved.first_matrix) as unknown as Matrix; history = [first()]; throw new HttpError("reply lost", 503); });
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("提交结果尚未确认"); expect(JSON.parse(sessionStorage.getItem(cacheKey)!).pending).toEqual(normalizedCoverage(saved.first_payload as RequirementCoverageRequest));
  w.unmount(); const reopened = panel(); await flushPromises();
  expect(service.submitRequirementCoverage).toHaveBeenCalledTimes(1); expect(service.getRequirementCoverageSubmission).toHaveBeenCalledWith(saved.first.project_id, saved.first.document_id, saved.first_payload.request_id);
  expect(reopened.text()).toContain("覆盖确认已保存：" + saved.first.id); expect(sessionStorage.getItem(cacheKey)).toBeNull();
});

it.each([404, 503])("retains the original request after a %s recovery result", async status => {
  cache(); service.getRequirementCoverageSubmission.mockRejectedValue(new HttpError("actual lookup failed", status));
  const w = panel(); await flushPromises(); expect(w.text()).toContain(saved.first_payload.request_id);
  expect(w.text()).toContain(status === 404 ? "不能据此判断未提交" : "actual lookup failed");
  expect(service.submitRequirementCoverage).not.toHaveBeenCalled(); expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
});

it("rejects a mismatched recovery response and keeps all original parameters", async () => {
  cache(); service.getRequirementCoverageSubmission.mockResolvedValue({ ...first(), request: { ...first().request, note: "wrong" } });
  const w = panel(); await flushPromises(); expect(w.text()).toContain("原请求参数不一致");
  expect(sessionStorage.getItem(cacheKey)).not.toBeNull(); expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("uses an explicit same-request retry after a transport failure", async () => {
  service.submitRequirementCoverage.mockRejectedValueOnce(new Error("network lost"));
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  await button(w, "重试原覆盖确认").trigger("click"); await flushPromises();
  const calls = service.submitRequirementCoverage.mock.calls; expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
  expect(w.text()).toContain("覆盖确认已保存");
});

it("requires an explicit refresh after a real 409 and uses the new expected confirmation", async () => {
  service.submitRequirementCoverage.mockRejectedValue(new HttpError("concurrent confirmation", 409));
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("原请求已明确拒绝"); expect(button(w, "重试原覆盖确认")).toBeUndefined();
  current = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  await button(w, "重新读取依据并确认覆盖").trigger("click"); await flushPromises();
  expect(service.submitRequirementCoverage).toHaveBeenCalledTimes(1); expect(sessionStorage.getItem(cacheKey)).toBeNull();
  expect(w.text()).toContain("已重新读取最新覆盖");
  expect((w.vm as unknown as { rows: unknown[] }).rows).toHaveLength(1);
});

it("shows latest, initial and fixed confirmation histories without changing the edit baseline", async () => {
  current = clone(saved.matrix) as unknown as Matrix; history = [clone(saved.retry) as unknown as RequirementCoverageReview, second(), first()];
  const w = panel(); await flushPromises();
  expect(w.findAll('[role="alert"]').map(item => item.text())).toEqual([]);
  await w.get('[aria-label="覆盖确认历史"]').setValue(saved.first.id); await flushPromises();
  expect(lastMatrix(w)).toEqual(saved.first_matrix);
  await w.get('[aria-label="覆盖确认历史"]').setValue("initial"); await flushPromises();
  expect(lastMatrix(w)).toEqual(saved.initial);
  expect((w.vm as unknown as { rows: unknown[] }).rows).toHaveLength(saved.second.case_links.length);
  expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("explicitly clears the complete association set using the latest confirmation identity", async () => {
  current = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  service.submitRequirementCoverage.mockRejectedValue(new HttpError("observe request", 409));
  const w = panel(); await flushPromises(); await button(w, `移除覆盖版本 ${first().case_links[0].case_version_id}`).trigger("click");
  await w.get('[aria-label="覆盖确认说明"]').setValue("明确撤销全部设计关联"); await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementCoverage.mock.calls[0][4]).toEqual({ request_id: saved.first_payload.request_id,
    expected_coverage_id: saved.first.id, note: "明确撤销全部设计关联", case_links: [] });
});

it("does not enable writing when catalog lookup returns a foreign project", async () => {
  project.listTestCases.mockResolvedValue(page([{ ...candidate().approved, project_id: "foreign-project" }]));
  const w = panel(); await flushPromises(); expect(w.text()).toContain("项目用例目录身份或分页不一致");
  expect(w.findAll("option").some(item => item.element.value === candidate().approved.id)).toBe(false);
});

it("rejects foreign full versions and requires every existing association to be read completely", async () => {
  const w = panel(); await flushPromises(); await w.get('[aria-label="覆盖确认说明"]').setValue("不能仅用历史链接确认");
  expect(button(w, "保存用例覆盖确认").attributes("disabled")).toBeDefined();
  project.getTestCase.mockResolvedValue({ ...candidate().approved, project_id: "another" });
  await w.get('[aria-label="覆盖候选用例"]').setValue(candidate().approved.id); await flushPromises();
  expect(w.text()).toContain("完整用例与当前项目"); expect(button(w, "加入所选固定版本")).toBeUndefined();
});

it("does not discard malformed pending storage or submit a fresh request", async () => {
  sessionStorage.setItem(cacheKey, "{not valid JSON"); const w = panel(); await edit(w);
  expect(button(w, "保存用例覆盖确认").attributes("disabled")).toBeDefined(); expect(sessionStorage.getItem(cacheKey)).toBe("{not valid JSON");
  expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("keeps a readable pending request when browser persistence is unavailable", async () => {
  const w = panel(); await edit(w); vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
  service.submitRequirementCoverage.mockRejectedValue(new HttpError("unknown", 503));
  await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("无法保存覆盖请求"); expect(w.text()).toContain(saved.first_payload.request_id);
});

it("recovers the original result for an archived project without enabling a new write", async () => {
  cache(); current = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "archived" } });
  const w = panel({ canWrite: false }); await flushPromises(); expect(w.text()).toContain("覆盖确认已保存");
  expect(w.text()).toContain("当前项目不可提交新覆盖"); expect(button(w, "保存用例覆盖确认").attributes("disabled")).toBeDefined();
  const read = button(w, `读取完整关联用例 ${candidate().version.id}`);
  expect((read.element as HTMLButtonElement).disabled).toBe(false);
  await read.trigger("click"); await flushPromises();
  expect(w.text()).toContain("完整步骤11"); expect(w.text()).toContain("实际读取 SHA256");
  expect(w.get(`[aria-label="覆盖 ${candidate().version.id} 验证点 retention"]`).attributes("disabled")).toBeDefined();
  expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("does not report a successful rebase after the latest basis read failed", async () => {
  cache(saved.first_payload, true); service.getRequirementCoverageSubmission.mockRejectedValue(new HttpError("not saved", 404));
  const w = panel(); await flushPromises();
  service.readRequirementMatrix.mockRejectedValueOnce(new HttpError("basis unavailable", 503));
  await button(w, "重新读取依据并确认覆盖").trigger("click"); await flushPromises();
  expect(w.text()).toContain("basis unavailable"); expect(w.text()).not.toContain("已重新读取最新覆盖");
  expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("detects stalled or duplicate history pages", async () => {
  current = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  service.listRequirementCoverage.mockResolvedValueOnce(page([first()], 0, true)).mockResolvedValueOnce(page([], 1, true));
  const w = panel(); await flushPromises(); await button(w, "读取更多覆盖确认").trigger("click"); await flushPromises();
  expect(w.text()).toContain("覆盖历史分页未前进");
});

it("keeps query pagination tied to the submitted query rather than edited text", async () => {
  project.listTestCases.mockResolvedValueOnce(page([candidate().approved], 0, true));
  const w = panel(); await flushPromises(); await w.get('[aria-label="查询项目用例"]').setValue("not-submitted");
  project.listTestCases.mockResolvedValue(page([], 1)); await button(w, "读取更多项目用例").trigger("click"); await flushPromises();
  expect(project.listTestCases.mock.calls[1][1]).toEqual({ query: undefined, limit: 20, offset: 1 });
});

it("fences a late original recovery when the selected requirement version changes", async () => {
  cache(); let resolve!: (value: RequirementCoverageReview) => void;
  service.getRequirementCoverageSubmission.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const w = panel(); await flushPromises();
  const changed = basis(); changed.version = { ...changed.version, id: "6a4464cd-ea3c-4bb5-911d-6b22ad32f1bb", content_sha256: "e".repeat(64), case_links: [] };
  current = changed; history = []; await w.setProps({ matrix: changed }); await flushPromises();
  resolve(first()); await flushPromises(); expect(w.text()).not.toContain("覆盖确认已保存：");
  expect(sessionStorage.getItem(cacheKey)).not.toBeNull(); expect(service.submitRequirementCoverage).not.toHaveBeenCalled();
});

it("waits for parent reads before initializing and starts without crypto.randomUUID", async () => {
  const w = panel({ disabled: true }); await flushPromises(); expect(service.listRequirementCoverage).not.toHaveBeenCalled();
  await w.setProps({ disabled: false }); await edit(w); vi.stubGlobal("crypto", {});
  service.submitRequirementCoverage.mockRejectedValue(new HttpError("observe", 503)); await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementCoverage.mock.calls[0][4].request_id).toMatch(/^coverage-/);
});

it("keeps the existing passive matrix read and explicitly opens coverage management", async () => {
  const w = keep(mount(RequirementMatrix, { props: { projectId: saved.first.project_id, documentId: saved.first.document_id,
    requirementId: saved.first.requirement_id, revision: 0, canWrite: true } })); await flushPromises();
  expect(service.listRequirementCoverage).not.toHaveBeenCalled(); await button(w, "管理用例覆盖").trigger("click"); await flushPromises();
  expect(service.listRequirementCoverage).toHaveBeenCalled(); expect(w.text()).toContain("用例覆盖确认");
});

it("validates actual confirmations while rejecting changed sources and unknown points", () => {
  expect(() => assertCoverage(first(), basis(), saved.first_payload as RequirementCoverageRequest)).not.toThrow();
  const changed = first(); changed.case_links[0].generation!.receipt.versions[0].content_hash = "e".repeat(64);
  expect(() => assertCoverage(changed, basis())).toThrow("生成收据");
  const request = normalizedCoverage(saved.first_payload as RequirementCoverageRequest); request.case_links[0].point_keys = ["unknown"];
  expect(() => validateCoverageRequest(request, basis())).toThrow("验证点");
});

it("keeps the chosen fixed version after refreshing a case with a newer draft", async () => {
  const later = { ...candidate().version, id: "a71caa46-122b-44c9-a69b-fd71ab619d18", version: 9 };
  project.listTestCaseVersions.mockResolvedValue([later, candidate().version]);
  const w = panel(); await flushPromises();
  await w.get('[aria-label="覆盖候选用例"]').setValue(candidate().approved.id); await flushPromises();
  await w.get('[aria-label="覆盖候选完整版本"]').setValue(candidate().version.id);
  await (w.vm as unknown as { readCase: () => Promise<void> }).readCase(); await flushPromises();
  expect((w.get('[aria-label="覆盖候选完整版本"]').element as HTMLSelectElement).value).toBe(candidate().version.id);
});

it("keeps the existing confirmation durable when its subsequent matrix refresh fails", async () => {
  const w = panel(); await edit(w);
  service.readRequirementMatrix.mockRejectedValueOnce(new HttpError("matrix read unavailable", 503));
  await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("覆盖确认已保存"); expect(w.text()).toContain("matrix read unavailable");
  expect(sessionStorage.getItem(cacheKey)).toBeNull(); expect(w.find('[aria-label="原覆盖确认请求"]').exists()).toBe(false);
  expect(service.submitRequirementCoverage).toHaveBeenCalledTimes(1);
});

it("rejects duplicate fixed versions or points without silently changing a request", () => {
  const request = normalizedCoverage(saved.first_payload as RequirementCoverageRequest);
  request.case_links.push(clone(request.case_links[0]));
  expect(() => validateCoverageRequest(request, basis())).toThrow("版本");
  request.case_links.pop(); request.case_links[0].point_keys.push("retention");
  expect(() => validateCoverageRequest(request, basis())).toThrow("验证点");
});
