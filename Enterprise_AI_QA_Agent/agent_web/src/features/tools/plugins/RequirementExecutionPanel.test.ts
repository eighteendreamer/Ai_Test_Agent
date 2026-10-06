// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import saved from "../../../../../docs/upgrade_2026-10-03/p4-requirement-execution-production-identity/md-native-finish/requirement-execution.json";
import cleanup from "../../../../../docs/upgrade_2026-10-03/p4-requirement-execution-md-fixed/execution-md/verification.json";
import type { RequirementMatrix as Matrix, RequirementExecutionReview as Review, RequirementExecutionRequest as Payload } from "../../../services/requirements.types";
import type { TestRunDetail } from "../../../types";
import { HttpError } from "../../../services/http";
import RequirementExecutionPanel from "./RequirementExecutionPanel.vue";
import { normalizedExecution, readBodyChecks, validateExecutionRequest } from "./requirementExecutionProduct";

const service = vi.hoisted(() => ({ listRequirementExecution: vi.fn(), getRequirementExecutionSubmission: vi.fn(),
  submitRequirementExecution: vi.fn(), readRequirementMatrix: vi.fn(), listRequirementExecutionCandidates: vi.fn() }));
vi.mock("../../../services/requirements.api", () => service);
const project = vi.hoisted(() => ({ getProjectOverview: vi.fn(), getTestRun: vi.fn(), getTestCase: vi.fn(), listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api: project }));
const clone = <T,>(v: T): T => structuredClone(v);
const first = () => clone(saved.first) as unknown as Review;
const basis = () => { const value = clone(saved.first_matrix) as unknown as Matrix; value.execution_review = null;
  value.points.forEach(point => { point.execution_checks = []; point.execution_status = "not_assessed"; }); return value; };
const page = <T,>(items: T[], offset = 0, has_more = false) => ({ items, offset, limit: 20, has_more });
const rawResult = () => clone(saved.results[0]);
const summaries = (result = rawResult()) => { const verification = result.actual.verification_results.find(v => v.metadata.phase === "case")!;
  return verification.metadata.checks.map((check, check_index) => ({ verification_id: verification.id, check_index,
    name: check.name, status: check.status, method: check.method, expected_path_tokens: check.expected_path_tokens })); };
const candidate = () => ({ result_id: rawResult().id, run_id: rawResult().run_id, case_id: rawResult().case_id,
  case_version_id: rawResult().case_version_id, status: rawResult().status, summary: rawResult().summary,
  created_at: rawResult().created_at, checks: summaries(), warnings: [] });
// The run envelope is a component fixture; body checks/versions are original stored samples.
const run = (result = rawResult(), projectId = saved.first.project_id) => ({ run: { id: result.run_id, project_id: projectId },
  results: [result], items: [{ id: result.run_item_id, result_id: result.id, case_version_id: result.case_version_id }] }) as unknown as TestRunDetail;
const cacheKey = `qa-agent:requirement-execution:${saved.first.project_id}:${saved.first.document_id}:${saved.first.requirement_id}:${saved.first.requirement_version_id}:${saved.coverage.id}`;
const wrappers: ReturnType<typeof mount>[] = [];
const panel = (props = {}) => { const w = mount(RequirementExecutionPanel, { props: { matrix: basis(), canWrite: true, disabled: false, ...props } }); wrappers.push(w); return w; };
const button = (w: ReturnType<typeof mount>, text: string) => w.findAll("button").find(item => item.text() === text)!;
const cache = (payload = saved.first_payload) => sessionStorage.setItem(cacheKey, JSON.stringify({ requirement_sha256: saved.first.requirement_version_sha256,
  coverage_sha256: saved.first.coverage_sha256, pending: payload, confirmedFailure: false }));
