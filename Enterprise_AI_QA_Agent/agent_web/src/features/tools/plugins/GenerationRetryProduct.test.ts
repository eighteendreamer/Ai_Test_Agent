// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import observed from "../../../../../docs/upgrade_2026-10-03/p4-generation-retry-production-first/docx-native-finish/generation-retries.json";
import unknown from "../../../../../docs/upgrade_2026-10-03/p4-generation-retry-delivery/unknown-retry/verification.json";
import type { GenerationGroup, GenerationScope, GenerationTask, GenerationRetryView } from "../../../services/caseGeneration.types";
import { HttpError } from "../../../services/http";
import GenerationRetryPanel from "./GenerationRetryPanel.vue";

const service = vi.hoisted(() => ({ getGenerationSettings: vi.fn(), saveGenerationSettings: vi.fn(),
  submitCaseGeneration: vi.fn(), submitDocumentGeneration: vi.fn(), getCaseGenerationSubmission: vi.fn(), listCaseGenerations: vi.fn(), readCaseGeneration: vi.fn(),
  readCaseGenerationScope: vi.fn(), getCaseGenerationTask: vi.fn(), readCaseGenerationResponse: vi.fn(), retryCaseGeneration: vi.fn(),
  getCaseGenerationRetrySubmission: vi.fn(), listCaseGenerationRetries: vi.fn()
} satisfies Record<keyof typeof import("../../../services/caseGeneration.api"), ReturnType<typeof vi.fn>>));
vi.mock("../../../services/caseGeneration.api", () => service);
const api = vi.hoisted(() => ({ listTestCaseVersions: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const clone = <T,>(value: T): T => structuredClone(value);
const original = observed.items[0]!; const saved = observed.items[1]!;
const parent = () => clone(original.parent_report.modules[0]) as unknown as GenerationTask;
const group = () => clone(original.parent_report.group) as unknown as GenerationGroup;
const scope = () => clone(original.scope) as unknown as GenerationScope;
const savedProps = () => ({ parent: clone(saved.parent_report.modules[0]) as unknown as GenerationTask,
  group: clone(saved.parent_report.group) as unknown as GenerationGroup, scope: clone(saved.scope) as unknown as GenerationScope });
const value = () => clone(original.view) as unknown as GenerationRetryView;
const key = () => `qa-agent:generation-retry:${group().project_id}:${group().document_id}:${parent().id}`;
const wrappers: ReturnType<typeof mount>[] = [];
function panel(changes = {}) {
  const w = mount(GenerationRetryPanel, { props: { parent: parent(), group: group(), scope: scope(), canWrite: true, disabled: false, ...changes } });
  wrappers.push(w); return w;
}
const button = (w: ReturnType<typeof mount>, label: string) => w.findAll("button").find(item => item.text() === label)!;
const newButton = (w: ReturnType<typeof mount>) => w.findAll("button").find(item => item.text().startsWith("为失败模块创建新任务 "))!;
const pendingCache = (creationKey = original.payload.creation_key) => ({ pending: { creation_key: creationKey }, confirmedFailure: false, expanded: [] });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; };
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => original.payload.creation_key });
  service.listCaseGenerationRetries.mockResolvedValue({ items: [], has_more: false, limit: 20, offset: 0 });
  service.retryCaseGeneration.mockResolvedValue(value()); service.getCaseGenerationRetrySubmission.mockResolvedValue(value());
  service.readCaseGenerationResponse.mockResolvedValue(clone(original.response));
  api.listTestCaseVersions.mockResolvedValue([clone(original.draft)]);
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); });

it("uses only creation_key for a new task while retaining the actual original failure", async () => {
  const w = panel(); await flushPromises(); await newButton(w).trigger("click"); await flushPromises();
  expect(service.retryCaseGeneration).toHaveBeenCalledWith(group().project_id, group().document_id, parent().id, original.payload);
  expect(w.text()).toContain(parent().id); expect(w.text()).toContain("原失败任务继续保留");
  expect(w.text()).toContain(original.view.task.id); expect(w.text()).toContain("重试草稿或候选已保存");
  expect(sessionStorage.getItem(key())).toBeNull();
});

it("recovers an accepted lost reply after remount without another POST", async () => {
  service.retryCaseGeneration.mockRejectedValueOnce(new Error("saved POST reply lost"));
  const w = panel(); await flushPromises(); await newButton(w).trigger("click"); await flushPromises();
  expect(JSON.parse(sessionStorage.getItem(key())!)).toEqual(pendingCache()); w.unmount();
  const restored = panel({ canWrite: false }); await flushPromises();
  expect(service.getCaseGenerationRetrySubmission).toHaveBeenCalledWith(group().project_id, group().document_id, parent().id, original.payload.creation_key);
  expect(service.retryCaseGeneration).toHaveBeenCalledTimes(1); expect(restored.text()).toContain(original.view.task.id);
  expect(newButton(restored).attributes("disabled")).toBeDefined();
});

