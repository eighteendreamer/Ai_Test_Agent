// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import actual from "../../../../../docs/upgrade_2026-10-03/p4-production-validated/md-native-finish/snapshot-view.json";
import verification from "../../../../../docs/upgrade_2026-10-03/p4-production-validated/md-native-finish/verification.json";
import interrupted from "../../../../../docs/upgrade_2026-10-03/p4-production-validated/md-native-restart/snapshot-view.json";
import grouped from "../../../../../docs/upgrade_2026-10-03/p4-grouped-live-fixed/grouped/md-False/verification.json";
import { assertImpactSnapshot } from "./documentImpactIdentity";
import type { DocumentImpactSnapshotView, DocumentImpactTaskRecord } from "../../../services/documentImpacts.types";
import DocumentImpactTask from "./DocumentImpactTask.vue";
import DocumentImpactSnapshot from "./DocumentImpactSnapshot.vue";
import DocumentImpactPanel from "./DocumentImpactPanel.vue";
import DocumentImpactSettings from "./DocumentImpactSettings.vue";
import ImpactCaseVersionSelector from "./ImpactCaseVersionSelector.vue";

const api = vi.hoisted(() => ({
  getDocumentImpactTask: vi.fn(), readDocumentImpactSnapshot: vi.fn(), getProjectOverview: vi.fn(),
  listDocumentImpactTasks: vi.fn(), listDocumentImpactSnapshots: vi.fn(), getDocumentImpactSubmission: vi.fn(), submitDocumentImpactTask: vi.fn(),
  getDocumentImpactSettings: vi.fn(), getDocumentImpactAnalysisSettings: vi.fn(), getDocumentImpactBatchSettings: vi.fn(), getDocumentImpactInvocationSettings: vi.fn(),
  saveDocumentImpactSettings: vi.fn(), saveDocumentImpactAnalysisSettings: vi.fn(), saveDocumentImpactBatchSettings: vi.fn(), saveDocumentImpactInvocationSettings: vi.fn(),
  listTestCases: vi.fn(), listTestCaseVersions: vi.fn(),
  getDocumentImpactReasoningSettings: vi.fn(), saveDocumentImpactReasoningSettings: vi.fn(),
}));
vi.mock("../../../services/api", () => ({ api }));
const clone = <T,>(value: T): T => structuredClone(value);
const view = () => clone(actual) as unknown as DocumentImpactSnapshotView;
const task = (status: DocumentImpactTaskRecord["status"] = "completed") => ({ ...clone(verification.task), status,
  snapshot_id: status === "completed" ? verification.task.snapshot_id : null }) as DocumentImpactTaskRecord;
const seed = actual.snapshot.seed;
const identity = { projectId: seed.project_id, documentId: seed.document_id };
const panelProps = { ...identity, differenceSnapshotId: seed.difference_snapshot.id };
const empty = () => ({ items: [], has_more: false, offset: 0, limit: 20 });
const cacheKey = `qa-agent:document-impact:${identity.projectId}:${identity.documentId}`;
const wrappers: ReturnType<typeof mount>[] = [];
function keep(w: ReturnType<typeof mount>) { wrappers.push(w); return w; }
function button(w: ReturnType<typeof mount>, label: string) { return w.findAll("button").find(value => value.text() === label)!; }
function panel() { return keep(mount(DocumentImpactPanel, { props: panelProps, global: { stubs: {
  DocumentImpactSettings: true, ImpactCaseVersionSelector: true,
  DocumentImpactTask: { name: "DocumentImpactTask", props: ["taskId", "request"], emits: ["updated"], template: '<div class="task-view">{{ taskId }}</div>' },
} } })); }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => "original-impact-request" });
  api.getDocumentImpactTask.mockResolvedValue(task()); api.readDocumentImpactSnapshot.mockResolvedValue(view());
  api.getProjectOverview.mockResolvedValue({ project: { id: identity.projectId, status: "active" } });
  api.listDocumentImpactTasks.mockResolvedValue(empty()); api.listDocumentImpactSnapshots.mockResolvedValue(empty());
  api.submitDocumentImpactTask.mockImplementation((_p, _d, request) => Promise.resolve({ ...task("queued"), creation_key: request.creation_key }));
  api.getDocumentImpactSubmission.mockResolvedValue({ ...task("queued"), creation_key: "original-impact-request" });
  const records = [seed.config, seed.analysis_context.config, seed.batch_settings, seed.invocation_snapshot.config];
  [api.getDocumentImpactSettings, api.getDocumentImpactAnalysisSettings, api.getDocumentImpactBatchSettings, api.getDocumentImpactInvocationSettings].forEach((fn, i) => fn.mockResolvedValue(clone(records[i])));
  [api.saveDocumentImpactSettings, api.saveDocumentImpactAnalysisSettings, api.saveDocumentImpactBatchSettings, api.saveDocumentImpactInvocationSettings]
    .forEach(fn => fn.mockImplementation((_project, payload) => Promise.resolve({ scope_key: identity.projectId, revision: 99, payload })));
  api.getDocumentImpactReasoningSettings.mockResolvedValue({ scope_key: "global", revision: 1,
    payload: { max_group_units: 64, max_group_read_bytes: 256000 } });
  api.saveDocumentImpactReasoningSettings.mockImplementation((_project, payload) => Promise.resolve({
    scope_key: identity.projectId, revision: 2, payload }));
});
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("renders actual grouped tasks separately from all evidence and retains individual reasons", () => {
  const saved = clone({ snapshot: grouped.snapshot, result: grouped.result }) as unknown as DocumentImpactSnapshotView;
  assertImpactSnapshot(saved, grouped.snapshot.seed.project_id, grouped.snapshot.seed.document_id);
  const w = keep(mount(DocumentImpactSnapshot, { props: { view: saved } }));
  expect(w.get('[aria-label="固定推理任务"]').text()).toContain("1 个文档发现任务 · 4 个证据项 · 1 个完整用例判断");
  expect(w.text()).toContain("处理说明：已完整对照第1项");
  expect(w.findAll(".impact-rule")).toHaveLength(1);
  expect(saved.result.units.filter(unit => unit.unit.kind !== "case").every(unit => unit.candidates.length === 0)).toBe(true);
});