let matrix: Matrix; let history: Review[];
async function edit(w: ReturnType<typeof mount>) {
  await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id); await flushPromises();
  await button(w, "选择正文核验 1").trigger("click");
  await w.get('[aria-label="核验 1 验证点 retention"]').setValue(true);
  await w.get('[aria-label="执行确认说明"]').setValue(saved.first_payload.note);
}
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => saved.first_payload.request_id });
  matrix = basis(); history = [];
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "active" } });
  project.getTestRun.mockResolvedValue(run()); project.getTestCase.mockResolvedValue(saved.selected_case.case);
  project.listTestCaseVersions.mockResolvedValue([saved.selected_case.version]);
  service.listRequirementExecution.mockImplementation((_p, _d, _r, _v, _l, offset = 0) => Promise.resolve(page(clone(history), offset)));
  service.listRequirementExecutionCandidates.mockResolvedValue(page([candidate()]));
  service.readRequirementMatrix.mockImplementation((_p, _d, _r, _v, _c, choice) => Promise.resolve(clone(choice === "initial" ? basis() : choice === saved.first.id ? saved.first_matrix : matrix)));
  service.getRequirementExecutionSubmission.mockResolvedValue(first());
  service.submitRequirementExecution.mockImplementation(async () => { matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()]; return first(); });
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("reads a real body failure, full 12-step JSON, False/0/null and explicitly selects its point", async () => {
  const w = panel(); await edit(w);
  expect(w.text()).toContain("核对完整操作11"); expect(w.text()).toContain("false"); expect(w.text()).toContain("null");
  expect(w.text()).toContain("全部正文与False/0/null保留"); expect(w.text()).toContain("原运行总体结果 failed");
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementExecution).toHaveBeenCalledWith(saved.first.project_id, saved.first.document_id,
    saved.first.requirement_id, saved.first.requirement_version_id, normalizedExecution(saved.first_payload as Payload));
  expect(w.text()).toContain("执行确认已保存："+saved.first.id); expect(sessionStorage.getItem(cacheKey)).toBeNull();
});
it("requires an explicit point selection and does not infer full requirement success", async () => {
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id); await flushPromises();
  await button(w, "选择正文核验 1").trigger("click"); await w.get('[aria-label="执行确认说明"]').setValue("原实际检查");
  expect(button(w, "保存正文执行确认").attributes("disabled")).toBeDefined();
  expect(w.text()).toContain("仅所选正文检查参与判定"); expect(service.submitRequirementExecution).not.toHaveBeenCalled();
});
it("retains a durable unknown POST and recovers on reopen without another submission", async () => {
  service.submitRequirementExecution.mockImplementation(async () => { matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()]; throw new HttpError("lost reply", 503); });
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("提交结果尚未确认"); expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
  w.unmount(); const reopened = panel(); await flushPromises();
  expect(reopened.text()).toContain("执行确认已保存："+saved.first.id);
  expect(service.submitRequirementExecution).toHaveBeenCalledTimes(1); expect(sessionStorage.getItem(cacheKey)).toBeNull();
});
it.each([404, 503])("keeps original parameters on recovery %s", async status => {
  cache(); service.getRequirementExecutionSubmission.mockRejectedValue(new HttpError("actual GET failed", status));
  const w = panel(); await flushPromises(); expect(w.text()).toContain(saved.first_payload.request_id);
  expect(w.text()).toContain(status === 404 ? "不能据此判断未提交" : "actual GET failed");
  expect(service.submitRequirementExecution).not.toHaveBeenCalled(); expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
});
it("rejects mismatched recovered parameters without losing the original request", async () => {
  cache(); service.getRequirementExecutionSubmission.mockResolvedValue({ ...first(), request: { ...first().request, note: "different" } });
  const w = panel(); await flushPromises(); expect(w.text()).toContain("原请求参数不一致"); expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
});
it("explicitly retries exactly the saved request after transport failure", async () => {
  service.submitRequirementExecution.mockRejectedValueOnce(new Error("network lost"));
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  await button(w, "重试原执行确认").trigger("click"); await flushPromises();
  expect(service.submitRequirementExecution.mock.calls[1]).toEqual(service.submitRequirementExecution.mock.calls[0]);
});
it("requires a deliberate re-read after 409 and keeps historical views separate from edit rows", async () => {
  service.submitRequirementExecution.mockRejectedValue(new HttpError("conflict", 409));
  const w = panel(); await edit(w); await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("原请求已明确拒绝"); expect(button(w, "重试原执行确认")).toBeUndefined();
  matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  await button(w, "重读依据并确认执行").trigger("click"); await flushPromises();
  await w.get('[aria-label="执行确认历史"]').setValue("initial"); await flushPromises();
  expect((w.vm as unknown as { rows: unknown[] }).rows).toHaveLength(1);
  expect(service.submitRequirementExecution).toHaveBeenCalledTimes(1); expect(sessionStorage.getItem(cacheKey)).toBeNull();
});
it("uses the global newest parent even when it belongs to another coverage basis", async () => {
  const other = { ...first(), id: saved.second.id, revision: 2, request: { ...first().request, coverage_id: "initial", expected_execution_id: first().id }, previous_execution_id: first().id };
  matrix = clone(saved.first_matrix) as unknown as Matrix; history = [other, first()];
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行确认说明"]').setValue("核对全局父确认");
  expect(w.get(`option[value="${other.id}"]`).attributes("disabled")).toBeDefined();
  service.submitRequirementExecution.mockRejectedValue(new HttpError("test capture", 409));
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementExecution.mock.calls[0][4].expected_execution_id).toBe(other.id);
  expect(service.submitRequirementExecution.mock.calls[0][4].coverage_id).toBe(saved.coverage.id);
});
it("submits an explicit empty selection to clear instead of changing prior records", async () => {
  matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  const w = panel(); await flushPromises(); await button(w, "移除正文核验 1").trigger("click");
  await w.get('[aria-label="执行确认说明"]').setValue("撤销此集合");
  service.submitRequirementExecution.mockRejectedValue(new HttpError("test capture", 409));
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementExecution.mock.calls[0][4]).toMatchObject({ checks: [], expected_execution_id: saved.first.id });
});
it("allows full original reads and request recovery after project archive while disabling new confirmation", async () => {
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "archived" } });
  cache(); matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id); await flushPromises();
  expect(w.text()).toContain("核对完整操作11"); expect(button(w, "保存正文执行确认").attributes("disabled")).toBeDefined();
  expect(w.text()).toContain("执行确认已保存"); expect(service.submitRequirementExecution).not.toHaveBeenCalled();
});
it.each(["project", "version", "hash", "body"])("rejects a mismatched original %s", async kind => {
  if (kind === "project") project.getTestRun.mockResolvedValue(run(rawResult(), "foreign"));
  if (kind === "version") project.listTestCaseVersions.mockResolvedValue([{ ...saved.selected_case.version, id: saved.first.id }]);
  if (kind === "hash") project.listTestCaseVersions.mockResolvedValue([{ ...saved.selected_case.version, content_hash: "0".repeat(64) }]);
  if (kind === "body") { const detail = run(); (detail.results[0].actual.verification_results as { metadata: { phase: string } }[]).forEach(v => { v.metadata.phase = "before"; }); project.getTestRun.mockResolvedValue(detail); }
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id); await flushPromises();
  expect(w.find('[role="alert"]').exists()).toBe(true); expect(button(w, "选择正文核验 1")).toBeUndefined();
});
it("keeps duplicate and nonadvancing pages visible as failures", async () => {
  service.listRequirementExecutionCandidates.mockResolvedValue(page([candidate()], 0, true));
  const w = panel(); await flushPromises();
  service.listRequirementExecutionCandidates.mockResolvedValue(page([candidate()], 1, false));
  await button(w, "读取更多执行结果").trigger("click"); await flushPromises(); expect(w.text()).toContain("候选身份或分页不一致");
});
it("shows missing body-check evidence without inventing checks", async () => {
  service.listRequirementExecutionCandidates.mockResolvedValue(page([{ ...candidate(), checks: [], warnings: ["原结果没有可关联的结构化正文核验"] }]));
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id); await flushPromises();
  expect(w.text()).toContain("原结果没有可关联的结构化正文核验"); expect(button(w, "选择正文核验 1")).toBeUndefined();
});
it("does not accept duplicate global history pages", async () => {
  matrix = clone(saved.first_matrix) as unknown as Matrix; history = [first()];
  service.listRequirementExecution.mockResolvedValue(page([first()], 0, true));
  const w = panel(); await flushPromises(); service.listRequirementExecution.mockResolvedValue(page([first()], 1));
  await button(w, "读取更多执行确认").trigger("click"); await flushPromises(); expect(w.text()).toContain("历史分页重复");
});
it("preserves cache on a fixed hash mismatch", async () => {
  cache(); const raw = JSON.parse(sessionStorage.getItem(cacheKey)!); raw.coverage_sha256 = "0".repeat(64); sessionStorage.setItem(cacheKey, JSON.stringify(raw));
  const w = panel(); await flushPromises(); expect(w.text()).toContain("覆盖哈希不一致"); expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
});
it("does not let a late response overwrite a new project context", async () => {
  let finish!: (v: TestRunDetail) => void; project.getTestRun.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const w = panel(); await flushPromises(); await w.get('[aria-label="执行候选结果"]').setValue(rawResult().id);
  const next = basis(); next.requirement.project_id = "other-project"; await w.setProps({ matrix: next });
  finish(run()); await flushPromises(); expect(w.text()).not.toContain("核对完整操作11");
});
it("reads original cleanup failure/body success and rejects client-supplied verdicts", () => {
  const result = clone(cleanup.results[0]) as unknown as ReturnType<typeof rawResult>;
  const cand = { ...candidate(), result_id: result.id, run_id: result.run_id, case_id: result.case_id, case_version_id: result.case_version_id,
    checks: summaries(result), status: result.status };
  const value = readBodyChecks(cand as never, run(result, cleanup.coverage.project_id), cleanup.matrix as unknown as Matrix);
  expect(value.result.status).toBe("failed"); expect(value.checks.map(c => c.status)).toEqual(["passed", "passed", "passed", "passed", "passed"]);
  expect(() => validateExecutionRequest({ ...saved.first_payload, checks: [{ ...saved.first_payload.checks[0], verdict: "passed" }] } as unknown as Payload, basis())).toThrow();
});