it("retains unknown through 404 and replays the exact original identifier", async () => {
  service.retryCaseGeneration.mockRejectedValueOnce(new HttpError("unknown reply", 503));
  const w = panel(); await flushPromises(); await newButton(w).trigger("click"); await flushPromises();
  service.getCaseGenerationRetrySubmission.mockRejectedValueOnce(new HttpError("not yet found", 404));
  await button(w, "查询原模块重试提交").trigger("click"); await flushPromises();
  expect(w.text()).toContain("尚未查到原重试提交"); expect(JSON.parse(sessionStorage.getItem(key())!)).toEqual(pendingCache());
  await button(w, "重试原模块提交").trigger("click"); await flushPromises();
  expect(service.retryCaseGeneration.mock.calls[1]![3]).toEqual(original.payload);
});

it("preserves pending when the returned parent, scope, module or original key is wrong", async () => {
  for (const field of ["parent_task_id", "scope_sha256", "module_sha256", "creation_key"] as const) {
    const wrong = value(); wrong.retry[field] = "wrong"; service.retryCaseGeneration.mockResolvedValueOnce(wrong);
    const w = panel(); await flushPromises(); await newButton(w).trigger("click"); await flushPromises();
    expect(w.text()).toContain("原编号继续保留"); expect(JSON.parse(sessionStorage.getItem(key())!)).toEqual(pendingCache());
    w.unmount(); sessionStorage.clear();
  }
});

it("does not resend unknown invocation or tasks with saved receipts", async () => {
  for (const task of [clone(unknown.tasks[0]),
    { ...value().task, status: "failed" }] as GenerationTask[]) {
    const w = panel({ parent: task }); await flushPromises();
    expect(w.findAll("button").some(item => item.text().startsWith("为失败模块创建新任务"))).toBe(false); w.unmount();
  }
  expect(service.retryCaseGeneration).not.toHaveBeenCalled();
});