it("rejects a foreign grouped plan and copied evidence candidates before displaying a result", () => {
  const saved = clone({ snapshot: grouped.snapshot, result: grouped.result }) as unknown as DocumentImpactSnapshotView;
  saved.result.reasoning_checkpoint!.task_id = "foreign";
  expect(() => assertImpactSnapshot(saved, grouped.snapshot.seed.project_id, grouped.snapshot.seed.document_id)).toThrow("不一致");
  saved.result.reasoning_checkpoint!.task_id = saved.snapshot.task_id;
  saved.result.units[0].candidates = [saved.result.rules[0].candidate];
  expect(() => assertImpactSnapshot(saved, grouped.snapshot.seed.project_id, grouped.snapshot.seed.document_id)).toThrow("不一致");
});

it("renders actual partial results, rule citations and separate case-unit impacts including false/0/null", async () => {
  const w = keep(mount(DocumentImpactTask, { props: { ...identity, taskId: task().id } })); await flushPromises();
  expect(w.text()).toContain("部分结果"); expect(w.text()).toContain("语义结论待评审"); expect(w.text()).toContain("是否生效未知");
  expect(w.text()).toContain("订单保留7天调整为9天"); expect(w.text()).toContain("重大影响候选");
  expect(w.text()).toContain("7 天"); expect(w.text()).toContain("9 天");
  for (const value of ["false", "null", "0", "extracted_text_positions"]) expect(w.text()).toContain(value);
  expect(w.findComponent(DocumentImpactSnapshot).exists()).toBe(true);
  const count = api.getDocumentImpactTask.mock.calls.length; await vi.advanceTimersByTimeAsync(4000);
  expect(api.getDocumentImpactTask).toHaveBeenCalledTimes(count);
  expect(api.submitDocumentImpactTask).not.toHaveBeenCalled();
});

it("shows the actual interrupted worker's unknown call and retained evidence limitations", () => {
  const w = keep(mount(DocumentImpactSnapshot, { props: { view: clone(interrupted) as unknown as DocumentImpactSnapshotView } }));
  expect(w.text()).toContain("调用结果未知"); expect(w.text()).toContain("部分结果");
  const unknown = interrupted.result.units.find(unit => unit.status === "unknown")!;
  for (const code of unknown.reason_codes) expect(w.text()).toContain(code);
  expect(w.text()).toContain("没有保存规则候选"); expect(w.text()).toContain("语义结论待评审");
});

it("escapes external candidate text and downloads the entire fixed result, preserving literal JSON values", async () => {
  const saved = view(); saved.result.rules[0]!.candidate.summary = '<img src=x onerror="alert(1)">';
  const w = keep(mount(DocumentImpactSnapshot, { props: { view: saved } }));
  expect(w.find("img").exists()).toBe(false); expect(w.text()).toContain('<img src=x onerror="alert(1)">');
  const create = vi.fn().mockReturnValue("blob:impact"); const revoke = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke }); vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await button(w, "下载完整影响结果 JSON").trigger("click");
  expect(create).toHaveBeenCalledTimes(1); expect(revoke).toHaveBeenCalledWith("blob:impact");
});

