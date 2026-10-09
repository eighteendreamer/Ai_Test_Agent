// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import observed from "../../../../../docs/upgrade_2026-10-03/p1-document-generation-verified/summary.json";
import DocumentGenerationPanel from "./DocumentGenerationPanel.vue";
import CaseGenerationTask from "./CaseGenerationTask.vue";
import { assertGenerationScope } from "./caseGenerationProduct";
import { HttpError } from "../../../services/http";
import type { DocumentGenerationScope, GenerationGroup } from "../../../services/caseGeneration.types";
import type { DocumentParseSnapshotView } from "../../../types";

const service = vi.hoisted(() => ({ submitDocumentGeneration: vi.fn(), getCaseGenerationSubmission: vi.fn(),
  readCaseGeneration: vi.fn(), readCaseGenerationScope: vi.fn(), listCaseGenerationRetries: vi.fn(),
  getCaseGenerationTask: vi.fn(), getCaseGenerationRetrySubmission: vi.fn() }));
vi.mock("../../../services/caseGeneration.api", () => service);
const api = vi.hoisted(() => ({ getProjectOverview: vi.fn(), getTestCase: vi.fn(), listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const clone = <T,>(v: T): T => structuredClone(v);
const wrappers: ReturnType<typeof mount>[] = [];
const view = () => ({ snapshot: clone(observed.fixed_scope.parse_snapshot), result: { status: "complete" } }) as unknown as DocumentParseSnapshotView;
const key = () => "qa-agent:document-generation:" + JSON.stringify([observed.group.project_id, observed.group.document_id, observed.fixed_scope.parse_snapshot.id]);
function panel(canWrite = true) {
  const w = mount(DocumentGenerationPanel, { props: { view: view(), canWrite }, global: { stubs: { RouterLink: true } } });
  wrappers.push(w); return w;
}
function button(w: ReturnType<typeof mount>, label: string) { return w.findAll("button").find(b => b.text() === label)!; }
function pending() { return { ...clone(observed.fixed_scope.request), creation_key: observed.group.creation_key }; }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear(); sessionStorage.clear();
  service.readCaseGeneration.mockResolvedValue(clone(observed.report));
  service.readCaseGenerationScope.mockResolvedValue(clone(observed.fixed_scope));
  service.getCaseGenerationSubmission.mockResolvedValue(clone(observed.group));
  service.listCaseGenerationRetries.mockResolvedValue({ items: [], limit: 20, offset: 0, has_more: false });
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); });

it("recovers actual module progress and retains the original failed module without resubmitting", async () => {
  localStorage.setItem(key(), JSON.stringify({ request: pending(), confirmedFailure: false }));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("模块进度 3 / 3；成功 2");
  expect(w.text()).toContain(observed.report.modules[1]!.id);
  expect(w.text()).toContain("新任务将复用已保存响应");
  expect(service.submitDocumentGeneration).not.toHaveBeenCalled();
  expect(service.getCaseGenerationSubmission).toHaveBeenCalledWith(observed.group.project_id, observed.group.document_id, observed.group.creation_key);
});
it("keeps an unknown submission identity after a lost response and allows read-only recovery", async () => {
  service.submitDocumentGeneration.mockRejectedValue(new HttpError("结果未确认", 503));
  const w = panel(); await button(w, "提交普通文档模块生成").trigger("click"); await flushPromises();
  const stored = JSON.parse(localStorage.getItem(key())!);
  expect(stored.request.parse_snapshot_id).toBe(observed.fixed_scope.parse_snapshot.id);
  expect(stored.request.creation_key).toMatch(/^document-generation-/);
  expect(stored.confirmedFailure).toBe(false);
  expect(button(w, "提交普通文档模块生成").attributes("disabled")).toBeDefined();
  expect(w.text()).not.toContain("新建生成请求");
  await button(w, "回查原普通文档生成").trigger("click"); await flushPromises();
  expect(service.submitDocumentGeneration).toHaveBeenCalledTimes(1);
  expect(service.getCaseGenerationSubmission.mock.calls[0]![2]).toBe(stored.request.creation_key);
});
it("allows correction only after a definite rejected submission", async () => {
  service.submitDocumentGeneration.mockRejectedValue(new HttpError("输入有误", 422));
  const w = panel(); await button(w, "提交普通文档模块生成").trigger("click"); await flushPromises();
  await button(w, "修改被拒绝的生成请求").trigger("click"); await flushPromises();
  expect(button(w, "提交普通文档模块生成").attributes("disabled")).toBeUndefined();
  expect(JSON.parse(localStorage.getItem(key())!).request).toBeNull();
});
it("rejects another snapshot's restored request instead of submitting a replacement", async () => {
  localStorage.setItem(key(), JSON.stringify({ request: { ...pending(), parse_snapshot_id: "wrong" } }));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("当前快照不一致");
  expect(button(w, "提交普通文档模块生成").attributes("disabled")).toBeDefined();
  expect(service.submitDocumentGeneration).not.toHaveBeenCalled();
});
it("uses code-point positions for the Python source and rejects a real range gap", () => {
  const scope = clone(observed.fixed_scope) as DocumentGenerationScope;
  scope.text += "😀"; scope.segments[scope.segments.length - 1]!.end_char += 1;
  expect(() => assertGenerationScope(scope, observed.group as GenerationGroup)).not.toThrow();
  scope.segments[1]!.start_char += 1;
  expect(() => assertGenerationScope(scope, observed.group as GenerationGroup)).toThrow("全文范围不同");
});
it("displays ordinary source and complete actual draft JSON in the shared task detail", async () => {
  const task = observed.report.modules[0]!; const version = observed.versions[0]!;
  api.getProjectOverview.mockResolvedValue({ project: { id: observed.group.project_id, status: "active" } });
  api.getTestCase.mockResolvedValue({ id: version.case_id, project_id: observed.group.project_id });
  api.listTestCaseVersions.mockResolvedValue([clone(version)]);
  const w = mount(CaseGenerationTask, { props: { projectId: observed.group.project_id, documentId: observed.group.document_id,
    groupId: observed.group.id, taskId: task.id } }); wrappers.push(w); await flushPromises();
  expect(w.text()).toContain("普通文档生成 · 固定解析快照");
  await button(w, "读取完整任务草稿 " + version.id).trigger("click"); await flushPromises();
  expect(w.text()).toContain("手机号必须唯一"); expect(w.text()).toContain('"布尔": false');
});
it("disables ordinary generation for an archived project", async () => {
  const w = panel(false); await button(w, "提交普通文档模块生成").trigger("click"); await flushPromises();
  expect(service.submitDocumentGeneration).not.toHaveBeenCalled();
});