it("shows actual saved-response reuse separately and rejects changed response references", async () => {
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [clone(saved.view)], has_more: false, limit: 20, offset: 0 });
  const w = panel(savedProps()); await flushPromises(); expect(w.text()).toContain("复用原持久响应");
  const wrong = clone(saved.view); wrong.retry.response!.sha256 = "wrong";
  service.getCaseGenerationRetrySubmission.mockResolvedValueOnce(wrong);
  await button(w, `刷新重试任务 ${saved.view.task.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("原编号继续保留");
});

it("reads the whole actual draft and original response and rejects a mismatched draft hash", async () => {
  service.listCaseGenerationRetries.mockResolvedValue({ items: [value()], has_more: false, limit: 20, offset: 0 });
  const w = panel(); await flushPromises(); await button(w, `读取完整重试草稿 ${original.draft.id}`).trigger("click"); await flushPromises();
  const draft = w.findAll("pre").map(item => JSON.parse(item.text())).find(item => item.id === original.draft.id);
  expect(draft).toEqual(original.draft); expect(draft.content_json.steps).toHaveLength(12);
  await button(w, `读取重试原响应 ${value().task.id}`).trigger("click"); await flushPromises();
  expect(w.findAll("pre").map(item => JSON.parse(item.text())).find(item => item.raw_response)).toEqual(original.response);
  api.listTestCaseVersions.mockResolvedValueOnce([{ ...clone(original.draft), content_hash: "wrong" }]);
  await button(w, `读取完整重试草稿 ${original.draft.id}`).trigger("click"); await flushPromises();
  expect(w.text()).toContain("与子任务收据不一致");
});

it("requires explicit ending of a definitively rejected key before creating another", async () => {
  service.retryCaseGeneration.mockRejectedValueOnce(new HttpError("known configuration rejected", 409));
  const w = panel(); await flushPromises(); await newButton(w).trigger("click"); await flushPromises();
  expect(w.text()).toContain("原重试提交已明确拒绝"); expect(button(w, "重试原模块提交")).toBeUndefined();
  expect(newButton(w).attributes("disabled")).toBeDefined();
  await button(w, "结束已拒绝重试编号").trigger("click"); await flushPromises();
  expect(newButton(w).attributes("disabled")).toBeUndefined(); expect(sessionStorage.getItem(key())).toBeNull();
});

it("ignores late history and reads the new project when the parent changes", async () => {
  const delayed = deferred<{ items: GenerationRetryView[]; has_more: boolean; limit: number; offset: number }>();
  service.listCaseGenerationRetries.mockReturnValueOnce(delayed.promise);
  const w = panel(); await w.setProps(savedProps());
  await flushPromises(); delayed.resolve({ items: [value()], has_more: false, limit: 20, offset: 0 }); await flushPromises();
  expect(w.text()).not.toContain(original.view.task.id); expect(w.text()).toContain(saved.parent_report.modules[0]!.id);
});

it("disables downloads and ignores late response after parent replacement", async () => {
  service.listCaseGenerationRetries.mockResolvedValue({ items: [value()], has_more: false, limit: 20, offset: 0 });
  const w = panel(); await flushPromises(); const delayed = deferred<typeof original.response>(); service.readCaseGenerationResponse.mockReturnValueOnce(delayed.promise);
  await button(w, `读取重试原响应 ${value().task.id}`).trigger("click");
  expect(button(w, `下载重试关系与结果 ${value().task.id}`).attributes("disabled")).toBeDefined();
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [], has_more: false, limit: 20, offset: 0 });
  await w.setProps(savedProps());
  delayed.resolve(clone(original.response)); await flushPromises(); expect(w.text()).not.toContain("完整重试原响应");
});

it("does not trust corrupt pending cache or foreign-project history", async () => {
  sessionStorage.setItem(key(), JSON.stringify({ ...pendingCache(), pending: { ...original.payload, lease_token: "not-allowed" } }));
  const wrong = value(); wrong.task.project_id = "other-project";
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [wrong], has_more: false, limit: 20, offset: 0 });
  const w = panel(); await flushPromises(); expect(w.text()).toContain("保存的重试编号无效"); expect(w.text()).toContain("与原请求");
  expect(newButton(w).attributes("disabled")).toBeDefined(); expect(service.getCaseGenerationRetrySubmission).not.toHaveBeenCalled();
});

it("rejects duplicate and stalled pagination instead of extending coverage", async () => {
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [value()], has_more: true, limit: 20, offset: 0 });
  const w = panel(); await flushPromises();
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [value()], has_more: true, limit: 20, offset: 1 });
  await button(w, `更多模块重试 ${parent().id}`).trigger("click"); await flushPromises(); expect(w.text()).toContain("分页重复");
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [], has_more: true, limit: 20, offset: 0 });
  await button(w, `刷新模块重试历史 ${parent().id}`).trigger("click"); await flushPromises(); expect(w.text()).toContain("分页未前进");
});

it("waits for the containing historical group read before recovering a pending key", async () => {
  sessionStorage.setItem(key(), JSON.stringify(pendingCache())); const w = panel({ disabled: true }); await flushPromises();
  expect(service.getCaseGenerationRetrySubmission).not.toHaveBeenCalled(); await w.setProps({ disabled: false }); await flushPromises();
  expect(w.text()).toContain(original.view.task.id); expect(service.getCaseGenerationRetrySubmission).toHaveBeenCalledTimes(1);
});

it("persists explicit failed-child expansion and restores its pending query without submitting", async () => {
  const child = value(); child.task.status = "failed"; child.task.checkpoint = null; child.task.error_code = "input_budget_or_configuration_rejected";
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [child], has_more: false, limit: 20, offset: 0 });
  const w = panel(); await flushPromises(); await button(w, `查看失败子任务的重试 ${child.task.id}`).trigger("click"); await flushPromises();
  expect(JSON.parse(sessionStorage.getItem(key())!).expanded).toEqual([child.task.id]);
  expect(w.findAll('[aria-label^="模块重试 "]')).toHaveLength(2); expect(service.retryCaseGeneration).not.toHaveBeenCalled();
  const childKey = `qa-agent:generation-retry:${group().project_id}:${group().document_id}:${child.task.id}`;
  sessionStorage.setItem(childKey, JSON.stringify(pendingCache("child-pending"))); w.unmount();
  service.listCaseGenerationRetries.mockResolvedValueOnce({ items: [child], has_more: false, limit: 20, offset: 0 });
  service.getCaseGenerationRetrySubmission.mockRejectedValueOnce(new HttpError("child not yet found", 404));
  const restored = panel(); await flushPromises();
  expect(service.getCaseGenerationRetrySubmission).toHaveBeenCalledWith(group().project_id, group().document_id, child.task.id, "child-pending");
  expect(restored.text()).toContain("child-pending"); expect(service.retryCaseGeneration).not.toHaveBeenCalled();
});

it("uses fallback request identity and retains the key when session storage cannot persist", async () => {
  vi.stubGlobal("crypto", {}); service.retryCaseGeneration.mockRejectedValueOnce(new Error("reply unknown"));
  const w = panel(); await flushPromises(); const failStorage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
  await newButton(w).trigger("click"); await flushPromises();
  expect(service.retryCaseGeneration.mock.calls[0]![3].creation_key).toMatch(/^generation-retry-/);
  expect(w.text()).toContain("无法保存重试请求"); expect(w.text()).toContain("原重试请求编号"); failStorage.mockRestore();
});