it.each(["project", "task", "snapshot", "difference", "input-hash", "coverage"])("rejects mismatched saved %s identity", async mismatch => {
  const record = task(); const saved = view();
  if (mismatch === "project") record.project_id = "foreign";
  if (mismatch === "task") record.id = "foreign";
  if (mismatch === "snapshot") saved.snapshot.id = "foreign";
  if (mismatch === "difference") saved.snapshot.seed = { ...saved.snapshot.seed, difference_snapshot: { id: "foreign" } };
  if (mismatch === "input-hash") saved.result.input_sha256 = "0".repeat(64);
  if (mismatch === "coverage") saved.snapshot.analyzed_count = 0;
  api.getDocumentImpactTask.mockResolvedValue(record); api.readDocumentImpactSnapshot.mockResolvedValue(saved);
  const w = keep(mount(DocumentImpactTask, { props: { ...identity, taskId: task().id } })); await flushPromises();
  expect(w.findComponent(DocumentImpactSnapshot).exists()).toBe(false); expect(w.get('[role="alert"]').text()).toContain("不一致");
});

it("discards a late result after switching task and stops polling a genuine failed state", async () => {
  let release!: (value: DocumentImpactSnapshotView) => void;
  api.readDocumentImpactSnapshot.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const w = keep(mount(DocumentImpactTask, { props: { ...identity, taskId: task().id } })); await flushPromises();
  api.getDocumentImpactTask.mockResolvedValue({ ...task("running"), id: "next" });
  await w.setProps({ taskId: "next" }); await flushPromises(); release(view()); await flushPromises();
  expect(w.text()).toContain("next"); expect(w.findComponent(DocumentImpactSnapshot).exists()).toBe(false);
  api.getDocumentImpactTask.mockResolvedValue({ ...task("failed"), id: "next", error_code: "document_impact_deadline", message: "原预算已耗尽" });
  await vi.advanceTimersByTimeAsync(1000); await flushPromises(); expect(w.text()).toContain("原预算已耗尽");
  const count = api.getDocumentImpactTask.mock.calls.length; await vi.advanceTimersByTimeAsync(5000);
  expect(api.getDocumentImpactTask).toHaveBeenCalledTimes(count);
});

it("recovers a lost submit response using the original request after remount and comparison selection changes", async () => {
  api.submitDocumentImpactTask.mockRejectedValueOnce(new Error("回执丢失"));
  const first = panel(); await flushPromises();
  await first.get('[aria-label="影响环境声明"]').setValue("qa");
  await first.get('[aria-label="影响发布版本声明"]').setValue("future-v2");
  await first.get("form").trigger("submit"); await flushPromises();
  const original = clone(api.submitDocumentImpactTask.mock.calls[0][2]);
  expect(first.text()).toContain("回执丢失"); first.unmount();
  const next = panel(); await flushPromises(); await next.setProps({ differenceSnapshotId: "new-comparison" }); await flushPromises();
  expect(api.submitDocumentImpactTask).toHaveBeenCalledTimes(1);
  expect(JSON.parse(sessionStorage.getItem(cacheKey)!).payload).toEqual(original);
  expect(next.getComponent({ name: "DocumentImpactTask" }).props("request")).toEqual(original);
  expect(next.text()).toContain("new-comparison"); expect(next.text()).toContain(original.difference_snapshot_id);
});

it("retries an unconfirmed submit with unchanged identity/range rather than current inputs", async () => {
  api.submitDocumentImpactTask.mockRejectedValueOnce(new Error("未确认"));
  const w = panel(); await flushPromises(); await w.get("form").trigger("submit"); await flushPromises();
  const original = clone(api.submitDocumentImpactTask.mock.calls[0][2]);
  await button(w, "重试原影响提交").trigger("click"); await flushPromises();
  expect(api.submitDocumentImpactTask.mock.calls[1][2]).toEqual(original);
  expect(w.find(".task-view").exists()).toBe(true);
});

it("opens exact older task history without a pending task replacing the selection", async () => {
  api.listDocumentImpactTasks.mockResolvedValue({ ...empty(), items: [{ ...task(), id: "older" }] });
  const w = panel(); await flushPromises(); await w.get("form").trigger("submit"); await flushPromises();
  await button(w, "查看历史影响任务 older").trigger("click");
  expect(w.getComponent({ name: "DocumentImpactTask" }).props("taskId")).toBe("older");
  expect(w.getComponent({ name: "DocumentImpactTask" }).props("request")).toBeUndefined();
  w.getComponent({ name: "DocumentImpactTask" }).vm.$emit("updated", task()); await flushPromises();
  expect(w.getComponent({ name: "DocumentImpactTask" }).props("taskId")).toBe("older");
});

