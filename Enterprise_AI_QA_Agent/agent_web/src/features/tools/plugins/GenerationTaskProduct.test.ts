// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import observed from "../../../../../docs/upgrade_2026-10-03/p4-coverage-product-app-verified/generation-product.json";
import retries from "../../../../../docs/upgrade_2026-10-03/p4-coverage-product-app-verified/generation-retry-product.json";
import CaseGenerationTask from "./CaseGenerationTask.vue";
import { HttpError } from "../../../services/http";
import type { GenerationGroupView } from "../../../services/caseGeneration.types";
const service = vi.hoisted(() => ({ getGenerationSettings: vi.fn(), saveGenerationSettings: vi.fn(), submitCaseGeneration: vi.fn(),
  getCaseGenerationSubmission: vi.fn(), listCaseGenerations: vi.fn(), readCaseGeneration: vi.fn(), readCaseGenerationScope: vi.fn(),
  getCaseGenerationTask: vi.fn(), readCaseGenerationResponse: vi.fn(), retryCaseGeneration: vi.fn(),
  getCaseGenerationRetrySubmission: vi.fn(), listCaseGenerationRetries: vi.fn()
} satisfies Record<keyof typeof import("../../../services/caseGeneration.api"), ReturnType<typeof vi.fn>>));
vi.mock("../../../services/caseGeneration.api", () => service);
const api = vi.hoisted(() => ({ getProjectOverview: vi.fn(), getTestCase: vi.fn(), listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const clone = <T,>(value: T): T => structuredClone(value);
const group = observed.normal.group; const task = observed.normal.modules[0]!;
const wrappers: ReturnType<typeof mount>[] = [];
function panel(changes = {}) { const w = mount(CaseGenerationTask, { props: { projectId:group.project_id,
  documentId:group.document_id, groupId:group.id, taskId:task.id, ...changes } }); wrappers.push(w); return w; }
function button(w: ReturnType<typeof mount>, text: string) { return w.findAll("button").find(item => item.text() === text)!; }
function retryPanel(index: number) {
  const item = retries.items[index]!;
  service.readCaseGeneration.mockResolvedValue(clone(item.parent_report)); service.readCaseGenerationScope.mockResolvedValue(clone(item.scope));
  service.getCaseGenerationTask.mockResolvedValue(clone(item.parent_report.modules[0])); service.getCaseGenerationRetrySubmission.mockResolvedValue(clone(item.view));
  api.getProjectOverview.mockResolvedValue({ project:{id:item.view.task.project_id,status:"active"} });
  api.getTestCase.mockResolvedValue({id:item.draft.case_id,project_id:item.view.task.project_id}); api.listTestCaseVersions.mockResolvedValue([clone(item.draft)]);
  return panel({projectId:item.view.task.project_id,documentId:item.view.task.document_id,groupId:item.parent_report.group.id,
    taskId:item.view.task.id,parentTaskId:item.view.retry.parent_task_id,retryKey:item.view.retry.creation_key});
}
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); sessionStorage.clear();
  service.readCaseGeneration.mockResolvedValue(clone(observed.normal)); service.readCaseGenerationScope.mockResolvedValue(clone(observed.scope));
  api.getProjectOverview.mockResolvedValue({project:{id:group.project_id,status:"active"}});
  api.getTestCase.mockResolvedValue(clone(observed.current_case)); api.listTestCaseVersions.mockResolvedValue([clone(observed.draft)]);
  service.readCaseGenerationResponse.mockResolvedValue(clone(observed.responses[0]));
  service.listCaseGenerationRetries.mockResolvedValue({items:[],limit:20,offset:0,has_more:false});
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); });