it("keeps archived projects read-only and rejects foreign catalog records", async () => {
  api.getProjectOverview.mockResolvedValue({ project: { id: identity.projectId, status: "archived" } });
  api.listDocumentImpactTasks.mockResolvedValue({ ...empty(), items: [{ ...task(), project_id: "foreign" }] });
  const w = panel(); await flushPromises(); expect(w.text()).toContain("不可提交"); expect(w.text()).toContain("不一致");
  await w.get("form").trigger("submit"); expect(api.submitDocumentImpactTask).not.toHaveBeenCalled();
});

it("ignores an old project catalog that arrives after switching document identity", async () => {
  let resolveOld!: (value: unknown) => void;
  api.listDocumentImpactTasks.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const w = panel(); await flushPromises();
  api.getProjectOverview.mockResolvedValue({ project: { id: "new-project", status: "active" } });
  await w.setProps({ projectId: "new-project", documentId: "new-document" }); await flushPromises();
  resolveOld({ ...empty(), items: [task()] }); await flushPromises();
  expect(w.text()).not.toContain(task().creation_key); expect(w.text()).not.toContain("不一致");
});

it("edits all five configuration payloads and preserves token and grouping contracts", async () => {
  const w = keep(mount(DocumentImpactSettings, { props: { projectId: identity.projectId, canWrite: true } })); await flushPromises();
  await w.get('[aria-label="任务总超时（秒）"]').setValue(300); await w.get('form[aria-label="影响范围设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactSettings.mock.calls[0][1].timeout_seconds).toBe(300);
  await w.get('[aria-label="影响分析指导"]').setValue("原文引用需要完整"); await w.get('form[aria-label="影响模型指导设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactAnalysisSettings.mock.calls[0][1].analysis_guidance).toBe("原文引用需要完整");
  await w.get('[aria-label="边界上下文字符"]').setValue(0); await w.get('form[aria-label="影响分段设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactBatchSettings.mock.calls[0][1].boundary_context_chars).toBe(0);
  await w.get('[aria-label="影响内容计量"]').setValue("tiktoken_content"); await w.get('[aria-label="影响计量编码"]').setValue("o200k_base");
  expect((w.get('[aria-label="影响请求发送策略"]').element as HTMLSelectElement).value).toBe("provider_defaults");
  await w.get('[aria-label="影响请求发送策略"]').setValue("single_attempt");
  await w.get('form[aria-label="影响调用预算设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactInvocationSettings.mock.calls[0][1].encoding_name).toBe("o200k_base");
  expect(api.saveDocumentImpactInvocationSettings.mock.calls[0][1].transport_policy).toBe("single_attempt");
  await w.get('[aria-label="影响内容计量"]').setValue("utf8_bytes_estimate"); await w.get('form[aria-label="影响调用预算设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactInvocationSettings.mock.calls[1][1].encoding_name).toBeNull();
  await w.get('[aria-label="每组证据数量上限"]').setValue(2);
  await w.get('form[aria-label="影响推理分组设置"]').trigger("submit"); await flushPromises();
  expect(api.saveDocumentImpactReasoningSettings.mock.calls[0][1]).toEqual({ max_group_units: 2, max_group_read_bytes: 256000 });
});

it("shows configuration persistence failure and does not accept a foreign settings response", async () => {
  const w = keep(mount(DocumentImpactSettings, { props: { projectId: identity.projectId, canWrite: true } })); await flushPromises();
  api.saveDocumentImpactSettings.mockRejectedValueOnce(new Error("配置数据库写入失败"));
  await w.get('form[aria-label="影响范围设置"]').trigger("submit"); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("配置数据库写入失败");
  api.saveDocumentImpactSettings.mockResolvedValue({ scope_key: "foreign", revision: 99, payload: seed.config.payload });
  await w.get('form[aria-label="影响范围设置"]').trigger("submit"); await flushPromises(); expect(w.text()).toContain("不一致");
});

it("chooses historical complete case versions from the current project without mode filtering", async () => {
  const reference = seed.cases[0];
  api.listTestCases.mockResolvedValue({ ...empty(), items: [reference.case] });
  const version = { id: reference.version_id, case_id: reference.case.id, version: 1, content_json: { steps: Array.from({ length: 12 }, (_, i) => i), value: false } };
  api.listTestCaseVersions.mockResolvedValue([version]);
  const w = keep(mount(ImpactCaseVersionSelector, { props: { projectId: identity.projectId, modelValue: [], disabled: false } })); await flushPromises();
  await button(w, `读取 ${reference.case.case_key} 的完整版本`).trigger("click"); await flushPromises();
  expect(api.listTestCases.mock.calls[0][1]).not.toHaveProperty("mode_key"); expect(w.text()).toContain("false");
  await w.get('input[type="checkbox"]').setValue(true); expect(w.emitted("update:modelValue")![0]).toEqual([[version.id]]);
});