it("reads the observed fixed group and complete 12-step JSON/notes without generating", async () => {
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("草稿或停用候选已保存"); expect(w.text()).toContain(observed.scope.requirement.id);
  await button(w,`读取完整任务草稿 ${observed.draft.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("完整步骤11"); expect(w.text()).toContain('"布尔": false'); expect(w.text()).toContain('"零": 0');
  expect(w.text()).toContain(observed.draft.execution_notes!);
  await button(w,"读取固定任务原响应").trigger("click"); await flushPromises();
  expect(service.readCaseGenerationResponse).toHaveBeenCalledWith(group.project_id,group.document_id,task.id);
  expect(service.submitCaseGeneration).not.toHaveBeenCalled(); expect(service.retryCaseGeneration).not.toHaveBeenCalled();
});

it.each([0,1])("opens the actual retry strategy %i through the original request", async index => {
  const item = retries.items[index]!; const w = retryPanel(index); await flushPromises();
  expect(w.text()).toContain(item.view.retry.creation_key); expect(w.text()).toContain(item.view.retry.parent_task_id);
  expect(w.text()).toContain(index === 1 ? "复用已保存响应" : "执行原固定请求");
  expect(service.getCaseGenerationRetrySubmission).toHaveBeenCalledWith(item.view.task.project_id,item.view.task.document_id,
    item.view.retry.parent_task_id,item.view.retry.creation_key);
  await button(w,`读取完整任务草稿 ${item.draft.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("完整步骤11"); expect(service.retryCaseGeneration).not.toHaveBeenCalled();
});

it.each(["group","scope","task"])("rejects a mismatched fixed %s instead of falling back to latest", async field => {
  if (field === "group") service.readCaseGeneration.mockResolvedValue({...clone(observed.normal),group:{...clone(group),id:"foreign"}});
  if (field === "scope") service.readCaseGenerationScope.mockResolvedValue({...clone(observed.scope),sha256:"foreign"});
  const w = panel(field === "task" ? {taskId:"foreign"} : {}); await flushPromises();
  expect(w.get('[role="alert"]').text()).toMatch(/其他|不一致|不属于/);
  expect(w.find(".generation-task-status").exists()).toBe(false); expect(service.submitCaseGeneration).not.toHaveBeenCalled();
});

it("rejects a wrong retry child from the actual saved-request reply", async () => {
  const w = retryPanel(0); service.getCaseGenerationRetrySubmission.mockResolvedValue({...clone(retries.items[0]!.view),task:{...clone(retries.items[0]!.view.task),id:"foreign"}});
  await flushPromises(); expect(w.get('[role="alert"]').text()).toContain("其他父任务或子任务");
});

it("requires both parts of the retry navigation identity", async () => {
  const w = panel({parentTaskId:"parent"}); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("完整重试来源"); expect(service.readCaseGeneration).not.toHaveBeenCalled();
});

it("keeps archived history and full JSON readable while disabling new retries", async () => {
  const item = retries.items[0]!;
  service.readCaseGeneration.mockResolvedValue(clone(item.parent_report)); service.readCaseGenerationScope.mockResolvedValue(clone(item.scope));
  api.getProjectOverview.mockResolvedValue({project:{id:item.view.task.project_id,status:"archived"}});
  const w = panel({projectId:item.view.task.project_id,documentId:item.view.task.document_id,groupId:item.parent_report.group.id,taskId:item.view.retry.parent_task_id});
  await flushPromises(); expect(w.text()).toContain("生成任务失败");
  expect(button(w,`为失败模块创建新任务 ${item.view.retry.parent_task_id}`).attributes("disabled")).toBeDefined();
  expect(button(w,"刷新固定生成任务").attributes("disabled")).toBeUndefined();
});

it("allows fixed reads when project permission lookup fails and exposes that failure", async () => {
  api.getProjectOverview.mockRejectedValue(new Error("项目查询不可用")); const w = panel(); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("项目查询不可用"); expect(w.text()).toContain(task.id);
});

it.each(["project","hash"])("rejects a complete case whose %s differs from the saved receipt", async field => {
  if (field === "project") api.getTestCase.mockResolvedValue({...clone(observed.current_case),project_id:"foreign"});
  else api.listTestCaseVersions.mockResolvedValue([{...clone(observed.draft),content_hash:"foreign"}]);
  const w = panel(); await flushPromises(); await button(w,`读取完整任务草稿 ${observed.draft.id}`).trigger("click"); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("完整草稿与项目或固定收据不一致");
  expect(w.find(`[aria-label="完整任务草稿 ${observed.draft.id}"]`).exists()).toBe(false);
});

it("shows 503 without resubmitting and permits explicit read recovery", async () => {
  service.readCaseGeneration.mockRejectedValueOnce(new HttpError("读取暂不可用",503)); const w = panel(); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("读取暂不可用"); await button(w,"刷新固定生成任务").trigger("click"); await flushPromises();
  expect(w.find(".generation-task-status").exists()).toBe(true); expect(service.submitCaseGeneration).not.toHaveBeenCalled();
});

it("does not allow a late group response to replace a new task route", async () => {
  let resolve!: (value: unknown) => void;
  service.readCaseGeneration.mockImplementationOnce(() => new Promise(done => {resolve=done;}));
  const w = panel(); await flushPromises(); await w.setProps({taskId:"missing-task"}); await flushPromises();
  resolve(clone(observed.normal)); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("任务不属于"); expect(w.find(".generation-task-status").exists()).toBe(false);
});

it("polls an actual queued module and stops after unmount", async () => {
  const value = clone(observed.normal) as unknown as GenerationGroupView; value.modules[0]!.status="queued"; value.modules[0]!.checkpoint=null;
  service.readCaseGeneration.mockResolvedValue(value); const w=panel(); await flushPromises();
  expect(w.text()).toContain("等待生成"); service.readCaseGeneration.mockResolvedValue(clone(observed.normal));
  await vi.advanceTimersByTimeAsync(1500); await flushPromises(); expect(w.text()).toContain("草稿或停用候选已保存");
  const count=service.readCaseGeneration.mock.calls.length; w.unmount(); await vi.advanceTimersByTimeAsync(10000);
  expect(service.readCaseGeneration).toHaveBeenCalledTimes(count);
});
